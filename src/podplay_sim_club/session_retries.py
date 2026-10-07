"""Grounded retry agreements for any missed player-owned session.

A missed product event is evidence and motivation, not permission to create a
replacement.  This module creates a fresh local intent only after an authorized
captain proposes it and the required teammate or opposing captain accepts.
"""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .booking_intents import load_intent_ledger, save_intent
from .protocol import (
    append_protocol_facts_locked,
    make_commitment_proposal,
    make_commitment_transition,
    make_message,
    reply_causation,
)
from .storage import Storage
from .time import isoformat


class SessionRetryError(ValueError):
    """Raised when a retry cannot be grounded in a missed session."""


def retry_intent_id(source: Dict[str, Any]) -> str:
    observation = source.get("eventObservation")
    event_id = observation.get("eventId") if isinstance(observation, dict) else None
    if not isinstance(event_id, str) or not event_id:
        raise SessionRetryError("missed session has no observed event")
    return f"intent:retry:{event_id}"


def eligible_proposers(source: Dict[str, Any]) -> List[str]:
    """Return actors authorized to propose another instance of this session."""
    journey = source.get("journey")
    owner = source.get("bookerActorId")
    participants = source.get("participants")
    if not isinstance(owner, str) or not isinstance(participants, list) or owner not in participants:
        return []
    if journey == "customer_training":
        return [owner]
    if journey == "customer_team_signup":
        captains = []
        for side in (source.get("challenger"), source.get("opponent")):
            captain = side.get("captainActorId") if isinstance(side, dict) else None
            if isinstance(captain, str) and captain in participants and captain not in captains:
                captains.append(captain)
        return captains
    return []


def required_acceptor(source: Dict[str, Any], proposer: str) -> str:
    participants = source.get("participants")
    if proposer not in eligible_proposers(source) or not isinstance(participants, list):
        raise SessionRetryError("actor is not authorized to retry this session")
    if source.get("journey") == "customer_training":
        candidate = next((actor for actor in participants if isinstance(actor, str) and actor != proposer), None)
    else:
        candidate = next((actor for actor in eligible_proposers(source) if actor != proposer), None)
    if not isinstance(candidate, str):
        raise SessionRetryError("missed session has no required retry acceptor")
    return candidate


def propose_retry(root: Path, source_intent_id: str, proposer: str, now: datetime) -> Dict[str, Any]:
    """Propose a fresh instance without copying product booking artifacts."""
    storage = Storage(root)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        source = ledger["intents"].get(source_intent_id)
        _validate_source(source)
        assert isinstance(source, dict)
        acceptor = required_acceptor(source, proposer)
        intent_id = retry_intent_id(source)
        existing = ledger["intents"].get(intent_id)
        if isinstance(existing, dict):
            return deepcopy(existing)
        observed_at = isoformat(now)
        journey = source["journey"]
        intent = {
            "schemaVersion": 1,
            "id": intent_id,
            "status": "captain_invited",
            "journey": journey,
            "reason": "missed_session_retry",
            "createdAt": observed_at,
            "requestedBy": proposer,
            "bookerActorId": proposer,
            "participants": deepcopy(source["participants"]),
            "constraints": deepcopy(source["constraints"]),
            "evidence": {
                "priorIntentId": source_intent_id,
                "missedEventId": source["eventObservation"]["eventId"],
                "missedObservation": "missed_session",
            },
            "conversationId": f"conversation:{intent_id}",
            "commitmentId": f"commitment:{intent_id}",
            "nextAction": "await_retry_acceptance",
            "remoteWrites": 0,
        }
        for key in ("trainingTeamId", "fallbackJourney"):
            if key in source:
                intent[key] = deepcopy(source[key])
        if journey == "customer_team_signup":
            challenger, opponent = _ordered_sides(source, proposer)
            intent["challenger"] = challenger
            intent["opponent"] = opponent
        save_intent(storage, ledger, intent)
        message_id = f"{intent_id}:proposal"
        storage.append_jsonl(storage.channel_path("club"), {
            "id": message_id,
            "conversationId": intent["conversationId"],
            "commitmentId": intent["commitmentId"],
            "speechAct": "propose",
            "channel": "club",
            "kind": "session_retry_proposal",
            "from": proposer,
            "to": [acceptor],
            "intentId": intent_id,
            "sourceIntentId": source_intent_id,
            "inReplyTo": f"event:{source['eventObservation']['eventId']}:training-missed",
            "text": "Our previous session was missed. I propose a fresh session with the same named players and constraints; accept if you still want to play.",
            "observedAt": observed_at,
            "remoteWrites": 0,
        })
        append_protocol_facts_locked(storage, _proposal_facts(intent, source, proposer, acceptor, observed_at))
    return intent


def accept_retry(root: Path, intent_id: str, actor_id: str, now: datetime) -> Dict[str, Any]:
    """Ground the fresh proposal with the required actor's acceptance."""
    storage = Storage(root)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        intent = ledger["intents"].get(intent_id)
        if not isinstance(intent, dict) or intent.get("reason") != "missed_session_retry":
            raise SessionRetryError("session retry proposal is unavailable")
        if intent.get("status") == "agreed":
            return deepcopy(intent)
        if intent.get("status") != "captain_invited":
            raise SessionRetryError("session retry is no longer awaiting acceptance")
        evidence = intent.get("evidence")
        source_id = evidence.get("priorIntentId") if isinstance(evidence, dict) else None
        source = ledger["intents"].get(source_id)
        _validate_source(source)
        assert isinstance(source, dict)
        proposer = intent.get("requestedBy")
        if not isinstance(proposer, str) or actor_id != required_acceptor(source, proposer):
            raise SessionRetryError("actor is not the required retry acceptor")
        observed_at = isoformat(now)
        intent = deepcopy(intent)
        intent.update({
            "status": "agreed",
            "agreedAt": observed_at,
            "nextAction": (
                "plan_live_w37_availability_then_player_booking"
                if intent.get("journey") == "customer_training"
                else "plan_team_signup_booking"
            ),
        })
        save_intent(storage, ledger, intent)
        storage.append_jsonl(storage.channel_path("club"), {
            "id": f"{intent_id}:acceptance",
            "conversationId": intent.get("conversationId"),
            "commitmentId": intent.get("commitmentId"),
            "speechAct": "accept",
            "channel": "club",
            "kind": "session_retry_acceptance",
            "from": actor_id,
            "to": [proposer],
            "intentId": intent_id,
            "inReplyTo": f"{intent_id}:proposal",
            "text": "I accept the fresh session proposal. The lead may now plan one legal court; no booking or attendance is implied yet.",
            "observedAt": observed_at,
            "remoteWrites": 0,
        })
        append_protocol_facts_locked(storage, _acceptance_facts(storage, intent, proposer, actor_id, observed_at))
    return intent


def _validate_source(source: Any) -> None:
    if not isinstance(source, dict) or source.get("journey") not in {"customer_training", "customer_team_signup"}:
        raise SessionRetryError("retry source is not a supported player-owned session")
    observation = source.get("eventObservation")
    if source.get("status") != "missed_session" or not isinstance(observation, dict) or observation.get("outcome") != "missed_session":
        raise SessionRetryError("retry source is not an evidenced missed session")
    if not isinstance(source.get("participants"), list) or not isinstance(source.get("constraints"), dict):
        raise SessionRetryError("missed session is missing participants or constraints")


def _ordered_sides(source: Dict[str, Any], proposer: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    challenger = source.get("challenger")
    opponent = source.get("opponent")
    if not isinstance(challenger, dict) or not isinstance(opponent, dict):
        raise SessionRetryError("missed team session is missing its captain sides")
    if challenger.get("captainActorId") == proposer:
        return deepcopy(challenger), deepcopy(opponent)
    if opponent.get("captainActorId") == proposer:
        return deepcopy(opponent), deepcopy(challenger)
    raise SessionRetryError("retry proposer is not one of the team captains")


def _proposal_facts(
    intent: Dict[str, Any],
    source: Dict[str, Any],
    proposer: str,
    acceptor: str,
    observed_at: str,
) -> List[Dict[str, Any]]:
    message_id = f"{intent['id']}:proposal"
    conversation_id = intent["conversationId"]
    commitment_id = intent["commitmentId"]
    correlation_id = f"journey:{intent['id']}"
    beneficiaries = [actor_id for actor_id in intent["participants"] if isinstance(actor_id, str)]
    message_fact = make_message(
        message_id=message_id,
        conversation_id=conversation_id,
        correlation_id=correlation_id,
        sender_actor_id=proposer,
        recipient_actor_ids=[acceptor],
        speech_act="propose",
        content_code="session.retry.same_participants_and_constraints",
        occurred_at=observed_at,
        commitment_id=commitment_id,
        parameters={"sourceIntentRef": source["id"]},
    )
    commitment_fact = make_commitment_proposal(
        commitment_id=commitment_id,
        conversation_id=conversation_id,
        correlation_id=correlation_id,
        proposer_actor_id=proposer,
        debtor_actor_id=acceptor,
        beneficiary_actor_ids=beneficiaries,
        kind="join_fresh_session",
        content={
            "capabilityId": "player.accept_session_retry",
            "intentRef": intent["id"],
            "sourceIntentRef": source["id"],
        },
        occurred_at=observed_at,
        causation_id=message_fact["factId"],
    )
    return [message_fact, commitment_fact]


def _acceptance_facts(
    storage: Storage, intent: Dict[str, Any], proposer: str, acceptor: str, observed_at: str
) -> List[Dict[str, Any]]:
    message_id = f"{intent['id']}:acceptance"
    proposal_message_id = f"{intent['id']}:proposal"
    conversation_id = intent["conversationId"]
    commitment_id = intent["commitmentId"]
    correlation_id = f"journey:{intent['id']}"
    message_fact = make_message(
        message_id=message_id,
        conversation_id=conversation_id,
        correlation_id=correlation_id,
        sender_actor_id=acceptor,
        recipient_actor_ids=[proposer],
        speech_act="accept",
        content_code="session.retry.accepted",
        occurred_at=observed_at,
        commitment_id=commitment_id,
        in_reply_to=proposal_message_id,
        grounding="understood",
        causation_id=reply_causation(proposal_message_id),
    )
    commitment_fact = make_commitment_transition(
        storage,
        commitment_id=commitment_id,
        transition="accepted",
        occurred_at=observed_at,
        actor_id=acceptor,
        causation_id=message_fact["factId"],
    )
    return [message_fact, commitment_fact]
