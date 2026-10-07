"""Canonical event and per-actor participation facts.

Events describe one stable local occurrence. Participation is always a
separate actor+occurrence record, preventing a partial product read-back from
overwriting the event's other people.
"""

from hashlib import sha256
from typing import Any, Dict, Optional

from .facts import make_fact
from .protocol import append_protocol_facts_locked
from .storage import Storage


def occurrence_for_intent(intent_id: str) -> str:
    if not isinstance(intent_id, str) or not intent_id:
        raise ValueError("occurrence requires a durable intent id")
    digest = sha256(intent_id.encode("utf-8")).hexdigest()[:20]
    return f"occurrence:intent:{digest}"


def participation_id(occurrence_key: str, actor_id: str) -> str:
    return f"participation:{occurrence_key}:{actor_id}"


def ensure_planned_locked(
    storage: Storage,
    intent: Dict[str, Any],
    occurred_at: str,
    planned_window: Optional[Dict[str, Any]] = None,
) -> str:
    intent_id = intent.get("id")
    if not isinstance(intent_id, str):
        raise ValueError("planned occurrence requires a durable intent")
    occurrence_key = intent.get("occurrenceKey")
    if not isinstance(occurrence_key, str):
        occurrence_key = occurrence_for_intent(intent_id)
        intent["occurrenceKey"] = occurrence_key
    participants = [actor for actor in intent.get("participants", []) if isinstance(actor, str)]
    booker = intent.get("bookerActorId")
    correlation_id = f"journey:{intent_id}"
    facts = [make_fact(
        fact_type="event.planned", natural_key=f"{occurrence_key}:planned",
        subject_ref=occurrence_key, correlation_id=correlation_id,
        source_kind="intent_policy", occurred_at=occurred_at,
        actor_id="lead", payload={
            "kind": str(intent.get("journey") or "club_event"),
            "socialIntentRef": intent_id,
            "commitmentRefs": [intent["commitmentId"]] if isinstance(intent.get("commitmentId"), str) else [],
            "bookerActorId": booker,
            "plannedWindow": planned_window,
        },
    )]
    for actor_id in participants:
        roles = ["participant"]
        if actor_id == booker:
            roles.append("booking_owner")
        if actor_id in {
            side.get("captainActorId") for side in (intent.get("challenger"), intent.get("opponent"))
            if isinstance(side, dict)
        }:
            roles.append("captain")
        facts.append(make_fact(
            fact_type="participation.expected",
            natural_key=f"{occurrence_key}:{actor_id}:expected",
            subject_ref=participation_id(occurrence_key, actor_id),
            correlation_id=correlation_id, source_kind="intent_policy",
            occurred_at=occurred_at, actor_id="lead", payload={
                "occurrenceKey": occurrence_key, "actorId": actor_id,
                "roles": roles, "invitation": "not_observed",
                "checkIn": "not_observed", "attendance": "not_observed",
                "resultReporter": False,
            },
        ))
    append_protocol_facts_locked(storage, facts)
    return occurrence_key


def record_window_locked(
    storage: Storage, *, occurrence_key: str, intent_id: str,
    planned_window: Dict[str, Any], occurred_at: str,
) -> None:
    fact = make_fact(
        fact_type="event.window_planned",
        natural_key=f"{occurrence_key}:window:{planned_window.get('startTime')}:{planned_window.get('endTime')}",
        subject_ref=occurrence_key, correlation_id=f"journey:{intent_id}",
        source_kind="preview_plan", occurred_at=occurred_at, actor_id="lead",
        payload={"plannedWindow": dict(planned_window)},
    )
    append_protocol_facts_locked(storage, [fact])


def record_booked_locked(
    storage: Storage, *, occurrence_key: str, intent_id: str, event_ref: str,
    booker_actor_id: str, occurred_at: str, evidence_ref: str,
) -> None:
    facts = [make_fact(
        fact_type="event.booked", natural_key=f"{occurrence_key}:booked:{event_ref}",
        subject_ref=occurrence_key, correlation_id=f"journey:{intent_id}",
        source_kind="preview_readback", evidence_ref=evidence_ref,
        occurred_at=occurred_at, actor_id="lead",
        payload={"productEventRef": event_ref},
    ), _participation_fact(
        "participation.invitation_observed", occurrence_key, intent_id,
        booker_actor_id, occurred_at, evidence_ref,
        {"invitation": "owner", "checkIn": "not_observed", "attendance": "not_observed"},
    )]
    append_protocol_facts_locked(storage, facts)


def record_invitation_locked(
    storage: Storage, *, occurrence_key: str, intent_id: str, actor_id: str,
    status: str, occurred_at: str, evidence_ref: str,
) -> None:
    append_protocol_facts_locked(storage, [_participation_fact(
        "participation.invitation_observed", occurrence_key, intent_id,
        actor_id, occurred_at, evidence_ref,
        {"invitation": status.lower(), "checkIn": "not_observed", "attendance": "not_observed"},
    )])


def record_check_in_locked(
    storage: Storage, *, occurrence_key: str, intent_id: str, actor_id: str,
    status: str, occurred_at: str, evidence_ref: str,
) -> None:
    attendance = "checked_in" if status == "CHECKED_IN" else "not_observed"
    append_protocol_facts_locked(storage, [_participation_fact(
        "participation.check_in_observed", occurrence_key, intent_id,
        actor_id, occurred_at, evidence_ref,
        {"checkIn": status.lower(), "attendance": attendance},
    )])


def record_observation_locked(
    storage: Storage, *, occurrence_key: str, intent_id: str, outcome: str,
    participant_observations: Dict[str, Dict[str, Any]], occurred_at: str,
    evidence_ref: str,
) -> None:
    facts = [make_fact(
        fact_type="event.observed", natural_key=f"{occurrence_key}:observed:{outcome}",
        subject_ref=occurrence_key, correlation_id=f"journey:{intent_id}",
        source_kind="preview_readback", evidence_ref=evidence_ref,
        occurred_at=occurred_at, actor_id="lead", payload={"outcome": outcome},
    )]
    for actor_id, observation in participant_observations.items():
        check_in = observation.get("checkIn")
        accepted = observation.get("accepted")
        attendance = (
            "checked_in" if check_in == "CHECKED_IN"
            else "missed" if outcome == "missed_session" and check_in == "NOT_CHECKED_IN"
            else "inconclusive"
        )
        facts.append(_participation_fact(
            "participation.attendance_observed", occurrence_key, intent_id,
            actor_id, occurred_at, evidence_ref, {
                "invitation": "accepted" if accepted else "not_observed",
                "checkIn": str(check_in or "not_observed").lower(),
                "attendance": attendance,
            },
        ))
    append_protocol_facts_locked(storage, facts)


def record_result_requested_locked(
    storage: Storage, *, occurrence_key: str, intent_id: str,
    reporter_actor_id: str, occurred_at: str,
) -> None:
    facts = [make_fact(
        fact_type="event.result_requested", natural_key=f"{occurrence_key}:result-requested",
        subject_ref=occurrence_key, correlation_id=f"journey:{intent_id}",
        source_kind="club_message", occurred_at=occurred_at, actor_id="sofia",
        payload={"reporterActorId": reporter_actor_id},
    ), _participation_fact(
        "participation.result_reporter_selected", occurrence_key, intent_id,
        reporter_actor_id, occurred_at, None, {"resultReporter": True},
    )]
    append_protocol_facts_locked(storage, facts)


def record_result_locked(
    storage: Storage, *, occurrence_key: str, intent_id: str,
    reporter_actor_id: str, winner: str, evidence_kind: str,
    occurred_at: str, evidence_ref: str,
) -> None:
    append_protocol_facts_locked(storage, [make_fact(
        fact_type="event.result_reported", natural_key=f"{occurrence_key}:result-reported",
        subject_ref=occurrence_key, correlation_id=f"journey:{intent_id}",
        source_kind="captain_report", evidence_ref=evidence_ref,
        occurred_at=occurred_at, actor_id=reporter_actor_id,
        payload={"winner": winner, "evidenceKind": evidence_kind, "score": None},
    )])


def _participation_fact(
    fact_type: str, occurrence_key: str, intent_id: str, actor_id: str,
    occurred_at: str, evidence_ref: Optional[str], changes: Dict[str, Any],
) -> Dict[str, Any]:
    return make_fact(
        fact_type=fact_type,
        natural_key=f"{occurrence_key}:{actor_id}:{fact_type}:{occurred_at}",
        subject_ref=participation_id(occurrence_key, actor_id),
        correlation_id=f"journey:{intent_id}", source_kind="preview_readback",
        evidence_ref=evidence_ref, occurred_at=occurred_at, actor_id="lead",
        payload={"occurrenceKey": occurrence_key, "actorId": actor_id, **changes},
    )
