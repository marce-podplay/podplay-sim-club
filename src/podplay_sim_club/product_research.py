"""Safe feature contracts and release-aware Preview research state.

This module deliberately separates a *request to verify* from a product
entitlement or successful journey.  It stores only labels, booleans, counts,
and an opaque target fingerprint; raw Preview responses stay in memory.
"""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .facts import reject_private_data
from .preview_readonly import PreviewReadError, collection_items
from .preview_seed import SIGNUP_SETTINGS, load_preview_seed
from .storage import Storage
from .time import isoformat


STATE_FILE = "product-research.json"
CONTRACT_VERSION = 1
FEATURE_TEAM_SIGNUP = "team-signup"
ALLOWED_REQUESTERS = {"sofia", "riley"}


class ProductResearchError(ValueError):
    pass


def feature_contract(feature_id: str) -> Dict[str, Any]:
    """Return the product-neutral contract for one deliberately small slice."""
    if feature_id != FEATURE_TEAM_SIGNUP:
        raise ProductResearchError("unsupported feature contract")
    return {
        "schemaVersion": CONTRACT_VERSION,
        "featureId": FEATURE_TEAM_SIGNUP,
        "displayRole": "Big Brother",
        "vocabulary": {
            "verificationRequest": "A named request to observe a bounded feature surface.",
            "acknowledgement": "A named verifier's explicit accept or pass decision.",
            "configurationObservation": "A sanitized, time-bounded Preview read-back.",
            "issueCandidate": "A reproducible concern, not a regression finding.",
        },
        "discoveryBoundary": {
            "remoteMethods": ["GET"],
            "knownSurfaces": [
                "tenant signup flags",
                "configured area and pod presence",
                "customer booking quota shape",
            ],
            "notExposed": [
                "release/change feed",
                "team-signup entitlement",
                "attendance",
            ],
            "forbiddenState": ["credentials", "raw IDs", "private URLs", "raw tenant settings"],
        },
        "authority": [
            {"actor": "Sofia or Riley", "may": "request verification and review evidence", "mayNot": "override player consent or product authorization"},
            {"actor": "named player", "may": "accept or pass their own verification request", "mayNot": "accept for another player or claim attendance"},
            {"actor": "authenticated Preview caller", "may": "perform the bounded GET read", "mayNot": "write, enroll, book, or alter configuration"},
        ],
        "probe": {
            "kind": "configuration-discovery",
            "writes": 0,
            "readBack": "tenant flags, configured venue/pod presence, and quota field availability",
            "failureRule": "one failed or unavailable read creates an unknown observation, never a regression",
        },
    }


def create_verification_request(
    root: Path, *, feature_id: str, requester: str, verifier_ids: Iterable[str], requested_at: Optional[datetime] = None
) -> Dict[str, Any]:
    contract = feature_contract(feature_id)
    if requester not in ALLOWED_REQUESTERS:
        raise ProductResearchError("only Sofia or Riley may request feature verification")
    verifiers = _named_verifiers(root, verifier_ids)
    if not verifiers:
        raise ProductResearchError("at least one named player or staff verifier is required")
    observed_at = isoformat(_utc(requested_at or datetime.now(timezone.utc)))
    natural = f"{feature_id}\0{requester}\0{','.join(verifiers)}\0{observed_at}"
    probe_id = "probe:feature:" + sha256(natural.encode("utf-8")).hexdigest()[:20]
    request = {
        "probeId": probe_id,
        "featureId": feature_id,
        "contractVersion": contract["schemaVersion"],
        "displayRole": contract["displayRole"],
        "requestedBy": requester,
        "requestedAt": observed_at,
        "status": "awaiting_acknowledgement",
        "verifiers": [{"actorId": actor_id, "decision": None, "decidedAt": None} for actor_id in verifiers],
        "configuration": None,
        "issueCandidate": None,
    }
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        state["probes"][probe_id] = request
        storage.write_json(storage.state / STATE_FILE, state)
    return deepcopy(request)


def acknowledge_verification(
    root: Path, *, probe_id: str, actor_id: str, decision: str, decided_at: Optional[datetime] = None
) -> Dict[str, Any]:
    if decision not in {"accept", "pass"}:
        raise ProductResearchError("verification decision must be accept or pass")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        probe = _probe(state, probe_id)
        if probe["status"] != "awaiting_acknowledgement":
            raise ProductResearchError("verification request is no longer awaiting acknowledgement")
        verifier = next((item for item in probe["verifiers"] if item["actorId"] == actor_id), None)
        if verifier is None:
            raise ProductResearchError("only a named verifier may decide")
        if verifier["decision"] is not None:
            raise ProductResearchError("verifier already decided")
        verifier["decision"] = decision
        verifier["decidedAt"] = isoformat(_utc(decided_at or datetime.now(timezone.utc)))
        decisions = [item["decision"] for item in probe["verifiers"]]
        if "pass" in decisions:
            probe["status"] = "passed"
        elif all(value == "accept" for value in decisions):
            probe["status"] = "ready_for_readback"
        storage.write_json(storage.state / STATE_FILE, state)
        return deepcopy(probe)


def discover_team_signup_context_with_client(
    root: Path, *, probe_id: str, client: Any, target_fingerprint: str, observed_at: datetime
) -> Dict[str, Any]:
    """Perform the contract's bounded GET discovery after every verifier accepts.

    The configured location is used only to select data in memory.  It is never
    written to state, so this projection cannot leak a tenant's raw identifiers.
    """
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        probe = _probe(state, probe_id)
        if probe["featureId"] != FEATURE_TEAM_SIGNUP or probe["status"] != "ready_for_readback":
            raise ProductResearchError("team-signup probe is not ready for a read-back")

    result = _configuration_observation(root, client)
    observation = {
        "targetFingerprint": target_fingerprint,
        "observedAt": isoformat(_utc(observed_at)),
        "result": result["result"],
        "summary": result["summary"],
        "errorCode": result.get("errorCode"),
        "release": {"status": "unknown", "reason": "no supported Preview release/change read surface is known"},
    }
    reject_private_data(observation, "product_research.configuration")
    with storage.writer_lock():
        state = _load(storage)
        probe = _probe(state, probe_id)
        # Decisions cannot be bypassed during a concurrent discovery run.
        if probe["status"] != "ready_for_readback":
            raise ProductResearchError("team-signup probe changed before read-back")
        probe["configuration"] = observation
        probe["status"] = "observed" if result["result"] == "observed" else "inconclusive"
        storage.write_json(storage.state / STATE_FILE, state)
        return deepcopy(probe)


def issue_candidate(root: Path, *, probe_id: str, reason: str) -> Dict[str, Any]:
    """Create an explicitly non-regression issue candidate from an observed probe."""
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 240:
        raise ProductResearchError("issue candidate reason is required and bounded")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        probe = _probe(state, probe_id)
        if probe["status"] not in {"observed", "inconclusive"}:
            raise ProductResearchError("a read-back is required before creating an issue candidate")
        probe["issueCandidate"] = {
            "status": "candidate",
            "reason": reason.strip(),
            "regression": "unconfirmed",
            "requires": ["a passing baseline or repeated controlled probe", "sanitized read-back evidence"],
        }
        storage.write_json(storage.state / STATE_FILE, state)
        return deepcopy(probe)


def research_snapshot(root: Path) -> Dict[str, Any]:
    state = _load(Storage(root))
    return {"contracts": [feature_contract(FEATURE_TEAM_SIGNUP)], "probes": list(state["probes"].values())}


def _configuration_observation(root: Path, client: Any) -> Dict[str, Any]:
    try:
        settings = {
            item.get("id"): item.get("value")
            for item in collection_items(client.get("/apis/v2/tenants/current/settings"))
            if isinstance(item, dict) and item.get("id") in SIGNUP_SETTINGS
        }
        seed = load_preview_seed(root)
        location = seed.get("location") if isinstance(seed.get("location"), dict) else {}
        area_id, pod_id = location.get("areaId"), location.get("podId")
        if not isinstance(area_id, str) or not isinstance(pod_id, str):
            raise PreviewReadError("configured location is missing")
        areas = collection_items(client.get("/apis/v2/areas"))
        area_present = any(isinstance(item, dict) and item.get("id") == area_id for item in areas)
        pods = collection_items(client.get(f"/apis/v2/areas/{area_id}/pods")) if area_present else []
        pod_present = any(isinstance(item, dict) and item.get("id") == pod_id for item in pods)
        # This customer-visible settings shape is an observable booking boundary,
        # not proof that a team member is eligible or enrolled.
        booking = client.get(f"/apis/v2/users/current/settings/booking/{area_id}/pods") if pod_present else {"items": []}
        has_quota_shape = any(
            isinstance(item, dict) and isinstance(item.get("pod"), dict) and item["pod"].get("id") == pod_id and isinstance(item.get("quotas"), dict)
            for item in collection_items(booking)
        )
        return {"result": "observed", "summary": {
            "tenantSignupFlagsObserved": len(settings) == len(SIGNUP_SETTINGS),
            "waiverRequired": _truthy(settings.get("feature.areaLiabilityWaiver")) or _truthy(settings.get("feature.liabilityWaiver")),
            "phoneRequired": _truthy(settings.get("feature.requirePhoneNumberOnSignup")),
            "testPaymentsEnabled": _truthy(settings.get("stripe.testModeEnabled")),
            "configuredAreaPresent": area_present,
            "configuredPodPresent": pod_present,
            "bookingQuotaShapePresent": has_quota_shape,
        }}
    except PreviewReadError:
        return {"result": "unknown", "summary": {}, "errorCode": "read_failed"}


def _named_verifiers(root: Path, verifier_ids: Iterable[str]) -> list[str]:
    roster = Storage(root).load_json(root / "characters" / "roster.json", default={})
    known = {row.get("actorId") for row in roster.get("characters", []) if isinstance(row, dict) and row.get("role") in {"player", "club_staff", "club_owner"}}
    values = list(dict.fromkeys(actor_id for actor_id in verifier_ids if isinstance(actor_id, str)))
    if not values or any(actor_id not in known for actor_id in values):
        raise ProductResearchError("every verifier must be a named player or staff member")
    return values


def _load(storage: Storage) -> Dict[str, Any]:
    state = storage.load_json(storage.state / STATE_FILE, default={"schemaVersion": 1, "probes": {}})
    if not isinstance(state, dict) or state.get("schemaVersion") != 1 or not isinstance(state.get("probes"), dict):
        raise ProductResearchError("product research state is invalid")
    return state


def _probe(state: Dict[str, Any], probe_id: str) -> Dict[str, Any]:
    probe = state["probes"].get(probe_id)
    if not isinstance(probe, dict):
        raise ProductResearchError("feature probe was not found")
    return probe


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
