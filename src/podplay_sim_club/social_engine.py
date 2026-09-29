"""Durable Sims-style motives and legal interaction advertisements.

This module deliberately selects offers, never product actions.  The lead may
later turn an accepted offer into a legal booking or invitation workflow.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage import Storage
from .time import isoformat, parse_instant


STATE_FILE = "social-engine.json"
SCENARIO_FILE = "social-engine.json"


def tick(root: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Advance motives and advertise only interactions that pass hard gates."""
    storage = Storage(root)
    scenario = storage.load_json(root / "scenarios" / SCENARIO_FILE)
    roster = storage.load_json(root / "characters" / "roster.json")
    if not isinstance(scenario, dict) or scenario.get("schemaVersion") != 1:
        raise ValueError("social engine scenario is invalid")
    if not isinstance(roster, dict) or roster.get("schemaVersion") != 3:
        raise ValueError("character roster is invalid")
    instant = now or parse_instant(None)
    observed_at = isoformat(instant)
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        if not isinstance(state, dict):
            state = {}
        people = _enabled_people(roster)
        previous = state.get("lastTickAt")
        elapsed = max(0.0, (instant - parse_instant(previous)).total_seconds() / 60) if isinstance(previous, str) else 0.0
        motives = state.setdefault("motives", {})
        for actor_id in people:
            motive = motives.setdefault(actor_id, {"desireToPlay": 40, "curiosity": 0, "frustration": 0})
            _age_motive(motive, elapsed, scenario["clock"])
        _apply_release_notes(storage, motives, state)
        opportunities = _published_opportunities(storage, people)
        offers = _offers(people, roster, motives, opportunities, state, scenario, instant)
        state.update({"schemaVersion": 1, "lastTickAt": observed_at, "offers": offers, "opportunities": opportunities})
        storage.write_json(storage.state / STATE_FILE, state)
    return {"observedAt": observed_at, "offers": offers, "opportunities": opportunities, "motives": motives}


def record_blocked(root: Path, actor_id: str, reason: str, now: Optional[datetime] = None) -> None:
    """A failed slot or unanswered invitation raises frustration once per cause."""
    storage = Storage(root)
    observed_at = isoformat(now or parse_instant(None))
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        motives = state.setdefault("motives", {})
        motive = motives.setdefault(actor_id, {"desireToPlay": 40, "curiosity": 0, "frustration": 0})
        causes = state.setdefault("frustrationCauses", {})
        key = f"{actor_id}:{reason}"
        if key not in causes:
            motive["frustration"] = min(100, int(motive["frustration"]) + 25)
            causes[key] = observed_at
        state["lastTickAt"] = observed_at
        storage.write_json(storage.state / STATE_FILE, state)


def record_completed_event(root: Path, participants: List[str], now: Optional[datetime] = None) -> None:
    """Closeness changes only after a shared completed event."""
    storage = Storage(root)
    observed_at = isoformat(now or parse_instant(None))
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        closeness = state.setdefault("closeness", {})
        for left in participants:
            for right in participants:
                if left < right:
                    key = f"{left}|{right}"
                    closeness[key] = min(100, int(closeness.get(key, 0)) + 10)
            motive = state.setdefault("motives", {}).setdefault(left, {"desireToPlay": 40, "curiosity": 0, "frustration": 0})
            motive["desireToPlay"] = max(0, int(motive["desireToPlay"]) - 35)
        state["lastTickAt"] = observed_at
        storage.write_json(storage.state / STATE_FILE, state)


def record_feature_trial(root: Path, actor_id: str, release_note_id: str, now: Optional[datetime] = None) -> None:
    """A feature trial consumes curiosity for that release once."""
    storage = Storage(root)
    observed_at = isoformat(now or parse_instant(None))
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        motive = state.setdefault("motives", {}).setdefault(actor_id, {"desireToPlay": 40, "curiosity": 0, "frustration": 0})
        motive["curiosity"] = 0
        state.setdefault("triedReleases", {}).setdefault(actor_id, {})[release_note_id] = observed_at
        state["lastTickAt"] = observed_at
        storage.write_json(storage.state / STATE_FILE, state)


def _enabled_people(roster: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {row["actorId"]: row for row in roster.get("characters", []) if isinstance(row, dict) and isinstance(row.get("actorId"), str) and row.get("role") == "player"}


def _age_motive(motive: Dict[str, Any], minutes: float, clock: Dict[str, Any]) -> None:
    motive["desireToPlay"] = min(100, int(motive.get("desireToPlay", 0)) + round(minutes * int(clock["desirePerHour"]) / 60))
    motive["curiosity"] = max(0, int(motive.get("curiosity", 0)) - round(minutes * int(clock["curiosityDecayPerHour"]) / 60))
    motive["frustration"] = max(0, int(motive.get("frustration", 0)) - round(minutes * int(clock["frustrationDecayPerHour"]) / 60))


def _apply_release_notes(storage: Storage, motives: Dict[str, Any], state: Dict[str, Any]) -> None:
    notes = storage.load_json(storage.state / "release-notes.json", default={})
    rows = notes.get("notes", []) if isinstance(notes, dict) else []
    seen = state.setdefault("seenReleases", {})
    tried = state.setdefault("triedReleases", {})
    for note in rows:
        if not isinstance(note, dict) or not isinstance(note.get("id"), str):
            continue
        for actor_id in note.get("actorIds", []):
            if actor_id not in motives or note["id"] in seen.get(actor_id, {}) or note["id"] in tried.get(actor_id, {}):
                continue
            motives[actor_id]["curiosity"] = min(100, int(motives[actor_id]["curiosity"]) + 35)
            seen.setdefault(actor_id, {})[note["id"]] = True


def _published_opportunities(storage: Storage, people: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    ledger = storage.load_json(storage.state / "booking-intents.json", default={})
    rows = []
    for intent in ledger.get("intents", {}).values() if isinstance(ledger, dict) else []:
        if not isinstance(intent, dict) or intent.get("status") not in {"event_created", "players_registered"}:
            continue
        participants = [actor_id for actor_id in intent.get("participants", []) if actor_id in people]
        if participants:
            rows.append({"id": intent.get("id"), "participants": participants, "event": intent.get("event"), "kind": "published_event"})
    return rows


def _offers(people: Dict[str, Dict[str, Any]], roster: Dict[str, Any], motives: Dict[str, Any], opportunities: List[Dict[str, Any]], state: Dict[str, Any], scenario: Dict[str, Any], instant: datetime) -> List[Dict[str, Any]]:
    offers = []
    cooldowns = state.setdefault("cooldowns", {})
    threshold = int(scenario["thresholds"]["desireToPlay"])
    for actor_id, person in people.items():
        motive = motives[actor_id]
        if int(motive["frustration"]) >= int(scenario["thresholds"]["frustration"]):
            offers.append({"actorId": actor_id, "kind": "ask_riley", "reason": "frustrated_by_blocked_or_unanswered_offer"})
            cooldowns[actor_id] = isoformat(instant)
            continue
        if int(motive["desireToPlay"]) < threshold or _cooling_down(cooldowns, actor_id, instant, scenario):
            continue
        for opportunity in opportunities:
            if actor_id in opportunity["participants"]:
                offers.append({"actorId": actor_id, "kind": "accept_event", "opportunityId": opportunity["id"], "reason": "published_event_addressed_to_player"})
                cooldowns[actor_id] = isoformat(instant)
                break
        else:
            team_id = next(iter(person.get("captainOf", [])), None)
            if team_id:
                teammate = _available_teammate(roster, people, actor_id, team_id)
                if teammate:
                    offers.append({"actorId": actor_id, "kind": "invite_teammate", "teammateId": teammate, "reason": "desire_to_play_and_static_affinity"})
                    cooldowns[actor_id] = isoformat(instant)
    return offers


def _cooling_down(cooldowns: Dict[str, Any], actor_id: str, instant: datetime, scenario: Dict[str, Any]) -> bool:
    previous = cooldowns.get(actor_id)
    if not isinstance(previous, str):
        return False
    return (instant - parse_instant(previous)).total_seconds() < int(scenario["cooldownMinutes"]) * 60


def _available_teammate(roster: Dict[str, Any], people: Dict[str, Dict[str, Any]], actor_id: str, team_id: str) -> Optional[str]:
    team = next((row for row in roster.get("teams", []) if isinstance(row, dict) and row.get("id") == team_id), {})
    for member in team.get("members", []) if isinstance(team, dict) else []:
        candidate = people.get(member)
        if member != actor_id and candidate and member in people[actor_id].get("affinities", []):
            return member
    return None
