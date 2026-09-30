"""Read back finished published events without treating elapsed time as play."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .booking_intents import load_intent_ledger, save_intent
from .credentials import PreviewCredentials
from .firebase_auth import FirebaseAuthenticator
from .preview_readonly import PreviewReadonlyClient, collection_items
from .preview_participation import ACCEPTED_STATUSES, check_in_status, find_actor_invitation, find_owner_invitation
from .identity_registry import IdentityRegistry
from .players import actor_auth_cache
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


def observe_finished_training_sessions(root: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Read one finished player-owned training booking and report its evidence.

    A passed end time is never proof of a session.  Only read-back invitations
    whose accepted players are all still not checked in become a missed session.
    """
    storage = Storage(root)
    instant = now or parse_instant(None)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        candidates = []
        for intent in ledger["intents"].values():
            booking = intent.get("booking") if isinstance(intent, dict) else None
            plan = intent.get("previewPlan") if isinstance(intent, dict) else None
            if (
                isinstance(intent, dict) and intent.get("journey") == "customer_training"
                and intent.get("status") == "booked" and isinstance(booking, dict)
                and isinstance(plan, dict) and isinstance(booking.get("eventId"), str)
                and isinstance(plan.get("endTime"), str) and parse_instant(plan["endTime"]) <= instant
            ):
                candidates.append(intent)
        state = storage.load_json(storage.state / STATE_FILE, default={})
        observed = state.setdefault("events", {}) if isinstance(state, dict) else {}
        candidates = [row for row in candidates if row["booking"]["eventId"] not in observed]
    if not candidates:
        return {"action": "pass", "detail": "no unobserved finished training session", "remoteWrites": 0}

    intent = sorted(candidates, key=lambda row: str(row["previewPlan"].get("endTime")))[0]
    owner_id = intent.get("bookerActorId")
    participants = intent.get("participants")
    event_id = intent["booking"]["eventId"]
    if not isinstance(owner_id, str) or not isinstance(participants, list) or owner_id not in participants:
        raise ValueError("finished training session is missing its named players")
    credentials = PreviewCredentials.load(root)
    policy = TargetPolicy.from_config(credentials.config)
    identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure(
        tuple(actor_id for actor_id in participants if isinstance(actor_id, str))
    )
    owner = identities[owner_id]
    owner_auth = FirebaseAuthenticator(actor_auth_cache(root, owner_id)).authenticate(
        owner.email, owner.password, credentials.firebase_api_key
    )
    client = PreviewReadonlyClient(policy, owner_auth.id_token)
    invitations = {owner_id: find_owner_invitation(client, event_id)}
    for actor_id in participants:
        if actor_id == owner_id or not isinstance(actor_id, str):
            continue
        invitation = find_actor_invitation(client, event_id, identities[actor_id].podplay_user_id)
        if invitation is not None:
            invitations[actor_id] = invitation
    accepted = {
        actor_id: invitation for actor_id, invitation in invitations.items()
        if actor_id == owner_id or invitation.get("status") in ACCEPTED_STATUSES
    }
    no_show = len(accepted) == len(participants) and all(
        check_in_status(invitation) == "NOT_CHECKED_IN" for invitation in accepted.values()
    )
    all_checked_in = len(accepted) == len(participants) and all(
        check_in_status(invitation) == "CHECKED_IN" for invitation in accepted.values()
    )
    observed_at = isoformat(instant)
    record = {
        "eventId": event_id, "intentId": intent["id"], "observedAt": observed_at,
        "startTime": intent["previewPlan"].get("startTime"), "endTime": intent["previewPlan"].get("endTime"),
        "participants": {actor_id: {"accepted": actor_id in accepted, "checkIn": check_in_status(invitation)} for actor_id, invitation in invitations.items()},
        "outcome": "missed_session" if no_show else "attendance_observed" if all_checked_in else "attendance_inconclusive",
        "played": False, "remoteWrites": 0,
    }
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        entries = state.setdefault("events", {})
        if event_id in entries:
            return {"action": "pass", "detail": "finished training session was already observed", "remoteWrites": 0}
        entries[event_id] = record
        state.update({"schemaVersion": 1, "lastObservedAt": observed_at})
        storage.write_json(storage.state / STATE_FILE, state)
        ledger = load_intent_ledger(storage)
        stored = ledger["intents"].get(intent["id"])
        if isinstance(stored, dict):
            stored["eventObservation"] = record
            stored["status"] = "missed_session" if no_show else "attendance_observed" if all_checked_in else "attendance_inconclusive"
            save_intent(storage, ledger, stored, make_active=False)
        if no_show:
            storage.append_jsonl(storage.channel_path("club"), {
                "id": f"event:{event_id}:training-missed", "channel": "club", "from": "riley",
                "to": participants, "kind": "training_missed_session", "intentId": intent["id"], "eventId": event_id,
                "text": "The W37 training window ended without either player checking in. The booking remains history; King may ask Mai to arrange a fresh session.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
    action = "training_missed_session_announced" if no_show else "training_attendance_observed" if all_checked_in else "training_attendance_inconclusive"
    return {"action": action, "eventId": event_id, "remoteWrites": 0}


def reconcile_missed_training_recoveries(root: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Turn an evidenced missed session into the ordinary agreed training path.

    The pair already agreed and accepted the missed session.  This preserves
    that social fact while requiring a fresh live slot, booking, invitation,
    and check-in.  It does not reuse the expired product event.
    """
    storage = Storage(root)
    observed_at = isoformat(now or parse_instant(None))
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        source = next((intent for intent in ledger["intents"].values() if isinstance(intent, dict)
                       and intent.get("journey") == "customer_training"
                       and intent.get("status") == "missed_session"
                       and isinstance(intent.get("eventObservation"), dict)
                       and intent["eventObservation"].get("outcome") == "missed_session"), None)
        if not isinstance(source, dict):
            return {"action": "pass", "detail": "no missed training session needs recovery", "remoteWrites": 0}
        event_id = source["eventObservation"].get("eventId")
        participants = source.get("participants")
        constraints = source.get("constraints")
        owner = source.get("bookerActorId")
        if not isinstance(event_id, str) or not isinstance(participants, list) or not isinstance(constraints, dict) or not isinstance(owner, str):
            raise ValueError("missed training session is missing its recovery details")
        recovery_id = f"intent:recovery:{event_id}"
        existing = ledger["intents"].get(recovery_id)
        if isinstance(existing, dict):
            return {"action": "pass", "detail": "missed training recovery already exists", "intentId": recovery_id, "remoteWrites": 0}
        recovery = {
            "schemaVersion": 1, "id": recovery_id, "status": "agreed",
            "reason": "missed_session_recovery", "journey": "customer_training",
            "createdAt": observed_at, "requestedBy": owner, "bookerActorId": owner,
            "participants": list(participants), "constraints": constraints,
            "evidence": {"missedEventId": event_id, "priorIntentId": source["id"]},
            "nextAction": "plan_live_w37_availability_then_player_booking", "remoteWrites": 0,
        }
        save_intent(storage, ledger, recovery)
        storage.append_jsonl(storage.channel_path("club"), {
            "id": f"{recovery_id}:ready", "channel": "club", "from": "riley", "to": list(participants),
            "kind": "training_recovery_ready", "intentId": recovery_id, "inReplyTo": f"event:{event_id}:training-missed",
            "text": "The missed session is recorded. Your prior agreement is ready for a fresh W37 slot; King can book again after the lead plans availability.",
            "observedAt": observed_at, "remoteWrites": 0,
        })
    return {"action": "training_recovery_ready", "intentId": recovery_id, "remoteWrites": 0}
