"""Read back finished published events without treating elapsed time as play."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .booking_intents import load_intent_ledger, save_intent
from .credentials import PreviewCredentials
from .firebase_auth import FirebaseAuthenticator
from .preview_readonly import PreviewReadonlyClient, collection_items
from .storage import Storage
from .target_policy import TargetPolicy
from .time import isoformat, parse_instant


STATE_FILE = "event-observations.json"


def observe_finished_events(root: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Observe each finished owner event once.  Observation never means played."""
    storage = Storage(root)
    instant = now or parse_instant(None)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        candidates = [
            intent for intent in ledger["intents"].values()
            if isinstance(intent, dict)
            and intent.get("status") in {"event_created", "players_registered"}
            and isinstance(intent.get("event"), dict)
            and isinstance(intent["event"].get("eventId"), str)
            and isinstance(intent["event"].get("endTime"), str)
            and parse_instant(intent["event"]["endTime"]) <= instant
        ]
        state = storage.load_json(storage.state / STATE_FILE, default={})
        observed = state.setdefault("events", {}) if isinstance(state, dict) else {}
        candidates = [intent for intent in candidates if intent["event"]["eventId"] not in observed]
    if not candidates:
        return {"action": "pass", "detail": "no unobserved finished event", "remoteWrites": 0}

    credentials = PreviewCredentials.load(root)
    policy = TargetPolicy.from_config(credentials.config)
    auth = FirebaseAuthenticator(root / "secrets" / "preview-auth-cache.json").authenticate(
        credentials.admin_email, credentials.admin_password, credentials.firebase_api_key
    )
    client = PreviewReadonlyClient(policy, auth.id_token)
    # Observe exactly one event per tick to keep the external read auditable.
    intent = candidates[0]
    event_id = intent["event"]["eventId"]
    event = client.get(f"/apis/v2/events/{event_id}")
    signups = collection_items(client.get(f"/apis/v2/events/{event_id}/signups"))
    active_signups = [row for row in signups if isinstance(row, dict) and row.get("isCanceled") is not True]
    if not isinstance(event, dict) or event.get("id") != event_id:
        raise ValueError("finished-event read-back did not return the requested event")
    observed_at = isoformat(instant)
    empty = len(active_signups) == 0
    record = {
        "eventId": event_id,
        "intentId": intent["id"],
        "observedAt": observed_at,
        "startTime": event.get("startTime"),
        "endTime": event.get("endTime"),
        "activeSignupCount": len(active_signups),
        "outcome": "empty" if empty else "attendance_observed",
        "played": False,
        "remoteWrites": 0,
    }
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        entries = state.setdefault("events", {})
        if event_id in entries:
            return {"action": "pass", "detail": "finished event was already observed", "remoteWrites": 0}
        entries[event_id] = record
        state.update({"schemaVersion": 1, "lastObservedAt": observed_at})
        storage.write_json(storage.state / STATE_FILE, state)
        ledger = load_intent_ledger(storage)
        stored = ledger["intents"].get(intent["id"])
        if isinstance(stored, dict):
            stored["eventObservation"] = record
            # The remote event stays published history.  This local status only
            # removes it from the club's live-invitation state.
            stored["status"] = "observed_empty" if empty else "attendance_observed"
            save_intent(storage, ledger, stored, make_active=False)
        if empty:
            storage.append_jsonl(storage.channel_path("club"), {
                "id": f"event:{event_id}:empty-observed", "channel": "club", "from": "riley",
                "to": intent.get("participants", []), "kind": "event_empty_observed",
                "text": "The hour passed and nobody came. The event remains on the calendar; ask the club for a new session if you want another chance.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
    return {"action": "empty_event_announced" if empty else "attendance_observed", "eventId": event_id, "signupCount": len(active_signups), "remoteWrites": 0}
