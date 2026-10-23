"""Player-owned training check-in choices and guarded Preview bridge."""

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .booking_intents import load_intent_ledger, save_intent
from .preview_participation import check_in_window
from .storage import Storage
from .time import isoformat, parse_instant


# Both journeys produce a player-owned booking and named player invitations.
# Check-in authority belongs to that shared contract, not to the social reason
# (practice versus a team challenge) which originally created the booking.
PLAYER_OWNED_CHECK_IN_JOURNEYS = {"customer_training", "customer_team_signup"}
CHECK_IN_ELIGIBLE_BOOKING_STATUSES = {"booked", "booked_browser_observed", "team_members_registered"}


def record_choice(root: Path, *, intent_id: str, actor_id: str, now: datetime) -> Dict[str, Any]:
    """Persist one participant's arrival choice; this performs no product I/O."""
    storage = Storage(root)
    observed_at = isoformat(now)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        intent = ledger["intents"].get(intent_id)
        if not isinstance(intent, dict) or intent.get("journey") not in PLAYER_OWNED_CHECK_IN_JOURNEYS:
            raise ValueError("check-in choice requires a booked player-owned intent")
        participants = intent.get("participants") if isinstance(intent.get("participants"), list) else []
        if intent.get("status") not in CHECK_IN_ELIGIBLE_BOOKING_STATUSES or actor_id not in participants:
            raise ValueError("actor is not a named booked training participant")
        choices = intent.get("checkInChoices") if isinstance(intent.get("checkInChoices"), dict) else {}
        choices[actor_id] = {"status": "accepted", "requestedAt": observed_at}
        intent["checkInChoices"] = choices
        intent["nextAction"] = "lead_may_record_named_player_check_in"
        save_intent(storage, ledger, intent, make_active=False)
    return {"intentId": intent_id, "actorId": actor_id, "action": "check_in", "observedAt": observed_at}


def offers(root: Path, *, now: datetime, people: Dict[str, Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Return individual arrival offers inside the verified local time window."""
    storage = Storage(root)
    ledger = load_intent_ledger(storage)
    messages = storage.read_jsonl(storage.channel_path("club"))
    already_checked_in = {
        (row.get("intentId"), row.get("occurrenceKey"), row.get("from"))
        for row in messages
        if isinstance(row, dict) and row.get("kind") == "training_check_in_recorded"
    }
    result = []
    for intent_id, intent in ledger["intents"].items():
        if not isinstance(intent, dict) or intent.get("journey") not in PLAYER_OWNED_CHECK_IN_JOURNEYS or intent.get("status") not in CHECK_IN_ELIGIBLE_BOOKING_STATUSES:
            continue
        plan = intent.get("previewPlan") if isinstance(intent.get("previewPlan"), dict) else {}
        booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
        occurrence = booking.get("occurrenceKey")
        start, end = plan.get("startTime"), plan.get("endTime")
        if not isinstance(start, str) or not isinstance(end, str):
            continue
        eligible, _detail = check_in_window(parse_instant(start), parse_instant(end), now)
        if not eligible:
            continue
        owner = intent.get("bookerActorId")
        participants = [actor for actor in intent.get("participants", []) if isinstance(actor, str)]
        accepted = {
            row.get("actorId") for row in intent.get("shareRedemptions", [])
            if isinstance(row, dict) and row.get("status") == "ACCEPTED"
        }
        # The owner is already the reservation holder. Every other person must
        # be product-accepted before anyone can claim this is a shared session.
        if not isinstance(owner, str) or any(actor != owner and actor not in accepted for actor in participants):
            continue
        for actor_id in participants:
            if actor_id in people and (intent_id, occurrence, actor_id) not in already_checked_in and (None, occurrence, actor_id) not in already_checked_in:
                result.append({
                    "actorId": actor_id, "kind": "check_in_training", "intentId": intent_id,
                    "reason": "accepted_training_player_is_inside_verified_arrival_window",
                })
    return result


def next_task(root: Path, *, now: datetime) -> Dict[str, Any]:
    """Return one current player-authorized check-in task, if any."""
    storage = Storage(root)
    ledger = load_intent_ledger(storage)
    checked_in = {
        (row.get("intentId"), row.get("occurrenceKey"), row.get("from"))
        for row in storage.read_jsonl(storage.channel_path("club"))
        if isinstance(row, dict) and row.get("kind") == "training_check_in_recorded"
    }
    for intent_id, intent in ledger["intents"].items():
        if not isinstance(intent, dict) or intent.get("journey") not in PLAYER_OWNED_CHECK_IN_JOURNEYS or intent.get("status") not in CHECK_IN_ELIGIBLE_BOOKING_STATUSES:
            continue
        booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
        occurrence = booking.get("occurrenceKey")
        choices = intent.get("checkInChoices") if isinstance(intent.get("checkInChoices"), dict) else {}
        for actor_id, choice in choices.items():
            if (
                isinstance(actor_id, str) and (intent_id, occurrence, actor_id) not in checked_in and (None, occurrence, actor_id) not in checked_in
                and isinstance(choice, dict) and choice.get("status") == "accepted"
            ):
                return {"action": "check_in_training", "intentId": intent_id, "actorId": actor_id}
    return {"action": "pass", "detail": "no player-authorized check-in awaits the lead"}


def advance(
    root: Path, task: Dict[str, Any], *, now: datetime,
    apply_preview: bool = False, confirmation: Optional[str] = None,
    check_in: Optional[Callable[[Path, bool, Optional[str], str, str], int]] = None,
) -> Dict[str, Any]:
    """Perform one named check-in only on an explicit apply tick."""
    if task.get("action") != "check_in_training":
        return {"action": "pass", "detail": "no player-authorized check-in awaits the lead", "remoteWrites": 0}
    intent_id, actor_id = task.get("intentId"), task.get("actorId")
    if not isinstance(intent_id, str) or not isinstance(actor_id, str):
        return {"action": "pass", "detail": "check-in task is invalid", "remoteWrites": 0}
    if not apply_preview:
        return _record(root, now, intent_id, actor_id, "awaiting_preview_apply", "player arrival choice waits for an explicit apply tick")
    if not isinstance(confirmation, str) or not confirmation.strip():
        return _record(root, now, intent_id, actor_id, "apply_refused", "check-in apply requires the exact Preview origin")
    if check_in is None:
        from .cli import check_in_training_player
        check_in = check_in_training_player
    exit_code = check_in(root, True, confirmation, intent_id, actor_id)
    # The command's exit code reports completion, not a write count. A player
    # may already be checked in, so report no inferred write here.
    return _record(root, now, intent_id, actor_id, "read_back_complete" if exit_code == 0 else "apply_failed", "named player check-in adapter completed and persisted its read-back" if exit_code == 0 else "named player check-in adapter did not complete", remote_writes=0)


def _record(root: Path, now: datetime, intent_id: str, actor_id: str, action: str, detail: str, *, remote_writes: int = 0) -> Dict[str, Any]:
    storage = Storage(root)
    row = {"observedAt": isoformat(now), "intentId": intent_id, "actorId": actor_id, "action": action, "detail": detail, "remoteWrites": remote_writes}
    with storage.writer_lock():
        state = storage.load_json(storage.state / "check-in-bridge.json", default={})
        attempts = state.get("attempts", []) if isinstance(state, dict) else []
        storage.write_json(storage.state / "check-in-bridge.json", {"schemaVersion": 1, "lastAttempt": row, "attempts": (attempts + [row])[-100:]})
    return {"actorId": "lead", **row}
