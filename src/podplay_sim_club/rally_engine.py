"""Bounded Rally Engine session bookkeeping around a scenario runtime."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from .storage import Storage
from .time import isoformat, parse_instant
from .tournament import run_tick as run_tournament_tick


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
        if not isinstance(state, dict) or state.get("status") == "complete":
            state = {"schemaVersion": 1, "id": scenario["id"], "startedAt": isoformat(instant), "endsAt": isoformat(instant + timedelta(hours=hours)), "ticks": 0, "status": "active"}
        if parse_instant(state["endsAt"]) <= instant:
            state.update({"status": "complete", "completedAt": isoformat(instant), "lastAction": "session window ended"})
            storage.write_json(storage.state / STATE_FILE, state)
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
