"""Payer-owned approval commitments for an expectation-matched booking plan."""

from datetime import timedelta
from pathlib import Path
from typing import Any, Dict

from .protocol import (
    ProtocolError, append_protocol_facts_locked, commitment_id_for,
    conversation_id_for, make_commitment_proposal, make_commitment_transition,
    make_message,
)
from .storage import Storage
from .time import isoformat, parse_instant


QUOTE_MAX_AGE = timedelta(minutes=15)


class PayerApprovalError(ValueError):
    pass


def request(root: Path, booking_plan_id: str, occurred_at: str) -> Dict[str, Any]:
    """Let the lead ask the named payer for approval of one exact quote."""
    storage = Storage(root)
    plan, comparison = _eligible_plan(storage, booking_plan_id, occurred_at)
    payer = plan["payerActorId"]
    quote_ref = comparison["quoteEvidenceRef"]
    subject = f"payer-approval:{booking_plan_id}:{quote_ref}"
    commitment_id = commitment_id_for(subject)
    conversation_id = conversation_id_for(subject)
    facts = [
        make_message(
            message_id=f"message:{subject}:request",
            conversation_id=conversation_id,
            correlation_id=f"journey:{booking_plan_id}",
            sender_actor_id="lead",
            recipient_actor_ids=[payer],
            speech_act="request",
            content_code="payer_approval_requested",
            occurred_at=occurred_at,
            commitment_id=commitment_id,
            parameters={"bookingPlanId": booking_plan_id, "quoteEvidenceRef": quote_ref},
        ),
        make_commitment_proposal(
            commitment_id=commitment_id,
            conversation_id=conversation_id,
            correlation_id=f"journey:{booking_plan_id}",
            proposer_actor_id="lead",
            debtor_actor_id=payer,
            beneficiary_actor_ids=list(plan["participantActorIds"]),
            kind="payer_approval",
            content={
                "bookingPlanId": booking_plan_id,
                "quoteEvidenceRef": quote_ref,
                "comparisonId": comparison["comparisonId"],
                "requestedFunding": plan["requestedFunding"],
            },
            occurred_at=occurred_at,
            causation_id=f"comparison:{comparison['comparisonId']}",
            expires_at=isoformat(parse_instant(comparison["assessedAt"]) + QUOTE_MAX_AGE),
        ),
    ]
    with storage.writer_lock():
        append_protocol_facts_locked(storage, facts)
    return {"bookingPlanId": booking_plan_id, "commitmentId": commitment_id, "payerActorId": payer, "status": "requested", "remoteWrites": 0}


def accept(root: Path, booking_plan_id: str, payer_actor_id: str, occurred_at: str) -> Dict[str, Any]:
    """Record only the named payer's acceptance of a fresh matching quote."""
    storage = Storage(root)
    plan, comparison = _eligible_plan(storage, booking_plan_id, occurred_at)
    if payer_actor_id != plan["payerActorId"]:
        raise PayerApprovalError("only the named payer may approve this plan")
    subject = f"payer-approval:{booking_plan_id}:{comparison['quoteEvidenceRef']}"
    commitment_id = commitment_id_for(subject)
    try:
        with storage.writer_lock():
            transition = make_commitment_transition(
                storage, commitment_id=commitment_id, transition="accepted",
                actor_id=payer_actor_id, occurred_at=occurred_at,
                causation_id=f"comparison:{comparison['comparisonId']}",
            )
            append_protocol_facts_locked(storage, [transition])
    except ProtocolError as exc:
        raise PayerApprovalError(str(exc)) from exc
    return {"bookingPlanId": booking_plan_id, "commitmentId": commitment_id, "payerActorId": payer_actor_id, "status": "accepted", "remoteWrites": 0}


def require_accepted(root: Path, booking_plan_id: str, occurred_at: str) -> Dict[str, Any]:
    """Return the matching approval or refuse before a guarded product write."""
    storage = Storage(root)
    plan, comparison = _eligible_plan(storage, booking_plan_id, occurred_at)
    subject = f"payer-approval:{booking_plan_id}:{comparison['quoteEvidenceRef']}"
    commitment_id = commitment_id_for(subject)
    commitments = storage.load_json(storage.state / "projections" / "commitments.json", default={})
    row = commitments.get("commitments", {}).get(commitment_id) if isinstance(commitments, dict) else None
    if not isinstance(row, dict) or row.get("status") != "accepted":
        raise PayerApprovalError("team signup apply requires the named payer's accepted approval")
    if row.get("debtorActorId") != plan["payerActorId"]:
        raise PayerApprovalError("payer approval does not belong to this plan's payer")
    return {"bookingPlanId": booking_plan_id, "payerActorId": plan["payerActorId"], "commitmentId": commitment_id, "quoteEvidenceRef": comparison["quoteEvidenceRef"]}


def _eligible_plan(storage: Storage, booking_plan_id: str, occurred_at: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    projected = storage.load_json(storage.state / "projections" / "booking-plans.json", default={})
    rows = projected.get("bookingPlans", {}) if isinstance(projected, dict) else {}
    row = rows.get(booking_plan_id) if isinstance(rows, dict) else None
    if not isinstance(row, dict) or not isinstance(row.get("plan"), dict):
        raise PayerApprovalError("booking plan is unavailable")
    comparison = row.get("comparison")
    if not isinstance(comparison, dict) or comparison.get("status") != "within_expectation":
        raise PayerApprovalError("payer approval requires a matching expectation comparison")
    try:
        age = parse_instant(occurred_at) - parse_instant(comparison["assessedAt"])
    except (KeyError, ValueError, TypeError) as exc:
        raise PayerApprovalError("comparison timestamp is invalid") from exc
    if age < timedelta(0) or age > QUOTE_MAX_AGE:
        raise PayerApprovalError("payer approval requires a fresh quote comparison")
    return row["plan"], comparison
