"""Reconcile a visibly confirmed player booking into the social ledger.

This is intentionally narrower than a booking adapter: it accepts only facts a
player could read on the rendered confirmation screen and stores no product
identifier, URL, payment data, or attendance claim.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .booking_intents import load_intent_ledger, save_intent
from .event_lifecycle import record_check_in_locked
from .facts import append_fact_locked, make_fact
from .storage import Storage
from .time import isoformat, parse_instant


class BrowserBookingObservationError(ValueError):
    """The supplied visible booking observation cannot reconcile an intent."""


def reconcile(
    root: Path, *, intent_id: str, owner_actor_id: str, venue_label: str,
    start_time: str, end_time: str, group_size: int,
    observed_at: datetime | None = None,
) -> Mapping[str, Any]:
    """Write the browser-confirmed booking fact, local intent, and diary."""
    start = parse_instant(start_time)
    end = parse_instant(end_time)
    if end <= start:
        raise BrowserBookingObservationError("visible booking window is invalid")
    if not venue_label or len(venue_label) > 120 or group_size < 1 or group_size > 20:
        raise BrowserBookingObservationError("visible booking details are invalid")
    when = isoformat(observed_at or parse_instant(None))
    storage = Storage(root)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        intent = ledger["intents"].get(intent_id)
        if not isinstance(intent, dict):
            raise BrowserBookingObservationError("booking intent is unavailable")
        if intent.get("bookerActorId") != owner_actor_id:
            raise BrowserBookingObservationError("only the named booking owner may report this browser observation")
        participants = intent.get("participants")
        if not isinstance(participants, list) or owner_actor_id not in participants:
            raise BrowserBookingObservationError("booking intent has no valid named owner")
        if group_size < len(participants):
            raise BrowserBookingObservationError("visible group size is smaller than the agreed named roster")

        occurrence = f"browser-observed:{intent_id}"
        plan = {
            "occurrenceKey": occurrence, "venueLabel": venue_label,
            "startTime": isoformat(start), "endTime": isoformat(end),
            "durationMinutes": int((end - start).total_seconds() // 60),
            "groupSize": group_size, "source": "visible_browser",
        }
        booking = {
            "status": "observed_booked", "occurrenceKey": occurrence,
            "venueLabel": venue_label, "startTime": plan["startTime"], "endTime": plan["endTime"],
            "groupSize": group_size, "ownerConfirmed": True, "source": "visible_browser",
            "observedAt": when,
        }
        intent.update({
            "status": "booked_browser_observed",
            "nextAction": "owner_may_capture_and_deliver_player_invitation",
            "previewPlan": plan,
            "booking": booking,
            "browserBookingObservation": {
                "status": "confirmed", "observedAt": when,
                "doesNotMean": ["participant_invitation", "participant_acceptance", "check_in", "attendance", "play", "result"],
            },
        })
        save_intent(storage, ledger, intent, make_active=False)

        fact = make_fact(
            fact_type="event.booked",
            natural_key=f"{intent_id}\0{plan['startTime']}\0visible-browser",
            subject_ref=occurrence, correlation_id=f"journey:{intent_id}",
            source_kind="visible_browser", actor_id=owner_actor_id,
            payload={
                "kind": "customer_training", "socialIntentRef": intent_id,
                "plannedWindow": {"startTime": plan["startTime"], "endTime": plan["endTime"]},
                "venueLabel": venue_label, "groupSize": group_size,
                "bookingState": "owner_confirmed_visible_browser",
                "doesNotMean": ["participant_invitation", "participant_acceptance", "check_in", "attendance", "play", "result"],
            },
            occurred_at=when, evidence_ref=f"browser-observation:{intent_id}:booking-confirmed",
        )
        append_fact_locked(storage, fact)
        diary_id = f"{intent_id}:visible-booking-confirmed"
        prior = {row.get("id") for row in storage.read_jsonl(storage.actor_journal_path(owner_actor_id)) if isinstance(row, dict)}
        if diary_id not in prior:
            storage.append_jsonl(storage.actor_journal_path(owner_actor_id), {
                "id": diary_id, "actorId": owner_actor_id, "intentId": intent_id,
                "step": "visible_booking_confirmed", "observedAt": when,
                "detail": "I saw the training booking confirmation in the player browser. The named players still need their own invitation and decision.",
            })
        message_id = f"{intent_id}:booking-observed"
        prior_messages = {row.get("id") for row in storage.read_jsonl(storage.channel_path("club")) if isinstance(row, dict)}
        if message_id not in prior_messages:
            storage.append_jsonl(storage.channel_path("club"), {
                "id": message_id, "channel": "club", "kind": "browser_booking_observed",
                "speechAct": "inform", "from": owner_actor_id,
                "to": [actor for actor in participants if actor != owner_actor_id],
                "intentId": intent_id, "observedAt": when, "remoteWrites": 0,
                "text": "I saw our training booking confirmed in the player browser. I will now prepare the player invitation; nobody else is confirmed yet.",
            })
    return {
        "intentId": intent_id, "ownerActorId": owner_actor_id,
        "status": "booked_browser_observed", "observedAt": when, "remoteWrites": 0,
    }


def reconcile_invitation_acceptance(
    root: Path, *, intent_id: str, recipient_actor_id: str,
    observed_at: datetime | None = None,
) -> Mapping[str, Any]:
    """Record one recipient's visibly confirmed invitation acceptance locally.

    The caller supplies no product ID, URL, payment detail, check-in, or
    attendance claim.  This makes the visible acceptance available to the
    arrival workflow while preserving the distinction from later check-in.
    """
    when = isoformat(observed_at or parse_instant(None))
    storage = Storage(root)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        intent = ledger["intents"].get(intent_id)
        if not isinstance(intent, dict) or intent.get("status") != "booked_browser_observed":
            raise BrowserBookingObservationError("browser-observed booking is unavailable")
        owner = intent.get("bookerActorId")
        participants = intent.get("participants")
        if (
            not isinstance(owner, str)
            or not isinstance(participants, list)
            or recipient_actor_id == owner
            or recipient_actor_id not in participants
        ):
            raise BrowserBookingObservationError("recipient is not a named non-owner participant")
        existing = intent.get("shareRedemptions") if isinstance(intent.get("shareRedemptions"), list) else []
        by_actor = {row.get("actorId"): row for row in existing if isinstance(row, dict) and isinstance(row.get("actorId"), str)}
        by_actor[recipient_actor_id] = {
            "actorId": recipient_actor_id,
            "status": "ACCEPTED",
            "source": "visible_browser",
            "observedAt": when,
        }
        intent["shareRedemptions"] = [by_actor[actor] for actor in participants if isinstance(actor, str) and actor in by_actor]
        intent["nextAction"] = "await_arrival_window_for_named_player_check_in"
        save_intent(storage, ledger, intent, make_active=False)
        booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
        occurrence = booking.get("occurrenceKey") if isinstance(booking.get("occurrenceKey"), str) else f"browser-observed:{intent_id}"
        append_fact_locked(storage, make_fact(
            fact_type="event.invitation_accepted",
            natural_key=f"{intent_id}\0{recipient_actor_id}\0visible-browser",
            subject_ref=occurrence, correlation_id=f"journey:{intent_id}",
            source_kind="visible_browser", actor_id=recipient_actor_id,
            payload={
                "socialIntentRef": intent_id,
                "acceptanceState": "recipient_confirmed_visible_browser",
                "doesNotMean": ["check_in", "attendance", "play", "result"],
            }, occurred_at=when, evidence_ref=f"browser-observation:{intent_id}:invitation-accepted",
        ))
        message_id = f"{intent_id}:browser-invitation-accepted:{recipient_actor_id}"
        if not any(row.get("id") == message_id for row in storage.read_jsonl(storage.channel_path("club")) if isinstance(row, dict)):
            storage.append_jsonl(storage.channel_path("club"), {
                "id": message_id, "channel": "club", "kind": "browser_invitation_accepted",
                "speechAct": "inform", "from": recipient_actor_id, "to": [owner], "intentId": intent_id,
                "observedAt": when, "remoteWrites": 0,
                "text": "I saw and accepted the training invitation in my player browser. This is not a check-in or attendance claim.",
            })
    return {"intentId": intent_id, "recipientActorId": recipient_actor_id, "status": "invitation_accepted_browser_observed", "observedAt": when, "remoteWrites": 0}


def reconcile_staff_check_in(
    root: Path, *, intent_id: str, participant_actor_id: str,
    observed_at: datetime | None = None,
) -> Mapping[str, Any]:
    """Persist one explicitly rendered staff-confirmed arrival.

    This is deliberately narrower than a staff adapter: callers may record
    only the named participant and a visible confirmation. It never stores a
    QR value, product identifier, URL, or claim of play/result.
    """
    when = isoformat(observed_at or parse_instant(None))
    storage = Storage(root)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        intent = ledger["intents"].get(intent_id)
        if not isinstance(intent, dict) or intent.get("status") != "booked_browser_observed":
            raise BrowserBookingObservationError("browser-observed booking is unavailable")
        participants = intent.get("participants")
        if not isinstance(participants, list) or participant_actor_id not in participants:
            raise BrowserBookingObservationError("participant is not named on this browser-observed booking")
        booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
        occurrence = booking.get("occurrenceKey") if isinstance(booking.get("occurrenceKey"), str) else f"browser-observed:{intent_id}"
        check_ins = intent.get("browserCheckIns") if isinstance(intent.get("browserCheckIns"), dict) else {}
        check_ins[participant_actor_id] = {
            "status": "CHECKED_IN", "source": "visible_staff_browser", "observedAt": when,
            "doesNotMean": ["play", "result"],
        }
        intent["browserCheckIns"] = check_ins
        named = [actor for actor in participants if isinstance(actor, str)]
        intent["nextAction"] = (
            "await_independent_live_play_evidence"
            if named and all(check_ins.get(actor, {}).get("status") == "CHECKED_IN" for actor in named)
            else "await_remaining_named_player_check_in"
        )
        save_intent(storage, ledger, intent, make_active=False)
        record_check_in_locked(
            storage, occurrence_key=occurrence, intent_id=intent_id,
            actor_id=participant_actor_id, status="CHECKED_IN", occurred_at=when,
            evidence_ref=f"browser-observation:{intent_id}:staff-check-in",
        )
        diary_id = f"{intent_id}:visible-staff-check-in:{participant_actor_id}"
        if not any(row.get("id") == diary_id for row in storage.read_jsonl(storage.actor_journal_path(participant_actor_id)) if isinstance(row, dict)):
            storage.append_jsonl(storage.actor_journal_path(participant_actor_id), {
                "id": diary_id, "actorId": participant_actor_id, "intentId": intent_id,
                "step": "visible_staff_check_in_confirmed", "observedAt": when,
                "detail": "The rendered staff check-in screen confirmed my arrival. This is not evidence of play or a result.",
            })
        message_id = f"{intent_id}:visible-staff-check-in:{participant_actor_id}"
        if not any(row.get("id") == message_id for row in storage.read_jsonl(storage.channel_path("staff-planning")) if isinstance(row, dict)):
            storage.append_jsonl(storage.channel_path("staff-planning"), {
                "id": message_id, "channel": "staff-planning", "kind": "browser_staff_check_in_confirmed",
                "from": "riley", "to": [participant_actor_id], "intentId": intent_id,
                "observedAt": when, "remoteWrites": 0,
                "text": "The rendered front-desk check-in screen confirmed this named player's arrival. It is not evidence of play or a result.",
            })
    return {"intentId": intent_id, "participantActorId": participant_actor_id, "status": "checked_in_browser_observed", "observedAt": when, "remoteWrites": 0}
