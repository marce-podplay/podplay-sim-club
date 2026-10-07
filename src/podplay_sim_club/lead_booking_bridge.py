"""Bridge a socially agreed intent to the existing guarded Preview adapters.

The Rally Engine owns *selection* of a single durable agreement.  This module
owns the deliberately small transition from that agreement to a live Preview
plan or, when an operator has supplied the exact target confirmation, to the
existing player-owned booking adapter.  It never decides who should play and
does not manufacture invitations, acceptance, attendance, or results.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .booking_intents import load_intent_ledger
from .storage import Storage
from .time import isoformat


STATE_FILE = "lead-booking-bridge.json"


def advance(
    root: Path,
    task: Dict[str, Any],
    *,
    now: datetime,
    plan_preview: bool = False,
    apply_preview: bool = False,
    confirmation: Optional[str] = None,
    planner: Optional[Callable[[Path, str, bool], int]] = None,
    training_booker: Optional[Callable[[Path, bool, Optional[str], str], int]] = None,
    team_booker: Optional[Callable[[Path, bool, Optional[str], str], int]] = None,
) -> Dict[str, Any]:
    """Advance at most one agreed booking task.

    Planning is an authenticated read/evaluation and records a durable
    ``preview_ready`` or ``preview_blocked`` intent state.  Applying requires
    the caller to opt in *and* pass the exact Preview origin through the
    existing write policy.  This separation makes an unattended Rally tick
    informative without granting it an implicit product write capability.
    """
    if not isinstance(task, dict) or task.get("action") == "pass":
        return {"action": "pass", "detail": "no socially agreed booking awaits the lead bridge", "remoteWrites": 0}
    intent_id = task.get("intentId")
    if not isinstance(intent_id, str) or not intent_id:
        return {"action": "pass", "detail": "booking task has no durable intent ID", "remoteWrites": 0}

    # Import lazily: cli imports Rally Engine, while this bridge is called by
    # the engine only after module initialization has completed.
    if planner is None or training_booker is None or team_booker is None:
        from .cli import book_preview_match, plan_preview_intent, signup_preview_team_challenge

        planner = planner or plan_preview_intent
        training_booker = training_booker or book_preview_match
        team_booker = team_booker or signup_preview_team_challenge

    action = str(task.get("action"))
    journey = _journey(root, intent_id)
    if journey not in {"customer_training", "customer_team_signup"}:
        return _record(root, now, intent_id, journey, "pass", "intent journey is not eligible for the lead booking bridge")

    if action in {"plan_training_booking", "plan_team_signup_booking"}:
        if not plan_preview and not apply_preview:
            return _record(
                root, now, intent_id, journey, "awaiting_live_plan",
                "social agreement is durable; live Preview availability remains an explicit lead planning step",
            )
        assert planner is not None
        exit_code = planner(root, intent_id, False)
        current = _intent(root, intent_id)
        status = current.get("status") if isinstance(current, dict) else None
        if exit_code == 0 and status == "preview_ready":
            return _record(
                root, now, intent_id, journey, "preview_plan_ready",
                "live W37 availability and booking evaluation are ready; an explicitly confirmed apply tick may book",
            )
        blocker = _blocker(current)
        return _record(
            root, now, intent_id, journey, "preview_plan_blocked",
            blocker or "live availability or booking evaluation did not produce a legal plan",
        )

    if action in {"apply_training_booking", "apply_team_signup_booking"}:
        if not apply_preview:
            return _record(
                root, now, intent_id, journey, "awaiting_preview_apply",
                "a verified Preview plan exists; booking waits for an apply tick with the exact target confirmation",
            )
        if not isinstance(confirmation, str) or not confirmation.strip():
            return _record(root, now, intent_id, journey, "apply_refused", "apply mode requires the exact Preview target confirmation")
        assert training_booker is not None and team_booker is not None
        exit_code = (
            training_booker(root, True, confirmation, intent_id)
            if journey == "customer_training"
            else team_booker(root, True, confirmation, intent_id)
        )
        current = _intent(root, intent_id)
        status = current.get("status") if isinstance(current, dict) else None
        if exit_code == 0 and status in {"booked", "team_signup_in_progress", "team_members_registered"}:
            return _record(
                root, now, intent_id, journey, "booking_read_back",
                "player-owned booking adapter completed and persisted its Preview read-back",
                remote_writes=int(current.get("remoteWrites", 0)),
            )
        return _record(root, now, intent_id, journey, "apply_failed", _blocker(current) or "guarded Preview booking adapter did not complete")

    return _record(root, now, intent_id, journey, "pass", f"unsupported lead booking task {action!r}")


def _intent(root: Path, intent_id: str) -> Dict[str, Any]:
    value = load_intent_ledger(Storage(root))["intents"].get(intent_id)
    return value if isinstance(value, dict) else {}


def _journey(root: Path, intent_id: str) -> Optional[str]:
    value = _intent(root, intent_id).get("journey")
    return value if isinstance(value, str) else None


def _blocker(intent: Dict[str, Any]) -> Optional[str]:
    plan = intent.get("previewPlan")
    blocker = plan.get("blocker") if isinstance(plan, dict) else None
    return blocker if isinstance(blocker, str) and blocker else None


def _record(
    root: Path,
    now: datetime,
    intent_id: str,
    journey: Optional[str],
    action: str,
    detail: str,
    *,
    remote_writes: int = 0,
) -> Dict[str, Any]:
    storage = Storage(root)
    row = {
        "observedAt": isoformat(now), "intentId": intent_id, "journey": journey,
        "action": action, "detail": detail, "remoteWrites": remote_writes,
    }
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        attempts = state.get("attempts", []) if isinstance(state, dict) else []
        attempts = attempts if isinstance(attempts, list) else []
        storage.write_json(storage.state / STATE_FILE, {
            "schemaVersion": 1, "lastAttempt": row, "attempts": (attempts + [row])[-100:],
        })
    return {"actorId": "lead", **row}
