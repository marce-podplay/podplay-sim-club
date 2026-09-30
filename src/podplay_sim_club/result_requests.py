"""Sofia's bounded, no-score result-request capability."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .booking_intents import load_intent_ledger, save_intent
from .players import canonical_actor_id
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
                and prior.get("journey") != "customer_training"
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
            # This first adapter has read-back attendance per named player.
            # Tournament and owner Open Play events use their own result
            # protocols until their participant/check-in adapters exist.
            and intent.get("journey") == "customer_training"
            and intent.get("status") == "attendance_observed"
            and isinstance(intent.get("eventObservation"), dict)
            and intent["eventObservation"].get("outcome") == "attendance_observed"
            and isinstance(intent.get("booking"), dict)
            and intent["booking"].get("eventId") == intent["eventObservation"].get("eventId")
            and not isinstance(intent.get("resultRequest"), dict)
        ]
        if not candidates:
            return {"action": "pass", "detail": "no attended event awaits a result request", "remoteWrites": 0}
        intent = sorted(candidates, key=lambda row: str(row["eventObservation"].get("endTime")))[0]
        participants = [actor for actor in intent.get("participants", []) if isinstance(actor, str)]
        reporter = _reporter(intent, participants)
        if reporter is None:
            return {"action": "pass", "detail": "attended event has no accountable reporter", "remoteWrites": 0}
        event_id = intent["eventObservation"].get("eventId")
        if not isinstance(event_id, str):
            return {"action": "pass", "detail": "attended event is missing its event identifier", "remoteWrites": 0}
        request = {
            "eventId": event_id,
            "reporterActorId": reporter,
            "requestedAt": observed_at,
            "status": "requested",
            "evidence": "verified_check_in_only",
            "result": None,
        }
        intent["resultRequest"] = request
        save_intent(storage, ledger, intent, make_active=False)
        storage.append_jsonl(storage.channel_path("club"), {
            "id": f"event:{event_id}:result-requested",
            "channel": "club",
            "from": "sofia",
            "to": [reporter],
            "kind": "event_result_requested",
            "intentId": intent["id"],
            "eventId": event_id,
            "text": f"Sofia asks {_label(reporter)} for the result. Reply with the winning player or team only; no score is required.",
            "payload": {"reporterActorId": reporter, "evidence": "verified_check_in_only", "result": "UNREPORTED"},
            "observedAt": observed_at,
            "remoteWrites": 0,
        })
    return {"action": "event_result_requested", "intentId": intent["id"], "eventId": event_id, "reporter": reporter, "remoteWrites": 0}


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
