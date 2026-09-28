"""Bounded Rally Engine session bookkeeping around a scenario runtime."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from .storage import Storage
from .time import isoformat, parse_instant
from .tournament import run_tick as run_tournament_tick
from .booking_intents import load_intent_ledger


STATE_FILE = "rally-engine.json"


def tick(root: Path, hours: int = 2, now: Optional[datetime] = None) -> Dict[str, Any]:
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
            return {"status": "complete", "state": state, "remoteWrites": 0}
        if parse_instant(state["endsAt"]) <= instant:
            state.update({"status": "complete", "completedAt": isoformat(instant), "lastAction": "session window ended"})
            storage.write_json(storage.state / STATE_FILE, state)
            _write_run_summary(storage, state)
            return {"status": "complete", "state": state, "remoteWrites": 0}
        storage.write_json(storage.state / STATE_FILE, state)
    result = run_tournament_tick(root, now=instant)
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default=state)
        state["ticks"] = int(state.get("ticks", 0)) + 1
        state["lastTickAt"] = isoformat(instant)
        state["lastAction"] = result["action"]
        storage.write_json(storage.state / STATE_FILE, state)
    return {"status": "active", "state": state, "action": result["action"], "remoteWrites": 0}


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
