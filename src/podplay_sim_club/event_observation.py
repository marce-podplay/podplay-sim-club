"""Read back finished published events without treating elapsed time as play."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .booking_intents import load_intent_ledger, save_intent
from .credentials import PreviewCredentials
from .firebase_auth import FirebaseAuthenticator
from .preview_readonly import PreviewReadError, PreviewReadonlyClient, collection_items
from .preview_participation import ACCEPTED_STATUSES, check_in_status, find_actor_invitation, find_owner_invitation
from .preview_event_signup import read_captain_attendance_invitation
from .identity_registry import IdentityRegistry
from .event_lifecycle import record_observation_locked
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
            and intent.get("journey") != "owner_team_signup"
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
    """Read one finished player-owned booking and report its evidence.

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
            owner_event = intent.get("event") if isinstance(intent, dict) else None
            plan = intent.get("previewPlan") if isinstance(intent, dict) else None
            event_record = booking if isinstance(booking, dict) else owner_event
            if (
                isinstance(intent, dict)
                and (
                    (intent.get("journey") in {"customer_training", "customer_team_signup"}
                     and intent.get("status") in {"booked", "team_signup_in_progress", "team_members_registered"})
                    or (intent.get("journey") == "owner_team_signup" and intent.get("status") == "players_registered")
                )
                and isinstance(event_record, dict)
                and isinstance(plan, dict) and isinstance(event_record.get("eventId"), str)
                and isinstance(plan.get("endTime"), str) and parse_instant(plan["endTime"]) <= instant
            ):
                candidate = dict(intent)
                candidate["_observationEvent"] = event_record
                candidates.append(candidate)
        state = storage.load_json(storage.state / STATE_FILE, default={})
        observed = state.setdefault("events", {}) if isinstance(state, dict) else {}
        candidates = [row for row in candidates if row["_observationEvent"]["eventId"] not in observed]
    if not candidates:
        return {"action": "pass", "detail": "no unobserved finished training session", "remoteWrites": 0}

    intent = sorted(candidates, key=lambda row: str(row["previewPlan"].get("endTime")))[0]
    owner_id = intent.get("bookerActorId")
    participants = intent.get("participants")
    event_id = intent["_observationEvent"]["eventId"]
    if intent.get("journey") == "owner_team_signup":
        teams = intent.get("teams") if isinstance(intent.get("teams"), list) else []
        owner_id = teams[0].get("captainActorId") if teams and isinstance(teams[0], dict) else None
    if not isinstance(owner_id, str) or not isinstance(participants, list) or owner_id not in participants:
        raise ValueError("finished player-owned session is missing its named players")
    credentials = PreviewCredentials.load(root)
    policy = TargetPolicy.from_config(credentials.config)
    identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure(
        tuple(actor_id for actor_id in participants if isinstance(actor_id, str))
    )
    readback_error = None
    try:
        if intent.get("journey") == "owner_team_signup":
            signup_rows = intent.get("signups") if isinstance(intent.get("signups"), list) else []
            captain_ids = {row.get("captainActorId") for row in intent.get("teams", []) if isinstance(row, dict)}
            invitations = {}
            for actor_id in participants:
                if not isinstance(actor_id, str):
                    continue
                identity = identities[actor_id]
                auth = FirebaseAuthenticator(actor_auth_cache(root, actor_id)).authenticate(
                    identity.email, identity.password, credentials.firebase_api_key
                )
                client = PreviewReadonlyClient(policy, auth.id_token)
                if actor_id in captain_ids:
                    signup_id = next((row.get("signupId") for row in signup_rows
                                      if isinstance(row, dict) and row.get("actorId") == actor_id), None)
                    invitations[actor_id] = read_captain_attendance_invitation(client, event_id, signup_id)
                else:
                    invitation = find_actor_invitation(client, event_id, identity.podplay_user_id)
                    if invitation is not None:
                        invitations[actor_id] = invitation
        else:
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
    except PreviewReadError:
        # A product read-back gap proves neither absence nor attendance.  Retain
        # the finished event once as explicitly inconclusive so the engine does
        # not retry blindly or turn an endpoint shape into a player no-show.
        invitations = {}
        readback_error = "participant_readback_unavailable"
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
    participant_observations = {
        actor_id: (
            {"accepted": actor_id in accepted, "checkIn": check_in_status(invitations[actor_id])}
            if actor_id in invitations else {"accepted": False, "checkIn": "UNKNOWN"}
        )
        for actor_id in participants if isinstance(actor_id, str)
    }
    record = {
        "eventId": event_id, "intentId": intent["id"], "observedAt": observed_at,
        "startTime": intent["previewPlan"].get("startTime"), "endTime": intent["previewPlan"].get("endTime"),
        "participants": participant_observations,
        "outcome": "missed_session" if no_show else "attendance_observed" if all_checked_in else "attendance_inconclusive",
        "reason": readback_error,
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
                "speechAct": "inform",
                "text": "The W37 session window ended without the named players checking in. The booking remains history; an authorized captain may propose a fresh session to the required teammate or opposing captain.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
        elif readback_error:
            storage.append_jsonl(storage.channel_path("club"), {
                "id": f"event:{event_id}:attendance-inconclusive", "channel": "club", "from": "riley",
                "to": participants, "kind": "event_attendance_inconclusive", "intentId": intent["id"], "eventId": event_id,
                "speechAct": "inform",
                "text": "The event ended, but Preview could not provide participant attendance read-back. Attendance, play, and attendance-based benefits remain unrecorded.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
        occurrence = intent.get("occurrenceKey") or intent.get("booking", {}).get("occurrenceKey")
        if isinstance(occurrence, str):
            record_observation_locked(
                storage, occurrence_key=occurrence, intent_id=intent["id"],
                outcome=record["outcome"], participant_observations=record["participants"],
                occurred_at=observed_at,
                evidence_ref=f"evidence:preview:event:{event_id}:finished-readback",
            )
    action = "training_missed_session_announced" if no_show else "training_attendance_observed" if all_checked_in else "training_attendance_inconclusive"
    return {"action": action, "eventId": event_id, "remoteWrites": 0}
