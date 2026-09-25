"""Durable occurrence ledger for PR-preview matches."""

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .storage import Storage


JsonObject = Dict[str, Any]
LEDGER_FILE = "preview-matches.json"
LEGACY_FILE = "preview-manual-match.json"


class PreviewMatchLedgerError(ValueError):
    """Raised when the local match ledger is incomplete or ambiguous."""


def load_match_ledger(storage: Storage, target_origin: str) -> JsonObject:
    """Load the ledger, migrating the single-match checkpoint when necessary."""

    path = storage.state / LEDGER_FILE
    value = storage.load_json(path, default=None)
    if value is None:
        value = _new_ledger(target_origin)
        legacy_path = storage.state / LEGACY_FILE
        legacy = storage.load_json(legacy_path, default=None)
        if isinstance(legacy, dict) and legacy:
            _assert_origin(legacy, target_origin, "legacy manual-match checkpoint")
            key = _occurrence_from_match(legacy)
            value["matches"][key] = deepcopy(legacy)
            value["activeOccurrenceKey"] = key
            storage.write_json(path, value)
        return value

    if not isinstance(value, dict) or value.get("schemaVersion") != 2:
        raise PreviewMatchLedgerError("preview match ledger has an unsupported schema")
    _assert_origin(value, target_origin, "preview match ledger")
    if not isinstance(value.get("matches"), dict):
        raise PreviewMatchLedgerError("preview match ledger is missing matches")
    active = value.get("activeOccurrenceKey")
    if active is not None and active not in value["matches"]:
        raise PreviewMatchLedgerError("active preview occurrence is missing from the ledger")
    return value


def save_match(
    storage: Storage,
    ledger: JsonObject,
    match: JsonObject,
    *,
    make_active: bool = True,
) -> str:
    key = _occurrence_from_match(match)
    ledger["matches"][key] = deepcopy(match)
    if make_active:
        ledger["activeOccurrenceKey"] = key
    storage.write_json(storage.state / LEDGER_FILE, ledger)
    return key


def select_match(
    ledger: JsonObject,
    occurrence: Optional[str] = None,
) -> Tuple[str, JsonObject]:
    matches = ledger["matches"]
    key = occurrence or ledger.get("activeOccurrenceKey")
    if key is None and len(matches) == 1:
        key = next(iter(matches))
    if key is None:
        raise PreviewMatchLedgerError(
            "no active preview match; select one with `preview select --occurrence ...`"
        )
    match = matches.get(key)
    if not isinstance(match, dict):
        raise PreviewMatchLedgerError(f"preview occurrence is not in the ledger: {key}")
    return key, deepcopy(match)


def select_active(storage: Storage, ledger: JsonObject, occurrence: str) -> None:
    if occurrence not in ledger["matches"]:
        raise PreviewMatchLedgerError(
            f"preview occurrence is not in the ledger: {occurrence}"
        )
    ledger["activeOccurrenceKey"] = occurrence
    storage.write_json(storage.state / LEDGER_FILE, ledger)


def sanitized_match_index(ledger: JsonObject) -> JsonObject:
    """Return only observatory-safe fields from every local occurrence."""

    rows = []
    for key, match in ledger["matches"].items():
        plan = match.get("plan") if isinstance(match.get("plan"), dict) else {}
        event = match.get("event") if isinstance(match.get("event"), dict) else {}
        event_id = match.get("eventId")
        blue_invitation = (
            match.get("blueInvitation")
            if isinstance(match.get("blueInvitation"), dict)
            else {}
        )
        rows.append(
            {
                "occurrenceKey": key,
                "occurrenceCode": _short_code("OCC", key),
                "active": key == ledger.get("activeOccurrenceKey"),
                "phase": match.get("phase"),
                "eventId": event_id,
                "eventCode": _short_code("EVT", event_id),
                "label": event.get("name") or _event_label(event),
                "eventStatus": event.get("status"),
                "startTime": plan.get("startTime"),
                "endTime": plan.get("endTime"),
                "podId": plan.get("podId"),
                "participants": [
                    {
                        "actorId": "red-captain",
                        "label": "Andy Bogard",
                        "role": "booking owner",
                        "attendance": "not refreshed",
                    },
                    {
                        "actorId": "blue-captain",
                        "label": "Terry Bogard",
                        "role": "invited player",
                        "invitationStatus": blue_invitation.get("status", "unknown"),
                        "attendance": blue_invitation.get("checkInStatus", "not refreshed"),
                    },
                ],
            }
        )
    rows.sort(key=lambda row: (str(row.get("startTime") or ""), row["occurrenceKey"]))
    return {
        "activeOccurrenceKey": ledger.get("activeOccurrenceKey"),
        "matches": rows,
    }


def _short_code(prefix: str, value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    compact = "".join(character for character in value if character.isalnum())
    return f"{prefix}-{compact[:8].upper()}" if compact else None


def _event_label(event: JsonObject) -> str:
    subtype = event.get("subtype")
    if subtype == "PRIVATE":
        return "Private booking"
    if subtype == "OPEN_PLAY":
        return "Open Play"
    return "Preview event"


def _new_ledger(target_origin: str) -> JsonObject:
    return {
        "schemaVersion": 2,
        "targetOrigin": target_origin,
        "activeOccurrenceKey": None,
        "matches": {},
    }


def _assert_origin(value: JsonObject, target_origin: str, label: str) -> None:
    if value.get("targetOrigin") != target_origin:
        raise PreviewMatchLedgerError(f"{label} belongs to another origin")


def _occurrence_from_match(match: JsonObject) -> str:
    plan = match.get("plan") if isinstance(match.get("plan"), dict) else None
    key = plan.get("occurrenceKey") if plan else None
    if not isinstance(key, str) or not key:
        raise PreviewMatchLedgerError("preview match is missing its occurrence key")
    return key
