"""Riley's deterministic front-desk offer handling.

Riley can acknowledge an eligible request once.  The lead owns the later
availability scan and the one existing-event reschedule write.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .booking_intents import load_intent_ledger, save_intent
from .preview_readiness import may_start_early
from .storage import Storage
from .time import isoformat, parse_instant


STATE_FILE = "front-desk.json"


def tick(root: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    storage = Storage(root)
    instant = now or parse_instant(None)
    observed_at = isoformat(instant)
    with storage.writer_lock():
        messages = storage.read_jsonl(storage.channel_path("club"))
        state = storage.load_json(storage.state / STATE_FILE, default={})
        if not isinstance(state, dict):
            state = {}
        handled = state.setdefault("handled", {})
        offers = _riley_offers(storage)
        for offer in offers:
            message_id = offer.get("messageId")
            if not isinstance(message_id, str) or message_id in handled:
                continue
            request = next((row for row in messages if row.get("id") == message_id), None)
            result = _handle_request(storage, messages, request, observed_at)
            handled[message_id] = result
            state.update({"schemaVersion": 1, "lastTickAt": observed_at})
            storage.write_json(storage.state / STATE_FILE, state)
            return result
        state.update({"schemaVersion": 1, "lastTickAt": observed_at})
        storage.write_json(storage.state / STATE_FILE, state)
    return {"actorId": "riley", "action": "pass", "detail": "no unanswered player request", "observedAt": observed_at}


def pending_reschedule(root: Path, request_id: Optional[str] = None) -> Dict[str, Any]:
    state = Storage(root).load_json(Storage(root).state / STATE_FILE, default={})
    handled = state.get("handled", {}) if isinstance(state, dict) else {}
    rows = [row for key, row in handled.items() if isinstance(row, dict) and row.get("action") == "propose_reschedule" and (request_id is None or key == request_id)]
    if len(rows) != 1:
        raise ValueError("exactly one pending Riley reschedule request is required")
    return dict(rows[0])


def mark_rescheduled(root: Path, request_id: str, plan: Dict[str, Any], now: Optional[datetime] = None) -> None:
    storage = Storage(root)
    observed_at = isoformat(now or parse_instant(None))
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        row = state.get("handled", {}).get(request_id) if isinstance(state, dict) else None
        if not isinstance(row, dict) or row.get("action") != "propose_reschedule":
            raise ValueError("Riley reschedule request is not pending")
        row.update({"action": "rescheduled", "plan": dict(plan), "completedAt": observed_at})
        storage.write_json(storage.state / STATE_FILE, state)


def _riley_offers(storage: Storage) -> list[Dict[str, Any]]:
    social = storage.load_json(storage.state / "social-engine.json", default={})
    offers = social.get("offers", []) if isinstance(social, dict) else []
    return [row for row in offers if isinstance(row, dict) and row.get("actorId") == "riley"]


def _handle_request(storage: Storage, messages: list, request: Any, observed_at: str) -> Dict[str, Any]:
    if not isinstance(request, dict):
        return {"actorId": "riley", "action": "pass", "detail": "request disappeared", "observedAt": observed_at}
    if request.get("kind") == "new_event_request":
        return _new_event_request(storage, request, observed_at)
    if request.get("kind") == "share_invitation_expired":
        return _expired_share_invitation(storage, request, observed_at)
    request_id = request.get("id")
    intent_id = request.get("intentId")
    if not isinstance(request_id, str) or not isinstance(intent_id, str):
        return _reply(storage, request, observed_at, "I need the event’s intent reference before I can check a move.", "request_missing_intent")
    ledger = load_intent_ledger(storage)
    intent = ledger["intents"].get(intent_id)
    if not isinstance(intent, dict):
        return _reply(storage, request, observed_at, "I can’t find that match in the club ledger.", "request_unknown_intent")
    event = intent.get("event") if isinstance(intent.get("event"), dict) else intent.get("booking")
    event_id = event.get("eventId") if isinstance(event, dict) else None
    if intent.get("status") not in {"event_created", "players_registered", "booked"} or not isinstance(event_id, str):
        return _reply(storage, request, observed_at, "That match is not a published event yet, so there is nothing to move.", "request_not_published")
    if not _captains_agreed(messages, intent, intent_id):
        return _reply(storage, request, observed_at, "I need the eligible player—or both doubles captains—to request the earlier time first.", "request_missing_agreement")
    return _reply(storage, request, observed_at, "I’ll ask the lead to check the nearest legal court and move this same event if it is free.", "propose_reschedule", intent_id=intent_id, event_id=event_id)


def _new_event_request(storage: Storage, request: Dict[str, Any], observed_at: str) -> Dict[str, Any]:
    request_id = request.get("id")
    requester = request.get("from")
    if not isinstance(request_id, str) or not isinstance(requester, str):
        return _reply(storage, request, observed_at, "I need to know who is asking before I can open a new-session request.", "request_missing_requester")
    intent_id = f"intent:new-event:{request_id}"
    ledger = load_intent_ledger(storage)
    if intent_id not in ledger["intents"]:
        save_intent(storage, ledger, {
            "schemaVersion": 1, "id": intent_id, "status": "requested",
            "reason": "community_new_event_request", "journey": "owner_open_play",
            "createdAt": observed_at, "requestedBy": requester, "participants": [requester],
            "constraints": {"durationMinutes": 60, "daysAhead": [0, 14], "localWindows": ["00:00-24:00"], "slotPolicy": "nearest_real_contiguous_slot", "freeToParticipants": True},
            "evidence": {"clubRequestId": request_id}, "remoteWrites": 0,
        })
    return _reply(storage, request, observed_at, "I logged the new-session request. The lead may plan a court, but nothing will publish until the owner promotes it.", "new_event_requested", intent_id=intent_id)


def _expired_share_invitation(storage: Storage, request: Dict[str, Any], observed_at: str) -> Dict[str, Any]:
    """Turn a missed booking window into one fresh, non-published request."""
    request_id = request.get("id")
    requester = request.get("from")
    participants = request.get("participants")
    duration = request.get("durationMinutes")
    if (
        not isinstance(request_id, str)
        or not isinstance(requester, str)
        or not isinstance(participants, list)
        or not all(isinstance(actor_id, str) for actor_id in participants)
        or not isinstance(duration, int)
        or duration < 30
        or duration % 30
    ):
        return _reply(storage, request, observed_at, "I need the named players and duration before I can reopen this session.", "request_missing_session_details")
    intent_id = f"intent:rebooking:{request_id}"
    ledger = load_intent_ledger(storage)
    if intent_id not in ledger["intents"]:
        save_intent(storage, ledger, {
            "schemaVersion": 1, "id": intent_id, "status": "requested",
            "reason": "expired_unsent_share_invitation", "journey": "customer_training",
            "createdAt": observed_at, "requestedBy": requester, "bookerActorId": requester, "participants": list(participants),
            "constraints": {"durationMinutes": duration, "daysAhead": [0, 14], "localWindows": ["00:00-24:00"], "slotPolicy": "nearest_valid_low_contention"},
            "evidence": {"expiredShareInvitationRequestId": request_id}, "remoteWrites": 0,
        })
    return _reply(storage, request, observed_at, "I logged a fresh 30-minute Women Fighters request. The old booking stays in history; the lead must find a new free court before King can book and share a new invitation.", "rebooking_requested", intentId=intent_id)


def _captains_agreed(messages: list, intent: Dict[str, Any], intent_id: str) -> bool:
    senders = {row.get("from") for row in messages if isinstance(row, dict) and row.get("kind") == "reschedule_request" and row.get("intentId") == intent_id}
    if not may_start_early(intent):
        return False
    if intent.get("journey") in {"customer_team_signup", "doubles_challenge"} or intent.get("reason") == "team_rivalry_doubles":
        captains = {side.get("captainActorId") or side.get("captainId") for side in (intent.get("challenger"), intent.get("opponent")) if isinstance(side, dict)}
        return len(captains) == 2 and captains <= senders
    return bool(senders.intersection(set(intent.get("participants", []))))


def _reply(storage: Storage, request: Dict[str, Any], observed_at: str, text: str, action: str, **extra: Any) -> Dict[str, Any]:
    request_id = request["id"]
    row = {"id": f"{request_id}:riley", "channel": "club", "from": "riley", "to": [request.get("from")], "kind": "staff_reply", "inReplyTo": request_id, "text": text, "observedAt": observed_at}
    storage.append_jsonl(storage.channel_path("club"), row)
    return {"actorId": "riley", "action": action, "requestId": request_id, "detail": text, "observedAt": observed_at, **extra}
