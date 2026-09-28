"""Durable, human-reported tournament coordination for the Preview Club."""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .booking_intents import load_intent_ledger, save_intent
from .storage import Storage
from .time import isoformat, parse_instant


SCENARIO_FILE = "tournament-bogard-japan-women-fighters.json"
STATE_FILE = "tournament.json"


def run_tick(root: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Advance one idempotent tournament decision; never writes to Preview."""
    storage = Storage(root)
    scenario = storage.load_json(root / "scenarios" / SCENARIO_FILE)
    if not isinstance(scenario, dict) or scenario.get("schemaVersion") != 1:
        raise ValueError("tournament scenario is invalid")
    instant = now or parse_instant(None)
    observed_at = isoformat(instant)
    key = f"tournament:{scenario['id']}"
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default=None)
        if not isinstance(state, dict):
            state = {"schemaVersion": 1, "id": scenario["id"], "tick": 0, "rounds": {}}
        state["tick"] = int(state.get("tick", 0)) + 1
        rounds = state.setdefault("rounds", {})
        semi = scenario["rounds"][0]
        final = scenario["rounds"][1]
        ledger = load_intent_ledger(storage)
        semi_state = rounds.setdefault("semi-final", {"status": "pending"})
        action: Dict[str, Any]
        if semi_state["status"] == "pending":
            intent_id = _record_owner_intent(storage, ledger, key, semi, observed_at)
            semi_state.update({"status": "awaiting_event", "intentId": intent_id})
            action = _message(storage, key, observed_at, "sofia", semi["participants"], "tournament_owner_announcement", "Sofia announces the first tournament match: Bogard-Higashi vs Japan Team. The nearest published free Open Play will be the official fixture.")
        elif semi_state["status"] == "awaiting_event":
            _record_owner_intent(storage, ledger, key, semi, observed_at)
            intent = ledger["intents"].get(semi_state["intentId"], {})
            event_start = intent.get("event", {}).get("startTime") if isinstance(intent.get("event"), dict) else None
            if isinstance(event_start, str) and (parse_instant(event_start) - instant).total_seconds() > 30 * 60:
                semi_state.update({"status": "blocked_immediate_window", "blockedAt": observed_at, "reason": "fixture_is_not_within_30_minutes"})
                action = _message(storage, key, observed_at, "sofia", semi["participants"], "tournament_announcement_superseded", "The earlier tournament announcement is superseded: no legal 60-minute fixture was available within 30 minutes. Rally Engine will wait for an immediate slot; the distant event is not an active tournament fixture.")
            elif intent.get("status") == "players_registered":
                semi_state["status"] = "scheduled"
                semi_state["eventId"] = intent.get("event", {}).get("eventId")
                action = _message(storage, key, observed_at, "sofia", semi["participants"], "tournament_fixture_confirmed", "The first fixture is published and every named player is registered. Play the scheduled session, then Andy will report the winner.")
            else:
                action = {"actorId": "lead", "action": "pass", "detail": "first fixture awaits event creation and player registration", "observedAt": observed_at}
        elif semi_state["status"] == "scheduled":
            intent = ledger["intents"].get(semi_state["intentId"], {})
            end_time = intent.get("event", {}).get("endTime") if isinstance(intent.get("event"), dict) else None
            ended = isinstance(end_time, str) and parse_instant(end_time) <= instant
            if ended:
                semi_state["status"] = "awaiting_result"
                action = _message(storage, key, observed_at, semi["resultReporter"], ["sofia", "lead"], "tournament_result_requested", "Andy, how did Bogard-Higashi vs Japan Team go? Reply with the winning team only; no score is required.")
            else:
                action = {"actorId": "lead", "action": "pass", "detail": "first fixture remains scheduled; wait for its real end time", "observedAt": observed_at}
        elif semi_state["status"] == "reported":
            final_state = rounds.setdefault("final", {"status": "pending"})
            if final_state["status"] == "pending":
                winner = semi_state["winner"]
                participants = list(_team_players(scenario, winner)) + list(final["womenFighters"])
                round_copy = {**final, "participants": participants}
                intent_id = _record_owner_intent(storage, ledger, key, round_copy, observed_at)
                final_state.update({"status": "awaiting_event", "intentId": intent_id, "winner": winner})
                action = _message(storage, key, observed_at, "sofia", participants, "tournament_final_announcement", f"Sofia announces the final: {winner} vs Women Fighters Team. The final becomes official once its free Open Play is published.")
            else:
                action = {"actorId": "lead", "action": "pass", "detail": "final is already being prepared", "observedAt": observed_at}
        else:
            action = {"actorId": "lead", "action": "pass", "detail": "tournament awaits a human-reported winner", "observedAt": observed_at}
        state["lastTickAt"] = observed_at
        state["lastAction"] = action
        storage.write_json(storage.state / STATE_FILE, state)
    return {"tick": state["tick"], "action": action, "remoteWrites": 0, "state": state}


def report_winner(root: Path, winner: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Store a player-reported winner; this intentionally does not infer results."""
    storage = Storage(root)
    scenario = storage.load_json(root / "scenarios" / SCENARIO_FILE)
    permitted = set(scenario["rounds"][0]["teams"])
    if winner not in permitted:
        raise ValueError("winner must be Bogard-Higashi or Japan Team")
    observed_at = isoformat(now or parse_instant(None))
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default=None)
        if not isinstance(state, dict) or state.get("rounds", {}).get("semi-final", {}).get("status") != "awaiting_result":
            raise ValueError("the first fixture is not waiting for a player report")
        state["rounds"]["semi-final"].update({"status": "reported", "winner": winner, "reportedAt": observed_at})
        storage.append_jsonl(storage.channel_path("club"), {"id": f"tournament:{scenario['id']}:semi-final:reported", "occurrenceKey": f"tournament:{scenario['id']}", "channel": "club", "from": "red-captain", "to": ["sofia", "lead"], "kind": "tournament_result_reported", "text": f"Andy reports that {winner} won the first fixture. No score recorded.", "observedAt": observed_at, "remoteWrites": 0})
        storage.write_json(storage.state / STATE_FILE, state)
    return state


def _team_players(scenario: Dict[str, Any], team: str) -> list:
    semi = scenario["rounds"][0]
    return semi["participants"][:2] if team == "Bogard-Higashi" else semi["participants"][2:]


def _record_owner_intent(storage: Storage, ledger: Dict[str, Any], key: str, round_spec: Dict[str, Any], observed_at: str) -> str:
    intent_id = f"intent:{key}:{round_spec['id']}"
    constraints = {"durationMinutes": round_spec["durationMinutes"], "daysAhead": [0, 0], "maxDelayMinutes": 30, "localWindows": ["00:00-24:00"], "slotPolicy": "within_30_minutes_or_wait", "freeToParticipants": True}
    if intent_id not in ledger["intents"]:
        intent = {"schemaVersion": 1, "id": intent_id, "status": "agreed", "reason": "owner_tournament_fixture", "journey": "owner_open_play", "createdAt": observed_at, "participants": deepcopy(round_spec["participants"]), "requestedBy": "sofia", "constraints": constraints, "evidence": {"tournamentOccurrenceKey": key, "round": round_spec["id"]}, "promotionOccurrenceKey": key, "promotionKind": "tournament_owner_announcement", "remoteWrites": 0}
        save_intent(storage, ledger, intent)
    elif ledger["intents"][intent_id].get("status") in {"agreed", "preview_blocked"}:
        intent = deepcopy(ledger["intents"][intent_id])
        intent["constraints"] = constraints
        intent["status"] = "agreed"
        intent.pop("previewPlan", None)
        save_intent(storage, ledger, intent)
    return intent_id


def _message(storage: Storage, key: str, observed_at: str, sender: str, recipients: list, kind: str, text: str) -> Dict[str, Any]:
    storage.append_jsonl(storage.channel_path("club"), {"id": f"{key}:{kind}", "occurrenceKey": key, "channel": "club", "from": sender, "to": recipients, "kind": kind, "text": text, "observedAt": observed_at, "remoteWrites": 0})
    return {"actorId": sender, "action": "message", "detail": text, "observedAt": observed_at}
