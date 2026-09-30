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
        _apply_missed_training_motives(storage, motives, state)
        _apply_owner_training_motives(storage, motives, state)
        offers = (_staff_offers(storage, roster)
                  + _training_offers(storage, people, roster, motives, state, scenario, instant)
                  + _offers(people, roster, motives, opportunities, state, scenario, instant))
        owner_offer = _quiet_club_owner_offer(offers, opportunities, state, scenario, instant)
        if owner_offer:
            offers.append(owner_offer)
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


def _staff_offers(storage: Storage, roster: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Unanswered player requests reach the front desk before any other offer."""
    staff_ids = {
        row["actorId"]
        for row in roster.get("staff", [])
        if isinstance(row, dict) and isinstance(row.get("actorId"), str)
    }
    if not staff_ids:
        return []
    messages = storage.read_jsonl(storage.channel_path("club"))
    answered = {
        row.get("inReplyTo")
        for row in messages
        if isinstance(row, dict) and row.get("from") in staff_ids
    }
    offers = []
    for row in messages:
        if not isinstance(row, dict):
            continue
        kind = row.get("kind")
        message_id = row.get("id")
        if kind not in {"player_request", "reschedule_request", "new_event_request", "share_invitation_expired"} or not isinstance(message_id, str):
            continue
        if message_id in answered:
            continue
        offers.append({
            "actorId": next(iter(staff_ids)),
            "kind": "reschedule_match" if kind == "reschedule_request" else "answer_player",
            "messageId": message_id,
            "reason": "player_request_is_staff_priority",
            "priority": "answer_player_requests",
        })
    return offers


def _apply_missed_training_motives(
    storage: Storage, motives: Dict[str, Dict[str, Any]], state: Dict[str, Any]
) -> None:
    """A confirmed no-show raises motives once; it does not invent a dialogue."""
    messages = storage.read_jsonl(storage.channel_path("club"))
    applied = state.setdefault("missedTrainingMotivesApplied", {})
    for row in messages:
        if not isinstance(row, dict) or row.get("kind") != "training_missed_session":
            continue
        message_id = row.get("id")
        targets = row.get("to")
        if not isinstance(message_id, str) or message_id in applied or not isinstance(targets, list):
            continue
        for actor_id in targets:
            if actor_id not in motives:
                continue
            motive = motives[actor_id]
            motive["desireToPlay"] = min(100, int(motive.get("desireToPlay", 0)) + 10)
            motive["frustration"] = min(100, int(motive.get("frustration", 0)) + 15)
        applied[message_id] = True


def _apply_owner_training_motives(storage: Storage, motives: Dict[str, Dict[str, Any]], state: Dict[str, Any]) -> None:
    """A Sofia team-training nudge raises desire once without making a booking."""
    applied = state.setdefault("ownerTrainingMotivesApplied", {})
    for row in storage.read_jsonl(storage.channel_path("club")):
        if not isinstance(row, dict) or row.get("kind") != "owner_team_training_nudge":
            continue
        message_id, targets = row.get("id"), row.get("to")
        if not isinstance(message_id, str) or message_id in applied or not isinstance(targets, list):
            continue
        for actor_id in targets:
            if actor_id in motives:
                motives[actor_id]["desireToPlay"] = min(100, int(motives[actor_id].get("desireToPlay", 0)) + 15)
        applied[message_id] = True


def _enabled_people(roster: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    # A rostered future player is dormant until an actual team interaction
    # addresses them; they are not a staff member or a fabricated user.  Once
    # King/Mai have an evidenced booking, acceptance, or missed-session notice,
    # they must be able to receive the same bounded offers as active players.
    return {
        row["actorId"]: row
        for row in roster.get("characters", [])
        if isinstance(row, dict)
        and isinstance(row.get("actorId"), str)
        and row.get("role") in {"player", "future_player"}
    }


def _training_offers(storage: Storage, people: Dict[str, Dict[str, Any]], roster: Dict[str, Any], motives: Dict[str, Any], state: Dict[str, Any], scenario: Dict[str, Any], instant: datetime) -> List[Dict[str, Any]]:
    """Offer captain-led practice only after Sofia has specifically advertised it."""
    messages = storage.read_jsonl(storage.channel_path("club"))
    nudged = {actor_id for row in messages if isinstance(row, dict) and row.get("kind") == "owner_team_training_nudge"
              for actor_id in (row.get("to") if isinstance(row.get("to"), list) else []) if isinstance(actor_id, str)}
    if not nudged:
        return []
    threshold = int(scenario["thresholds"]["desireToPlay"])
    cooldowns = state.setdefault("interactionCooldowns", {})
    offers = []
    for actor_id, person in people.items():
        if actor_id not in nudged or int(motives[actor_id].get("desireToPlay", 0)) < threshold:
            continue
        team_id = next(iter(person.get("captainOf", [])), None)
        teammate = _available_teammate(roster, people, actor_id, team_id) if isinstance(team_id, str) else None
        if teammate and not _cooling_down(cooldowns, _cooldown_key(actor_id, "propose_team_training", teammate), instant, scenario):
            offers.append({"actorId": actor_id, "kind": "propose_team_training", "teammateId": teammate, "reason": "owner_training_nudge_and_desire"})
    ledger = storage.load_json(storage.state / "booking-intents.json", default={})
    for intent in ledger.get("intents", {}).values() if isinstance(ledger, dict) else []:
        if not isinstance(intent, dict) or intent.get("journey") != "customer_training" or intent.get("status") != "captain_invited":
            continue
        participants = intent.get("participants")
        captain = intent.get("bookerActorId")
        if not isinstance(participants, list) or len(participants) != 2 or not isinstance(captain, str):
            continue
        teammate = next((actor_id for actor_id in participants if actor_id != captain and actor_id in nudged), None)
        if isinstance(teammate, str):
            offers.append({"actorId": teammate, "kind": "accept_team_training", "intentId": intent.get("id"), "reason": "captain_training_invitation_addressed_to_teammate"})
    return offers


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
    # Cooldowns belong to an interaction, not to a person.  A captain who has
    # just challenged a rival should not resend that challenge every beat, but
    # may still accept an event or invite a teammate to a separate short game.
    # Retire the old actor-wide map without turning it into a new broad gate.
    legacy_cooldowns = state.pop("cooldowns", None)
    if isinstance(legacy_cooldowns, dict):
        state.setdefault("legacyActorCooldowns", legacy_cooldowns)
    # Earlier interaction keys were written while an offer was only visible.
    # Retire those once; version 2 starts a cooldown only after the selected
    # legal interaction has been persisted.
    if state.get("interactionCooldownSemantics") != "action-only":
        advertised = state.get("interactionCooldowns")
        if isinstance(advertised, dict) and advertised:
            state.setdefault("legacyAdvertisedInteractionCooldowns", advertised)
        state["interactionCooldowns"] = {}
        state["interactionCooldownSemantics"] = "action-only"
    cooldowns = state.setdefault("interactionCooldowns", {})
    threshold = int(scenario["thresholds"]["desireToPlay"])
    for actor_id, person in people.items():
        motive = motives[actor_id]
        if int(motive["frustration"]) >= int(scenario["thresholds"]["frustration"]):
            offers.append({"actorId": actor_id, "kind": "ask_riley", "reason": "frustrated_by_blocked_or_unanswered_offer"})
            continue
        if int(motive["desireToPlay"]) < threshold:
            continue
        for opportunity in opportunities:
            if actor_id in opportunity["participants"]:
                cooldown_key = _cooldown_key(actor_id, "accept_event", str(opportunity.get("id") or "unknown"))
                if _cooling_down(cooldowns, cooldown_key, instant, scenario):
                    continue
                offers.append({"actorId": actor_id, "kind": "accept_event", "opportunityId": opportunity["id"], "reason": "published_event_addressed_to_player"})
                break
        else:
            team_id = next(iter(person.get("captainOf", [])), None)
            if team_id:
                teammate = _available_teammate(roster, people, actor_id, team_id)
                if teammate:
                    invite_key = _cooldown_key(actor_id, "invite_teammate", teammate)
                    if not _cooling_down(cooldowns, invite_key, instant, scenario):
                        offers.append({"actorId": actor_id, "kind": "invite_teammate", "teammateId": teammate, "reason": "desire_to_play_and_static_affinity"})
                    rival = _high_rival_captain(roster, team_id)
                    challenge_key = _cooldown_key(actor_id, "challenge_rival_captain", rival or "")
                    if rival and not _cooling_down(cooldowns, challenge_key, instant, scenario):
                        offers.append({"actorId": actor_id, "kind": "challenge_rival_captain", "rivalCaptainId": rival, "teammateId": teammate, "reason": "desire_to_play_and_high_rivalry"})
    return offers


def _cooldown_key(actor_id: str, interaction: str, target: str) -> str:
    return f"{actor_id}:{interaction}:{target}"


def record_interaction_cooldown(
    root: Path, actor_id: str, interaction: str, target: str, now: Optional[datetime] = None
) -> None:
    """Start an interaction-specific cooldown after, never before, an action."""
    storage = Storage(root)
    observed_at = isoformat(now or parse_instant(None))
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        state["interactionCooldownSemantics"] = "action-only"
        cooldowns = state.setdefault("interactionCooldowns", {})
        cooldowns[_cooldown_key(actor_id, interaction, target)] = observed_at
        state["lastTickAt"] = observed_at
        storage.write_json(storage.state / STATE_FILE, state)


def _cooling_down(cooldowns: Dict[str, Any], key: str, instant: datetime, scenario: Dict[str, Any]) -> bool:
    previous = cooldowns.get(key)
    if not isinstance(previous, str):
        return False
    return (instant - parse_instant(previous)).total_seconds() < int(scenario["cooldownMinutes"]) * 60


def _available_teammate(roster: Dict[str, Any], people: Dict[str, Dict[str, Any]], actor_id: str, team_id: str) -> Optional[str]:
    team = next((row for row in roster.get("teams", []) if isinstance(row, dict) and row.get("id") == team_id), {})
    characters = {row.get("id"): row for row in roster.get("characters", []) if isinstance(row, dict)}
    for member in team.get("members", []) if isinstance(team, dict) else []:
        character = characters.get(member)
        candidate_id = character.get("actorId") if isinstance(character, dict) else None
        if (
            isinstance(candidate_id, str) and candidate_id != actor_id and candidate_id in people
            and member in people[actor_id].get("affinities", [])
        ):
            return candidate_id
    return None


def _high_rival_captain(roster: Dict[str, Any], team_id: str) -> Optional[str]:
    teams = {row.get("id"): row for row in roster.get("teams", []) if isinstance(row, dict)}
    for rivalry in roster.get("rivalries", []):
        if not isinstance(rivalry, dict) or rivalry.get("intensity") != "high":
            continue
        rivals = rivalry.get("teams", [])
        if not isinstance(rivals, list) or team_id not in rivals or len(rivals) != 2:
            continue
        rival_team = next((candidate for candidate in rivals if candidate != team_id), None)
        captain = teams.get(rival_team, {}).get("captainId") if isinstance(rival_team, str) else None
        character = next((row for row in roster.get("characters", []) if isinstance(row, dict) and row.get("id") == captain), {})
        actor_id = character.get("actorId") if isinstance(character, dict) else None
        if isinstance(actor_id, str):
            return actor_id
    return None


def _quiet_club_owner_offer(offers: List[Dict[str, Any]], opportunities: List[Dict[str, Any]], state: Dict[str, Any], scenario: Dict[str, Any], instant: datetime) -> Optional[Dict[str, Any]]:
    """Offer Sofia one bounded interest check when the club has gone quiet.

    This is an advertisement, not a product event or discount.  A player must
    still ask Riley for a session before the lead can plan anything.
    """
    promotion = scenario.get("quietClubPromotion")
    if not isinstance(promotion, dict) or offers or opportunities:
        return None
    previous = state.get("quietClubPromotionAt")
    if isinstance(previous, str):
        elapsed = (instant - parse_instant(previous)).total_seconds()
        if elapsed < int(promotion.get("cooldownMinutes", 0)) * 60:
            return None
    return {
        "actorId": "sofia",
        "kind": "stimulate_quiet_club",
        "reason": "no_active_player_or_staff_interaction",
        "action": "advertise_interest_check",
    }
