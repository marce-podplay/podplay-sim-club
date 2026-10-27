"""Riley's non-coercive watch and blocker-routing over community initiatives.

The front desk may acknowledge that an agreed initiative is being followed,
but this is deliberately not approval, booking authority, or a substitute for
a named player's decision.  One acknowledgement is emitted for each meaningful
initiative-state change so the channel stays useful rather than becoming a
stream of reminders.  A routed follow-up is a request for the *next* named
person to consider their own action; it never supplies their agreement or
performs a product operation.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .booking_intents import load_intent_ledger
from .booking_plans import make_comparison_fact
from .facts import append_fact
from .inspectors import InspectorNotFoundError, assess_booking_plan
from .payer_approval import PayerApprovalError, request as request_payer_approval
from .projections import rebuild_projections
from .storage import Storage
from .time import isoformat, parse_instant


STATE_FILE = "staff-follow-up.json"
WATCHED_STATUSES = {"agreed", "preview_ready", "planned", "awaiting_payer_approval", "booked_browser_observed"}


def next_task(root: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Return one durable, secret-free Riley follow-up task without writing.

    The engine uses this to hand work to the staff-worker lane.  It is only a
    recommendation: routing the message remains a later Riley worker action.
    """
    storage = Storage(root)
    ledger = load_intent_ledger(storage)
    state = storage.load_json(storage.state / STATE_FILE, default={})
    watched = state.get("watched", {}) if isinstance(state, dict) else {}
    watched = watched if isinstance(watched, dict) else {}
    review_required = _review_required_intents(storage)
    candidates = []
    for intent_id, intent in ledger.get("intents", {}).items():
        if not isinstance(intent_id, str) or not isinstance(intent, dict):
            continue
        status = str(intent.get("status") or "unknown")
        if status not in WATCHED_STATUSES:
            continue
        next_action = "coordinator_review_observed_browser_journey" if intent_id in review_required else str(intent.get("nextAction") or "await a named decision")
        signature = f"v4:{status}:{next_action}" if intent_id in review_required else f"v3:{status}:{next_action}"
        if watched.get(intent_id, {}).get("signature") != signature:
            urgency = 2 if intent_id in review_required else (1 if status == "preview_ready" else 0)
            candidates.append((urgency, str(intent.get("createdAt") or ""), intent_id, intent, signature, next_action))
    if not candidates:
        return {"action": "pass", "detail": "all actionable initiatives already have a current staff watch", "remoteWrites": 0}
    _, _, intent_id, intent, signature, next_action = sorted(candidates, reverse=True)[0]
    route = _blocker_route(intent, next_action)
    return {
        "action": "route_staff_initiative", "actorId": "riley", "intentId": intent_id,
        "signature": signature, "nextAction": next_action, "staffAction": route["staffAction"],
        "requestedFrom": route["requestedFrom"], "blocker": route["blocker"],
        "detail": route["text"], "remoteWrites": 0,
    }


def tick(root: Path, now: Optional[datetime] = None, *, intent_id: Optional[str] = None) -> Dict[str, Any]:
    """Acknowledge one newly actionable initiative for Riley's follow-up queue."""
    storage = Storage(root)
    observed_at = isoformat(now or parse_instant(None))
    requested_intent_id = intent_id
    payer_request = None
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        state = storage.load_json(storage.state / STATE_FILE, default={})
        watched = state.get("watched", {}) if isinstance(state, dict) else {}
        if not isinstance(watched, dict):
            watched = {}
        review_required = _review_required_intents(storage)
        candidates = []
        for candidate_intent_id, intent in ledger.get("intents", {}).items():
            if not isinstance(candidate_intent_id, str) or not isinstance(intent, dict):
                continue
            status = str(intent.get("status") or "unknown")
            if status not in WATCHED_STATUSES:
                continue
            next_action = (
                "coordinator_review_observed_browser_journey"
                if candidate_intent_id in review_required
                else str(intent.get("nextAction") or "await a named decision")
            )
            # Bump the message contract when routing semantics improve so an
            # existing watch can receive one updated, more useful follow-up.
            signature = f"v4:{status}:{next_action}" if candidate_intent_id in review_required else f"v3:{status}:{next_action}"
            if watched.get(candidate_intent_id, {}).get("signature") != signature:
                urgency = 2 if candidate_intent_id in review_required else (1 if status == "preview_ready" else 0)
                candidates.append((urgency, str(intent.get("createdAt") or ""), candidate_intent_id, intent, signature))
        if requested_intent_id is not None:
            candidates = [candidate for candidate in candidates if candidate[2] == requested_intent_id]
        if not candidates:
            storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "lastTickAt": observed_at, "watched": watched})
            return {"actorId": "riley", "action": "pass", "detail": "all actionable initiatives already have a current staff watch", "remoteWrites": 0}
        _, _, intent_id, intent, signature = sorted(candidates, reverse=True)[0]
        participants = [actor for actor in intent.get("participants", []) if isinstance(actor, str)]
        next_action = (
            "coordinator_review_observed_browser_journey"
            if intent_id in review_required
            else str(intent.get("nextAction") or "await a named decision")
        )
        route = _blocker_route(intent, next_action)
        watched[intent_id] = {
            "signature": signature,
            "status": str(intent.get("status")),
            "nextAction": next_action,
            "observedAt": observed_at,
            "blocker": route["blocker"],
            "requestedFrom": route["requestedFrom"],
            "staffAction": route["staffAction"],
        }
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "lastTickAt": observed_at, "watched": watched})
        storage.append_jsonl(storage.channel_path("club"), {
            "id": f"staff-follow-up:{intent_id}:{signature}", "channel": "club",
            "kind": "staff_initiative_follow_up", "from": "riley", "to": participants,
            "intentId": intent_id, "observedAt": observed_at,
            "text": f"Riley is tracking this initiative. Next operational step: {next_action}. Any player or payer decision remains with the named person.",
            "remoteWrites": 0,
        })
        storage.append_jsonl(storage.channel_path("staff-planning"), {
            "id": f"staff-blocker-route:{intent_id}:{signature}",
            "channel": "staff-planning", "kind": "staff_blocker_route",
            "from": "riley", "to": route["requestedFrom"],
            "intentId": intent_id, "observedAt": observed_at,
            "blocker": route["blocker"], "staffAction": route["staffAction"],
            "text": route["text"], "remoteWrites": 0,
        })
        payer_request = _payer_request(intent, route)
    # Requesting an accept-or-pass decision is a local protocol message, not
    # an approval.  It deliberately occurs after the staff-watch transaction
    # because the protocol writer owns its own lock.
    payer_request_result = None
    if payer_request:
        try:
            # Riley's operational tool first records the mechanical
            # expectation comparison from already-observed quote evidence.
            # That is not a price claim or an approval; it simply tells the
            # payer whether the displayed terms meet their own known bounds.
            comparison = assess_booking_plan(root, payer_request, observed_at)
            append_fact(root, make_comparison_fact(
                comparison=comparison, occurred_at=observed_at,
                correlation_id=f"journey:{payer_request}",
            ))
            rebuild_projections(root)
            payer_request_result = request_payer_approval(root, payer_request, observed_at)
        except (InspectorNotFoundError, PayerApprovalError, ValueError):
            # Quotes can age out between the planning observation and this
            # message.  Staff then leave the initiative visible for a fresh
            # plan rather than asking a player to decide against stale terms.
            payer_request_result = {"status": "not_requested", "reason": "fresh_quote_required", "remoteWrites": 0}
    return {
        "actorId": "riley", "action": "route_initiative_blocker",
        "intentId": intent_id, "detail": next_action, "observedAt": observed_at,
        "blocker": route["blocker"], "requestedFrom": route["requestedFrom"],
        "staffAction": route["staffAction"], "payerRequest": payer_request_result,
        "remoteWrites": 0,
    }


def _review_required_intents(storage: Storage) -> set[str]:
    """Return intents whose reported browser worker must not be replayed.

    A worker-side ``blocked``/``pass`` receipt means the coordinator has an
    uncertainty to inspect.  It does not mean that the product rejected the
    action, nor does it give staff authority to rerun it.  This local routing
    overlay keeps the initiative card honest without changing the social
    agreement itself.
    """
    state = storage.load_json(storage.state / "worker-contracts.json", default={})
    contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
    if not isinstance(contracts, dict):
        return set()
    return {
        contract["intentId"]
        for contract in contracts.values()
        if isinstance(contract, dict)
        and contract.get("status") == "reported"
        and contract.get("outcome") in {"blocked", "pass"}
        and isinstance(contract.get("intentId"), str)
    }


def _blocker_route(intent: Dict[str, Any], next_action: str) -> Dict[str, Any]:
    """Describe the least-authoritative human follow-up for an initiative.

    This maps a durable workflow gate to an addressed message.  The mapping
    intentionally has no "auto-resolve" path: a plan still needs the lead's
    read-only availability action, while a payment or arrival still needs the
    named player to choose.
    """
    booker = intent.get("bookerActorId")
    participants = [actor for actor in intent.get("participants", []) if isinstance(actor, str)]
    if next_action == "coordinator_review_observed_browser_journey":
        return {
            "blocker": "observed_browser_journey_requires_review",
            "requestedFrom": ["lead"],
            "staffAction": "request_coordinator_evidence_review",
            "text": "Riley asks the coordinator to review the bounded browser evidence before any retry. Staff will not infer a product failure or repeat the journey.",
        }
    if "plan_live" in next_action or next_action.startswith("plan_"):
        return {
            "blocker": "live_availability_not_read",
            "requestedFrom": ["lead"],
            "staffAction": "request_bounded_availability_plan",
            "text": "Riley asks the lead to run one bounded availability-and-quote read. This is planning only; it creates no booking and no player decision.",
        }
    if "capture" in next_action and "invitation" in next_action:
        recipient = booker if isinstance(booker, str) else (participants[0] if participants else "lead")
        return {
            "blocker": "owner_invitation_handoff_pending",
            "requestedFrom": [recipient],
            "staffAction": "request_visible_invitation_capture",
            "text": "Riley sees a browser-confirmed booking. The named owner may visibly generate or recover the player invitation; staff cannot accept it for anyone.",
        }
    plan = intent.get("previewPlan") if isinstance(intent.get("previewPlan"), dict) else {}
    if intent.get("status") == "preview_ready" and isinstance(plan.get("bookingPlanId"), str):
        recipient = booker if isinstance(booker, str) else (participants[0] if participants else "lead")
        return {
            "blocker": "named_payer_decision_pending",
            "requestedFrom": [recipient],
            "staffAction": "request_payer_accept_or_pass",
            "text": "Riley has a fresh expectation-matched quote and asks the named payer for an explicit accept or pass. Staff cannot approve it for them.",
        }
    if "payer" in next_action:
        recipient = booker if isinstance(booker, str) else (participants[0] if participants else "lead")
        return {
            "blocker": "named_payer_decision_pending",
            "requestedFrom": [recipient],
            "staffAction": "request_accept_or_pass",
            "text": "Riley asks the named payer to accept or pass the current quote. Silence is not approval, and staff cannot decide on their behalf.",
        }
    if "apply" in next_action or "booking" in next_action:
        return {
            "blocker": "guarded_booking_apply_pending",
            "requestedFrom": ["lead"],
            "staffAction": "request_guarded_apply_review",
            "text": "Riley asks the lead to review whether the fresh plan and named approvals still permit a guarded apply. This note does not authorize a booking.",
        }
    return {
        "blocker": "named_operational_step_pending",
        "requestedFrom": participants or ["lead"],
        "staffAction": "request_named_follow_up",
        "text": "Riley has surfaced the remaining named operational step. The addressed person may accept, pass, or leave it pending.",
    }


def _payer_request(intent: Dict[str, Any], route: Dict[str, Any]) -> Optional[str]:
    """Return a plan eligible for a fresh payer request, if this is that gate."""
    if route.get("staffAction") != "request_payer_accept_or_pass":
        return None
    plan = intent.get("previewPlan")
    plan_id = plan.get("bookingPlanId") if isinstance(plan, dict) else None
    return plan_id if isinstance(plan_id, str) and plan_id else None
