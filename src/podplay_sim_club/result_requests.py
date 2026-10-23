"""Sofia's bounded, no-score result-request capability."""

from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Optional

from .booking_intents import load_intent_ledger, save_intent
from .players import canonical_actor_id
from .protocol import append_protocol_facts_locked, conversation_id_for, make_message, reply_causation
from .event_lifecycle import ensure_planned_locked, record_result_locked, record_result_requested_locked
from .storage import Storage
from .time import isoformat, parse_instant


def request_finished_event_result(root: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Ask one accountable player for a winner after verified attendance.

    This is deliberately only a request.  Check-in evidence permits asking;
    it does not prove play, select a winner, or record one.  A future rally or
    point feed can replace the human report behind this same boundary.
    """
    storage = Storage(root)
    observed_at = isoformat(now or parse_instant(None))
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        # A previous broad prototype could ask from an owner/tournament event
        # whose attendee evidence was only a signup count.  Withdraw it rather
        # than leaving a misleading request in the club's durable history.
        for prior in ledger["intents"].values():
            request = prior.get("resultRequest") if isinstance(prior, dict) else None
            if (
                isinstance(request, dict)
                and request.get("status") == "requested"
                and request.get("evidence") == "verified_check_in_only"
                and prior.get("journey") not in {"customer_training", "customer_team_signup"}
            ):
                request["status"] = "withdrawn"
                request["withdrawnAt"] = observed_at
                request["withdrawalReason"] = "named_check_in_evidence_not_available"
                save_intent(storage, ledger, prior, make_active=False)
                storage.append_jsonl(storage.channel_path("club"), {
                    "id": f"event:{request.get('eventId')}:result-request-withdrawn",
                    "channel": "club", "from": "sofia", "to": [request.get("reporterActorId")],
                    "kind": "event_result_request_withdrawn",
                    "intentId": prior["id"], "eventId": request.get("eventId"),
                    "text": "Sofia withdrew this generic result request: named check-in evidence was not available. The event remains unreported.",
                    "observedAt": observed_at, "remoteWrites": 0,
                })
        candidates = [
            intent for intent in ledger["intents"].values()
            if isinstance(intent, dict)
            # Player-owned bookings have read-back attendance per named
            # participant. Tournament and owner Open Play use their own
            # protocols until their participant/check-in adapters exist.
            and intent.get("journey") in {"customer_training", "customer_team_signup"}
            and intent.get("status") == "attendance_observed"
            and isinstance(intent.get("eventObservation"), dict)
            and intent["eventObservation"].get("outcome") == "attendance_observed"
            and isinstance(intent.get("booking"), dict)
            and intent["booking"].get("eventId") == intent["eventObservation"].get("eventId")
            and not isinstance(intent.get("resultRequest"), dict)
        ]
        # A Club Arcade companion is a separate fictional evidence source.  It
        # may request a captain's confirmation after its scheduled session,
        # even when PodPlay has no attendance evidence.  It never changes the
        # product attendance observation.
        replay_candidates = [
            intent for intent in ledger["intents"].values()
            if isinstance(intent, dict)
            and intent.get("journey") in {"customer_training", "customer_team_signup"}
            and isinstance(intent.get("arcadeReplay"), dict)
            and intent["arcadeReplay"].get("status") == "ready"
            and not isinstance(intent.get("resultRequest"), dict)
            and _session_has_ended(intent, observed_at)
        ]
        candidates.extend(replay_candidates)
        if not candidates:
            return {"action": "pass", "detail": "no attended event awaits a result request", "remoteWrites": 0}
        # A replay companion may deliberately exist without a product
        # attendance observation, so use its booked/planned end time too.
        intent = sorted(candidates, key=_result_candidate_end_time)[0]
        participants = [actor for actor in intent.get("participants", []) if isinstance(actor, str)]
        reporter = _reporter(intent, participants)
        if reporter is None:
            return {"action": "pass", "detail": "attended event has no accountable reporter", "remoteWrites": 0}
        observation = intent.get("eventObservation") if isinstance(intent.get("eventObservation"), dict) else {}
        booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
        replay = intent.get("arcadeReplay") if isinstance(intent.get("arcadeReplay"), dict) else None
        plan = intent.get("previewPlan") if isinstance(intent.get("previewPlan"), dict) else {}
        occurrence = intent.get("occurrenceKey") or booking.get("occurrenceKey") or plan.get("occurrenceKey")
        event_id = observation.get("eventId") or booking.get("eventId")
        if not isinstance(event_id, str):
            if replay is None or not isinstance(occurrence, str):
                return {"action": "pass", "detail": "attended event is missing its event identifier", "remoteWrites": 0}
            event_id = _local_arcade_event_ref(occurrence)
        evidence = "arcade_replay" if replay is not None else "verified_check_in_only"
        request = {
            "eventId": event_id,
            "reporterActorId": reporter,
            "requestedAt": observed_at,
            "status": "requested",
            "evidence": evidence,
            "result": None,
        }
        intent["resultRequest"] = request
        save_intent(storage, ledger, intent, make_active=False)
        message_id = f"event:{event_id}:result-requested"
        conversation_id = conversation_id_for(f"event-result:{event_id}")
        message = {
            "id": message_id,
            "conversationId": conversation_id, "speechAct": "request",
            "channel": "club",
            "from": "sofia",
            "to": [reporter],
            "kind": "event_result_requested",
            "intentId": intent["id"],
            "eventId": event_id,
            "text": (
                f"Sofia asks {_label(reporter)} to confirm the Club Arcade replay winner. "
                "The replay is the fictional result source; no score report is required."
                if evidence == "arcade_replay" else
                f"Sofia asks {_label(reporter)} for the result. Reply with the winning player or team only; no score is required."
            ),
            "payload": {"reporterActorId": reporter, "evidence": evidence, "result": "UNREPORTED"},
            "observedAt": observed_at,
            "remoteWrites": 0,
        }
        storage.append_jsonl(storage.channel_path("club"), message)
        append_protocol_facts_locked(storage, [make_message(
            message_id=message_id, conversation_id=conversation_id,
            correlation_id=f"journey:{intent['id']}", sender_actor_id="sofia",
            recipient_actor_ids=[reporter], speech_act="request",
            content_code="event.result.requested", occurred_at=observed_at,
            parameters={"intentRef": intent["id"], "eventRef": event_id, "evidenceKind": evidence},
        )])
        if isinstance(occurrence, str):
            intent["occurrenceKey"] = occurrence
            ensure_planned_locked(storage, intent, observed_at, {
                "startTime": booking.get("startTime") or plan.get("startTime"),
                "endTime": booking.get("endTime") or plan.get("endTime"),
                "podRef": plan.get("podId"),
            })
            save_intent(storage, ledger, intent, make_active=False)
            record_result_requested_locked(
                storage, occurrence_key=occurrence, intent_id=intent["id"],
                reporter_actor_id=reporter, occurred_at=observed_at,
            )
    return {"action": "event_result_requested", "intentId": intent["id"], "eventId": event_id, "reporter": reporter, "remoteWrites": 0}


def report_finished_event_result(
    root: Path,
    intent_id: str,
    reporter_actor_id: str,
    winner: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Persist one captain's no-score result attestation for a requested event.

    This is the common completion boundary for player-owned sessions.  The
    reported winner is an attestation, not a product result feed and not an
    inference from check-ins.  It deliberately supports player or team labels
    that are evidenced by the intent's named participants.
    """
    if not isinstance(intent_id, str) or not intent_id:
        raise ValueError("an event result needs its durable intent id")
    if not isinstance(winner, str) or not winner.strip():
        raise ValueError("winner must be a named player or team")
    storage = Storage(root)
    observed_at = isoformat(now or parse_instant(None))
    reporter_actor_id = canonical_actor_id(reporter_actor_id)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        intent = ledger["intents"].get(intent_id)
        request = intent.get("resultRequest") if isinstance(intent, dict) else None
        if not isinstance(intent, dict) or not isinstance(request, dict):
            raise ValueError("event has no pending result request")
        if request.get("status") == "reported":
            return dict(request)
        if request.get("status") != "requested":
            raise ValueError("event result request is not active")
        if reporter_actor_id != canonical_actor_id(str(request.get("reporterActorId", ""))):
            raise ValueError("only the requested booking captain may report this result")
        replay = intent.get("arcadeReplay") if isinstance(intent.get("arcadeReplay"), dict) else None
        replay_expected = _replay_winner_label(intent, replay) if request.get("evidence") == "arcade_replay" else None
        permitted = _permitted_winners(intent)
        normalized = winner.strip().casefold()
        matched = next((value for value in permitted if value.casefold() == normalized), None)
        # The UI transports a safe actor id from the replay record.  Normalize
        # it to the human label that is persisted in the club journal.
        if matched is None and replay_expected is not None and isinstance(replay, dict):
            winner_ids = replay.get("winner")
            if isinstance(winner_ids, list) and normalized in {str(value).casefold() for value in winner_ids}:
                matched = replay_expected
        if matched is None:
            raise ValueError("winner must name an event participant or their roster team")
        if request.get("evidence") == "arcade_replay":
            if matched != replay_expected:
                raise ValueError("winner must match the attached Club Arcade replay")
            evidence = "arcade_replay_captain_confirmation"
        else:
            evidence = "captain_attestation"
        result = {
            "eventId": request["eventId"], "reporterActorId": reporter_actor_id,
            "reportedAt": observed_at, "status": "reported", "evidence": evidence,
            "winner": matched, "score": None,
        }
        intent["resultRequest"] = result
        intent["result"] = result
        observation = intent.get("eventObservation")
        if isinstance(observation, dict):
            # Replay confirmation is fictional club evidence.  It must not
            # overwrite the product-derived attendance observation.
            if evidence == "captain_attestation":
                observation["played"] = True
                observation["resultEvidence"] = evidence
            else:
                observation["fictionalPlayed"] = True
                observation["fictionalResultEvidence"] = evidence
        save_intent(storage, ledger, intent, make_active=False)
        message_id = f"event:{result['eventId']}:result-reported"
        request_message_id = f"event:{result['eventId']}:result-requested"
        conversation_id = conversation_id_for(f"event-result:{result['eventId']}")
        message = {
            "id": message_id, "channel": "club",
            "conversationId": conversation_id, "speechAct": "report",
            "from": reporter_actor_id, "to": ["sofia", "riley"],
            "kind": "event_result_reported", "intentId": intent_id,
            "eventId": result["eventId"],
            "text": (
                f"{_label(reporter_actor_id)} confirms the Club Arcade replay: {matched} won. No score recorded."
                if evidence == "arcade_replay_captain_confirmation" else
                f"{_label(reporter_actor_id)} reports that {matched} won. No score recorded."
            ),
            "payload": {"winner": matched, "evidence": evidence, "score": None},
            "observedAt": observed_at, "remoteWrites": 0,
        }
        storage.append_jsonl(storage.channel_path("club"), message)
        append_protocol_facts_locked(storage, [make_message(
            message_id=message_id, conversation_id=conversation_id,
            correlation_id=f"journey:{intent_id}", sender_actor_id=reporter_actor_id,
            recipient_actor_ids=["sofia", "riley"], speech_act="report",
            content_code="event.result.reported", occurred_at=observed_at,
            in_reply_to=request_message_id, grounding="understood",
            causation_id=reply_causation(request_message_id),
            parameters={"intentRef": intent_id, "winnerLabel": matched, "evidenceKind": evidence},
        )])
        booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
        plan = intent.get("previewPlan") if isinstance(intent.get("previewPlan"), dict) else {}
        occurrence = intent.get("occurrenceKey") or booking.get("occurrenceKey") or plan.get("occurrenceKey")
        if isinstance(occurrence, str):
            record_result_locked(
                storage, occurrence_key=occurrence, intent_id=intent_id,
                reporter_actor_id=reporter_actor_id, winner=matched,
                evidence_kind=evidence, occurred_at=observed_at,
                evidence_ref=f"evidence:club:event:{result['eventId']}:result-report",
            )
    # Only the completed-result boundary may affect social recovery and team
    # dynamics.  A booking, invitation, or check-in never does.  The helper is
    # idempotent by occurrence, so an already-reported result cannot amplify
    # cohesion or competitive appetite on a later tick.
    if evidence in {"captain_attestation", "arcade_replay_captain_confirmation"}:
        from .social_engine import record_completed_event
        team_id = intent.get("trainingTeamId") if isinstance(intent.get("trainingTeamId"), str) else None
        record_completed_event(
            root,
            [actor for actor in intent.get("participants", []) if isinstance(actor, str)],
            now=parse_instant(observed_at), team_id=team_id, occurrence_key=occurrence if isinstance(occurrence, str) else result["eventId"],
        )
    return result


def _reporter(intent: Dict[str, Any], participants: list[str]) -> Optional[str]:
    # Player-owned sessions are answered by their booker.  A later team/doubles
    # adapter can set ``captainActorIds`` and has a stable designated captain.
    captains = intent.get("captainActorIds")
    if isinstance(captains, list):
        for actor_id in captains:
            if isinstance(actor_id, str) and actor_id in participants:
                return actor_id
    booker = intent.get("bookerActorId")
    return booker if isinstance(booker, str) and booker in participants else (participants[0] if participants else None)


def _label(actor_id: str) -> str:
    labels = {"king-captain": "King", "mai": "Mai Shiranui", "andy-bogard": "Andy Bogard", "terry-bogard": "Terry Bogard", "kyo-captain": "Kyo Kusanagi", "benimaru": "Benimaru Nikaido"}
    return labels.get(canonical_actor_id(actor_id), actor_id)


def _permitted_winners(intent: Dict[str, Any]) -> list[str]:
    participants = [actor for actor in intent.get("participants", []) if isinstance(actor, str)]
    names = [_label(actor) for actor in participants]
    team_id = intent.get("trainingTeamId")
    if isinstance(team_id, str) and team_id:
        names.append(" ".join(part.title() for part in team_id.split("-")))
    for side in (intent.get("challenger"), intent.get("opponent")):
        if isinstance(side, dict) and isinstance(side.get("teamName"), str):
            names.append(side["teamName"])
    # Stable de-duplication preserves the explicit player names for UI choices.
    return list(dict.fromkeys(names))


def _session_has_ended(intent: Dict[str, Any], observed_at: str) -> bool:
    booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
    plan = intent.get("previewPlan") if isinstance(intent.get("previewPlan"), dict) else {}
    end_time = booking.get("endTime") or plan.get("endTime")
    if not isinstance(end_time, str):
        return False
    try:
        return parse_instant(end_time) <= parse_instant(observed_at)
    except ValueError:
        return False


def _result_candidate_end_time(intent: Dict[str, Any]) -> str:
    observation = intent.get("eventObservation") if isinstance(intent.get("eventObservation"), dict) else {}
    booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
    plan = intent.get("previewPlan") if isinstance(intent.get("previewPlan"), dict) else {}
    return str(observation.get("endTime") or booking.get("endTime") or plan.get("endTime") or "")


def _local_arcade_event_ref(occurrence_key: str) -> str:
    """Create an opaque local-only reference when PodPlay has no event id."""
    digest = sha256(occurrence_key.encode("utf-8")).hexdigest()[:16]
    return f"local-arcade-{digest}"


def _replay_winner_label(intent: Dict[str, Any], replay: Any) -> str:
    if not isinstance(replay, dict):
        raise ValueError("attached Club Arcade replay is unavailable")
    winner = replay.get("winner")
    if not isinstance(winner, list) or not all(isinstance(value, str) for value in winner):
        raise ValueError("attached Club Arcade replay has no winner")
    labels = [_label(actor) for actor in winner]
    # Training's two participants are opposite sides in Arcade v1, so an
    # individual label is the source-of-truth selection.
    permitted = _permitted_winners(intent)
    for label in labels:
        if label in permitted:
            return label
    raise ValueError("attached Club Arcade replay winner is not a participant")
