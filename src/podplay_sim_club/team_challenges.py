"""Durable, no-write plans for rivalry-led team journeys."""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .booking_intents import load_intent_ledger, save_intent
from .storage import Storage
from .time import isoformat


class TeamChallengeError(ValueError):
    """Raised when a team challenge cannot be tied back to the roster."""


def plan_challenge(root: Path, scenario_name: str, now: datetime) -> Dict[str, Any]:
    """Record one agreed doubles challenge; product writes require a later adapter."""
    storage = Storage(root)
    scenario = storage.load_json(root / "scenarios" / scenario_name)
    roster = storage.load_json(root / "characters" / "roster.json")
    if not isinstance(scenario, dict) or scenario.get("schemaVersion") != 1:
        raise TeamChallengeError("team challenge scenario is invalid")
    if not isinstance(roster, dict) or roster.get("schemaVersion") != 3:
        raise TeamChallengeError("character roster is invalid")
    if scenario.get("journey") != "doubles_challenge":
        raise TeamChallengeError("team challenge must describe a doubles journey")

    teams = {team.get("id"): team for team in roster.get("teams", []) if isinstance(team, dict)}
    characters = {
        character.get("id"): character
        for character in roster.get("characters", [])
        if isinstance(character, dict)
    }
    challenger = _validate_side(scenario.get("challenger"), teams, characters)
    opponent = _validate_side(scenario.get("opponent"), teams, characters)
    if challenger["teamId"] == opponent["teamId"]:
        raise TeamChallengeError("a challenge needs two different teams")

    duration = scenario.get("durationMinutes")
    if not isinstance(duration, int) or duration <= 0 or duration % 30:
        raise TeamChallengeError("challenge duration must be a positive 30-minute multiple")
    scenario_id = scenario.get("id")
    if not isinstance(scenario_id, str) or not scenario_id:
        raise TeamChallengeError("team challenge is missing its ID")
    participant_characters = [
        challenger["captainId"], challenger["teammateId"],
        opponent["captainId"], opponent["teammateId"],
    ]
    participant_actors = [characters[character_id]["actorId"] for character_id in participant_characters]
    if len(set(participant_actors)) != 4:
        raise TeamChallengeError("each selected doubles player needs a distinct actor identity")

    intent_id = f"intent:team-challenge:{scenario_id}"
    observed_at = isoformat(now)
    intent = {
        "schemaVersion": 1,
        "id": intent_id,
        "status": "agreed",
        "reason": "team_rivalry_doubles",
        "journey": "customer_team_signup",
        "createdAt": observed_at,
        "participants": participant_actors,
        "participantCharacters": participant_characters,
        "challenger": challenger,
        "opponent": opponent,
        "constraints": {
            "durationMinutes": duration,
            "daysAhead": deepcopy(scenario.get("daysAhead", [0, 14])),
            "localWindows": deepcopy(scenario.get("localWindows", ["00:00-24:00"])),
            "slotPolicy": "nearest_valid_low_contention",
        },
        "fallbackJourney": scenario.get("escalateTo", "owner_open_play"),
        "nextAction": "seed_japan_team_then_plan_team_signup",
        "remoteWrites": 0,
    }
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        existing = ledger["intents"].get(intent_id)
        if isinstance(existing, dict):
            return deepcopy(existing)
        save_intent(storage, ledger, intent)
        storage.append_jsonl(
            storage.channel_path("club"),
            {
                "id": f"{intent_id}:agreement",
                "channel": "club",
                "kind": "team_challenge_agreement",
                "from": challenger["captainActorId"],
                "to": participant_actors[1:],
                "text": "Andy and Terry challenge Kyo and Benimaru to a 60-minute doubles match.",
                "observedAt": observed_at,
                "remoteWrites": 0,
            },
        )
    return intent


def _validate_side(
    side: Any, teams: Dict[str, Dict[str, Any]], characters: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    if not isinstance(side, dict):
        raise TeamChallengeError("challenge side is invalid")
    team_id = side.get("teamId")
    captain_id = side.get("captainId")
    teammate_id = side.get("teammateId")
    team = teams.get(team_id)
    if not isinstance(team, dict) or team.get("captainId") != captain_id:
        raise TeamChallengeError("challenge captain does not match its roster team")
    members = team.get("members")
    if not isinstance(members, list) or teammate_id not in members or teammate_id == captain_id:
        raise TeamChallengeError("challenge teammate does not match its roster team")
    for character_id in (captain_id, teammate_id):
        character = characters.get(character_id)
        if not isinstance(character, dict) or not isinstance(character.get("actorId"), str):
            raise TeamChallengeError(f"challenge player needs an enabled actor: {character_id}")
    return {
        "teamId": team_id,
        "teamName": team.get("name", team_id),
        "captainId": captain_id,
        "captainActorId": characters[captain_id]["actorId"],
        "teammateId": teammate_id,
        "teammateActorId": characters[teammate_id]["actorId"],
        "guestEligible": deepcopy(side.get("guestEligible", [])),
    }
