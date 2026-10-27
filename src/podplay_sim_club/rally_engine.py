"""Bounded Rally Engine session bookkeeping around a scenario runtime."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from .storage import Storage
from .time import isoformat, parse_instant
from .tournament import run_tick as run_tournament_tick
from .booking_intents import load_intent_ledger
from .social_engine import tick as run_social_tick
from .front_desk import tick as run_front_desk_tick
from .event_observation import observe_finished_events, observe_finished_training_sessions
from .arcade import attach_completed_training_replay, attach_ended_active_session_replay
from .result_requests import request_finished_event_result
from .owner_engagement import tick as run_owner_engagement_tick
from .club_rounds import tick as run_club_round
from .training_sessions import next_booking_task
from .team_challenges import next_team_signup_task
from .share_invitations import next_share_invitation_task
from .check_in_bridge import next_task as next_check_in_task
from .local_evidence import capture_live_play_receipts
from .active_sessions import tick as tick_active_sessions
from .staff_follow_up import next_task as next_staff_follow_up_task
from .staff_worker_dispatch import queue as queue_staff_worker_contract
from .journey_policy import dispatch as dispatch_browser_journey, pending_or_new as browser_work_pending_or_new
from .worker_dispatch import queue as queue_worker_contract


STATE_FILE = "rally-engine.json"


def tick(
    root: Path,
    hours: int = 2,
    now: Optional[datetime] = None,
    *,
    plan_preview: bool = False,
    apply_preview: bool = False,
    confirmation: Optional[str] = None,
) -> Dict[str, Any]:
    if not 1 <= hours <= 24:
        raise ValueError("hours must be between 1 and 24")
    storage = Storage(root)
    scenario = storage.load_json(root / "scenarios" / "rally-engine.json")
    if not isinstance(scenario, dict) or scenario.get("schemaVersion") != 1:
        raise ValueError("Rally Engine scenario is invalid")
    instant = now or parse_instant(None)
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default=None)
        if not isinstance(state, dict):
            state = {"schemaVersion": 1, "id": scenario["id"], "startedAt": isoformat(instant), "endsAt": isoformat(instant + timedelta(hours=hours)), "ticks": 0, "status": "active"}
        if state.get("status") == "complete":
            _write_run_summary(storage, state)
            state.update({"status": "monitoring", "resumedAt": isoformat(instant), "lastAction": "tournament monitoring resumed"})
        if state.get("status") == "active" and parse_instant(state["endsAt"]) <= instant:
            state.update({"status": "monitoring", "completedAt": isoformat(instant), "lastAction": "session window ended; tournament monitoring continues"})
            storage.write_json(storage.state / STATE_FILE, state)
            _write_run_summary(storage, state)
        storage.write_json(storage.state / STATE_FILE, state)
    social = run_social_tick(root, now=instant)
    staff_task = next_staff_follow_up_task(root, now=instant)
    staff_follow_up = queue_staff_worker_contract(root, task=staff_task)
    actors = scenario.get("actors", [])
    if not isinstance(actors, list) or not all(isinstance(actor_id, str) for actor_id in actors):
        raise ValueError("Rally Engine actors are invalid")
    actor_limit = int(scenario.get("maxActorTurnsPerRound", 10))
    round_limit = int(scenario.get("maxRoundsPerTick", 5))
    if not 1 <= actor_limit <= 10 or not 1 <= round_limit <= 5:
        raise ValueError("Rally Engine turn limits are invalid")
    if not actors:
        raise ValueError("Rally Engine needs at least one actor")
    rounds = []
    owner_engagement = {"actorId": "sofia", "action": "pass"}
    player = {"actorId": None, "action": "pass"}
    front_desk = {"actorId": "riley", "action": "pass"}
    cursor = int(state.get("schedulerCursor", 0)) % len(actors)
    for number in range(1, round_limit + 1):
        batch = actors[cursor:cursor + actor_limit]
        if not batch:
            cursor = 0
            break
        before = len(storage.read_jsonl(storage.channel_path("club")))
        actor_turns = []
        for actor_id in batch:
            if actor_id == "sofia":
                turn = run_owner_engagement_tick(root, now=instant)
                owner_engagement = turn
            elif actor_id == "riley":
                turn = run_front_desk_tick(root, now=instant)
                front_desk = turn
            else:
                turn = run_club_round(root, actor_id=actor_id, now=instant)
                if turn.get("action") != "pass":
                    player = turn
            actor_turns.append(turn)
            if turn.get("action") != "pass":
                social = run_social_tick(root, now=instant)
        after = len(storage.read_jsonl(storage.channel_path("club")))
        cursor += len(batch)
        if cursor >= len(actors):
            cursor = 0
            at_end_of_queue = True
        else:
            at_end_of_queue = False
        rounds.append({"number": number, "actorIds": batch, "actorTurns": actor_turns, "messageSent": after > before})
        if after == before or at_end_of_queue:
            break
    observation = observe_finished_events(root, now=instant)
    training_observation = observe_finished_training_sessions(root, now=instant)
    active_sessions = tick_active_sessions(root, now=instant)
    arcade_replay = attach_completed_training_replay(root, now=instant)
    local_arcade_replay = attach_ended_active_session_replay(root, now=instant)
    training_recovery = {
        "action": "pass",
        "detail": "missed sessions require a fresh grounded player proposal and acceptance",
        "remoteWrites": 0,
    }
    result_request = request_finished_event_result(root, now=instant)
    result = run_tournament_tick(root, now=instant)
    training = next_booking_task(root, now=instant)
    if training.get("action") != "pass":
        result = training
    team_signup = next_team_signup_task(root, now=instant)
    if team_signup.get("action") != "pass" and training.get("action") == "pass":
        result = team_signup
    if result_request.get("action") != "pass":
        result = result_request
    elif arcade_replay.get("action") != "pass":
        result = arcade_replay
    booking_task = training if training.get("action") != "pass" else team_signup
    share_task = next_share_invitation_task(root, now=instant)
    check_in_task = next_check_in_task(root, now=instant)
    # Product actions are always owned by the named Sim's rendered browser
    # journey.  A social agreement is sufficient to queue that work item, not
    # to run a Preview adapter or make a booking.  Check-in and invitation
    # work take priority over a new booking so one sequential browser actor
    # receives the next concrete, personally-owned step.
    #
    # ``plan_preview`` / ``apply_preview`` remain accepted CLI arguments for
    # compatibility with saved invocations, but this engine no longer turns
    # either flag into an integration acting for a Sim.
    booking_bridge = {"action": "pass", "detail": "booking integrations are disabled; named Sim browser work is queued instead", "remoteWrites": 0}
    share_bridge = {"action": "pass", "detail": "invitation integrations are disabled; named Sim browser work is queued instead", "remoteWrites": 0}
    check_in_bridge = {"action": "pass", "detail": "check-in integrations are disabled; named Sim browser work is queued instead", "remoteWrites": 0}
    next_task = next(
        (
            task for task in (check_in_task, share_task, booking_task)
            if browser_work_pending_or_new(root, task=task, role="player")
        ),
        {"action": "pass", "detail": "no new or pending named-Sim browser work"},
    )
    browser_bridge = dispatch_browser_journey(root, task=next_task, role="player")
    worker_contract = queue_worker_contract(root, browser_job=browser_bridge)
    if next_task.get("action") != "pass" and result_request.get("action") == "pass":
        result = worker_contract if worker_contract.get("action") != "pass" else browser_bridge
    evidence_capture = capture_live_play_receipts(root, observed_at=instant)
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default=state)
        state["ticks"] = int(state.get("ticks", 0)) + 1
        state["lastTickAt"] = isoformat(instant)
        state["lastAction"] = result["action"]
        state["schedulerCursor"] = cursor
        storage.write_json(storage.state / STATE_FILE, state)
        _write_interpretation_audit(storage, rounds, isoformat(instant))
    return {"status": state["status"], "state": state, "action": result["action"], "socialOffers": social["offers"], "rounds": rounds, "ownerEngagement": owner_engagement, "player": player, "frontDesk": front_desk, "staffTask": staff_task, "staffFollowUp": staff_follow_up, "observation": observation, "trainingObservation": training_observation, "arcadeReplay": arcade_replay, "localArcadeReplay": local_arcade_replay, "trainingRecovery": training_recovery, "resultRequest": result_request, "training": training, "teamSignup": team_signup, "bookingBridge": booking_bridge, "shareInvitationTask": share_task, "shareInvitationBridge": share_bridge, "checkInTask": check_in_task, "checkInBridge": check_in_bridge, "browserJourney": browser_bridge, "workerContract": worker_contract, "activeSessions": active_sessions, "evidenceCapture": evidence_capture, "remoteWrites": int(booking_bridge.get("remoteWrites", 0)) + int(share_bridge.get("remoteWrites", 0)) + int(check_in_bridge.get("remoteWrites", 0))}


def _write_interpretation_audit(storage: Storage, rounds: list[Dict[str, Any]], observed_at: str) -> None:
    path = storage.state / "actor-interpretations.json"
    audit = storage.load_json(path, default={})
    rows = audit.get("turns", []) if isinstance(audit, dict) else []
    for round_ in rounds:
        for turn in round_.get("actorTurns", []):
            if isinstance(turn, dict):
                rows.append({"observedAt": observed_at, "round": round_.get("number"), "actorId": turn.get("actorId"), "status": "skipped" if turn.get("action") == "pass" else "actionable", "action": turn.get("action"), "reason": turn.get("detail")})
    storage.write_json(path, {"schemaVersion": 1, "lastTickAt": observed_at, "turns": rows[-500:]})


def _write_run_summary(storage: Storage, state: Dict[str, Any]) -> None:
    """Write one immutable, secret-free audit record for a completed run."""
    safe_start = str(state.get("startedAt", "unknown")).replace(":", "-").replace("+", "_")
    path = storage.state / "runs" / f"rally-engine-{safe_start}.json"
    if path.is_file():
        return
    tournament = storage.load_json(storage.state / "tournament.json", default={})
    ledger = load_intent_ledger(storage)
    semi = tournament.get("rounds", {}).get("semi-final", {}) if isinstance(tournament, dict) else {}
    intent_id = semi.get("intentId") if isinstance(semi, dict) else None
    intent = ledger.get("intents", {}).get(intent_id, {}) if isinstance(intent_id, str) else {}
    messages = storage.read_jsonl(storage.channel_path("club"))
    summary = {
        "schemaVersion": 1,
        "engine": "Rally Engine",
        "run": state,
        "tournament": {"semiFinal": semi},
        "fixture": {key: intent.get(key) for key in ("id", "status", "participants", "constraints", "previewPlan", "event", "remoteWrites")},
        "messageKinds": [row.get("kind") for row in messages if row.get("occurrenceKey") == "tournament:bogard-japan-women-fighters-001"],
        "conclusion": "blocked_before_play" if isinstance(semi, dict) and semi.get("status") == "blocked_immediate_window" else "completed",
        "generatedAt": isoformat(parse_instant(None)),
    }
    storage.write_json(path, summary)
