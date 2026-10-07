"""Validated, append-only, secret-free community facts."""

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Dict, Iterable, Optional, Union

from .storage import Storage
from .time import isoformat, parse_instant


FACT_FILE = "journal/facts.jsonl"
PRIVACY_LEVELS = {"public", "community", "operator"}
REQUIRED_FIELDS = {
    "schemaVersion", "factId", "type", "occurredAt", "recordedAt",
    "subjectRef", "correlationId", "source", "privacy", "payload",
}
FORBIDDEN_KEY_FRAGMENTS = {
    "authorization", "bearertoken", "cookie", "credential", "email",
    "firebaseuid", "idtoken", "password", "paymentmethodid", "refreshtoken",
    "redeemurl", "secret", "stripeid",
}
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
JWT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}$")
PRIVATE_VALUE_PREFIXES = ("sk_", "rk_", "pm_", "cus_", "pi_", "seti_", "firebase:")


class FactValidationError(ValueError):
    """Raised when a fact is unsafe or structurally invalid."""


def deterministic_fact_id(fact_type: str, natural_key: str) -> str:
    if not isinstance(fact_type, str) or not fact_type or not isinstance(natural_key, str) or not natural_key:
        raise FactValidationError("fact type and natural key are required")
    digest = sha256(f"{fact_type}\0{natural_key}".encode("utf-8")).hexdigest()[:20]
    return f"fact:{fact_type}:{digest}"


def make_fact(
    *,
    fact_type: str,
    natural_key: str,
    subject_ref: str,
    correlation_id: str,
    source_kind: str,
    payload: Dict[str, Any],
    occurred_at: Union[datetime, str],
    actor_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    causation_id: Optional[str] = None,
    evidence_ref: Optional[str] = None,
    privacy: str = "community",
) -> Dict[str, Any]:
    occurred = isoformat(occurred_at) if isinstance(occurred_at, datetime) else isoformat(parse_instant(occurred_at))
    fact = {
        "schemaVersion": 1,
        "factId": deterministic_fact_id(fact_type, natural_key),
        "type": fact_type,
        "occurredAt": occurred,
        "recordedAt": occurred,
        "actorId": actor_id,
        "subjectRef": subject_ref,
        "conversationId": conversation_id,
        "causationId": causation_id,
        "correlationId": correlation_id,
        "source": {"kind": source_kind, "evidenceRef": evidence_ref},
        "privacy": privacy,
        "payload": deepcopy(payload),
    }
    validate_fact(fact)
    return fact


def validate_fact(fact: Any) -> None:
    if not isinstance(fact, dict) or fact.get("schemaVersion") != 1:
        raise FactValidationError("fact schema version is invalid")
    missing = REQUIRED_FIELDS - set(fact)
    if missing:
        raise FactValidationError(f"fact is missing fields: {', '.join(sorted(missing))}")
    for field in ("factId", "type", "occurredAt", "recordedAt", "subjectRef", "correlationId"):
        if not isinstance(fact.get(field), str) or not fact[field]:
            raise FactValidationError(f"fact field is invalid: {field}")
    parse_instant(fact["occurredAt"])
    parse_instant(fact["recordedAt"])
    if fact.get("privacy") not in PRIVACY_LEVELS:
        raise FactValidationError("private facts do not belong in the community journal")
    source = fact.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("kind"), str) or not source["kind"]:
        raise FactValidationError("fact source is invalid")
    if not isinstance(fact.get("payload"), dict):
        raise FactValidationError("fact payload must be an object")
    _reject_secret_keys(fact)


def append_fact_locked(storage: Storage, fact: Dict[str, Any]) -> bool:
    """Append one validated fact while the caller owns the writer lock."""
    validate_fact(fact)
    path = storage.state / FACT_FILE
    if any(row.get("factId") == fact["factId"] for row in storage.read_jsonl(path)):
        return False
    storage.append_jsonl(path, deepcopy(fact))
    return True


def append_facts_locked(storage: Storage, facts: Iterable[Dict[str, Any]]) -> int:
    return sum(1 for fact in facts if append_fact_locked(storage, fact))


def append_fact(root: Path, fact: Dict[str, Any]) -> bool:
    storage = Storage(root)
    with storage.writer_lock():
        return append_fact_locked(storage, fact)


def load_facts(root: Path) -> list[Dict[str, Any]]:
    storage = Storage(root)
    rows = storage.read_jsonl(storage.state / FACT_FILE)
    for row in rows:
        validate_fact(row)
    return rows


def _reject_secret_keys(value: Any, path: str = "fact") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = "".join(character for character in str(key).lower() if character.isalnum())
            if any(fragment in normalized for fragment in FORBIDDEN_KEY_FRAGMENTS):
                raise FactValidationError(f"secret-shaped field is forbidden in facts: {path}.{key}")
            _reject_secret_keys(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_secret_keys(nested, f"{path}[{index}]")
    elif isinstance(value, str):
        lowered = value.lower()
        if EMAIL_PATTERN.match(value) or JWT_PATTERN.match(value):
            raise FactValidationError(f"secret-shaped value is forbidden in facts: {path}")
        if lowered.startswith(("http://", "https://")) or lowered.startswith(PRIVATE_VALUE_PREFIXES):
            raise FactValidationError(f"private reference value is forbidden in facts: {path}")
        if len(value) > 512:
            raise FactValidationError(f"unbounded string is forbidden in facts: {path}")


def reject_private_data(value: Any, path: str = "value") -> None:
    """Apply the journal's privacy boundary to other durable safe records."""
    _reject_secret_keys(value, path)
