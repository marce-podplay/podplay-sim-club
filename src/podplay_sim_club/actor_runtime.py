"""Bounded, durable actor turns that only propose local Preview Club work."""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .booking_intents import load_intent_ledger, save_intent
from .orchestrator import Orchestrator
from .storage import Storage
from .time import isoformat, parse_instant


RUNTIME_FILE = "runtime.json"


def run_tick(root: Path, max_turns: int = 10, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Run a deterministic actor queue; it never performs a preview write."""
    if not 1 <= max_turns <= 30:
        raise ValueError("max_turns must be between 1 and 30")
    storage = Storage(root)
    scenario = storage.load_json(root / "scenarios" / "actor-runtime.json")
    if not isinstance(scenario, dict) or scenario.get("schemaVersion") != 1:
        raise ValueError("actor runtime scenario is invalid")
    campaign = scenario.get("freeHourCampaign")
    actors = scenario.get("actors")
    if not isinstance(campaign, dict) or not isinstance(actors, list) or not actors:
        raise ValueError("actor runtime scenario is incomplete")
    instant = now or parse_instant(None)
    observed_at = isoformat(instant)

    with storage.writer_lock():
        world = Orchestrator(root).ensure_world(instant)
        state = storage.load_json(storage.state / RUNTIME_FILE, default=None)
        if not isinstance(state, dict):
            state = {"schemaVersion": 1, "tick": 0, "actorCursor": 0, "turns": 0}
        state["tick"] = int(state.get("tick", 0)) + 1
        turns = []
        for _ in range(max_turns):
            actor_id = actors[int(state.get("actorCursor", 0)) % len(actors)]
            state["actorCursor"] = (int(state.get("actorCursor", 0)) + 1) % len(actors)
            turn = _take_turn(storage, world, campaign, str(actor_id), observed_at)
            turns.append(turn)
            state["turns"] = int(state.get("turns", 0)) + 1
            _write_memory(storage, str(actor_id), turn)
        state["lastTickAt"] = observed_at
        state["lastTurns"] = turns
        storage.write_json(storage.state / RUNTIME_FILE, state)
        storage.write_json(storage.world_path, world)
    return {"tick": state["tick"], "turns": turns, "remoteWrites": 0}


def _take_turn(storage: Storage, world: Dict[str, Any], campaign: Dict[str, Any], actor_id: str, observed_at: str) -> Dict[str, Any]:
    key = f"actor-runtime:{world['season']['id']}:{campaign['campaignId']}"
    messages = storage.read_jsonl(storage.channel_path("club"))
    kinds = {row.get("kind") for row in messages if row.get("occurrenceKey") == key}
    intent_id = f"intent:{key}"
    ledger = load_intent_ledger(storage)

    if actor_id == campaign["owner"] and "runtime_event_request" not in kinds:
        return _message(storage, key, observed_at, actor_id, ["lead"], "Please create the free Open Play first; promotion waits for its published event record.", "runtime_event_request", "requested the owner Open Play")
    if actor_id == "red-captain" and "runtime_red_acceptance" not in kinds:
        return _message(storage, key, observed_at, actor_id, [campaign["owner"]], "Red is in if the area, pod, and zero-price outcome are visible before check-in.", "runtime_red_acceptance", "accepted with visibility requirements")
    if actor_id == "blue-captain" and "runtime_blue_acceptance" not in kinds:
        return _message(storage, key, observed_at, actor_id, [campaign["owner"], "red-captain"], "Blue accepts a free one-hour session and will verify the invitation before arrival.", "runtime_blue_acceptance", "accepted and requested invitation evidence")
    if actor_id == "alex" and "runtime_location_review" not in kinds:
        return _message(storage, key, observed_at, actor_id, [campaign["owner"]], "Before booking, expose exact area, pod, local start time, and whether a 60-minute session exists.", "runtime_location_review", "requested location and duration evidence")
    if actor_id == "riley" and "runtime_support_watch" not in kinds:
        return _message(storage, key, observed_at, actor_id, [campaign["owner"]], "If the preview cannot offer one contiguous free hour, record the constraint once; do not retry blindly.", "runtime_support_watch", "set support observation rule")
    if actor_id == "lead" and intent_id not in ledger["intents"] and {"runtime_event_request", "runtime_red_acceptance", "runtime_blue_acceptance"}.issubset(kinds):
        intent = {
            "schemaVersion": 1, "id": intent_id, "status": "agreed", "reason": "owner_open_play", "journey": campaign.get("journey", "owner_open_play"), "createdAt": observed_at,
            "participants": deepcopy(campaign["participants"]), "requestedBy": campaign["owner"],
            "constraints": {"durationMinutes": campaign["durationMinutes"], "daysAhead": deepcopy(campaign["daysAhead"]), "localWindows": deepcopy(campaign["localWindows"]), "slotPolicy": campaign["slotPolicy"], "freeToParticipants": campaign["freeToParticipants"]},
            "evidence": {"runtimeOccurrenceKey": key}, "remoteWrites": 0,
        }
        save_intent(storage, ledger, intent)
        world.setdefault("bookingIntents", []).append({"id": intent_id, "status": "agreed", "reason": intent["reason"], "participants": deepcopy(intent["participants"]), "createdAt": observed_at})
        return {"actorId": actor_id, "action": "intent", "detail": "recorded a 60-minute owner Open Play intent", "observedAt": observed_at}
    if actor_id == campaign["owner"] and intent_id in ledger["intents"] and ledger["intents"][intent_id].get("status") == "event_created" and "runtime_announcement" not in kinds:
        return _message(storage, key, observed_at, actor_id, campaign["participants"], campaign["message"], "runtime_announcement", "promoted the published Open Play")
    if actor_id == "red-captain" and "runtime_announcement" in kinds and "runtime_andy_signup" not in kinds:
        return _message(storage, key, observed_at, actor_id, [campaign["owner"]], "Andy sees the published Open Play and is ready to self-sign up.", "runtime_andy_signup", "requested self-signup")
    if actor_id == "blue-captain" and "runtime_announcement" in kinds and "runtime_terry_signup" not in kinds:
        return _message(storage, key, observed_at, actor_id, [campaign["owner"]], "Terry sees the published Open Play and is ready to self-sign up.", "runtime_terry_signup", "requested self-signup")
    return {"actorId": actor_id, "action": "pass", "detail": "no new relevant information", "observedAt": observed_at}


def _message(storage: Storage, key: str, observed_at: str, actor_id: str, recipients: list, text: str, kind: str, detail: str) -> Dict[str, Any]:
    storage.append_jsonl(storage.channel_path("club"), {"id": f"{key}:{kind}", "occurrenceKey": key, "channel": "club", "from": actor_id, "to": recipients, "kind": kind, "text": text, "observedAt": observed_at})
    return {"actorId": actor_id, "action": "message", "detail": detail, "observedAt": observed_at}


def _write_memory(storage: Storage, actor_id: str, turn: Dict[str, Any]) -> None:
    storage.write_text(storage.state / "actors" / actor_id / "memory.md", f"# {actor_id}\n\nLast turn: {turn['action']} — {turn['detail']}\nObserved: {turn['observedAt']}\n")
