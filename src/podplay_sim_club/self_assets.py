"""Typed, secret-free foundations for customer self-asset discovery.

This module performs no product requests. Future actor-owned probes hand their
responses to these strict per-subject sanitizers before evidence or facts are
persisted.
"""

from copy import deepcopy
from hashlib import sha256
from typing import Any, Dict, Optional

from .facts import make_fact, reject_private_data
from .storage import Storage
from .time import isoformat, parse_instant


BELIEF_STATUSES = {"unknown", "expected", "observed", "contradicted", "stale"}
EVIDENCE_RESULTS = {"observed", "unknown", "error"}
ERROR_CODES = {"missing_field", "not_observed", "not_supported", "request_failed", "unexpected_shape"}
PRODUCT_SUBJECTS = {
    "virtual_credit_balance",
    "owned_package_entitlements",
    "membership_assignment",
    "membership_benefits_understood",
    "payment_readiness",
    "booking_permissions",
}
EXPECTATION_SUBJECTS = {"spending_budget", "acceptable_booking_price"}
SUBJECTS = PRODUCT_SUBJECTS | EXPECTATION_SUBJECTS | {"rating"}


class SelfAssetValidationError(ValueError):
    """Raised when an asset observation is ambiguous, unsafe, or malformed."""


def belief_id(actor_id: str, subject: str) -> str:
    _require_actor_subject(actor_id, subject)
    return f"belief:{actor_id}:{subject}"


def make_evidence(
    *,
    probe_id: str,
    target_fingerprint: str,
    capability_id: str,
    actor_id: str,
    subject: str,
    observed_at: str,
    result: str,
    observation: Any = None,
    error_code: Optional[str] = None,
    contract_version: int = 1,
) -> Dict[str, Any]:
    """Build one sanitized subject observation; raw responses are discarded."""
    _require_nonempty("probe_id", probe_id)
    _require_nonempty("target_fingerprint", target_fingerprint)
    _require_nonempty("capability_id", capability_id)
    _require_actor_subject(actor_id, subject)
    if subject not in PRODUCT_SUBJECTS:
        raise SelfAssetValidationError("product evidence requires a discoverable product subject")
    if result not in EVIDENCE_RESULTS:
        raise SelfAssetValidationError("evidence result is invalid")
    if not isinstance(contract_version, int) or isinstance(contract_version, bool) or contract_version < 1:
        raise SelfAssetValidationError("evidence contract version is invalid")
    occurred = isoformat(parse_instant(observed_at))
    if result == "observed":
        if error_code is not None:
            raise SelfAssetValidationError("observed evidence cannot have an error code")
        safe_observation = sanitize_observation(subject, observation)
    else:
        if observation is not None:
            raise SelfAssetValidationError("unknown or failed evidence cannot carry an observation")
        if error_code not in ERROR_CODES:
            raise SelfAssetValidationError("unknown or failed evidence needs a bounded error code")
        safe_observation = None
    natural = "\0".join((probe_id, target_fingerprint, actor_id, subject, occurred, result))
    evidence_id = f"evidence:self-assets:{sha256(natural.encode('utf-8')).hexdigest()[:20]}"
    evidence = {
        "schemaVersion": 1,
        "evidenceId": evidence_id,
        "probeId": probe_id,
        "targetFingerprint": target_fingerprint,
        "actorId": actor_id,
        "capabilityId": capability_id,
        "subject": subject,
        "observedAt": occurred,
        "safeObservation": safe_observation,
        "contractVersion": contract_version,
        "result": result,
        "errorCode": error_code,
        "source": {"kind": "preview_readback", "actorOwned": True},
    }
    validate_evidence(evidence)
    return evidence


def validate_evidence(evidence: Any) -> None:
    if not isinstance(evidence, dict) or evidence.get("schemaVersion") != 1:
        raise SelfAssetValidationError("evidence schema version is invalid")
    required = {
        "schemaVersion", "evidenceId", "probeId", "targetFingerprint", "actorId",
        "capabilityId", "subject", "observedAt", "safeObservation",
        "contractVersion", "result", "errorCode", "source",
    }
    if set(evidence) != required:
        raise SelfAssetValidationError("evidence fields do not match the contract")
    for field in ("evidenceId", "probeId", "targetFingerprint", "actorId", "capabilityId", "subject"):
        _require_nonempty(field, evidence.get(field))
    natural = "\0".join((
        evidence["probeId"], evidence["targetFingerprint"], evidence["actorId"],
        evidence["subject"], evidence["observedAt"], evidence["result"],
    ))
    expected_id = f"evidence:self-assets:{sha256(natural.encode('utf-8')).hexdigest()[:20]}"
    if evidence["evidenceId"] != expected_id:
        raise SelfAssetValidationError("evidence ID does not match its observation")
    if evidence["subject"] not in PRODUCT_SUBJECTS:
        raise SelfAssetValidationError("evidence subject is unsupported")
    parse_instant(evidence["observedAt"])
    result = evidence.get("result")
    if result not in EVIDENCE_RESULTS:
        raise SelfAssetValidationError("evidence result is invalid")
    if result == "observed":
        if evidence.get("errorCode") is not None:
            raise SelfAssetValidationError("observed evidence cannot have an error code")
        sanitize_observation(evidence["subject"], evidence.get("safeObservation"))
    else:
        if evidence.get("safeObservation") is not None or evidence.get("errorCode") not in ERROR_CODES:
            raise SelfAssetValidationError("unknown evidence must contain only a bounded error")
    if (
        not isinstance(evidence.get("contractVersion"), int)
        or isinstance(evidence.get("contractVersion"), bool)
        or evidence["contractVersion"] < 1
    ):
        raise SelfAssetValidationError("evidence contract version is invalid")
    if evidence.get("source") != {"kind": "preview_readback", "actorOwned": True}:
        raise SelfAssetValidationError("evidence provenance is invalid")
    reject_private_data(evidence, "evidence")


def persist_evidence_locked(storage: Storage, evidence: Dict[str, Any]) -> str:
    """Persist a validated immutable evidence record while holding the writer lock."""
    validate_evidence(evidence)
    name = evidence["evidenceId"].replace(":", "-") + ".json"
    path = storage.state / "evidence" / name
    existing = storage.load_json(path, default=None)
    if existing is not None and existing != evidence:
        raise SelfAssetValidationError("evidence ID collision has different content")
    if existing is None:
        storage.write_json(path, deepcopy(evidence))
    return evidence["evidenceId"]


def make_belief_fact(
    *,
    actor_id: str,
    subject: str,
    status: str,
    occurred_at: str,
    correlation_id: str,
    expected_value: Any = None,
    observed_value: Any = None,
    evidence_ref: Optional[str] = None,
    expires_at: Optional[str] = None,
    source_kind: str = "belief_reducer",
) -> Dict[str, Any]:
    """Create one explicit belief transition.

    Staleness and contradiction are facts, not rebuild-time guesses. This keeps
    projection replay deterministic for a fixed journal.
    """
    _require_actor_subject(actor_id, subject)
    if status not in BELIEF_STATUSES:
        raise SelfAssetValidationError("belief status is invalid")
    if status == "expected" and expected_value is None:
        raise SelfAssetValidationError("an expected belief needs an expected value")
    if status == "observed" and observed_value is None:
        raise SelfAssetValidationError("an observed belief needs an observed value")
    if status == "contradicted" and (expected_value is None or observed_value is None):
        raise SelfAssetValidationError("a contradicted belief needs expected and observed values")
    if status in {"observed", "contradicted"} and not isinstance(evidence_ref, str):
        raise SelfAssetValidationError("observed belief transitions require evidence")
    if subject in PRODUCT_SUBJECTS and observed_value is not None:
        observed_value = sanitize_observation(subject, observed_value)
    reject_private_data(expected_value, "belief.expectedValue")
    if expires_at is not None and not isinstance(expires_at, str):
        raise SelfAssetValidationError("belief expiry must be a timestamp or null")
    expiry = isoformat(parse_instant(expires_at)) if isinstance(expires_at, str) else None
    payload = {
        "actorId": actor_id,
        "subject": subject,
        "status": status,
        "expectedValue": deepcopy(expected_value),
        "observedValue": deepcopy(observed_value),
        "expiresAt": expiry,
    }
    natural_suffix = evidence_ref or isoformat(parse_instant(occurred_at))
    return make_fact(
        fact_type=f"belief.{status}",
        natural_key=f"{belief_id(actor_id, subject)}:{status}:{natural_suffix}",
        subject_ref=belief_id(actor_id, subject),
        correlation_id=correlation_id,
        source_kind=source_kind,
        evidence_ref=evidence_ref,
        occurred_at=occurred_at,
        actor_id=actor_id,
        privacy="operator",
        payload=payload,
    )


def sanitize_observation(subject: str, value: Any) -> Any:
    if subject == "virtual_credit_balance":
        if isinstance(value, dict):
            _exact_dict(value, {"amount", "unit"}, "virtual-credit balance")
            if value.get("unit") != "virtual_credit":
                raise SelfAssetValidationError("virtual-credit unit is invalid")
            value = value.get("amount")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SelfAssetValidationError("virtual-credit balance must be explicitly numeric")
        return {"amount": float(value), "unit": "virtual_credit"}
    if subject == "payment_readiness":
        if isinstance(value, dict):
            _exact_dict(value, {"ready"}, "payment readiness")
            value = value.get("ready")
        if not isinstance(value, bool):
            raise SelfAssetValidationError("payment readiness must be boolean")
        return {"ready": value}
    if subject == "membership_assignment":
        return _membership(value)
    if subject == "membership_benefits_understood":
        if not isinstance(value, list) or not all(isinstance(item, str) and 0 < len(item) <= 160 for item in value):
            raise SelfAssetValidationError("membership benefits must be bounded display statements")
        return list(value)
    if subject == "owned_package_entitlements":
        if not isinstance(value, list):
            raise SelfAssetValidationError("package entitlements must be a list")
        rows = [_package_item(item) for item in value]
        return sorted(rows, key=lambda row: (row["kind"], row["label"], str(row.get("expiresAt") or "")))
    if subject == "booking_permissions":
        return _booking_permissions(value)
    raise SelfAssetValidationError(f"no product sanitizer exists for subject: {subject}")


def _membership(value: Any) -> Dict[str, Any]:
    allowed = {"assigned", "type", "displayName", "isOwner", "startDate"}
    _exact_dict(value, allowed, "membership assignment")
    if not isinstance(value.get("assigned"), bool):
        raise SelfAssetValidationError("membership assignment needs an explicit assigned flag")
    result = {"assigned": value["assigned"]}
    for field in ("type", "displayName"):
        item = value.get(field)
        if item is not None:
            if not isinstance(item, str) or not item or len(item) > 160:
                raise SelfAssetValidationError(f"membership {field} is invalid")
            result[field] = item
    if value.get("isOwner") is not None:
        if not isinstance(value["isOwner"], bool):
            raise SelfAssetValidationError("membership owner flag is invalid")
        result["isOwner"] = value["isOwner"]
    if value.get("startDate") is not None:
        result["startDate"] = isoformat(parse_instant(value["startDate"]))
    if not value["assigned"] and len(result) != 1:
        raise SelfAssetValidationError("unassigned membership cannot carry enrollment details")
    return result


def _package_item(value: Any) -> Dict[str, Any]:
    allowed = {"kind", "label", "remainingQuantity", "unit", "expiresAt", "status"}
    _exact_dict(value, allowed, "package entitlement")
    result: Dict[str, Any] = {}
    for field in ("kind", "label", "unit", "status"):
        item = value.get(field)
        if not isinstance(item, str) or not item or len(item) > 160:
            raise SelfAssetValidationError(f"package {field} is invalid")
        result[field] = item
    quantity = value.get("remainingQuantity")
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or quantity < 0:
        raise SelfAssetValidationError("package remaining quantity is invalid")
    result["remainingQuantity"] = float(quantity)
    expiry = value.get("expiresAt")
    result["expiresAt"] = isoformat(parse_instant(expiry)) if isinstance(expiry, str) else None
    return result


def _booking_permissions(value: Any) -> Dict[str, Any]:
    allowed = {
        "strategy", "selectableUnit", "selectableBlock", "allowMultiReservationBooking",
        "minDurationHours", "maxDurationHours", "remainingFuturePrivateReservations",
    }
    _exact_dict(value, allowed, "booking permissions")
    result = {}
    for key, item in value.items():
        if key == "allowMultiReservationBooking" and not isinstance(item, bool):
            raise SelfAssetValidationError("multi-reservation permission is invalid")
        if (
            key != "allowMultiReservationBooking"
            and (isinstance(item, bool) or not isinstance(item, (str, int, float, type(None))))
        ):
            raise SelfAssetValidationError(f"booking permission is invalid: {key}")
        result[key] = item
    return result


def _exact_dict(value: Any, allowed: set, label: str) -> None:
    if not isinstance(value, dict) or not set(value) <= allowed:
        raise SelfAssetValidationError(f"{label} contains unsupported fields")


def _require_actor_subject(actor_id: Any, subject: Any) -> None:
    _require_nonempty("actor_id", actor_id)
    _require_nonempty("subject", subject)
    if subject not in SUBJECTS:
        raise SelfAssetValidationError(f"unsupported belief subject: {subject}")


def _require_nonempty(field: str, value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise SelfAssetValidationError(f"{field} is required")
