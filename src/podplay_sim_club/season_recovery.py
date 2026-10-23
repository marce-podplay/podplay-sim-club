"""Riley-facing, lead-gated recovery of a disposable Preview season.

Riley can inspect and request a reconciliation plan.  Only the external lead
can approve and execute it, and execution reuses the existing exact-origin
identity-seed adapter.  Durable state is deliberately summary-only.
"""

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Dict

from .config import RunMode
from .credentials import PreviewCredentials
from .firebase_auth import FirebaseAuthenticator
from .identity_registry import IdentityRegistry, IdentityRegistryError
from .preview_readonly import PreviewReadonlyClient
from .preview_seed import apply_identity_seed, build_seed_plan, load_preview_seed, seed_actor_ids
from .preview_write import PreviewWriteError
from .storage import Storage
from .target_policy import TargetPolicy
from .time import isoformat


STATE_FILE = "season-recovery.json"
RECOVERY_REQUESTERS = {"riley", "sofia"}


class SeasonRecoveryError(ValueError):
    pass


def plan(root: Path) -> Dict[str, Any]:
    """Inspect a Preview season and prepare a local-only bootstrap sequence.

    A Firebase login can outlive the disposable Preview database.  The plan
    therefore records only a one-way fingerprint of the observed product
    profiles.  It can flag the particularly strong signal for a fresh season
    (all formerly present profiles disappeared), but it never creates a
    profile, sends email, or makes a player decision.
    """
    credentials = PreviewCredentials.load(root)
    policy = TargetPolicy.from_config(credentials.config)
    identities = _existing_identities(root)
    admin = FirebaseAuthenticator(root / "secrets" / "preview-auth-cache.json").authenticate(
        credentials.admin_email, credentials.admin_password, credentials.firebase_api_key
    )
    raw = build_seed_plan(root, PreviewReadonlyClient(policy, admin.id_token), identities)
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        summary = _summary(raw, policy.pull_request_number, state.get("latestPlan"))
        state["latestPlan"] = summary
        if not summary["reconciliationRequired"]:
            state["request"] = None
        storage.write_json(storage.state / STATE_FILE, state)
    return summary


def request(root: Path, actor_id: str) -> Dict[str, Any]:
    if actor_id not in RECOVERY_REQUESTERS:
        raise SeasonRecoveryError("only Riley or Sofia may request season recovery")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        planned = state.get("latestPlan")
        if not isinstance(planned, dict):
            raise SeasonRecoveryError("Riley needs a fresh recovery plan before requesting reconciliation")
        if not planned.get("reconciliationRequired"):
            raise SeasonRecoveryError("the current Preview season needs no identity reconciliation")
        season_status = planned.get("seasonStatus")
        new_season = isinstance(season_status, dict) and season_status.get("kind") == "new_preview_season_suspected"
        entry = {
            "requestId": f"recovery:{planned['targetFingerprint']}",
            "requestedBy": actor_id,
            "requestedAt": isoformat(datetime.now(timezone.utc)),
            "targetFingerprint": planned["targetFingerprint"],
            "actorIds": [row["actorId"] for row in planned["actors"] if row["status"] == "missing"],
            "requestedAction": (
                "bootstrap_new_preview_season_product_profiles"
                if new_season
                else "reconcile_preview_product_profiles"
            ),
            "status": "requested",
            "approvedBy": None,
            "approvedAt": None,
        }
        state["request"] = entry
        storage.write_json(storage.state / STATE_FILE, state)
        return entry


def approve(root: Path, actor_id: str) -> Dict[str, Any]:
    if actor_id != "lead":
        raise SeasonRecoveryError("only the external lead may approve reconciliation")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        entry = state.get("request")
        if not isinstance(entry, dict) or entry.get("status") != "requested":
            raise SeasonRecoveryError("there is no pending recovery request")
        entry.update({"status": "approved", "approvedBy": actor_id, "approvedAt": isoformat(datetime.now(timezone.utc))})
        storage.write_json(storage.state / STATE_FILE, state)
        return dict(entry)


def apply(root: Path, confirmation: str) -> Dict[str, Any]:
    """Execute only a fresh, lead-approved plan under exact-origin confirmation."""
    credentials = PreviewCredentials.load(root)
    read_policy = TargetPolicy.from_config(credentials.config)
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        entry = state.get("request")
        planned = state.get("latestPlan")
        if not isinstance(entry, dict) or entry.get("status") != "approved":
            raise SeasonRecoveryError("lead approval is required before reconciliation")
        if not isinstance(planned, dict) or entry.get("targetFingerprint") != planned.get("targetFingerprint"):
            raise SeasonRecoveryError("recovery request does not match the current plan")
    write_policy = TargetPolicy.from_config(replace(
        credentials.config, mode=RunMode.PREVIEW_WRITE, preview_write_confirmation=confirmation,
    ))
    identities = _existing_identities(root)
    admin = FirebaseAuthenticator(root / "secrets" / "preview-auth-cache.json").authenticate(
        credentials.admin_email, credentials.admin_password, credentials.firebase_api_key
    )
    raw_plan, results = apply_identity_seed(
        root, read_policy, write_policy, PreviewReadonlyClient(read_policy, admin.id_token),
        identities, credentials.firebase_api_key, admin.id_token,
    )
    # Re-read after the bounded bootstrap.  Persisting the pre-write plan
    # would misleadingly say profiles are still missing after a successful
    # reconciliation.
    verified_raw = build_seed_plan(root, PreviewReadonlyClient(read_policy, admin.id_token), identities)
    with storage.writer_lock():
        state = _load(storage)
        completed = _summary(verified_raw, read_policy.pull_request_number, state.get("latestPlan"))
        state["latestPlan"] = completed
        state["request"] = {
            **state.get("request", {}), "status": "executed",
            "executedAt": isoformat(datetime.now(timezone.utc)),
            "actorsReconciled": len(results),
        }
        storage.write_json(storage.state / STATE_FILE, state)
    return {"plan": completed, "actorsReconciled": len(results), "status": "executed"}


def snapshot(root: Path) -> Dict[str, Any]:
    state = _load(Storage(root))
    return {"latestPlan": state.get("latestPlan"), "request": state.get("request")}


def _existing_identities(root: Path) -> Dict[str, Any]:
    seed = load_preview_seed(root)
    ids = seed_actor_ids(seed)
    stored = IdentityRegistry(root / "secrets" / "preview-actors.json").stored()
    missing = [actor_id for actor_id in ids if actor_id not in stored]
    if missing:
        raise IdentityRegistryError("private actor registry is missing: " + ", ".join(missing))
    return {actor_id: stored[actor_id] for actor_id in ids}


def _summary(raw: Dict[str, Any], pr_number: int, previous: Dict[str, Any] | None = None) -> Dict[str, Any]:
    actors = raw.get("actors", {})
    if not isinstance(actors, dict):
        raise SeasonRecoveryError("identity plan is malformed")
    statuses = []
    for actor_id, row in actors.items():
        if not isinstance(actor_id, str) or not isinstance(row, dict) or row.get("status") not in {"exists", "missing"}:
            raise SeasonRecoveryError("identity plan actor status is malformed")
        statuses.append({"actorId": actor_id, "status": row["status"]})
    statuses.sort(key=lambda row: row["actorId"])
    required_roles = raw.get("staffFixture", {}).get("productRoles", []) if isinstance(raw.get("staffFixture"), dict) else []
    if not isinstance(actors, dict):
        raise SeasonRecoveryError("identity plan actors are malformed")
    fingerprint = _profile_fingerprint(raw, statuses)
    transition = _transition(previous, statuses, fingerprint)
    reconciliation_required = any(row["status"] == "missing" for row in statuses)
    return {
        "schemaVersion": 2,
        "targetFingerprint": f"pr-{pr_number}:season-recovery-v1",
        "observedAt": isoformat(datetime.now(timezone.utc)),
        "profileFingerprint": fingerprint,
        "seasonStatus": transition,
        "signupPrerequisitesSafe": raw.get("prerequisitesOk") is True,
        "configuredVenuePresent": True,
        "actors": statuses,
        "staffRolesRequired": list(required_roles) if isinstance(required_roles, list) else [],
        "reconciliationRequired": reconciliation_required,
        "bootstrap": {
            "status": "approval_required" if reconciliation_required else "ready",
            "scope": "preview_product_profiles_and_required_staff_roles",
            "steps": [
                "read_only_identity_profile_inspection",
                "staff_requests_or_reviews_the_local_bootstrap_plan",
                "external_lead_approves_the_exact_preview_reconciliation",
                "reconcile_missing_product_profiles_and_required_staff_roles",
                "read_back_each_profile_before_any_browser_journey_is_enabled",
            ] if reconciliation_required else [
                "read_back_each_profile_before_any_browser_journey_is_enabled",
            ],
            "createsFirebaseIdentity": False,
            "sendsEmail": False,
            "enablesPlayerActions": False,
        },
    }


def _load(storage: Storage) -> Dict[str, Any]:
    value = storage.load_json(storage.state / STATE_FILE, default={"schemaVersion": 1, "latestPlan": None, "request": None})
    if not isinstance(value, dict) or value.get("schemaVersion") not in {1, 2}:
        raise SeasonRecoveryError("season recovery state is invalid")
    if value.get("schemaVersion") == 1:
        # Old state held no sensitive product references, so this migration is
        # local and safe.  The next read-only plan supplies the new fingerprint.
        value["schemaVersion"] = 2
    if not isinstance(value.get("latestPlan"), (dict, type(None))) or not isinstance(value.get("request"), (dict, type(None))):
        raise SeasonRecoveryError("season recovery state is invalid")
    return value


def _profile_fingerprint(raw: Dict[str, Any], statuses: list[Dict[str, str]]) -> str:
    """Return a non-reversible continuity marker; never persist product IDs."""
    actors = raw.get("actors", {})
    material = {
        "tenant": str(raw.get("tenantId") or ""),
        "area": str(raw.get("areaId") or ""),
        "pod": str(raw.get("podId") or ""),
        "actors": [
            {
                "actorId": row["actorId"],
                "status": row["status"],
                "profile": str(actors.get(row["actorId"], {}).get("podplayUserId") or ""),
            }
            for row in statuses
        ],
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def _transition(previous: Dict[str, Any] | None, statuses: list[Dict[str, str]], fingerprint: str) -> Dict[str, str]:
    if not isinstance(previous, dict) or not isinstance(previous.get("profileFingerprint"), str):
        return {"kind": "initial_observation", "reason": "no_prior_safe_profile_observation"}
    if previous["profileFingerprint"] == fingerprint:
        return {"kind": "continuing", "reason": "same_safe_profile_observation"}
    old_actors = previous.get("actors") if isinstance(previous.get("actors"), list) else []
    had_profiles = any(isinstance(row, dict) and row.get("status") == "exists" for row in old_actors)
    all_missing = bool(statuses) and all(row["status"] == "missing" for row in statuses)
    if had_profiles and all_missing:
        return {"kind": "new_preview_season_suspected", "reason": "all_previously_present_product_profiles_are_absent"}
    return {"kind": "product_profile_state_changed", "reason": "safe_profile_fingerprint_changed"}
