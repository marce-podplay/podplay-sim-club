"""Durable booking intents produced by character agreements."""

from copy import deepcopy
from typing import Any, Dict, Optional, Tuple

from .storage import Storage


JsonObject = Dict[str, Any]
INTENT_FILE = "booking-intents.json"


class BookingIntentError(ValueError):
    """Raised when an intent cannot be selected or validated."""


def load_intent_ledger(storage: Storage) -> JsonObject:
    value = storage.load_json(storage.state / INTENT_FILE, default=None)
    if value is None:
        return {"schemaVersion": 1, "activeIntentId": None, "intents": {}}
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise BookingIntentError("booking-intent ledger has an unsupported schema")
    if not isinstance(value.get("intents"), dict):
        raise BookingIntentError("booking-intent ledger is missing intents")
    active = value.get("activeIntentId")
    if active is not None and active not in value["intents"]:
        raise BookingIntentError("active booking intent is missing from the ledger")
    return value


def save_intent(
    storage: Storage,
    ledger: JsonObject,
    intent: JsonObject,
    *,
    make_active: bool = True,
) -> str:
    intent_id = intent.get("id")
    if not isinstance(intent_id, str) or not intent_id:
        raise BookingIntentError("booking intent is missing its ID")
    ledger["intents"][intent_id] = deepcopy(intent)
    if make_active:
        ledger["activeIntentId"] = intent_id
    storage.write_json(storage.state / INTENT_FILE, ledger)
    return intent_id


def select_intent(
    ledger: JsonObject, intent_id: Optional[str] = None
) -> Tuple[str, JsonObject]:
    selected = intent_id or ledger.get("activeIntentId")
    if selected is None and len(ledger["intents"]) == 1:
        selected = next(iter(ledger["intents"]))
    if selected is None:
        raise BookingIntentError("no active booking intent")
    intent = ledger["intents"].get(selected)
    if not isinstance(intent, dict):
        raise BookingIntentError(f"booking intent does not exist: {selected}")
    return selected, deepcopy(intent)


def sanitized_intents(ledger: JsonObject) -> JsonObject:
    rows = []
    for intent_id, intent in ledger["intents"].items():
        preview_plan = (
            intent.get("previewPlan")
            if isinstance(intent.get("previewPlan"), dict)
            else {}
        )
        rows.append(
            {
                "id": intent_id,
                "active": intent_id == ledger.get("activeIntentId"),
                "status": intent.get("status"),
                "reason": intent.get("reason"),
                "participants": deepcopy(intent.get("participants", [])),
                "createdAt": intent.get("createdAt"),
                "startTime": preview_plan.get("startTime"),
                "endTime": preview_plan.get("endTime"),
                "occurrenceKey": preview_plan.get("occurrenceKey"),
            }
        )
    rows.sort(key=lambda row: (str(row.get("createdAt") or ""), row["id"]))
    return {"activeIntentId": ledger.get("activeIntentId"), "intents": rows}
