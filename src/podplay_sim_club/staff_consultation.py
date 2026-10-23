"""Non-coercive, local-only consultation plans for staff and partners.

This is a communication primitive, not a workflow executor.  A plan gives a
named person enough context to choose accept, pass, or request changes.  An
acceptance means only "continue planning"; it never grants product authority
or changes Preview configuration.
"""

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Dict

from .facts import reject_private_data
from .configuration_experiments import EXPERIMENT_ID, experiment_snapshot
from .storage import Storage
from .time import isoformat


STATE_FILE = "staff-consultations.json"
CHANNEL = "staff-planning"
STAFF = {"sofia", "riley"}
ROLE_LABELS = {"podplay-admin", "lead", "big-brother", "admin"}
ACTOR_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


class StaffConsultationError(ValueError):
    pass


def propose_rate_cards_plan(root: Path, *, proposer: str, recipient: str) -> Dict[str, Any]:
    """Offer a reversible, pod-scoped Rate Cards research plan to a named person."""
    if proposer not in STAFF:
        raise StaffConsultationError("only Sofia or Riley may propose a staff consultation")
    _named_person(recipient)
    storage = Storage(root)
    plan_id = f"consultation:rate-cards-pod:{recipient}"
    with storage.writer_lock():
        state = _load(storage)
        plan = state["plans"].get(plan_id)
        if not isinstance(plan, dict):
            observed_at = _now()
            plan = {
                "planId": plan_id,
                "planType": "rate_cards_pod_activation_research",
                "status": "addressed",
                "proposerActorId": proposer,
                "recipientActorId": recipient,
                "createdAt": observed_at,
                "decision": None,
                "purpose": "learn whether one selected experience can safely use Unified Pricing while Legacy experiences remain unchanged",
                "whyThisIsLowRisk": [
                    "pod is the narrowest supported Booking Engine override scope",
                    "a pod may remain Legacy while another pod uses Unified",
                    "the plan begins with read-backs and can stop before any product change",
                ],
                "codeDerivedFacts": _rate_card_code_facts(),
                "proposedWorkflow": [
                    {"phase": "1", "action": "verify the selected pod's effective booking engine, rate card, and activation readiness", "decision": "stop if readiness is not sufficient"},
                    {"phase": "2", "action": "if you support planning, choose pod scope or request a different scope", "decision": "no configuration occurs from this response"},
                    {"phase": "3", "action": "an authorized real product administrator separately decides whether any configuration change is appropriate", "decision": "read back configuration again before any player journey"},
                    {"phase": "4", "action": "named players may later accept or pass a bounded price-verification journey", "decision": "only after an effective configuration and declared prediction exist"},
                ],
                "responseOptions": ["accept", "pass", "request_changes"],
                "acceptMeans": "continue planning only; it is not approval to configure, publish a rate card, invite players, or claim a price",
                "remoteWrites": 0,
            }
            reject_private_data(plan, "staffConsultationPlan")
            state["plans"][plan_id] = plan
            _append_once(storage, {
                "id": plan_id, "channel": CHANNEL, "kind": "staff_consultation_plan",
                "from": proposer, "to": [recipient], "planType": plan["planType"],
                "text": "I propose a reversible, pod-scoped Rate Cards research plan. Please accept, pass, or request changes. An acceptance continues planning only and cannot change product configuration.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            storage.write_json(storage.state / STATE_FILE, state)
        elif "codeDerivedFacts" not in plan:
            # A later code review may enrich an already-addressed plan, but it
            # cannot alter its recipient, decision, or status.
            plan["codeDerivedFacts"] = _rate_card_code_facts()
            reject_private_data(plan, "staffConsultationPlan")
            storage.write_json(storage.state / STATE_FILE, state)
        return _public(plan)


def attach_rate_cards_scope_readback(root: Path, *, actor_id: str) -> Dict[str, Any]:
    """Give the plan recipient a safe live-readback supplement.

    The handoff deliberately contains only normalized enum/absence facts. It
    does not let the recipient infer that a setting is changeable or that a
    price journey is ready.
    """
    if actor_id not in STAFF:
        raise StaffConsultationError("only Sofia or Riley may attach a scope read-back")
    experiment = next(
        (row for row in experiment_snapshot(root)["experiments"] if row.get("experimentId") == EXPERIMENT_ID),
        None,
    )
    scope = experiment.get("scopeDiscovery") if isinstance(experiment, dict) else None
    if not isinstance(scope, dict):
        raise StaffConsultationError("a Rate Cards scope-path read-back is required")
    safe_scope = {
        key: scope.get(key, "unknown")
        for key in ("tenantBaseline", "areaOverride", "areaEffective", "podOverride", "podEffective")
    }
    if any(value not in {"LEGACY", "UNIFIED", "absent", "unknown"} for value in safe_scope.values()):
        raise StaffConsultationError("scope read-back is invalid")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        plans = [plan for plan in state["plans"].values() if isinstance(plan, dict) and plan.get("planType") == "rate_cards_pod_activation_research"]
        if not plans:
            raise StaffConsultationError("a Rate Cards consultation must be addressed before adding read-back evidence")
        observed_at = _now()
        for plan in plans:
            if plan.get("liveScopeReadback") == safe_scope:
                continue
            plan["liveScopeReadback"] = safe_scope
            plan.setdefault("codeDerivedFacts", _rate_card_code_facts())
            reject_private_data(plan, "staffConsultationPlan")
            _append_once(storage, {
                "id": f"{plan['planId']}:scope-readback", "channel": CHANNEL, "kind": "staff_consultation_evidence",
                "from": actor_id, "to": [plan["recipientActorId"]], "planId": plan["planId"],
                "text": "Live scope read-back is attached as normalized settings facts. It confirms neither configuration approval nor player readiness.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
        storage.write_json(storage.state / STATE_FILE, state)
        return _public(plans[0])


def respond(root: Path, *, plan_id: str, actor_id: str, decision: str, note: str = "") -> Dict[str, Any]:
    """Record the named recipient's explicit plan decision without actioning it."""
    if decision not in {"accept", "pass", "request_changes"}:
        raise StaffConsultationError("consultation decision must be accept, pass, or request_changes")
    safe_note = _safe_note(note)
    if decision == "request_changes" and not safe_note:
        raise StaffConsultationError("a request_changes response needs a concise explanation")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        plan = state["plans"].get(plan_id)
        if not isinstance(plan, dict):
            raise StaffConsultationError("staff consultation is unavailable")
        if actor_id != plan["recipientActorId"]:
            raise StaffConsultationError("only the named consultation recipient may respond")
        if plan["decision"] is None:
            observed_at = _now()
            response = {"actorId": actor_id, "value": decision, "decidedAt": observed_at, "note": safe_note or None}
            plan["decision"] = response
            plan.setdefault("history", []).append(response)
            plan["status"] = {
                "accept": "accepted_for_planning_only",
                "pass": "passed",
                "request_changes": "changes_requested",
            }[decision]
            _append_once(storage, {
                "id": f"{plan_id}:response", "channel": CHANNEL, "kind": "staff_consultation_response",
                "from": actor_id, "to": [plan["proposerActorId"]], "planId": plan_id,
                "decision": decision, "note": safe_note or None,
                "text": "Consultation response recorded. This decision does not perform or authorize a Preview product action.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            storage.write_json(storage.state / STATE_FILE, state)
        return _public(plan)


def revise_rate_cards_plan(root: Path, *, proposer: str, plan_id: str, note: str) -> Dict[str, Any]:
    """Reply to a named recipient's change request without overriding it."""
    if proposer not in STAFF:
        raise StaffConsultationError("only Sofia or Riley may revise a consultation plan")
    safe_note = _safe_note(note)
    if not safe_note:
        raise StaffConsultationError("a revision needs a concise explanation")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        plan = state["plans"].get(plan_id)
        if not isinstance(plan, dict) or plan.get("planType") != "rate_cards_pod_activation_research":
            raise StaffConsultationError("Rate Cards consultation is unavailable")
        if plan.get("proposerActorId") != proposer:
            raise StaffConsultationError("only the original proposer may revise this plan")
        if plan.get("status") != "changes_requested":
            raise StaffConsultationError("a revision requires the recipient to request changes first")
        observed_at = _now()
        revision = {"actorId": proposer, "revisedAt": observed_at, "note": safe_note}
        plan.setdefault("revisions", []).append(revision)
        plan["decision"] = None
        plan["status"] = "addressed_revised"
        plan["proposedWorkflow"][1] = {
            "phase": "2",
            "action": "ask an authorized product administrator to provide an approved Rate Card pricing basis; do not invent amounts from this consultation",
            "decision": "stop until the pricing basis is explicit",
        }
        reject_private_data(plan, "staffConsultationPlan")
        _append_once(storage, {
            "id": f"{plan_id}:revision:{len(plan['revisions'])}", "channel": CHANNEL, "kind": "staff_consultation_revision",
            "from": proposer, "to": [plan["recipientActorId"]], "planId": plan_id,
            "text": "Riley revised the plan in response to the request for changes. Please accept, pass, or request changes again; no product action is authorized.",
            "note": safe_note, "observedAt": observed_at, "remoteWrites": 0,
        })
        storage.write_json(storage.state / STATE_FILE, state)
        return _public(plan)


def snapshot(root: Path) -> Dict[str, Any]:
    plans = [_public(plan) for plan in _load(Storage(root))["plans"].values() if isinstance(plan, dict)]
    return {"channel": CHANNEL, "plans": sorted(plans, key=lambda plan: plan["planId"]), "remoteWrites": 0}


def _load(storage: Storage) -> Dict[str, Any]:
    state = storage.load_json(storage.state / STATE_FILE, default={"schemaVersion": 1, "plans": {}})
    if not isinstance(state, dict) or state.get("schemaVersion") != 1 or not isinstance(state.get("plans"), dict):
        raise StaffConsultationError("staff consultation state is invalid")
    return state


def _named_person(actor_id: str) -> None:
    if not isinstance(actor_id, str) or not ACTOR_PATTERN.fullmatch(actor_id) or actor_id in ROLE_LABELS:
        raise StaffConsultationError("consultation recipient must be a named person, not a role label")


def _safe_note(note: str) -> str:
    if not isinstance(note, str) or len(note.strip()) > 280:
        raise StaffConsultationError("consultation note must be at most 280 characters")
    value = note.strip()
    if re.search(r"https?://|\b(?:password|token|credential|secret)\b", value, flags=re.IGNORECASE):
        raise StaffConsultationError("consultation note must not contain a private reference")
    reject_private_data({"note": value}, "staffConsultationNote")
    return value


def _rate_card_code_facts() -> Dict[str, Any]:
    return {
        "bookingEngineOverrideScopes": ["region", "area", "pod"],
        "narrowestScope": "pod",
        "coexistence": "a pod can use Unified while another remains Legacy",
        "unifiedGuard": "the product checks for a usable rate card and pricing schedule before a Unified switch",
        "source": "reviewed product code; live target confirmation remains required",
    }


def _append_once(storage: Storage, row: Dict[str, Any]) -> None:
    path = storage.channel_path(CHANNEL)
    if not any(existing.get("id") == row["id"] for existing in storage.read_jsonl(path)):
        storage.append_jsonl(path, row)


def _public(plan: Dict[str, Any]) -> Dict[str, Any]:
    value = dict(plan)
    reject_private_data(value, "staffConsultationPublic")
    return value


def _now() -> str:
    return isoformat(datetime.now(timezone.utc))
