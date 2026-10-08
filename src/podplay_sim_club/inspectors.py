"""Read-only, secret-free projections for operators and the Observatory.

The CLI and web UI intentionally use these same views.  They do not read raw
Preview responses, credentials, private invitation links, or payment IDs.
"""

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

from .storage import Storage
from .booking_plans import compare_quote_to_payer_beliefs
from .affinity_groups import groups_for_team


class InspectorNotFoundError(ValueError):
    pass


def player_inspector(root: Path, actor_id: str) -> Dict[str, Any]:
    storage = Storage(root)
    roster = storage.load_json(root / "characters" / "roster.json", default={})
    social = storage.load_json(storage.state / "social-engine.json", default={})
    beliefs = storage.load_json(storage.state / "projections" / "beliefs.json", default={})
    character = _character(roster, actor_id)
    if character is None:
        raise InspectorNotFoundError("actor is not in the roster")
    motive = social.get("motives", {}).get(actor_id, {}) if isinstance(social, dict) else {}
    rows = beliefs.get("beliefs", {}) if isinstance(beliefs, dict) else {}
    belief_rows = []
    for belief in rows.values() if isinstance(rows, dict) else []:
        if isinstance(belief, dict) and belief.get("actorId") == actor_id:
            belief_rows.append(_belief_view(belief))
    belief_rows.sort(key=lambda row: row["subject"])
    team_groups = []
    for team_id in character.get("teamIds", []):
        if isinstance(team_id, str):
            team_groups.extend(groups_for_team(roster, team_id))
    return {
        "actorId": actor_id,
        "label": character.get("name", actor_id),
        "role": character.get("role", "community"),
        "teamIds": list(character.get("teamIds", [])),
        "captainOf": list(character.get("captainOf", [])),
        "affinityGroups": team_groups,
        "motives": {
            "play": motive.get("desireToPlay"),
            "competition": motive.get("competition"),
            "curiosity": motive.get("curiosity"),
            "frustration": motive.get("frustration"),
        },
        "beliefs": belief_rows,
        "source": "local projections",
    }


def booking_plan_inspector(root: Path, booking_plan_id: str) -> Dict[str, Any]:
    storage = Storage(root)
    projection = storage.load_json(storage.state / "projections" / "booking-plans.json", default={})
    rows = projection.get("bookingPlans", {}) if isinstance(projection, dict) else {}
    row = rows.get(booking_plan_id) if isinstance(rows, dict) else None
    if not isinstance(row, dict):
        raise InspectorNotFoundError("booking plan is not projected")
    plan = row.get("plan")
    if not isinstance(plan, dict):
        raise InspectorNotFoundError("booking plan is malformed")
    # Copy only the contract fields; the projection remains the canonical source.
    return {
        "bookingPlanId": row.get("bookingPlanId"),
        "stage": row.get("stage", "unknown"),
        "occurrenceKey": plan.get("occurrenceKey"),
        "participants": list(plan.get("participantActorIds", [])),
        "ownerActorId": plan.get("ownerActorId"),
        "payerActorId": plan.get("payerActorId"),
        "venueRef": plan.get("venueRef"),
        "window": deepcopy(plan.get("window")),
        "requestedFunding": deepcopy(plan.get("requestedFunding")),
        "quote": _quote_view(row.get("quote")),
        "comparison": deepcopy(row.get("comparison")),
        "payerApproval": _payer_approval(storage, booking_plan_id),
        "execution": deepcopy(row.get("execution")),
        "lastReducedFactId": row.get("lastReducedFactId"),
        "source": "local projections",
    }


def assess_booking_plan(root: Path, booking_plan_id: str, assessed_at: str) -> Dict[str, Any]:
    """Derive a comparison from existing safe projections without a product call."""
    storage = Storage(root)
    projected = booking_plan_inspector(root, booking_plan_id)
    if not isinstance(projected.get("quote"), dict):
        raise InspectorNotFoundError("booking plan has no observed quote to assess")
    quote = projected["quote"]
    evidence = {
        "schemaVersion": 1,
        "evidenceId": quote.get("evidenceRef"),
        "bookingPlanId": booking_plan_id,
        "observedAt": quote.get("observedAt"),
        "result": quote.get("status"),
        "errorCode": quote.get("errorCode"),
        "fields": quote.get("fields"),
        "source": {"kind": "preview_booking_quote", "readOnly": True},
    }
    plan_projection = storage.load_json(storage.state / "projections" / "booking-plans.json", default={})
    row = plan_projection.get("bookingPlans", {}).get(booking_plan_id) if isinstance(plan_projection, dict) else None
    if not isinstance(row, dict) or not isinstance(row.get("plan"), dict):
        raise InspectorNotFoundError("booking plan is malformed")
    beliefs = storage.load_json(storage.state / "projections" / "beliefs.json", default={})
    belief_rows = list(beliefs.get("beliefs", {}).values()) if isinstance(beliefs, dict) and isinstance(beliefs.get("beliefs"), dict) else []
    return compare_quote_to_payer_beliefs(
        booking_plan=row["plan"], quote_evidence=evidence,
        beliefs=belief_rows, assessed_at=assessed_at,
    )


def inspection_index(root: Path) -> Dict[str, Any]:
    """Small navigable index; details remain available through the inspectors."""
    storage = Storage(root)
    roster = storage.load_json(root / "characters" / "roster.json", default={})
    projection = storage.load_json(storage.state / "projections" / "booking-plans.json", default={})
    characters = roster.get("characters", []) if isinstance(roster, dict) else []
    people = []
    for row in characters:
        if isinstance(row, dict) and isinstance(row.get("actorId"), str):
            people.append({"actorId": row["actorId"], "label": row.get("name", row["actorId"])})
    plans = projection.get("bookingPlans", {}) if isinstance(projection, dict) else {}
    plan_rows = []
    for plan_id, row in plans.items() if isinstance(plans, dict) else []:
        if isinstance(row, dict):
            plan_rows.append({
                "bookingPlanId": plan_id,
                "stage": row.get("stage", "unknown"),
                "occurrenceKey": row.get("plan", {}).get("occurrenceKey") if isinstance(row.get("plan"), dict) else None,
            })
    return {"players": sorted(people, key=lambda row: row["label"]), "bookingPlans": sorted(plan_rows, key=lambda row: row["bookingPlanId"])}


def _character(roster: Any, actor_id: str) -> Optional[Dict[str, Any]]:
    if not isinstance(roster, dict):
        return None
    for row in roster.get("characters", []):
        if isinstance(row, dict) and row.get("actorId") == actor_id:
            return row
    return None


def _belief_view(belief: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "subject": belief.get("subject"),
        "status": belief.get("status", "unknown"),
        "expectedValue": deepcopy(belief.get("expectedValue")),
        "observedValue": deepcopy(belief.get("observedValue")),
        "sourceEvidenceRef": belief.get("sourceEvidenceRef"),
        "observedAt": belief.get("observedAt"),
        "expiresAt": belief.get("expiresAt"),
    }


def _quote_view(quote: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(quote, dict):
        return None
    return {
        "status": quote.get("status", "unknown"),
        "fields": deepcopy(quote.get("fields")),
        "errorCode": quote.get("errorCode"),
        "observedAt": quote.get("observedAt"),
        "evidenceRef": quote.get("evidenceRef"),
    }


def _payer_approval(storage: Storage, booking_plan_id: str) -> Optional[Dict[str, Any]]:
    projection = storage.load_json(storage.state / "projections" / "commitments.json", default={})
    commitments = projection.get("commitments", {}) if isinstance(projection, dict) else {}
    matches = []
    for row in commitments.values() if isinstance(commitments, dict) else []:
        content = row.get("content") if isinstance(row, dict) else None
        if isinstance(content, dict) and row.get("kind") == "payer_approval" and content.get("bookingPlanId") == booking_plan_id:
            matches.append(row)
    if not matches:
        return None
    row = sorted(matches, key=lambda item: str(item.get("proposedAt") or ""))[-1]
    return {"status": row.get("status"), "payerActorId": row.get("debtorActorId"), "quoteEvidenceRef": row.get("content", {}).get("quoteEvidenceRef"), "expiresAt": row.get("expiresAt"), "commitmentId": row.get("commitmentId")}
