"""Shared, evidence-bounded communication and commitment protocol.

Journey modules may keep their compatibility channel messages and intent
ledgers while migration is in progress.  This module owns the semantic facts:
who addressed whom, what speech act occurred, and which actor is authorized to
move a commitment through its lifecycle.
"""

from copy import deepcopy
from typing import Any, Dict, Iterable, Optional

from .facts import append_facts_locked, deterministic_fact_id, make_fact
from .projections import rebuild_projections_locked, reduce_facts
from .storage import Storage
from .time import parse_instant


TERMINAL_STATES = {"rejected", "expired", "satisfied", "violated", "canceled"}
TRANSITIONS = {
    "proposed": {"accepted", "rejected", "countered", "expired", "canceled"},
    "countered": {"accepted", "rejected", "countered", "expired", "canceled"},
    "accepted": {"satisfied", "violated", "canceled"},
}
LEAD_ACTORS = {"lead", "system"}
SPEECH_ACTS = {"inform", "report", "invite", "propose", "request", "offer", "accept", "reject", "counter", "acknowledge", "clarify", "complain", "escalate"}
GROUNDING_STATES = {"created", "addressed", "acknowledged", "clarification_requested", "understood"}


class ProtocolError(ValueError):
    """Raised when dialogue attempts to create an unauthorized social fact."""


def conversation_id_for(subject_ref: str) -> str:
    return f"conversation:{subject_ref}"


def commitment_id_for(subject_ref: str) -> str:
    return f"commitment:{subject_ref}"


def make_message(
    *,
    message_id: str,
    conversation_id: str,
    correlation_id: str,
    sender_actor_id: str,
    recipient_actor_ids: list[str],
    speech_act: str,
    content_code: str,
    occurred_at: str,
    grounding: str = "addressed",
    commitment_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    parameters: Optional[Dict[str, Any]] = None,
    causation_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not recipient_actor_ids or not all(isinstance(actor, str) and actor for actor in recipient_actor_ids):
        raise ProtocolError("a protocol message needs named recipients")
    if speech_act not in SPEECH_ACTS:
        raise ProtocolError("protocol message has an unknown speech act")
    if grounding not in GROUNDING_STATES:
        raise ProtocolError("protocol message has invalid grounding")
    payload: Dict[str, Any] = {
        "messageId": message_id,
        "speechAct": speech_act,
        "recipientActorIds": list(dict.fromkeys(recipient_actor_ids)),
        "contentCode": content_code,
        "grounding": grounding,
    }
    if commitment_id is not None:
        payload["commitmentId"] = commitment_id
    if in_reply_to is not None:
        payload["inReplyTo"] = in_reply_to
    if parameters:
        payload["parameters"] = deepcopy(parameters)
    return make_fact(
        fact_type="communication.message",
        natural_key=message_id,
        subject_ref=message_id,
        correlation_id=correlation_id,
        source_kind="club_message",
        payload=payload,
        occurred_at=occurred_at,
        actor_id=sender_actor_id,
        conversation_id=conversation_id,
        causation_id=causation_id,
    )


def make_commitment_proposal(
    *,
    commitment_id: str,
    conversation_id: str,
    correlation_id: str,
    proposer_actor_id: str,
    debtor_actor_id: str,
    beneficiary_actor_ids: list[str],
    kind: str,
    content: Dict[str, Any],
    occurred_at: str,
    causation_id: str,
    expires_at: Optional[str] = None,
) -> Dict[str, Any]:
    payload = {
        "kind": kind,
        "debtorActorId": debtor_actor_id,
        "creditorActorId": proposer_actor_id,
        "beneficiaryActorIds": list(dict.fromkeys(beneficiary_actor_ids)),
        "content": deepcopy(content),
    }
    if expires_at is not None:
        parse_instant(expires_at)
        payload["expiresAt"] = expires_at
    return make_fact(
        fact_type="commitment.proposed",
        natural_key=f"{commitment_id}:proposed",
        subject_ref=commitment_id,
        correlation_id=correlation_id,
        source_kind="protocol_reducer",
        payload=payload,
        occurred_at=occurred_at,
        actor_id=proposer_actor_id,
        conversation_id=conversation_id,
        causation_id=causation_id,
    )


def make_commitment_transition(
    storage: Storage,
    *,
    commitment_id: str,
    transition: str,
    actor_id: str,
    occurred_at: str,
    causation_id: str,
    evidence_ref: Optional[str] = None,
    replacement_content: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate authority and return one lifecycle fact.

    Accept/reject/counter belongs to the debtor.  Either party may cancel.
    Only the deterministic lead may expire or resolve an accepted commitment,
    and satisfaction/violation requires a sanitized evidence reference.
    Clarification is intentionally absent: it is a message and never changes
    commitment state.
    """
    facts = storage.read_jsonl(storage.state / "journal" / "facts.jsonl")
    projection = reduce_facts(facts)["commitments"]["commitments"].get(commitment_id)
    if not isinstance(projection, dict):
        raise ProtocolError("commitment is unavailable")
    current = projection.get("status")
    if transition not in TRANSITIONS.get(str(current), set()):
        raise ProtocolError(f"commitment cannot move from {current} to {transition}")
    debtor = projection.get("debtorActorId")
    creditor = projection.get("creditorActorId")
    if transition in {"accepted", "rejected", "countered"} and actor_id != debtor:
        raise ProtocolError("only the commitment debtor may answer this proposal")
    if transition == "canceled" and actor_id not in {debtor, creditor}:
        raise ProtocolError("only a commitment party may cancel it")
    if transition == "expired":
        if actor_id not in LEAD_ACTORS:
            raise ProtocolError("only the lead may expire a commitment")
        expires_at = projection.get("expiresAt")
        if isinstance(expires_at, str) and parse_instant(occurred_at) < parse_instant(expires_at):
            raise ProtocolError("commitment has not reached its expiry")
    if transition in {"satisfied", "violated"}:
        if actor_id not in LEAD_ACTORS:
            raise ProtocolError("only the lead may resolve an accepted commitment")
        if not isinstance(evidence_ref, str) or not evidence_ref:
            raise ProtocolError("commitment resolution requires evidence")
    payload = {
        "kind": projection.get("kind"),
        "debtorActorId": debtor,
        "creditorActorId": creditor,
        "beneficiaryActorIds": deepcopy(projection.get("beneficiaryActorIds", [])),
        "content": deepcopy(replacement_content if transition == "countered" and replacement_content is not None else projection.get("content", {})),
    }
    return make_fact(
        fact_type=f"commitment.{transition}",
        natural_key=f"{commitment_id}:{transition}:{occurred_at}",
        subject_ref=commitment_id,
        correlation_id=str(projection.get("correlationId") or f"protocol:{commitment_id}"),
        source_kind="protocol_reducer",
        payload=payload,
        occurred_at=occurred_at,
        actor_id=actor_id,
        conversation_id=projection.get("conversationId"),
        causation_id=causation_id,
        evidence_ref=evidence_ref,
    )


def append_protocol_facts_locked(storage: Storage, facts: Iterable[Dict[str, Any]]) -> int:
    count = append_facts_locked(storage, facts)
    rebuild_projections_locked(storage)
    return count


def reply_causation(message_id: str) -> str:
    return deterministic_fact_id("communication.message", message_id)
