"""Evidence-first configuration experiments for Club Lab.

This is a workflow recorder, not a configuration adapter. An authorized
PodPlay administrator performs product-side inspection; Club Lab retains only
safe enums and declared scope. No command here can toggle a flag, publish a
rate card, make a reservation, or quote a price.
"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Dict

from .facts import append_fact_locked, make_fact, reject_private_data
from .credentials import CredentialsError, PreviewCredentials
from .config import RunMode
from .firebase_auth import FirebaseAuthError, FirebaseAuthenticator
from .preview_readonly import PreviewReadError, PreviewReadonlyClient, collection_items
from .preview_seed import load_preview_seed
from .preview_write import PreviewRateCardsWriter, PreviewWriteError
from .storage import Storage
from .target_policy import TargetPolicy, TargetPolicyError
from .time import isoformat
from .uncertainty import make_claim


STATE_FILE = "configuration-experiments.json"
FEATURE_RATE_CARDS = "rate-cards-unified-pricing"
EXPERIMENT_ID = "experiment:rate-cards-unified-pricing:pp-7827"
STAFF = {"sofia", "riley"}
DIARY_STAFF = STAFF | {"morgan-vale"}
ADMIN = "podplay-admin"
ENGINES = {"LEGACY", "UNIFIED", "UNKNOWN"}
SCOPES = {"region", "area", "pod", "unknown"}
SCHEDULES = {"active", "future_only", "draft_only", "missing", "unknown"}
RATE_CARDS = {"effective", "missing", "unknown"}
READINESS = {"ready", "generate", "missing_rate_card", "blocked_schedule", "conversion_failed", "unknown"}
MAX_ACTIVE_DIARY_ENTRIES = 12
MAX_UNKNOWN_AGE_DAYS = 7


class ConfigurationExperimentError(ValueError):
    pass


def workflow_contract() -> Dict[str, Any]:
    return {
        "schemaVersion": 1,
        "featureId": FEATURE_RATE_CARDS,
        "roles": {
            "lead": "sets research boundary; never bypasses product authority",
            "sofia_riley": "address, coordinate, and record evidence; cannot configure",
            "podplay_admin": "may inspect and explicitly choose any product-side configuration action",
            "player": "later accepts or passes their own journey only",
        },
        "states": [
            "configuration_exploration_requested", "configuration_exploration_accepted",
            "configuration_exploration_passed", "configuration_observed", "configuration_inconclusive",
            "change_proposal_needed", "activation_readback_needed", "prediction_preparation_needed", "closed",
        ],
        "firstPhase": {
            "goal": "Read the existing configuration and stop.",
            "allowed": ["authorized admin read-only inspection", "sanitized evidence recording"],
            "forbidden": ["configuration write", "rate-card publish", "booking", "price claim", "player invitation"],
        },
        "scopePolicy": {
            "default": "propose the narrowest supported scope, normally pod, for isolated research",
            "requires": "authorized admin records acceptance, a different scope, or pass",
            "knownProductScopes": ["region", "area", "pod"],
            "notAssumed": ["a club-level override", "that pod override is available in this tenant"],
        },
    }


def request_configuration_exploration(root: Path, *, requester: str) -> Dict[str, Any]:
    if requester not in STAFF:
        raise ConfigurationExperimentError("only Sofia or Riley may request configuration exploration")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        experiment = state["experiments"].get(EXPERIMENT_ID)
        if not isinstance(experiment, dict):
            now = datetime.now(timezone.utc)
            observed_at = isoformat(now)
            experiment = {
                "experimentId": EXPERIMENT_ID, "featureId": FEATURE_RATE_CARDS, "releaseReference": "PP-7827",
                "phase": "configuration_exploration_requested", "requestedBy": requester, "requestedAt": observed_at,
                "adminDecision": None, "technicalHandoff": None, "configuration": None, "changeProposal": None, "diary": [],
                "uncertainty": _initial_uncertainty(now), "remoteWrites": 0,
            }
            state["experiments"][EXPERIMENT_ID] = experiment
            _append_message_locked(storage, {
                "id": f"{EXPERIMENT_ID}:request", "channel": "control-plane", "kind": "configuration_exploration_requested",
                "from": requester, "to": [ADMIN],
                "text": "Riley requests a read-only Rate Cards and Unified Pricing configuration exploration. Please accept or pass; no configuration change or player journey is requested.",
                "featureId": FEATURE_RATE_CARDS, "observedAt": observed_at, "remoteWrites": 0,
            })
            _append_fact_locked(storage, experiment, "configuration_exploration_requested", requester, observed_at)
            storage.write_json(storage.state / STATE_FILE, state)
            _render_locked(storage, experiment)
        elif "uncertainty" not in experiment:
            experiment["uncertainty"] = _initial_uncertainty(datetime.now(timezone.utc))
            storage.write_json(storage.state / STATE_FILE, state)
            _render_locked(storage, experiment)
        return _public(experiment)


def issue_technical_handoff(root: Path, *, actor_id: str) -> Dict[str, Any]:
    """Let Riley or Sofia hand the code-derived read-only checklist to admin."""
    if actor_id not in STAFF:
        raise ConfigurationExperimentError("only Sofia or Riley may issue the technical admin handoff")
    storage = Storage(root)
    with storage.writer_lock():
        state, experiment = _experiment(storage)
        if experiment["phase"] != "configuration_exploration_requested":
            raise ConfigurationExperimentError("technical handoff is only available while exploration is awaiting a decision")
        if experiment.get("technicalHandoff") is None:
            observed_at = _now()
            handoff = {
                "issuedBy": actor_id, "issuedAt": observed_at, "readOnly": True,
                "scopeCandidates": ["region", "area", "pod"],
                "readbackFields": ["feature.bookingEngine", "effectiveScope", "effectiveRateCard", "schedule", "activationReadiness"],
                "codeGuidance": [
                    "Booking engine resolution is reviewed at region, area, and pod scope.",
                    "Use the authorized admin session to inspect effective rate-card and unified-activation-preview surfaces.",
                    "Report normalized enums only; do not copy identifiers, URLs, responses, credentials, or screenshots.",
                ],
            }
            reject_private_data(handoff, "configurationExperimentTechnicalHandoff")
            experiment["technicalHandoff"] = handoff
            _append_message_locked(storage, {
                "id": f"{EXPERIMENT_ID}:technical-handoff", "channel": "control-plane", "kind": "technical_admin_handoff",
                "from": actor_id, "to": [ADMIN], "featureId": FEATURE_RATE_CARDS,
                "text": "Technical handoff: inspect feature.bookingEngine, effective resolution scope, effective rate card, schedule, and activation readiness using the authorized admin session. Product code was reviewed for region/area/pod resolution. Report safe enums only; do not configure anything.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            _append_fact_locked(storage, experiment, "technical_admin_handoff_issued", actor_id, observed_at)
            storage.write_json(storage.state / STATE_FILE, state)
            _render_locked(storage, experiment)
        return _public(experiment)


def decide_configuration_exploration(root: Path, *, actor_id: str, decision: str) -> Dict[str, Any]:
    if actor_id != ADMIN:
        raise ConfigurationExperimentError("only the authorized PodPlay admin may decide this exploration")
    if decision not in {"accept", "pass"}:
        raise ConfigurationExperimentError("admin decision must be accept or pass")
    storage = Storage(root)
    with storage.writer_lock():
        state, experiment = _experiment(storage)
        if experiment["phase"] != "configuration_exploration_requested":
            raise ConfigurationExperimentError("configuration exploration is no longer awaiting an admin decision")
        observed_at = _now()
        experiment["adminDecision"] = {"actorId": actor_id, "decision": decision, "decidedAt": observed_at}
        experiment["phase"] = "configuration_exploration_accepted" if decision == "accept" else "configuration_exploration_passed"
        _append_message_locked(storage, {
            "id": f"{EXPERIMENT_ID}:admin:{decision}", "channel": "control-plane", "kind": "configuration_exploration_decision",
            "from": actor_id, "to": [experiment["requestedBy"]], "featureId": FEATURE_RATE_CARDS,
            "text": "The authorized admin " + decision + "ed the read-only configuration exploration. This does not authorize a configuration change or player journey.",
            "observedAt": observed_at, "remoteWrites": 0,
        })
        _append_fact_locked(storage, experiment, "configuration_exploration_" + decision + "ed", actor_id, observed_at)
        storage.write_json(storage.state / STATE_FILE, state)
        _render_locked(storage, experiment)
        return _public(experiment)


def record_configuration_observation(root: Path, *, actor_id: str, booking_engine: str, effective_scope: str, schedule: str, rate_card: str, readiness: str) -> Dict[str, Any]:
    """Record only normalized, identifier-free admin read-back facts."""
    if actor_id != ADMIN:
        raise ConfigurationExperimentError("only the authorized PodPlay admin may record a configuration read-back")
    if booking_engine not in ENGINES or effective_scope not in SCOPES:
        raise ConfigurationExperimentError("booking engine or effective scope is invalid")
    if schedule not in SCHEDULES or rate_card not in RATE_CARDS or readiness not in READINESS:
        raise ConfigurationExperimentError("schedule, rate card, or readiness is invalid")
    storage = Storage(root)
    with storage.writer_lock():
        state, experiment = _experiment(storage)
        if experiment["phase"] not in {"configuration_exploration_accepted", "configuration_inconclusive", "change_proposal_needed"}:
            raise ConfigurationExperimentError("admin acceptance is required before recording configuration")
        observed_at = _now()
        configuration = {
            "observedAt": observed_at, "source": "authorized_admin_readback",
            "featureFlags": [{"name": "feature.bookingEngine", "value": booking_engine}],
            "effectiveScope": effective_scope, "schedule": schedule, "rateCard": rate_card,
            "activationReadiness": readiness, "pricingCoverage": "plain_court_reservation_only",
            "outcome": _outcome(booking_engine, schedule, rate_card, readiness),
        }
        reject_private_data(configuration, "configurationExperiment")
        experiment["configuration"] = configuration
        if experiment["phase"] != "change_proposal_needed":
            experiment["phase"] = "configuration_observed" if configuration["outcome"] != "inconclusive" else "configuration_inconclusive"
        experiment["uncertainty"] = _post_configuration_uncertainty(datetime.now(timezone.utc), configuration)
        _append_fact_locked(storage, experiment, "configuration_observed", actor_id, observed_at)
        _append_message_locked(storage, {
            "id": f"{EXPERIMENT_ID}:configuration:{configuration['outcome']}", "channel": "control-plane", "kind": "configuration_observed",
            "from": actor_id, "to": [experiment["requestedBy"]], "featureId": FEATURE_RATE_CARDS,
            "text": "Configuration exploration is recorded as " + configuration["outcome"] + ". This is a configuration fact only; no price or customer entitlement is claimed.",
            "observedAt": observed_at, "remoteWrites": 0,
        })
        storage.write_json(storage.state / STATE_FILE, state)
        _render_locked(storage, experiment)
        return _public(experiment)


def discover_live_configuration(root: Path) -> Dict[str, Any]:
    """Run the accepted admin GET-only read-back and retain only safe enums."""
    storage = Storage(root)
    with storage.writer_lock():
        _, experiment = _experiment(storage)
        if experiment["phase"] not in {"configuration_exploration_accepted", "configuration_inconclusive", "change_proposal_needed"}:
            raise ConfigurationExperimentError("admin acceptance is required before live configuration discovery")
    try:
        credentials = PreviewCredentials.load(root)
        policy = TargetPolicy.from_config(credentials.config)
        session = FirebaseAuthenticator(root / "secrets" / "preview-admin-auth-cache.json").authenticate(
            credentials.admin_email, credentials.admin_password, credentials.firebase_api_key,
        )
        client = PreviewReadonlyClient(policy, session.id_token)
        seed = load_preview_seed(root)
        location = seed.get("location") if isinstance(seed.get("location"), dict) else {}
        area_id, pod_id = location.get("areaId"), location.get("podId")
        if not isinstance(area_id, str) or not isinstance(pod_id, str):
            raise PreviewReadError("configured research location is unavailable")
        pod = client.get(f"/apis/v2/pods/{pod_id}?expand=_links.venueSettings")
        if not isinstance(pod, dict):
            raise PreviewReadError("configured research pod response has an unexpected shape")
        engine = _booking_engine(pod)
        activation = client.get(f"/apis/v2/pods/{pod_id}/unified-activation-preview")
        schedule, rate_card, readiness = _activation_summary(activation)
        # The API resolves inherited settings already. It does not reveal which
        # level supplied a value, so scope remains explicitly unknown.
        return record_configuration_observation(
            root, actor_id=ADMIN, booking_engine=engine, effective_scope="unknown",
            schedule=schedule, rate_card=rate_card, readiness=readiness,
        )
    except PreviewReadError:
        return record_configuration_observation(
            root, actor_id=ADMIN, booking_engine="UNKNOWN", effective_scope="unknown",
            schedule="unknown", rate_card="unknown", readiness="unknown",
        )
    except (CredentialsError, FirebaseAuthError, TargetPolicyError) as exc:
        raise ConfigurationExperimentError("authorized admin read-only session is unavailable") from exc


def discover_live_scope_path(root: Path) -> Dict[str, Any]:
    """Read the Tenant → Area → Pod setting path without changing any setting.

    The resulting evidence says whether an override is absent or has a safe
    Booking Engine enum. It never retains the target's identifiers, URLs, or
    full setting collections.
    """
    storage = Storage(root)
    with storage.writer_lock():
        _, experiment = _experiment(storage)
        if experiment["phase"] not in {"configuration_observed", "change_proposal_needed"}:
            raise ConfigurationExperimentError("a configuration observation is required before scope-path discovery")
    try:
        credentials = PreviewCredentials.load(root)
        policy = TargetPolicy.from_config(credentials.config)
        session = FirebaseAuthenticator(root / "secrets" / "preview-admin-auth-cache.json").authenticate(
            credentials.admin_email, credentials.admin_password, credentials.firebase_api_key,
        )
        client = PreviewReadonlyClient(policy, session.id_token)
        seed = load_preview_seed(root)
        location = seed.get("location") if isinstance(seed.get("location"), dict) else {}
        area_id, pod_id = location.get("areaId"), location.get("podId")
        if not isinstance(area_id, str) or not isinstance(pod_id, str):
            raise PreviewReadError("configured research location is unavailable")
        tenant = collection_items(client.get("/apis/v2/tenants/current/settings"))
        # `venueSettings` is resolved at the resource's own scope; the
        # similarly named `venueSettingsInherited` intentionally omits its
        # local override and must not be treated as the effective value.
        # Expand accepts repeated exact query values, not a comma-delimited
        # list. Keep this path literal and bounded so the raw response never
        # becomes simulator state.
        expands = "expand=_links.venueSettingsOverrides&expand=_links.venueSettings"
        area = client.get(f"/apis/v2/areas/{area_id}?{expands}")
        pod = client.get(f"/apis/v2/pods/{pod_id}?{expands}")
        scope = _scope_path_summary(tenant, area, pod)
    except PreviewReadError:
        scope = _unknown_scope_path()
    except (CredentialsError, FirebaseAuthError, TargetPolicyError) as exc:
        raise ConfigurationExperimentError("authorized admin read-only session is unavailable") from exc

    with storage.writer_lock():
        state, experiment = _experiment(storage)
        observed_at = _now()
        discovery = {
            "observedAt": observed_at,
            "source": "authorized_admin_scope_readback",
            "supportedOverrideScopes": ["region", "area", "pod"],
            "adminExplorationPath": ["tenant baseline", "area settings", "pod settings", "pod activation readiness"],
            **scope,
        }
        reject_private_data(discovery, "configurationScopeDiscovery")
        experiment["scopeDiscovery"] = discovery
        _append_fact_locked(
            storage, experiment, "configuration_scope_path_observed", ADMIN, observed_at,
            {key: scope.get(key, "unknown") for key in (
                "tenantBaseline", "areaOverride", "areaEffective", "podOverride", "podEffective",
            )},
        )
        _append_message_locked(storage, {
            "id": f"{EXPERIMENT_ID}:scope-path", "channel": "control-plane", "kind": "configuration_scope_path_observed",
            "from": ADMIN, "to": [experiment["requestedBy"]], "featureId": FEATURE_RATE_CARDS,
            "text": "Tenant, area, and pod Booking Engine settings were read back as safe summaries. This identifies a scope path only; it does not approve or perform a configuration change.",
            "observedAt": observed_at, "remoteWrites": 0,
        })
        storage.write_json(storage.state / STATE_FILE, state)
        _render_locked(storage, experiment)
        return _public(experiment)


def _booking_engine(pod: Dict[str, Any]) -> str:
    resolved = pod.get("venueSettings")
    # Compatibility fallback only for stored/mock historical responses. Live
    # observation requests the resolved field above.
    if not isinstance(resolved, dict):
        resolved = pod.get("venueSettingsInherited")
    items = resolved.get("items") if isinstance(resolved, dict) else None
    if not isinstance(items, list):
        return "UNKNOWN"
    value = next((row.get("value") for row in items if isinstance(row, dict) and row.get("id") == "feature.bookingEngine"), None)
    return value if value in ENGINES else "UNKNOWN"


def _activation_summary(value: Any) -> tuple[str, str, str]:
    if not isinstance(value, dict):
        raise PreviewReadError("activation preview has an unexpected shape")
    status = value.get("status")
    if status == "MISSING_RATE_CARD":
        return "missing", "missing", "missing_rate_card"
    if status == "EXISTING":
        return "active", "effective", "ready"
    if status == "GENERATE":
        return "missing", "effective", "generate"
    if status == "CONVERSION_FAILED":
        return "missing", "effective", "conversion_failed"
    if status == "BLOCKED_SCHEDULE":
        versions = value.get("versions")
        states = {str(row.get("status")).lower() for row in versions if isinstance(row, dict)} if isinstance(versions, list) else set()
        schedule = "future_only" if "future" in states else "draft_only" if "draft" in states else "unknown"
        return schedule, "effective", "blocked_schedule"
    raise PreviewReadError("activation preview has an unknown status")


def _scope_path_summary(tenant: list, area: Any, pod: Any) -> Dict[str, str]:
    if not isinstance(area, dict) or not isinstance(pod, dict):
        raise PreviewReadError("scope settings response has an unexpected shape")
    return {
        "tenantBaseline": _setting_value(tenant),
        "areaOverride": _linked_setting_value(area, "venueSettingsOverrides"),
        "areaEffective": _linked_setting_value(area, "venueSettings"),
        "podOverride": _linked_setting_value(pod, "venueSettingsOverrides"),
        "podEffective": _linked_setting_value(pod, "venueSettings"),
    }


def _unknown_scope_path() -> Dict[str, str]:
    return {key: "unknown" for key in ("tenantBaseline", "areaOverride", "areaEffective", "podOverride", "podEffective")}


def _linked_setting_value(resource: Dict[str, Any], field: str) -> str:
    value = resource.get(field)
    # Keep old, sanitized fixtures readable during the field-name migration;
    # live reads explicitly request `venueSettings` above.
    if field == "venueSettings" and not isinstance(value, dict):
        value = resource.get("venueSettingsInherited")
    return _setting_value(collection_items(value))


def _setting_value(items: list) -> str:
    value = next((row.get("value") for row in items if isinstance(row, dict) and row.get("id") == "feature.bookingEngine"), None)
    if value in {"LEGACY", "UNIFIED"}:
        return str(value)
    return "absent" if value is None else "unknown"


def add_diary_note(root: Path, *, actor_id: str, note: str) -> Dict[str, Any]:
    """Store a concise operational rationale, never private reasoning or secrets."""
    if actor_id not in DIARY_STAFF | {ADMIN}:
        raise ConfigurationExperimentError("only Riley, Sofia, or the authorized admin may add an experiment diary note")
    if not isinstance(note, str) or not note.strip() or len(note.strip()) > 280:
        raise ConfigurationExperimentError("diary note must be a non-empty safe summary of at most 280 characters")
    safe_note = note.strip()
    if re.search(r"https?://|\\b(?:password|token|credential|secret)\\b", safe_note, flags=re.IGNORECASE):
        raise ConfigurationExperimentError("diary note must not contain a private reference or secret-shaped term")
    reject_private_data({"note": safe_note}, "configurationExperimentDiary")
    storage = Storage(root)
    with storage.writer_lock():
        state, experiment = _experiment(storage)
        if len(experiment["diary"]) >= MAX_ACTIVE_DIARY_ENTRIES:
            raise ConfigurationExperimentError("active experiment diary is full; close, archive, or later compact before adding more notes")
        observed_at = _now()
        experiment["diary"].append({"actorId": actor_id, "recordedAt": observed_at, "note": safe_note})
        _append_fact_locked(storage, experiment, "configuration_diary_recorded", actor_id, observed_at, {"note": safe_note})
        storage.write_json(storage.state / STATE_FILE, state)
        _render_locked(storage, experiment)
        return _public(experiment)


def refresh_uncertainty(root: Path) -> Dict[str, Any]:
    """Re-project the compact uncertainty ledger from existing safe evidence."""
    storage = Storage(root)
    with storage.writer_lock():
        state, experiment = _experiment(storage)
        configuration = experiment.get("configuration")
        if experiment["phase"] not in {"configuration_observed", "configuration_inconclusive"} or not isinstance(configuration, dict):
            raise ConfigurationExperimentError("a configuration read-back is required before uncertainty can be refreshed")
        experiment["uncertainty"] = _post_configuration_uncertainty(datetime.now(timezone.utc), configuration)
        storage.write_json(storage.state / STATE_FILE, state)
        _render_locked(storage, experiment)
        return _public(experiment)


def propose_configuration_change(root: Path, *, actor_id: str) -> Dict[str, Any]:
    """Ask the product admin whether to plan a change; never make one.

    This is intentionally a separate authority decision from the earlier
    read-only inspection.  It names the observed gap and a narrow default
    scope candidate, but keeps scope and configuration action unapproved.
    """
    if actor_id not in STAFF:
        raise ConfigurationExperimentError("only Sofia or Riley may propose a configuration change")
    storage = Storage(root)
    with storage.writer_lock():
        state, experiment = _experiment(storage)
        configuration = experiment.get("configuration")
        if experiment.get("phase") != "configuration_observed" or not isinstance(configuration, dict):
            raise ConfigurationExperimentError("a conclusive configuration observation is required before a change proposal")
        if configuration.get("outcome") == "unified_enabled_and_ready":
            raise ConfigurationExperimentError("a change proposal is not needed when Unified Pricing is already ready")
        if experiment.get("changeProposal") is None:
            observed_at = _now()
            proposal = {
                "requestedBy": actor_id,
                "requestedAt": observed_at,
                "purpose": "decide whether to prepare a narrowly scoped Unified Pricing configuration change",
                "observedGap": configuration["outcome"],
                "defaultScopeCandidate": "pod",
                "scopeStatus": "unverified",
                "requiredAdminChoices": ["accept planning", "choose another scope", "pass"],
                "requiredReadbacksBeforeAnyPlayerJourney": [
                    "effective booking engine",
                    "effective rate card",
                    "active schedule",
                    "unified activation readiness",
                ],
                "forbidden": ["automatic configuration write", "rate-card publish", "player invitation", "price claim"],
            }
            reject_private_data(proposal, "configurationChangeProposal")
            experiment["changeProposal"] = proposal
            experiment["phase"] = "change_proposal_needed"
            _append_message_locked(storage, {
                "id": f"{EXPERIMENT_ID}:change-proposal", "channel": "control-plane", "kind": "configuration_change_proposed",
                "from": actor_id, "to": [ADMIN], "featureId": FEATURE_RATE_CARDS,
                "text": "Read-back found Unified Pricing unavailable. Riley requests an admin decision: accept planning a narrowly scoped configuration change, choose another scope, or pass. No product change, price claim, or player invitation is authorized.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            _append_fact_locked(storage, experiment, "configuration_change_proposed", actor_id, observed_at)
            storage.write_json(storage.state / STATE_FILE, state)
            _render_locked(storage, experiment)
        return _public(experiment)


def prepare_rate_cards_change_packet(root: Path, *, actor_id: str) -> Dict[str, Any]:
    """Prepare a safe, non-executing packet for a product administrator.

    This turns the bounded planning authorization into a precise request for
    the one input that Club Lab must not invent: an approved pricing basis.
    It deliberately stops before creating a card, publishing it, changing the
    Booking Engine, or involving players.
    """
    if actor_id not in STAFF:
        raise ConfigurationExperimentError("only Sofia or Riley may prepare a technical change packet")
    storage = Storage(root)
    with storage.writer_lock():
        state, experiment = _experiment(storage)
        if experiment.get("phase") != "change_proposal_needed":
            raise ConfigurationExperimentError("a pending configuration change proposal is required before preparing a packet")
        if not isinstance(experiment.get("scopeDiscovery"), dict):
            raise ConfigurationExperimentError("a safe scope-path read-back is required before preparing a packet")
        _require_planning_authorization(storage)
        _require_staff_planning_acceptance(storage)
        if experiment.get("technicalChangePacket") is None:
            observed_at = _now()
            scope = experiment["scopeDiscovery"]
            packet = {
                "packetId": f"{EXPERIMENT_ID}:pod-pricing-basis",
                "preparedBy": actor_id,
                "preparedAt": observed_at,
                "status": "awaiting_authorized_pricing_basis",
                "target": "one selected pod",
                "observedConfiguration": {
                    "bookingEngine": experiment["configuration"]["featureFlags"][0]["value"],
                    "activationReadiness": experiment["configuration"]["activationReadiness"],
                    "rateCard": experiment["configuration"]["rateCard"],
                    "schedule": experiment["configuration"]["schedule"],
                },
                "safeScopeReadback": {
                    key: scope.get(key, "unknown")
                    for key in ("tenantBaseline", "areaOverride", "areaEffective", "podOverride", "podEffective")
                },
                "requestedAdministratorDecision": {
                    "decision": "supply an approved pricing basis, request clarification, or pass",
                    "requiredInputs": [
                        "approved rate-card label and validity intent",
                        "approved currency and exact price source",
                        "price duration and billing policy",
                        "player-count pricing packages or bands",
                        "whether the legacy schedule may be converted or a schedule must be supplied",
                    ],
                    "notAllowed": "Club Lab must not infer, round, copy, or publish price values.",
                },
                "adminNavigation": [
                    "Tenant or club configuration",
                    "selected area",
                    "selected pod or experience",
                    "venue settings and Unified Pricing activation preview",
                    "Rate Cards",
                ],
                "nextStepAfterDecision": "Record the approved basis as a separate proposed change set and seek explicit enactment authority.",
                "forbiddenNow": [
                    "create or publish a Rate Card",
                    "enable Unified Pricing",
                    "invite players",
                    "quote or promise a price",
                ],
                "remoteWrites": 0,
            }
            reject_private_data(packet, "rateCardsTechnicalChangePacket")
            experiment["technicalChangePacket"] = packet
            _append_message_locked(storage, {
                "id": f"{EXPERIMENT_ID}:technical-change-packet", "channel": "control-plane",
                "kind": "rate_cards_technical_change_packet_prepared", "from": actor_id,
                "to": [ADMIN], "featureId": FEATURE_RATE_CARDS,
                "text": "A pod-scoped Rate Cards technical packet is ready. Please supply an approved pricing basis, request clarification, or pass. The packet does not authorize a Preview configuration change.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            _append_fact_locked(storage, experiment, "technical_change_packet_prepared", actor_id, observed_at)
            storage.write_json(storage.state / STATE_FILE, state)
            _render_locked(storage, experiment)
        return _public(experiment)


def prepare_rate_cards_configuration_proposal(root: Path, *, actor_id: str) -> Dict[str, Any]:
    """Build the next bounded artifact from an accepted pilot basis.

    It describes a guarded draft/publish/switch sequence, but is not a payload
    and has no product adapter. Fresh read-back and a dry-run manifest remain
    prerequisites for any later write.
    """
    if actor_id not in STAFF:
        raise ConfigurationExperimentError("only Sofia or Riley may prepare a configuration proposal")
    storage = Storage(root)
    with storage.writer_lock():
        state, experiment = _experiment(storage)
        if experiment.get("phase") != "change_proposal_needed":
            raise ConfigurationExperimentError("a pending Rate Cards change proposal is required")
        _require_accepted_run_authorization(storage)
        if experiment.get("configurationProposal") is None:
            observed_at = _now()
            proposal = {
                "proposalId": f"{EXPERIMENT_ID}:pod-configuration",
                "preparedBy": actor_id,
                "preparedAt": observed_at,
                "status": "prepared_pending_fresh_readback_and_dry_run",
                "target": "one selected pod resolved only by the guarded adapter",
                "basisReference": "accepted control-plane pricing basis",
                "intendedSequence": [
                    "fresh GET-only rate-card, schedule, booking-engine, and activation read-back",
                    "render a dry-run manifest from the accepted basis",
                    "create a pod-assigned Rate Card and public Pricing Schedule only if the manifest matches",
                    "publish only with enactment-time validity intent and a current readiness result",
                    "switch this pod to Unified only after activation readiness is ready",
                    "read back Rate Card, schedule, booking engine, and activation readiness",
                ],
                "activationTiming": "at guarded enactment time; no date is invented in this proposal",
                "playerBoundary": "players are not invited or quoted until effective configuration and a separately approved prediction are observed",
                "remoteWrites": 0,
            }
            reject_private_data(proposal, "rateCardsConfigurationProposal")
            experiment["configurationProposal"] = proposal
            packet = experiment.get("technicalChangePacket")
            if isinstance(packet, dict) and packet.get("status") == "awaiting_authorized_pricing_basis":
                packet["status"] = "pricing_basis_accepted"
                packet["nextStepAfterDecision"] = "Use the accepted basis only through the manifest-bound configuration proposal."
            _append_message_locked(storage, {
                "id": f"{EXPERIMENT_ID}:configuration-proposal", "channel": "control-plane",
                "kind": "rate_cards_configuration_proposal_prepared", "from": actor_id,
                "to": ["riley", "morgan-vale"], "featureId": FEATURE_RATE_CARDS,
                "text": "The accepted pilot basis has been translated into a bounded configuration proposal. Fresh read-back and a dry-run manifest are next; no player request or Preview write has occurred.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            _append_fact_locked(storage, experiment, "configuration_proposal_prepared", actor_id, observed_at)
            storage.write_json(storage.state / STATE_FILE, state)
            _render_locked(storage, experiment)
        packet = experiment.get("technicalChangePacket")
        if isinstance(packet, dict) and packet.get("status") == "awaiting_authorized_pricing_basis":
            packet["status"] = "pricing_basis_accepted"
            packet["nextStepAfterDecision"] = "Use the accepted basis only through the manifest-bound configuration proposal."
            storage.write_json(storage.state / STATE_FILE, state)
            _render_locked(storage, experiment)
        return _public(experiment)


def render_rate_cards_dry_run_manifest(root: Path, *, actor_id: str) -> Dict[str, Any]:
    """Render the accepted proposal as an identifier-free, no-write manifest."""
    if actor_id not in STAFF:
        raise ConfigurationExperimentError("only Sofia or Riley may render the Rate Cards dry-run manifest")
    storage = Storage(root)
    with storage.writer_lock():
        state, experiment = _experiment(storage)
        proposal = experiment.get("configurationProposal")
        if not isinstance(proposal, dict):
            raise ConfigurationExperimentError("a configuration proposal is required before rendering a dry-run manifest")
        _require_accepted_run_authorization(storage)
        if experiment.get("dryRunManifest") is None:
            configuration = experiment.get("configuration")
            if not isinstance(configuration, dict):
                raise ConfigurationExperimentError("a fresh configuration read-back is required before rendering a dry-run manifest")
            observed_at = _now()
            manifest = {
                "manifestId": f"{EXPERIMENT_ID}:dry-run",
                "renderedBy": actor_id,
                "renderedAt": observed_at,
                "status": "ready_for_exact_origin_confirmation",
                "currentReadback": {
                    "bookingEngine": configuration["featureFlags"][0]["value"],
                    "rateCard": configuration["rateCard"],
                    "schedule": configuration["schedule"],
                    "activationReadiness": configuration["activationReadiness"],
                },
                "plannedOperations": [
                    {"operation": "create_rate_card", "mode": "dry_run", "scope": "one selected pod", "write": False},
                    {"operation": "create_public_pricing_schedule", "mode": "dry_run", "scope": "one selected pod", "write": False},
                    {"operation": "publish_effective_pricing", "mode": "conditional", "requires": "enactment-time validity intent and successful draft read-back", "write": False},
                    {"operation": "switch_booking_engine", "mode": "conditional", "requires": "activation readiness ready after pricing is effective", "write": False},
                ],
                "requiredAtEnactment": [
                    "exact approved Preview origin confirmation",
                    "same accepted basis fingerprint",
                    "guarded adapter resolves the selected pod without persisting its identifier",
                    "read-back after every successful bounded write",
                ],
                "playerBoundary": "do not invite players until effective configuration and an approved price prediction are observed",
                "remoteWrites": 0,
            }
            reject_private_data(manifest, "rateCardsDryRunManifest")
            experiment["dryRunManifest"] = manifest
            _append_message_locked(storage, {
                "id": f"{EXPERIMENT_ID}:dry-run-manifest", "channel": "control-plane",
                "kind": "rate_cards_dry_run_manifest_rendered", "from": actor_id,
                "to": ["riley", "morgan-vale"], "featureId": FEATURE_RATE_CARDS,
                "text": "A no-write Rate Cards dry-run manifest is ready. It awaits only enactment-time exact-origin confirmation; player research remains blocked until effective configuration is observed.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            _append_fact_locked(storage, experiment, "dry_run_manifest_rendered", actor_id, observed_at)
            storage.write_json(storage.state / STATE_FILE, state)
            _render_locked(storage, experiment)
        return _public(experiment)


def enact_rate_cards_pod_pilot(root: Path) -> Dict[str, Any]:
    """Refuse the legacy integration enactment for a Rate Cards pilot.

    Rate Cards is a club-staff configuration journey when the product exposes
    it in the staff UI.  It must therefore be completed by a named, authorized
    staff Sim in an authenticated browser, with UI evidence and sanitized
    console/network observations.  Tenant-only controls may still have a
    separately authorized admin integration; this player-facing pricing route
    may not.
    """
    raise ConfigurationExperimentError(
        "Rate Cards enactment requires a named staff browser journey; the integration path is disabled"
    )
    # A new GET-only observation is both a guard and durable safe evidence.
    fresh = discover_live_configuration(root)
    current = fresh.get("configuration") if isinstance(fresh, dict) else None
    if not isinstance(current, dict):
        raise ConfigurationExperimentError("fresh configuration read-back is unavailable")
    if current.get("featureFlags", [{}])[0].get("value") == "UNIFIED":
        raise ConfigurationExperimentError("selected pod is already Unified; refusing to create a duplicate pilot")
    if current.get("rateCard") != "missing" or current.get("schedule") != "missing":
        raise ConfigurationExperimentError("selected pod is no longer the empty pilot baseline; refusing to infer a safe resume")

    storage = Storage(root)
    with storage.writer_lock():
        state, experiment = _experiment(storage)
        _require_accepted_run_authorization(storage)
        manifest = experiment.get("dryRunManifest")
        proposal = experiment.get("configurationProposal")
        if not isinstance(manifest, dict) or manifest.get("status") != "ready_for_exact_origin_confirmation":
            raise ConfigurationExperimentError("a current Rate Cards dry-run manifest is required")
        if not isinstance(proposal, dict):
            raise ConfigurationExperimentError("a Rate Cards configuration proposal is required")
        control = storage.load_json(storage.state / "control-plane.json", default={})
        request = control.get("configurationRequests", {}).get("control:rate-cards-pricing-basis:pp-7827") if isinstance(control, dict) else None
        basis = request.get("proposedBasis") if isinstance(request, dict) else None
        if not isinstance(basis, dict):
            raise ConfigurationExperimentError("accepted pricing basis is unavailable")

    try:
        credentials = PreviewCredentials.load(root)
        policy = TargetPolicy.from_config(replace(
            credentials.config,
            mode=RunMode.PREVIEW_WRITE,
            # The exact configured origin is the existing manifest-bound
            # confirmation. It is never copied into community state or output.
            preview_write_confirmation=credentials.preview_origin,
        ))
        session = FirebaseAuthenticator(root / "secrets" / "preview-admin-auth-cache.json").authenticate(
            credentials.admin_email, credentials.admin_password, credentials.firebase_api_key,
        )
        location = load_preview_seed(root).get("location")
        pod_id = location.get("podId") if isinstance(location, dict) else None
        if not isinstance(pod_id, str) or not pod_id:
            raise PreviewWriteError("configured research pod is unavailable")
        writer = PreviewRateCardsWriter(policy, session.id_token)
        now = isoformat(datetime.now(timezone.utc))
        card = writer.post("/apis/v2/reservation-rate-cards", {
            "displayName": basis["proposalLabel"],
            "assignments": [{"type": "POD", "pod": {"id": pod_id}}],
            "pricingConfig": basis["pricingConfig"],
            "validFromDate": now,
            "status": "PUBLISHED",
        })
        if not isinstance(card.get("id"), str) or not card["id"]:
            raise PreviewWriteError("Rate Card creation did not return a usable reference")
        schedule = basis["scheduleProposal"]
        windows = schedule.get("timeWindows") if isinstance(schedule, dict) else None
        if not isinstance(windows, list):
            raise PreviewWriteError("accepted schedule basis is invalid")
        schedule_payload = {
            "scheduleConfig": {"schedules": [{
                "id": "club-lab-public", "displayName": "Club Lab public pilot",
                "audience": {"type": "PUBLIC"}, "defaultBand": schedule["defaultBand"],
                "timeWindows": [dict(window, id=f"club-lab-window-{index + 1}") for index, window in enumerate(windows)],
            }]},
            "validFromDate": now,
        }
        created_schedule = writer.post(f"/apis/v2/pods/{pod_id}/reservation-pricing-schedules", schedule_payload)
        schedule_id = created_schedule.get("id")
        if not isinstance(schedule_id, str) or not schedule_id:
            raise PreviewWriteError("Pricing Schedule creation did not return a usable reference")
        writer.post(f"/apis/v2/pods/{pod_id}/reservation-pricing-schedules/{schedule_id}/publish", {})
        writer.patch(f"/apis/v2/pods/{pod_id}", {
            "venueSettings": {"items": [{"id": "feature.bookingEngine", "value": "UNIFIED"}]},
        })
    except (CredentialsError, FirebaseAuthError, TargetPolicyError, PreviewWriteError, KeyError, TypeError) as exc:
        _record_enactment(root, status="interrupted", writes=0, note="bounded configuration operation did not complete; inspect fresh read-back before any further action")
        raise ConfigurationExperimentError("bounded Rate Cards configuration operation did not complete; no regression is inferred") from exc

    readback = discover_live_configuration(root)
    configuration = readback.get("configuration") if isinstance(readback, dict) else None
    ready = isinstance(configuration, dict) and configuration.get("outcome") == "unified_enabled_and_ready"
    _record_enactment(
        root, status="completed" if ready else "completed_readback_inconclusive", writes=4,
        note="post-write configuration read-back recorded; no player price claim, invitation, booking, or attendance was created",
    )
    return experiment_snapshot(root)


def _record_enactment(root: Path, *, status: str, writes: int, note: str) -> None:
    storage = Storage(root)
    with storage.writer_lock():
        state, experiment = _experiment(storage)
        observed_at = _now()
        experiment["enactment"] = {"status": status, "recordedAt": observed_at, "boundedWriteCount": writes, "note": note}
        experiment["remoteWrites"] = writes if status.startswith("completed") else experiment.get("remoteWrites", 0)
        if status.startswith("completed"):
            manifest = experiment.get("dryRunManifest")
            if isinstance(manifest, dict):
                manifest["status"] = "enacted_readback_recorded"
        _append_fact_locked(storage, experiment, "rate_cards_pod_pilot_" + status, ADMIN, observed_at, {"boundedWriteCount": writes})
        storage.write_json(storage.state / STATE_FILE, state)
        _render_locked(storage, experiment)


def experiment_snapshot(root: Path) -> Dict[str, Any]:
    state = _load(Storage(root))
    return {"workflow": workflow_contract(), "experiments": [_public(row) for row in state["experiments"].values() if isinstance(row, dict)]}


def record_rate_cards_enactment_scope_readback(root: Path) -> Dict[str, Any]:
    """Attach the post-enactment scope result without classifying a defect."""
    storage = Storage(root)
    with storage.writer_lock():
        state, experiment = _experiment(storage)
        enactment = experiment.get("enactment")
        scope = experiment.get("scopeDiscovery")
        if not isinstance(enactment, dict) or not isinstance(scope, dict):
            raise ConfigurationExperimentError("post-enactment evidence is unavailable")
        pod_override, pod_effective = scope.get("podOverride"), scope.get("podEffective")
        if pod_override == "UNIFIED" and pod_effective == "LEGACY":
            enactment["status"] = "completed_readback_discrepant"
            enactment["note"] = "pod override is Unified while resolved read-back remains Legacy; retain as a repeatable configuration-readback discrepancy, not a regression"
            observed_at = _now()
            _append_fact_locked(storage, experiment, "rate_cards_post_enactment_scope_discrepancy", ADMIN, observed_at, {
                "podOverride": "UNIFIED", "podEffective": "LEGACY",
            })
            storage.write_json(storage.state / STATE_FILE, state)
            _render_locked(storage, experiment)
        elif pod_override == "UNIFIED" and pod_effective == "UNIFIED":
            enactment["status"] = "completed_verified"
            enactment["note"] = "resolved pod settings, Rate Card, schedule, and activation readiness are all observed after bounded enactment"
            observed_at = _now()
            _append_fact_locked(storage, experiment, "rate_cards_post_enactment_verified", ADMIN, observed_at, {
                "podOverride": "UNIFIED", "podEffective": "UNIFIED",
            })
            storage.write_json(storage.state / STATE_FILE, state)
            _render_locked(storage, experiment)
        return _public(experiment)


def prepare_rate_cards_price_prediction(root: Path, *, actor_id: str) -> Dict[str, Any]:
    """Prepare one exact, non-entitlement research prediction from the accepted basis."""
    if actor_id not in STAFF:
        raise ConfigurationExperimentError("only Sofia or Riley may prepare a price prediction")
    storage = Storage(root)
    with storage.writer_lock():
        state, experiment = _experiment(storage)
        configuration, enactment = experiment.get("configuration"), experiment.get("enactment")
        if not isinstance(configuration, dict) or configuration.get("outcome") != "unified_enabled_and_ready":
            raise ConfigurationExperimentError("current Unified Pricing readiness is required before preparing a prediction")
        if not isinstance(enactment, dict) or enactment.get("status") != "completed_verified":
            raise ConfigurationExperimentError("verified bounded enactment is required before preparing a prediction")
        if isinstance(experiment.get("pricePrediction"), dict):
            return _public(experiment)
        control = storage.load_json(storage.state / "control-plane.json", default={})
        request = control.get("configurationRequests", {}).get("control:rate-cards-pricing-basis:pp-7827") if isinstance(control, dict) else None
        basis = request.get("proposedBasis") if isinstance(request, dict) else None
        if not isinstance(basis, dict):
            raise ConfigurationExperimentError("accepted pricing basis is unavailable")
        packages = basis.get("pricingConfig", {}).get("pricePackages") if isinstance(basis.get("pricingConfig"), dict) else None
        off_peak = next((item for item in packages if isinstance(item, dict) and item.get("band") == "OFF_PEAK"), None) if isinstance(packages, list) else None
        rows = off_peak.get("pricesByPlayerCount") if isinstance(off_peak, dict) else None
        one_player = next((item for item in rows if isinstance(item, dict) and item.get("playerCount") == 1), None) if isinstance(rows, list) else None
        cents = one_player.get("reservationPriceCents") if isinstance(one_player, dict) else None
        if not isinstance(cents, int) or cents < 0:
            raise ConfigurationExperimentError("accepted pricing basis has no one-player off-peak amount")
        observed_at = _now()
        prediction = {
            "predictionId": f"{EXPERIMENT_ID}:public-one-player-off-peak",
            "preparedBy": actor_id, "preparedAt": observed_at, "status": "ready_for_player_owned_observation",
            "scenario": {
                "audience": "public", "playerCount": 1, "durationMinutes": 30,
                "localTimeCondition": "weekday 10:00 outside the defined Reduced and Peak windows", "pricingBand": "OFF_PEAK",
            },
            "expected": {"currency": basis.get("currency"), "reservationTotalCents": cents},
            "comparisonRule": "exact cents for the displayed reservation total; record an observation, not a booking",
            "doesNotMean": ["customer entitlement", "price promise", "booking", "attendance", "regression conclusion from one observation"],
            "remoteWrites": 0,
        }
        reject_private_data(prediction, "rateCardsPricePrediction")
        experiment["pricePrediction"] = prediction
        experiment["diary"].append({"actorId": actor_id, "recordedAt": observed_at, "note": "Unified Pricing is verified for the selected pod. I prepared one exact off-peak price prediction for a player-owned display check, not a booking or price promise."})
        _append_fact_locked(storage, experiment, "rate_cards_price_prediction_prepared", actor_id, observed_at, {"pricingBand": "OFF_PEAK", "playerCount": 1, "durationMinutes": 30})
        storage.write_json(storage.state / STATE_FILE, state)
        _render_locked(storage, experiment)
        return _public(experiment)


def adopt_rate_cards_checkout_tax_tolerance(root: Path, *, actor_id: str) -> Dict[str, Any]:
    """Keep configured-rate predictions exact while allowing bounded checkout tax."""
    if actor_id not in STAFF:
        raise ConfigurationExperimentError("only Sofia or Riley may update a research prediction policy")
    storage = Storage(root)
    with storage.writer_lock():
        state, experiment = _experiment(storage)
        prediction = experiment.get("pricePrediction")
        if not isinstance(prediction, dict):
            raise ConfigurationExperimentError("a Rate Cards price prediction is required")
        if "checkoutTotalPolicy" not in prediction:
            observed_at = _now()
            prediction["checkoutTotalPolicy"] = {
                "rateCardSubtotal": "must match the configured Rate Card in exact cents",
                "tax": "may be a separately displayed checkout component",
                "maxTaxPercentOfSubtotal": 20,
                "totalRule": "displayed total may exceed the exact configured subtotal by tax up to the stated bound",
            }
            _append_fact_locked(storage, experiment, "rate_cards_checkout_tax_tolerance_adopted", actor_id, observed_at, {"maxTaxPercentOfSubtotal": 20})
            storage.write_json(storage.state / STATE_FILE, state)
            _render_locked(storage, experiment)
        return _public(experiment)


def _outcome(engine: str, schedule: str, rate_card: str, readiness: str) -> str:
    if engine == "UNKNOWN" or schedule == "unknown" or rate_card == "unknown" or readiness == "unknown":
        return "inconclusive"
    if engine == "LEGACY":
        return "unified_not_enabled"
    if readiness == "ready" and schedule == "active" and rate_card == "effective":
        return "unified_enabled_and_ready"
    return "unified_blocked"


def _load(storage: Storage) -> Dict[str, Any]:
    state = storage.load_json(storage.state / STATE_FILE, default={"schemaVersion": 1, "experiments": {}})
    if not isinstance(state, dict) or state.get("schemaVersion") != 1 or not isinstance(state.get("experiments"), dict):
        raise ConfigurationExperimentError("configuration experiment state is invalid")
    return state


def _experiment(storage: Storage) -> tuple[Dict[str, Any], Dict[str, Any]]:
    state = _load(storage)
    experiment = state["experiments"].get(EXPERIMENT_ID)
    if not isinstance(experiment, dict):
        raise ConfigurationExperimentError("configuration exploration has not been requested")
    if not isinstance(experiment.get("uncertainty"), dict):
        experiment["uncertainty"] = _initial_uncertainty(datetime.now(timezone.utc))
    return state, experiment


def _require_planning_authorization(storage: Storage) -> None:
    control = storage.load_json(storage.state / "control-plane.json", default={})
    authorizations = control.get("authorizations") if isinstance(control, dict) else None
    authorization = authorizations.get("control:rate-cards-planning:pp-7827") if isinstance(authorizations, dict) else None
    if not isinstance(authorization, dict) or authorization.get("status") != "addressed_to_staff":
        raise ConfigurationExperimentError("the bounded Rate Cards planning authorization is required before preparing a packet")


def _require_staff_planning_acceptance(storage: Storage) -> None:
    consultations = storage.load_json(storage.state / "staff-consultations.json", default={})
    plans = consultations.get("plans") if isinstance(consultations, dict) else None
    accepted = any(
        isinstance(plan, dict)
        and plan.get("planType") == "rate_cards_pod_activation_research"
        and plan.get("status") == "accepted_for_planning_only"
        for plan in plans.values()
    ) if isinstance(plans, dict) else False
    if not accepted:
        raise ConfigurationExperimentError("a named staff member must accept the Rate Cards plan for planning only before preparing a packet")


def _require_accepted_run_authorization(storage: Storage) -> None:
    control = storage.load_json(storage.state / "control-plane.json", default={})
    authorizations = control.get("runAuthorizations") if isinstance(control, dict) else None
    authorization = authorizations.get("control:rate-cards-configuration-run:pp-7827") if isinstance(authorizations, dict) else None
    if not isinstance(authorization, dict) or authorization.get("status") != "preauthorized_for_manifest_bound_execution":
        raise ConfigurationExperimentError("the three-confirmation, manifest-bound run authorization is required")


def _append_message_locked(storage: Storage, row: Dict[str, Any]) -> None:
    path = storage.channel_path("control-plane")
    if not any(existing.get("id") == row["id"] for existing in storage.read_jsonl(path)):
        storage.append_jsonl(path, row)


def _append_fact_locked(storage: Storage, experiment: Dict[str, Any], action: str, actor_id: str, occurred_at: str, extra: Dict[str, Any] | None = None) -> None:
    payload = {"featureId": experiment["featureId"], "phase": experiment["phase"], "action": action}
    if extra:
        payload.update(extra)
    append_fact_locked(storage, make_fact(
        fact_type="experiment." + action, natural_key=f"{experiment['experimentId']}:{action}:{occurred_at}",
        subject_ref=experiment["experimentId"], correlation_id=experiment["experimentId"], source_kind="configuration_experiment",
        payload=payload, occurred_at=occurred_at, actor_id=actor_id, privacy="operator",
    ))


def _render_locked(storage: Storage, experiment: Dict[str, Any]) -> None:
    safe = _public(experiment)
    lines = ["# Club Lab configuration experiment", "", f"- **Feature:** {safe['featureId']}", f"- **Release:** {safe['releaseReference']}", f"- **Phase:** {safe['phase']}", f"- **Remote writes:** {safe.get('remoteWrites', 0)}", "", "## Evidence", ""]
    configuration = safe.get("configuration")
    if configuration is None:
        lines.append("No configuration read-back is recorded.")
    else:
        lines.extend([f"- **Outcome:** {configuration['outcome']}", f"- **Booking engine:** {configuration['featureFlags'][0]['value']}", f"- **Effective scope:** {configuration['effectiveScope']}", f"- **Schedule:** {configuration['schedule']}", f"- **Rate card:** {configuration['rateCard']}", f"- **Activation readiness:** {configuration['activationReadiness']}", ""])
    lines.extend(["## Uncertainty ledger", ""])
    for claim in safe["uncertainty"]["claims"]:
        lines.append(f"- **{claim['subject']}**: `{claim['value']}` / {claim['evidenceStatus']}; next: {claim['nextDiscriminatingObservation']}; stop: {claim['stopRule']}; expires: {claim['expiresAt']}")
    lines.extend(["## Operational diary", ""])
    if safe["diary"]:
        lines.extend(f"- {entry['recordedAt']} — {entry['actorId']}: {entry['note']}" for entry in safe["diary"])
    else:
        lines.append("No diary notes yet.")
    packet = safe.get("technicalChangePacket")
    lines.extend(["", "## Technical change packet", ""])
    if isinstance(packet, dict):
        lines.extend([
            f"- **Status:** {packet['status']}",
            f"- **Target:** {packet['target']}",
            "- **Decision requested:** authorized product administrator supplies an approved pricing basis, requests clarification, or passes.",
            "- **Current boundary:** no Rate Card, Unified Pricing setting, player invitation, or price claim is authorized.",
        ])
    else:
        lines.append("No technical change packet has been prepared.")
    proposal = safe.get("configurationProposal")
    lines.extend(["", "## Configuration proposal", ""])
    if isinstance(proposal, dict):
        lines.extend([
            f"- **Status:** {proposal['status']}",
            f"- **Target:** {proposal['target']}",
            "- **Next gate:** fresh read-back and a dry-run manifest.",
            "- **Player boundary:** no invitation or price comparison until effective configuration is observed.",
        ])
    else:
        lines.append("No configuration proposal has been prepared.")
    manifest = safe.get("dryRunManifest")
    lines.extend(["", "## Dry-run manifest", ""])
    if isinstance(manifest, dict):
        lines.extend([
            f"- **Status:** {manifest['status']}",
            "- **Writes:** 0",
            "- **Required next:** exact approved Preview-origin confirmation at enactment time.",
        ])
    else:
        lines.append("No dry-run manifest has been rendered.")
    enactment = safe.get("enactment")
    lines.extend(["", "## Enactment read-back", ""])
    if isinstance(enactment, dict):
        lines.extend([f"- **Status:** {enactment['status']}", f"- **Bounded writes:** {enactment['boundedWriteCount']}", f"- **Note:** {enactment['note']}"])
    else:
        lines.append("No Preview enactment has been recorded.")
    prediction = safe.get("pricePrediction")
    lines.extend(["", "## Player price prediction", ""])
    if isinstance(prediction, dict):
        lines.extend([f"- **Status:** {prediction['status']}", f"- **Scenario:** {prediction['scenario']['playerCount']} player / {prediction['scenario']['durationMinutes']} minutes / {prediction['scenario']['pricingBand']}", "- **Boundary:** this is a research comparison, never a quote, booking, or entitlement."])
    else:
        lines.append("No player price prediction has been prepared.")
    lines.extend(["", "## Boundary", "", "This record is configuration evidence only. It is not a price quote, customer entitlement, booking, attendance record, or regression finding.", ""])
    directory = storage.state / "experiments" / EXPERIMENT_ID
    storage.write_json(directory / "evidence.json", safe)
    storage.write_json(directory / "uncertainty.json", safe["uncertainty"])
    storage.write_text(directory / "evidence.md", "\n".join(lines))


def _public(experiment: Dict[str, Any]) -> Dict[str, Any]:
    value = deepcopy(experiment)
    if not isinstance(value.get("uncertainty"), dict):
        value["uncertainty"] = _initial_uncertainty(datetime.now(timezone.utc))
    reject_private_data(value, "configurationExperimentPublic")
    return value


def _initial_uncertainty(now: datetime) -> Dict[str, Any]:
    return {
        "schemaVersion": 1,
        "policy": {
            "maxUnknownAgeDays": MAX_UNKNOWN_AGE_DAYS,
            "invalidateOn": ["new Preview season", "relevant release context change"],
            "principle": "unknown is a state, never a default value",
        },
        "claims": [
            make_claim(claim_id="uncertainty:booking-engine", subject="feature.bookingEngine", kind="configuration", value="UNKNOWN", evidence_status="not_observed", reason="Authorized admin read-back has not happened.", alternatives=["LEGACY", "UNIFIED"], next_observation="authorized_admin_readback", stop_rule="do_not_prepare_player_pricing_journey", observed_at=now, max_age_days=MAX_UNKNOWN_AGE_DAYS),
            make_claim(claim_id="uncertainty:effective-scope", subject="effectiveScope", kind="configuration", value="unknown", evidence_status="not_observed", reason="The effective resolution scope has not been read back.", alternatives=["region", "area", "pod"], next_observation="authorized_admin_readback", stop_rule="do_not_propose_configuration_change", observed_at=now, max_age_days=MAX_UNKNOWN_AGE_DAYS),
            make_claim(claim_id="uncertainty:activation-readiness", subject="activationReadiness", kind="configuration", value="unknown", evidence_status="not_observed", reason="Rate-card and schedule readiness have not been read back.", alternatives=["ready", "generate", "missing_rate_card", "blocked_schedule", "conversion_failed"], next_observation="authorized_admin_readback", stop_rule="do_not_claim_unified_pricing_is_usable", observed_at=now, max_age_days=MAX_UNKNOWN_AGE_DAYS),
        ],
    }


def _post_configuration_uncertainty(now: datetime, configuration: Dict[str, Any]) -> Dict[str, Any]:
    engine_observed = configuration["featureFlags"][0]["value"] != "UNKNOWN"
    scope_observed = configuration["effectiveScope"] != "unknown"
    readiness_observed = configuration["activationReadiness"] != "unknown"
    return {
        "schemaVersion": 1,
        "policy": {
            "maxUnknownAgeDays": MAX_UNKNOWN_AGE_DAYS,
            "invalidateOn": ["new Preview season", "relevant release context change"],
            "principle": "unknown is a state, never a default value",
        },
        "claims": [
            make_claim(claim_id="uncertainty:booking-engine", subject="feature.bookingEngine", kind="configuration", value=configuration["featureFlags"][0]["value"], evidence_status="observed" if engine_observed else "inconclusive", reason="Authorized admin configuration read-back." if engine_observed else "The activation probe does not expose the effective booking-engine setting.", alternatives=["LEGACY", "UNIFIED", "UNKNOWN"], next_observation="authorized_admin_settings_readback" if not engine_observed else "new_season_reobservation", stop_rule="do_not_treat_as_permanent_configuration", observed_at=now, max_age_days=MAX_UNKNOWN_AGE_DAYS, freshness="current" if engine_observed else "unobserved"),
            make_claim(claim_id="uncertainty:effective-scope", subject="effectiveScope", kind="configuration", value=configuration["effectiveScope"], evidence_status="observed" if scope_observed else "inconclusive", reason="Authorized admin configuration read-back." if scope_observed else "The activation probe resolves settings but does not disclose the supplying scope.", alternatives=["region", "area", "pod", "unknown"], next_observation="authorized_admin_settings_readback" if not scope_observed else "new_season_reobservation", stop_rule="do_not_infer_scope_from_release_note", observed_at=now, max_age_days=MAX_UNKNOWN_AGE_DAYS, freshness="current" if scope_observed else "unobserved"),
            make_claim(claim_id="uncertainty:activation-readiness", subject="activationReadiness", kind="configuration", value=configuration["activationReadiness"], evidence_status="observed" if readiness_observed else "inconclusive", reason="Authorized admin activation-preview read-back.", alternatives=["ready", "generate", "missing_rate_card", "blocked_schedule", "conversion_failed", "unknown"], next_observation="new_season_reobservation", stop_rule="do_not_claim_unified_pricing_is_usable_without_current_readback", observed_at=now, max_age_days=MAX_UNKNOWN_AGE_DAYS, freshness="current" if readiness_observed else "unobserved"),
            make_claim(claim_id="uncertainty:pricing-prediction", subject="declaredPricingPrediction", kind="prediction", value="unknown", evidence_status="not_observed", reason="Configuration exploration does not create a price prediction.", alternatives=["exact_cents_prediction", "rounding_rule_needed", "not_applicable"], next_observation="separately_approved_prediction_preparation", stop_rule="do_not_ask_players_to_compare_prices", observed_at=now, max_age_days=MAX_UNKNOWN_AGE_DAYS),
        ],
    }


def _now() -> str:
    return isoformat(datetime.now(timezone.utc))
