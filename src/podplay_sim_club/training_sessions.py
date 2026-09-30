"""Captain-led two-player training agreements with no preview writes."""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .booking_intents import load_intent_ledger, save_intent
from .storage import Storage
from .time import isoformat


class TrainingSessionError(ValueError):
    pass


def propose(root: Path, scenario_name: str, now: datetime) -> Dict[str, Any]:
    """Let a captain offer one legal training session to a named teammate."""
    storage = Storage(root)
    scenario, captain, teammate = _validated_scenario(storage, scenario_name)
    intent_id = f"intent:training:{scenario['id']}"
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        existing = ledger["intents"].get(intent_id)
        if isinstance(existing, dict):
            return deepcopy(existing)
        observed_at = isoformat(now)
        intent = {
            "schemaVersion": 1,
            "id": intent_id,
            "status": "captain_invited",
            "journey": "customer_training",
            "reason": "captain_teammate_training",
            "createdAt": observed_at,
            "bookerActorId": captain,
            "participants": [captain, teammate],
            "constraints": {
                "durationMinutes": scenario["durationMinutes"],
                "daysAhead": deepcopy(scenario["daysAhead"]),
                "localWindows": deepcopy(scenario["localWindows"]),
                "slotPolicy": scenario["slotPolicy"],
            },
            "nextAction": "await_teammate_acceptance",
            "remoteWrites": 0,
        }
        save_intent(storage, ledger, intent)
        storage.append_jsonl(storage.channel_path("club"), {
            "id": f"{intent_id}:training-invitation",
            "channel": "club",
            "kind": "training_invitation",
            "from": captain,
            "to": [teammate],
            "intentId": intent_id,
            "text": "King proposes a 30-minute Women Fighters training session. Mai, accept and the lead may plan one available court.",
            "observedAt": observed_at,
            "remoteWrites": 0,
        })
    return intent


def accept(root: Path, scenario_name: str, now: datetime) -> Dict[str, Any]:
    """Persist only the named teammate's explicit training acceptance."""
    storage = Storage(root)
    scenario, captain, teammate = _validated_scenario(storage, scenario_name)
    intent_id = f"intent:training:{scenario['id']}"
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        intent = ledger["intents"].get(intent_id)
        if not isinstance(intent, dict):
            raise TrainingSessionError("captain must propose training before a teammate can accept")
        if intent.get("status") == "agreed":
            return deepcopy(intent)
        if intent.get("status") != "captain_invited" or intent.get("participants") != [captain, teammate]:
            raise TrainingSessionError("training intent is not awaiting this teammate")
        observed_at = isoformat(now)
        intent = deepcopy(intent)
        intent.update({"status": "agreed", "agreedAt": observed_at, "nextAction": "plan_live_w37_availability_then_player_booking"})
        save_intent(storage, ledger, intent)
        storage.append_jsonl(storage.channel_path("club"), {
            "id": f"{intent_id}:training-acceptance",
            "channel": "club",
            "kind": "training_acceptance",
            "from": teammate,
            "to": [captain],
            "intentId": intent_id,
            "inReplyTo": f"{intent_id}:training-invitation",
            "text": "Mai accepts the 30-minute Women Fighters training session. King may book one available court.",
            "observedAt": observed_at,
            "remoteWrites": 0,
        })
    return intent


def propose_pair(root: Path, captain: str, teammate: str, now: datetime) -> Dict[str, Any]:
    """Create one captain-led 30-minute practice agreement from the roster.

    Unlike the original Women Fighters fixture this is not a centrally scripted
    scenario: any roster captain and compatible teammate may use it after a
    legal social advertisement.  It remains entirely local until the lead
    plans and performs a guarded player-owned booking.
    """
    storage = Storage(root)
    team, names = _validated_pair(storage, captain, teammate)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        active = next((intent for intent in ledger["intents"].values() if isinstance(intent, dict)
                       and intent.get("journey") == "customer_training"
                       and intent.get("trainingTeamId") == team["id"]
                       and intent.get("status") in {"captain_invited", "agreed", "planned", "booked"}), None)
        if isinstance(active, dict):
            return deepcopy(active)
        sequence = 1 + sum(1 for intent in ledger["intents"].values() if isinstance(intent, dict)
                           and intent.get("journey") == "customer_training"
                           and intent.get("trainingTeamId") == team["id"])
        observed_at = isoformat(now)
        intent_id = f"intent:training:{team['id']}:{sequence:03d}"
        intent = {
            "schemaVersion": 1, "id": intent_id, "status": "captain_invited",
            "journey": "customer_training", "reason": "captain_teammate_training",
            "trainingTeamId": team["id"], "createdAt": observed_at,
            "bookerActorId": captain, "participants": [captain, teammate],
            "constraints": {"durationMinutes": 30, "daysAhead": [0, 14], "localWindows": ["00:00-24:00"], "slotPolicy": "nearest_valid_low_contention"},
            "nextAction": "await_teammate_acceptance", "remoteWrites": 0,
        }
        save_intent(storage, ledger, intent)
        storage.append_jsonl(storage.channel_path("club"), {
            "id": f"{intent_id}:training-invitation", "channel": "club", "kind": "training_invitation",
            "from": captain, "to": [teammate], "intentId": intent_id,
            "text": f"{names[captain]} proposes a 30-minute {team['name']} training session. {names[teammate]}, accept and the lead may plan one available W37 court.",
            "observedAt": observed_at, "remoteWrites": 0,
        })
    return intent


def accept_intent(root: Path, intent_id: str, teammate: str, now: datetime) -> Dict[str, Any]:
    """Accept a named captain-training invitation after validating its roster pair."""
    storage = Storage(root)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        intent = ledger["intents"].get(intent_id)
        if not isinstance(intent, dict) or intent.get("journey") != "customer_training":
            raise TrainingSessionError("training invitation is unavailable")
        participants = intent.get("participants")
        captain = intent.get("bookerActorId")
        if not isinstance(participants, list) or len(participants) != 2 or not isinstance(captain, str) or teammate not in participants or teammate == captain:
            raise TrainingSessionError("training invitation has no eligible teammate")
        team, names = _validated_pair(storage, captain, teammate)
        if intent.get("trainingTeamId") not in {None, team["id"]}:
            raise TrainingSessionError("training invitation does not match the roster team")
        if intent.get("status") == "agreed":
            return deepcopy(intent)
        if intent.get("status") != "captain_invited":
            raise TrainingSessionError("training invitation is no longer awaiting acceptance")
        observed_at = isoformat(now)
        intent = deepcopy(intent)
        intent.update({"status": "agreed", "agreedAt": observed_at, "nextAction": "plan_live_w37_availability_then_player_booking"})
        save_intent(storage, ledger, intent)
        storage.append_jsonl(storage.channel_path("club"), {
            "id": f"{intent_id}:training-acceptance", "channel": "club", "kind": "training_acceptance",
            "from": teammate, "to": [captain], "intentId": intent_id,
            "inReplyTo": f"{intent_id}:training-invitation",
            "text": f"{names[teammate]} accepts {names[captain]}'s 30-minute {team['name']} training session. {names[captain]} may book one available W37 court.",
            "observedAt": observed_at, "remoteWrites": 0,
        })
    return intent


def next_booking_task(root: Path) -> Dict[str, Any]:
    """Find one agreed training intent for the lead's guarded booking bridge."""
    storage = Storage(root)
    ledger = load_intent_ledger(storage)
    candidates = [
        intent for intent in ledger["intents"].values()
        if isinstance(intent, dict)
        and intent.get("journey") == "customer_training"
        and intent.get("status") == "agreed"
    ]
    if not candidates:
        return {"action": "pass", "detail": "no agreed training session awaits planning"}
    intent = sorted(candidates, key=lambda row: str(row.get("agreedAt") or row.get("createdAt") or ""))[0]
    return {
        "actorId": "lead",
        "action": "plan_training_booking",
        "intentId": intent["id"],
        "bookerActorId": intent["bookerActorId"],
        "participants": list(intent["participants"]),
        "durationMinutes": intent["constraints"]["durationMinutes"],
        "playerActionsAfterBooking": [
            "capture_share_invitation_link",
            "invite_player",
            "accept_invitation",
        ],
        "detail": "both named training players agreed; plan live W37 availability before the guarded player booking",
    }


def _validated_scenario(storage: Storage, scenario_name: str) -> tuple[Dict[str, Any], str, str]:
    scenario = storage.load_json(storage.root / "scenarios" / scenario_name)
    roster = storage.load_json(storage.root / "characters" / "roster.json")
    if not isinstance(scenario, dict) or scenario.get("schemaVersion") != 1 or scenario.get("journey") != "customer_training":
        raise TrainingSessionError("training scenario is invalid")
    captain, teammate = scenario.get("captainActorId"), scenario.get("teammateActorId")
    duration = scenario.get("durationMinutes")
    if not isinstance(captain, str) or not isinstance(teammate, str) or captain == teammate:
        raise TrainingSessionError("training scenario needs two distinct named actors")
    if not isinstance(duration, int) or duration < 30 or duration % 30:
        raise TrainingSessionError("training duration must be a positive 30-minute increment")
    characters = {
        row.get("actorId"): row
        for row in roster.get("characters", []) if isinstance(row, dict) and isinstance(row.get("actorId"), str)
    } if isinstance(roster, dict) else {}
    if captain not in characters or teammate not in characters:
        raise TrainingSessionError("training actors are not on the roster")
    team = next((row for row in roster.get("teams", []) if isinstance(row, dict) and row.get("id") == scenario.get("teamId")), None) if isinstance(roster, dict) else None
    if not isinstance(team, dict):
        raise TrainingSessionError("training team is not on the roster")
    actor_members = {
        row.get("actorId") for row in roster.get("characters", [])
        if isinstance(row, dict) and row.get("id") in team.get("members", [])
    }
    if captain not in actor_members or teammate not in actor_members:
        raise TrainingSessionError("training actors must belong to the same team")
    return scenario, captain, teammate


def _validated_pair(storage: Storage, captain: str, teammate: str) -> tuple[Dict[str, Any], Dict[str, str]]:
    roster = storage.load_json(storage.root / "characters" / "roster.json")
    if not isinstance(roster, dict):
        raise TrainingSessionError("character roster is invalid")
    characters = [row for row in roster.get("characters", []) if isinstance(row, dict) and isinstance(row.get("actorId"), str)]
    by_actor = {row["actorId"]: row for row in characters}
    captain_row, teammate_row = by_actor.get(captain), by_actor.get(teammate)
    if not isinstance(captain_row, dict) or not isinstance(teammate_row, dict):
        raise TrainingSessionError("training actors are not enabled on the roster")
    captain_teams = captain_row.get("captainOf")
    if not isinstance(captain_teams, list) or not captain_teams:
        raise TrainingSessionError("only a roster captain may propose team training")
    team = next((row for row in roster.get("teams", []) if isinstance(row, dict)
                 and row.get("id") in captain_teams and teammate_row.get("id") in row.get("members", [])), None)
    if not isinstance(team, dict):
        raise TrainingSessionError("captain and teammate must belong to the same team")
    affinities = captain_row.get("affinities")
    if not isinstance(affinities, list) or teammate_row.get("id") not in affinities:
        raise TrainingSessionError("training teammate is not a compatible roster affinity")
    return team, {captain: str(captain_row.get("name") or captain), teammate: str(teammate_row.get("name") or teammate)}
