"""Durable, no-write plans for rivalry-led team journeys."""

from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from .booking_intents import load_intent_ledger, save_intent
from .protocol import (
    append_protocol_facts_locked,
    commitment_id_for,
    conversation_id_for,
    make_commitment_proposal,
    make_commitment_transition,
    make_message,
    reply_causation,
)
from .storage import Storage
from .time import isoformat


class TeamChallengeError(ValueError):
    """Raised when a team challenge cannot be tied back to the roster."""


def propose_challenge(root: Path, scenario_name: str, now: datetime) -> Dict[str, Any]:
    """Send a captain-to-captain doubles invitation without booking a court."""
    storage = Storage(root)
    scenario = storage.load_json(root / "scenarios" / scenario_name)
    roster = storage.load_json(root / "characters" / "roster.json")
    if not isinstance(scenario, dict) or scenario.get("journey") != "doubles_challenge":
        raise TeamChallengeError("team challenge scenario is invalid")
    teams = {team.get("id"): team for team in roster.get("teams", []) if isinstance(team, dict)} if isinstance(roster, dict) else {}
    characters = {character.get("id"): character for character in roster.get("characters", []) if isinstance(character, dict)} if isinstance(roster, dict) else {}
    challenger = _validate_side(scenario.get("challenger"), teams, characters)
    opponent = _validate_side(scenario.get("opponent"), teams, characters)
    scenario_id = scenario.get("id")
    if not isinstance(scenario_id, str) or not scenario_id:
        raise TeamChallengeError("team challenge is missing its ID")
    intent_id = f"intent:team-challenge:{scenario_id}"
    observed_at = isoformat(now)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        existing = ledger["intents"].get(intent_id)
        if isinstance(existing, dict):
            return deepcopy(existing)
        intent = {
            "schemaVersion": 1, "id": intent_id, "status": "captain_invited",
            "reason": "team_rivalry_doubles", "journey": "customer_team_signup",
            "createdAt": observed_at,
            "participants": [challenger["captainActorId"], challenger["teammateActorId"], opponent["captainActorId"], opponent["teammateActorId"]],
            "bookerActorId": challenger["captainActorId"],
            "challenger": challenger, "opponent": opponent,
            "constraints": {"durationMinutes": scenario.get("durationMinutes", 60), "daysAhead": deepcopy(scenario.get("daysAhead", [0, 14])), "localWindows": deepcopy(scenario.get("localWindows", ["00:00-24:00"])), "slotPolicy": "nearest_valid_low_contention"},
            "fallbackJourney": scenario.get("escalateTo", "owner_open_play"), "nextAction": "await_opponent_captain_acceptance", "remoteWrites": 0,
            "conversationId": conversation_id_for(intent_id), "commitmentId": commitment_id_for(intent_id),
        }
        save_intent(storage, ledger, intent)
        message = {
            "id": f"{intent_id}:captain-invitation", "channel": "club", "kind": "captain_challenge_invitation",
            "conversationId": intent["conversationId"], "commitmentId": intent["commitmentId"], "speechAct": "propose",
            "from": challenger["captainActorId"], "to": [opponent["captainActorId"]], "intentId": intent_id,
            "text": f"{challenger['teamName']} challenges {opponent['teamName']} to doubles. Accept this captain invitation and the lead may plan one court; teammates are invited afterward.",
            "observedAt": observed_at, "remoteWrites": 0,
        }
        storage.append_jsonl(storage.channel_path("club"), message)
        _record_challenge_proposal(storage, intent, message, observed_at)
    return intent


def propose_captain_challenge(
    root: Path, challenger_actor_id: str, opponent_actor_id: str, now: datetime, *, duration_minutes: int = 60,
) -> Dict[str, Any]:
    """Propose a generic doubles challenge from two rostered captain actors.

    This is deliberately independent of rivalry intensity or a named scenario:
    all teams can be rivals.  The roster supplies the captain and one named
    teammate on each side; product availability and booking remain later lead
    responsibilities.
    """
    if challenger_actor_id == opponent_actor_id:
        raise TeamChallengeError("a captain cannot challenge their own team")
    if not isinstance(duration_minutes, int) or duration_minutes <= 0 or duration_minutes % 30:
        raise TeamChallengeError("challenge duration must be a positive 30-minute multiple")
    storage = Storage(root)
    roster = storage.load_json(root / "characters" / "roster.json")
    if not isinstance(roster, dict):
        raise TeamChallengeError("character roster is invalid")
    teams = {team.get("id"): team for team in roster.get("teams", []) if isinstance(team, dict)}
    characters = {character.get("id"): character for character in roster.get("characters", []) if isinstance(character, dict)}
    challenger = _side_for_captain_actor(challenger_actor_id, teams, characters)
    opponent = _side_for_captain_actor(opponent_actor_id, teams, characters)
    if challenger["teamId"] == opponent["teamId"]:
        raise TeamChallengeError("a challenge needs two different teams")
    observed_at = isoformat(now)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        for existing in ledger["intents"].values():
            if not isinstance(existing, dict) or existing.get("journey") != "customer_team_signup":
                continue
            left = existing.get("challenger") if isinstance(existing.get("challenger"), dict) else {}
            right = existing.get("opponent") if isinstance(existing.get("opponent"), dict) else {}
            pair = {left.get("teamId"), right.get("teamId")}
            if pair == {challenger["teamId"], opponent["teamId"]} and existing.get("status") in {"captain_invited", "agreed", "preview_ready", "team_signup_in_progress", "team_members_registered"}:
                return deepcopy(existing)
        key = f"{challenger['teamId']}-vs-{opponent['teamId']}:{now.strftime('%Y%m%dT%H')}"
        intent_id = f"intent:team-challenge:{key}"
        participants = [challenger["captainActorId"], challenger["teammateActorId"], opponent["captainActorId"], opponent["teammateActorId"]]
        intent = {
            "schemaVersion": 1, "id": intent_id, "status": "captain_invited",
            "reason": "recent_team_training_competition", "journey": "customer_team_signup",
            "createdAt": observed_at, "participants": participants,
            "bookerActorId": challenger["captainActorId"], "challenger": challenger, "opponent": opponent,
            "constraints": {"durationMinutes": duration_minutes, "daysAhead": [0, 14], "localWindows": ["00:00-24:00"], "slotPolicy": "nearest_valid_low_contention"},
            "fallbackJourney": "owner_open_play", "nextAction": "await_opponent_captain_acceptance", "remoteWrites": 0,
            "conversationId": conversation_id_for(intent_id), "commitmentId": commitment_id_for(intent_id),
        }
        save_intent(storage, ledger, intent)
        message = {
            "id": f"{intent_id}:captain-invitation", "channel": "club", "kind": "captain_challenge_invitation",
            "conversationId": intent["conversationId"], "commitmentId": intent["commitmentId"], "speechAct": "propose",
            "from": challenger["captainActorId"], "to": [opponent["captainActorId"]], "intentId": intent_id,
            "text": f"{challenger['teamName']} challenges {opponent['teamName']} to doubles after team training. Accept this captain invitation and the lead may plan one court; teammates are invited afterward.",
            "observedAt": observed_at, "remoteWrites": 0,
        }
        storage.append_jsonl(storage.channel_path("club"), message)
        _record_challenge_proposal(storage, intent, message, observed_at)
    return intent


def accept_challenge(root: Path, intent_id: str, actor_id: str, now: datetime) -> Dict[str, Any]:
    """Let only the addressed opposing captain accept an unbooked challenge."""
    storage = Storage(root)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        intent = ledger["intents"].get(intent_id)
        if not isinstance(intent, dict) or intent.get("journey") != "customer_team_signup":
            raise TeamChallengeError("team challenge invitation is unavailable")
        if intent.get("status") == "agreed":
            return deepcopy(intent)
        opponent = intent.get("opponent")
        challenger = intent.get("challenger")
        expected = opponent.get("captainActorId") if isinstance(opponent, dict) else None
        proposer = challenger.get("captainActorId") if isinstance(challenger, dict) else None
        if intent.get("status") != "captain_invited" or actor_id != expected or not isinstance(proposer, str):
            raise TeamChallengeError("only the addressed opposing captain may accept")
        observed_at = isoformat(now)
        intent = deepcopy(intent)
        intent.update({"status": "agreed", "agreedAt": observed_at, "nextAction": "plan_team_signup_booking"})
        save_intent(storage, ledger, intent)
        message = {
            "id": f"{intent_id}:captain-acceptance", "channel": "club",
            "kind": "captain_challenge_acceptance", "from": actor_id, "to": [proposer],
            "conversationId": intent.get("conversationId"), "commitmentId": intent.get("commitmentId"),
            "speechAct": "accept", "intentId": intent_id,
            "inReplyTo": f"{intent_id}:captain-invitation",
            "text": "The opposing captain accepts the doubles challenge. The lead may plan one legal court; no booking or attendance is implied.",
            "observedAt": observed_at, "remoteWrites": 0,
        }
        storage.append_jsonl(storage.channel_path("club"), message)
        _record_challenge_acceptance(storage, intent, actor_id, proposer, message, observed_at)
    return intent


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
        # The captain who initiated the accepted challenge owns only the
        # booking/payment action.  The generic team-signup adapter reads this
        # role rather than assuming a particular character or team.
        "bookerActorId": challenger["captainActorId"],
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
                "text": f"{challenger['teamName']} challenges {opponent['teamName']} to a {duration}-minute doubles match. The initiating captain may book after the agreement.",
                "observedAt": observed_at,
                "remoteWrites": 0,
            },
        )
    return intent


def resolve_booking_owner(intent: Dict[str, Any]) -> Optional[str]:
    """Return the socially designated booking owner for any booking intent.

    Team-signup journeys intentionally do not infer ownership from list order:
    their initiating captain is the only valid compatibility fallback for
    durable intents written before ``bookerActorId`` was added.  Other
    customer journeys retain their established first-participant fallback.
    """
    owner = intent.get("bookerActorId")
    if isinstance(owner, str) and owner:
        return owner
    participants = intent.get("participants")
    if intent.get("journey") == "customer_team_signup":
        challenger = intent.get("challenger")
        candidate = challenger.get("captainActorId") if isinstance(challenger, dict) else None
        if isinstance(candidate, str) and isinstance(participants, list) and candidate in participants:
            return candidate
        return None
    if isinstance(participants, list) and participants and isinstance(participants[0], str):
        return participants[0]
    return None


def next_team_signup_task(root: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Expose one captain-agreed team booking for the guarded lead bridge.

    The social/communication layer creates the agreement.  This function only
    makes that agreement observable to the lead; it never scans, books, or
    invites in the Preview product.
    """
    storage = Storage(root)
    ledger = load_intent_ledger(storage)
    candidates = [
        intent for intent in ledger["intents"].values()
        if isinstance(intent, dict)
        and intent.get("journey") == "customer_team_signup"
        and intent.get("status") in {"agreed", "preview_ready"}
        and isinstance(resolve_booking_owner(intent), str)
    ]
    if now is not None:
        candidates = [intent for intent in candidates if _booking_agreement_is_fresh(intent, now)]
    if not candidates:
        return {"action": "pass", "detail": "no captain-agreed team signup awaits planning"}
    active_id = ledger.get("activeIntentId")
    intent = sorted(
        candidates,
        key=lambda row: (
            row.get("id") != active_id,
            -_agreement_timestamp(row),
            str(row.get("id") or ""),
        ),
    )[0]
    owner = resolve_booking_owner(intent)
    assert isinstance(owner, str)
    participants = [actor for actor in intent.get("participants", []) if isinstance(actor, str)]
    return {
        "actorId": "lead",
        "action": "apply_team_signup_booking" if intent.get("status") == "preview_ready" else "plan_team_signup_booking",
        "intentId": intent["id"],
        "bookerActorId": owner,
        "participants": participants,
        "durationMinutes": intent.get("constraints", {}).get("durationMinutes"),
        "playerActionsAfterBooking": ["add_named_team_members", "read_back_member_acceptance", "check_in_window_then_player_check_ins"],
        "detail": "both captains agreed through the club channel; the lead may plan or apply the guarded team-signup booking",
    }


def _agreement_timestamp(intent: Dict[str, Any]) -> float:
    value = intent.get("agreedAt") or intent.get("createdAt") or ""
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _booking_agreement_is_fresh(intent: Dict[str, Any], now: datetime) -> bool:
    stamp = intent.get("agreedAt") or intent.get("createdAt")
    if not isinstance(stamp, str):
        return False
    try:
        agreed_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if now - agreed_at > timedelta(hours=2):
        return False
    if intent.get("status") != "preview_ready":
        return True
    plan = intent.get("previewPlan")
    observed_at = plan.get("observedAt") if isinstance(plan, dict) else None
    if not isinstance(observed_at, str):
        return False
    try:
        return now - datetime.fromisoformat(observed_at.replace("Z", "+00:00")) <= timedelta(minutes=15)
    except ValueError:
        return False


def _record_challenge_proposal(
    storage: Storage, intent: Dict[str, Any], message: Dict[str, Any], observed_at: str
) -> None:
    challenger, opponent = intent["challenger"], intent["opponent"]
    proposer, debtor = challenger["captainActorId"], opponent["captainActorId"]
    conversation_id, commitment_id = intent["conversationId"], intent["commitmentId"]
    message_fact = make_message(
        message_id=message["id"], conversation_id=conversation_id,
        correlation_id=f"journey:{intent['id']}", sender_actor_id=proposer,
        recipient_actor_ids=[debtor], speech_act="propose",
        content_code="team.challenge.proposed", occurred_at=observed_at,
        commitment_id=commitment_id,
        parameters={"intentRef": intent["id"], "durationMinutes": intent["constraints"]["durationMinutes"]},
    )
    commitment_fact = make_commitment_proposal(
        commitment_id=commitment_id, conversation_id=conversation_id,
        correlation_id=f"journey:{intent['id']}", proposer_actor_id=proposer,
        debtor_actor_id=debtor,
        beneficiary_actor_ids=[actor for actor in intent["participants"] if isinstance(actor, str)],
        kind="accept_team_challenge",
        content={"capabilityId": "player.accept_team_challenge", "intentRef": intent["id"]},
        occurred_at=observed_at, causation_id=message_fact["factId"],
    )
    append_protocol_facts_locked(storage, [message_fact, commitment_fact])


def _record_challenge_acceptance(
    storage: Storage, intent: Dict[str, Any], actor_id: str, proposer: str,
    message: Dict[str, Any], observed_at: str,
) -> None:
    conversation_id, commitment_id = intent.get("conversationId"), intent.get("commitmentId")
    if not isinstance(conversation_id, str) or not isinstance(commitment_id, str):
        return
    proposal_id = f"{intent['id']}:captain-invitation"
    message_fact = make_message(
        message_id=message["id"], conversation_id=conversation_id,
        correlation_id=f"journey:{intent['id']}", sender_actor_id=actor_id,
        recipient_actor_ids=[proposer], speech_act="accept",
        content_code="team.challenge.accepted", occurred_at=observed_at,
        commitment_id=commitment_id, in_reply_to=proposal_id,
        grounding="understood", causation_id=reply_causation(proposal_id),
        parameters={"intentRef": intent["id"]},
    )
    transition = make_commitment_transition(
        storage, commitment_id=commitment_id, transition="accepted",
        actor_id=actor_id, occurred_at=observed_at,
        causation_id=message_fact["factId"],
    )
    append_protocol_facts_locked(storage, [message_fact, transition])


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


def _side_for_captain_actor(
    actor_id: str, teams: Dict[str, Dict[str, Any]], characters: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Resolve one active captain and their first active teammate from roster data."""
    captain_id = next((character_id for character_id, character in characters.items()
                       if isinstance(character, dict) and character.get("actorId") == actor_id), None)
    if not isinstance(captain_id, str):
        raise TeamChallengeError("challenge captain needs a roster actor")
    team = next((team for team in teams.values() if isinstance(team, dict) and team.get("captainId") == captain_id), None)
    if not isinstance(team, dict):
        raise TeamChallengeError("challenge actor is not a roster captain")
    teammate_id = next((member for member in team.get("members", [])
                        if member != captain_id and isinstance(characters.get(member), dict)
                        and isinstance(characters[member].get("actorId"), str)), None)
    if not isinstance(teammate_id, str):
        raise TeamChallengeError("challenge team has no enabled teammate")
    return _validate_side(
        {"teamId": team.get("id"), "captainId": captain_id, "teammateId": teammate_id, "guestEligible": []},
        teams, characters,
    )
