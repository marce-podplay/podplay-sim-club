"""Rebuildable projections derived only from the community fact journal."""

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable

from .facts import FACT_FILE, validate_fact
from .storage import Storage


PROJECTION_DIR = "projections"


def rebuild_projections(root: Path) -> Dict[str, Any]:
    storage = Storage(root)
    with storage.writer_lock():
        return rebuild_projections_locked(storage)


def rebuild_projections_locked(storage: Storage) -> Dict[str, Any]:
    facts = storage.read_jsonl(storage.state / FACT_FILE)
    result = reduce_facts(facts)
    directory = storage.state / PROJECTION_DIR
    storage.write_json(directory / "conversations.json", result["conversations"])
    storage.write_json(directory / "commitments.json", result["commitments"])
    storage.write_json(directory / "events.json", result["events"])
    storage.write_json(directory / "participation.json", result["participation"])
    storage.write_json(directory / "beliefs.json", result["beliefs"])
    storage.write_json(directory / "booking-plans.json", result["bookingPlans"])
    storage.write_json(directory / "metadata.json", result["metadata"])
    return result


def reduce_facts(facts: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    conversations: Dict[str, Dict[str, Any]] = {}
    commitments: Dict[str, Dict[str, Any]] = {}
    events: Dict[str, Dict[str, Any]] = {}
    participation: Dict[str, Dict[str, Any]] = {}
    beliefs: Dict[str, Dict[str, Any]] = {}
    booking_plans: Dict[str, Dict[str, Any]] = {}
    seen: set[str] = set()
    last_fact_id = None
    for fact in facts:
        validate_fact(fact)
        fact_id = fact["factId"]
        if fact_id in seen:
            continue
        seen.add(fact_id)
        last_fact_id = fact_id
        fact_type = fact["type"]
        payload = fact["payload"]
        conversation_id = fact.get("conversationId")
        if fact_type == "communication.message" and isinstance(conversation_id, str):
            conversation = conversations.setdefault(conversation_id, {
                "schemaVersion": 1,
                "conversationId": conversation_id,
                "messageIds": [],
                "participantActorIds": [],
                "lastActivityAt": None,
                "lastReducedFactId": None,
            })
            message_id = payload.get("messageId")
            if isinstance(message_id, str) and message_id not in conversation["messageIds"]:
                conversation["messageIds"].append(message_id)
            actors = [fact.get("actorId")] + list(payload.get("recipientActorIds", []))
            for actor_id in actors:
                if isinstance(actor_id, str) and actor_id not in conversation["participantActorIds"]:
                    conversation["participantActorIds"].append(actor_id)
            conversation["lastSpeechAct"] = payload.get("speechAct")
            conversation["lastActivityAt"] = fact["occurredAt"]
            conversation["lastReducedFactId"] = fact_id
        if fact_type in {"commitment.proposed", "commitment.accepted", "commitment.rejected", "commitment.countered", "commitment.expired", "commitment.satisfied", "commitment.violated", "commitment.canceled"}:
            commitment_id = fact["subjectRef"]
            commitment = commitments.setdefault(commitment_id, {
                "schemaVersion": 1,
                "commitmentId": commitment_id,
                "kind": payload.get("kind"),
                "debtorActorId": payload.get("debtorActorId"),
                "creditorActorId": payload.get("creditorActorId"),
                "beneficiaryActorIds": deepcopy(payload.get("beneficiaryActorIds", [])),
                "content": deepcopy(payload.get("content", {})),
                "conversationId": conversation_id,
                "correlationId": fact.get("correlationId"),
                "status": "unknown",
                "proposedAt": None,
                "acceptedAt": None,
                "terminalAt": None,
                "expiresAt": payload.get("expiresAt"),
                "satisfactionEvidenceRef": None,
                "transitions": [],
                "lastReducedFactId": None,
            })
            transition = fact_type.split(".", 1)[1]
            commitment["status"] = transition
            commitment["content"] = deepcopy(payload.get("content", commitment.get("content", {})))
            commitment["transitions"].append({
                "factId": fact_id,
                "status": transition,
                "actorId": fact.get("actorId"),
                "occurredAt": fact["occurredAt"],
                "evidenceRef": fact.get("source", {}).get("evidenceRef"),
            })
            if transition == "proposed":
                commitment["proposedAt"] = fact["occurredAt"]
            elif transition == "accepted":
                commitment["acceptedAt"] = fact["occurredAt"]
            elif transition in {"rejected", "expired", "satisfied", "violated", "canceled"}:
                commitment["terminalAt"] = fact["occurredAt"]
            if transition in {"satisfied", "violated"}:
                commitment["satisfactionEvidenceRef"] = fact.get("source", {}).get("evidenceRef")
            commitment["lastReducedFactId"] = fact_id
        if fact_type.startswith("event."):
            occurrence_key = fact["subjectRef"]
            event = events.setdefault(occurrence_key, {
                "schemaVersion": 1, "occurrenceKey": occurrence_key,
                "kind": "club_event", "status": "unknown",
                "socialIntentRef": None, "commitmentRefs": [],
                "plannedWindow": None, "productEventRef": None,
                "productEvidenceRef": None,
                "result": {"status": "unreported"},
                "lastReducedFactId": None,
            })
            if fact_type == "event.planned":
                event.update({
                    "kind": payload.get("kind", event["kind"]), "status": "planned",
                    "socialIntentRef": payload.get("socialIntentRef"),
                    "commitmentRefs": deepcopy(payload.get("commitmentRefs", [])),
                    "plannedWindow": deepcopy(payload.get("plannedWindow")),
                })
            elif fact_type == "event.window_planned":
                event["plannedWindow"] = deepcopy(payload.get("plannedWindow"))
            elif fact_type == "event.booked":
                event["status"] = "booked"
                event["productEventRef"] = payload.get("productEventRef")
                event["productEvidenceRef"] = fact.get("source", {}).get("evidenceRef")
                # A booking plan and event share the stable occurrence key.
                # Keep the plan's funding/quote history intact, but let the
                # operational stage reflect the verified product write.
                for plan in booking_plans.values():
                    plan_data = plan.get("plan") if isinstance(plan, dict) else None
                    if not isinstance(plan_data, dict) or plan_data.get("occurrenceKey") != occurrence_key:
                        continue
                    plan["stage"] = "executed"
                    plan["execution"] = {
                        "status": "booked",
                        "productEventRef": payload.get("productEventRef"),
                        "evidenceRef": fact.get("source", {}).get("evidenceRef"),
                        "observedAt": fact["occurredAt"],
                    }
                    plan["lastReducedFactId"] = fact_id
            elif fact_type == "event.observed":
                event["status"] = payload.get("outcome", "observed")
                event["productEvidenceRef"] = fact.get("source", {}).get("evidenceRef")
            elif fact_type == "event.result_requested":
                event["result"] = {"status": "requested", "reporterActorId": payload.get("reporterActorId")}
            elif fact_type == "event.result_reported":
                event["status"] = "completed"
                event["result"] = {
                    "status": "reported", "winner": payload.get("winner"),
                    "evidenceKind": payload.get("evidenceKind"), "score": payload.get("score"),
                    "evidenceRef": fact.get("source", {}).get("evidenceRef"),
                }
            event["lastReducedFactId"] = fact_id
        if fact_type.startswith("participation."):
            participation_id = fact["subjectRef"]
            actor_id = payload.get("actorId")
            occurrence_key = payload.get("occurrenceKey")
            if not isinstance(actor_id, str) or not isinstance(occurrence_key, str):
                continue
            row = participation.setdefault(participation_id, {
                "schemaVersion": 1, "participationId": participation_id,
                "occurrenceKey": occurrence_key, "actorId": actor_id,
                "roles": [], "invitation": "not_observed",
                "checkIn": "not_observed", "attendance": "not_observed",
                "resultReporter": False, "evidenceRefs": [],
                "lastReducedFactId": None,
            })
            for key in ("roles", "invitation", "checkIn", "attendance", "resultReporter"):
                if key in payload:
                    row[key] = deepcopy(payload[key])
            evidence_ref = fact.get("source", {}).get("evidenceRef")
            if isinstance(evidence_ref, str) and evidence_ref not in row["evidenceRefs"]:
                row["evidenceRefs"].append(evidence_ref)
            row["lastReducedFactId"] = fact_id
        if fact_type.startswith("belief."):
            transition = fact_type.split(".", 1)[1]
            if transition not in {"unknown", "expected", "observed", "contradicted", "stale"}:
                continue
            actor_id = payload.get("actorId")
            subject = payload.get("subject")
            if not isinstance(actor_id, str) or not isinstance(subject, str):
                continue
            belief_id = fact["subjectRef"]
            belief = beliefs.setdefault(belief_id, {
                "schemaVersion": 1,
                "beliefId": belief_id,
                "actorId": actor_id,
                "subject": subject,
                "status": "unknown",
                "expectedValue": None,
                "observedValue": None,
                "sourceEvidenceRef": None,
                "observedAt": fact["occurredAt"],
                "expiresAt": None,
                "transitions": [],
                "lastReducedFactId": None,
            })
            if payload.get("expectedValue") is not None:
                belief["expectedValue"] = deepcopy(payload["expectedValue"])
            if transition == "unknown":
                belief["observedValue"] = None
            elif payload.get("observedValue") is not None:
                belief["observedValue"] = deepcopy(payload["observedValue"])
            evidence_ref = fact.get("source", {}).get("evidenceRef")
            if isinstance(evidence_ref, str):
                belief["sourceEvidenceRef"] = evidence_ref
            if payload.get("expiresAt") is not None:
                belief["expiresAt"] = payload["expiresAt"]
            belief["status"] = transition
            if transition != "stale" or not belief.get("observedAt"):
                belief["observedAt"] = fact["occurredAt"]
            belief["transitions"].append({
                "factId": fact_id,
                "status": transition,
                "occurredAt": fact["occurredAt"],
                "evidenceRef": evidence_ref,
            })
            belief["lastReducedFactId"] = fact_id
        if fact_type in {"booking_plan.created", "booking_plan.planned"}:
            plan = payload.get("plan")
            if isinstance(plan, dict) and isinstance(plan.get("bookingPlanId"), str):
                booking_plans[plan["bookingPlanId"]] = {
                    "schemaVersion": 1,
                    "bookingPlanId": plan["bookingPlanId"],
                    "plan": deepcopy(plan),
                    "quote": None,
                    "comparison": None,
                    "execution": None,
                    "stage": plan.get("stage", "draft"),
                    "lastReducedFactId": fact_id,
                }
        if fact_type in {"booking_quote.observed", "booking_quote.unknown", "booking_quote.error"}:
            plan = booking_plans.get(fact["subjectRef"])
            if plan is not None:
                result = payload.get("result")
                plan["quote"] = {
                    "status": result,
                    "fields": deepcopy(payload.get("fields")),
                    "errorCode": payload.get("errorCode"),
                    "evidenceRef": fact.get("source", {}).get("evidenceRef"),
                    "observedAt": fact["occurredAt"],
                }
                requested_stage = plan.get("plan", {}).get("stage") if isinstance(plan.get("plan"), dict) else None
                if plan.get("stage") not in {"executed", "observed"}:
                    plan["stage"] = "awaiting_payer_approval" if result == "observed" and requested_stage != "blocked" else "blocked"
                plan["lastReducedFactId"] = fact_id
        if fact_type in {"booking_quote.compared.within_expectation", "booking_quote.compared.mismatch", "booking_quote.compared.unknown"}:
            plan = booking_plans.get(fact["subjectRef"])
            comparison = payload.get("comparison")
            if plan is not None and isinstance(comparison, dict):
                plan["comparison"] = deepcopy(comparison)
                if plan.get("stage") not in {"executed", "observed"}:
                    plan["stage"] = "awaiting_payer_approval" if comparison.get("status") == "within_expectation" else "blocked"
                plan["lastReducedFactId"] = fact_id
    return {
        "conversations": {
            "schemaVersion": 1,
            "conversations": {key: conversations[key] for key in sorted(conversations)},
        },
        "commitments": {
            "schemaVersion": 1,
            "commitments": {key: commitments[key] for key in sorted(commitments)},
        },
        "events": {"schemaVersion": 1, "events": {key: events[key] for key in sorted(events)}},
        "participation": {"schemaVersion": 1, "participation": {key: participation[key] for key in sorted(participation)}},
        "beliefs": {"schemaVersion": 1, "beliefs": {key: beliefs[key] for key in sorted(beliefs)}},
        "bookingPlans": {"schemaVersion": 1, "bookingPlans": {key: booking_plans[key] for key in sorted(booking_plans)}},
        "metadata": {
            "schemaVersion": 1,
            "factCount": len(seen),
            "lastReducedFactId": last_fact_id,
        },
    }
