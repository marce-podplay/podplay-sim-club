"""Small, validated uncertainty records shared by future Club Lab engines."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable

from .facts import reject_private_data
from .time import isoformat


EVIDENCE_STATUSES = {"observed", "documented", "predicted", "not_observed", "inconclusive", "contradicted", "stale"}
FRESHNESS_STATUSES = {"current", "unobserved", "stale"}


class UncertaintyError(ValueError):
    pass


def make_claim(
    *, claim_id: str, subject: str, kind: str, value: Any, evidence_status: str,
    reason: str, alternatives: Iterable[str], next_observation: str, stop_rule: str,
    observed_at: datetime, max_age_days: int, freshness: str = "unobserved",
) -> Dict[str, Any]:
    if evidence_status not in EVIDENCE_STATUSES or freshness not in FRESHNESS_STATUSES:
        raise UncertaintyError("uncertainty status is invalid")
    if not isinstance(max_age_days, int) or not 1 <= max_age_days <= 90:
        raise UncertaintyError("uncertainty max age must be between 1 and 90 days")
    fields = [claim_id, subject, kind, reason, next_observation, stop_rule]
    if not all(isinstance(value, str) and value for value in fields):
        raise UncertaintyError("uncertainty claim fields must be non-empty strings")
    options = list(dict.fromkeys(item for item in alternatives if isinstance(item, str) and item))
    if not options:
        raise UncertaintyError("uncertainty claim needs at least one alternative")
    base = observed_at.replace(tzinfo=timezone.utc) if observed_at.tzinfo is None else observed_at.astimezone(timezone.utc)
    claim = {
        "claimId": claim_id, "subject": subject, "kind": kind, "value": value,
        "evidenceStatus": evidence_status, "freshness": freshness,
        "reason": reason, "alternatives": options,
        "nextDiscriminatingObservation": next_observation, "stopRule": stop_rule,
        "asOf": isoformat(base), "expiresAt": isoformat(base + timedelta(days=max_age_days)),
    }
    reject_private_data(claim, "uncertaintyClaim")
    return claim


def replace_claim(claims: Iterable[Dict[str, Any]], replacement: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Replace by subject while preserving a compact, deterministic ledger."""
    value = [deepcopy(claim) for claim in claims if claim.get("subject") != replacement["subject"]]
    value.append(deepcopy(replacement))
    value.sort(key=lambda claim: claim["subject"])
    return value
