"""Durable Sims-style motives and legal interaction advertisements.

This module deliberately selects offers, never product actions.  The lead may
later turn an accepted offer into a legal booking or invitation workflow.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .storage import Storage
from .time import isoformat, parse_instant
from .check_in_bridge import offers as check_in_offers


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
    _reconcile_reported_training_completions(root, instant)
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
            motive = motives.setdefault(actor_id, {"desireToPlay": 40, "curiosity": 0, "frustration": 0, "competition": 0})
            motive.setdefault("competition", 0)
            _age_motive(motive, elapsed, scenario["clock"])
        _apply_release_notes(storage, motives, state)
        opportunities = _published_opportunities(storage, people)
        _apply_missed_training_motives(storage, motives, state)
        _apply_owner_promotions(storage, motives, state)
        offers = (_staff_offers(storage, roster)
                  + _result_confirmation_offers(storage, people)
                  + _missed_session_offers(storage, motives, state, scenario, instant)
                  + _challenge_acceptance_offers(storage, people)
                  + _training_offers(storage, people, roster, motives, state, scenario, instant)
                  + _competition_challenge_offers(storage, people, roster, motives, state, scenario, instant)
                  + _booked_share_invitation_offers(storage, people, instant)
                  + _share_redemption_offers(storage, people, instant)
                  + check_in_offers(root, now=instant, people=people)
                  + _offers(people, roster, motives, opportunities, state, scenario, instant))
        owner_offer = _quiet_club_owner_offer(offers, opportunities, state, scenario, instant)
        if owner_offer:
            offers.append(owner_offer)
        state.update({"schemaVersion": 1, "lastTickAt": observed_at, "offers": offers, "opportunities": opportunities})
        storage.write_json(storage.state / STATE_FILE, state)
    return {"observedAt": observed_at, "offers": offers, "opportunities": opportunities, "motives": motives}


def _reconcile_reported_training_completions(root: Path, instant: datetime) -> None:
    """Apply missed local social consequences from already-reported results.

    Result reporting is the evidence boundary.  This reconciler exists so a
    process restart or a newly introduced consequence cannot strand an
    otherwise valid historical result; occurrence-level idempotency keeps it
    from replaying the social effect.
    """
    storage = Storage(root)
    ledger = storage.load_json(storage.state / "booking-intents.json", default={})
    intents = ledger.get("intents", {}) if isinstance(ledger, dict) else {}
    if not isinstance(intents, dict):
        return
    for intent in intents.values():
        if not isinstance(intent, dict) or intent.get("journey") != "customer_training":
            continue
        result = intent.get("result")
        team_id = intent.get("trainingTeamId")
        participants = intent.get("participants")
        if (
            not isinstance(result, dict)
            or result.get("status") != "reported"
            or not isinstance(team_id, str)
            or not isinstance(participants, list)
            or not all(isinstance(actor_id, str) for actor_id in participants)
        ):
            continue
        booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
        plan = intent.get("previewPlan") if isinstance(intent.get("previewPlan"), dict) else {}
        occurrence = intent.get("occurrenceKey") or booking.get("occurrenceKey") or plan.get("occurrenceKey") or result.get("eventId")
        if not isinstance(occurrence, str):
            continue
        reported_at = result.get("reportedAt")
        try:
            completed_at = parse_instant(reported_at) if isinstance(reported_at, str) else instant
        except ValueError:
            completed_at = instant
        record_completed_event(root, participants, now=completed_at, team_id=team_id, occurrence_key=occurrence)


def _result_confirmation_offers(storage: Storage, people: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Offer a replay confirmation only to its designated captain.

    The offer is intentionally narrower than a generic result prompt.  It
    exists only when a completed player session already has a deterministic
    local Arcade replay and the result request explicitly names that replay as
    its evidence.  The player can therefore confirm a grounded winner without
    turning check-in evidence into a fabricated score or result.
    """
    ledger = storage.load_json(storage.state / "booking-intents.json", default={})
    intents = ledger.get("intents", {}) if isinstance(ledger, dict) else {}
    if not isinstance(intents, dict):
        return []
    offers: List[Dict[str, Any]] = []
    for intent_id, intent in intents.items():
        if not isinstance(intent_id, str) or not isinstance(intent, dict):
            continue
        request = intent.get("resultRequest")
        replay = intent.get("arcadeReplay")
        reporter = request.get("reporterActorId") if isinstance(request, dict) else None
        if (
            not isinstance(request, dict)
            or request.get("status") != "requested"
            or request.get("evidence") != "arcade_replay"
            or not isinstance(replay, dict)
            or replay.get("status") != "ready"
            or not isinstance(reporter, str)
            or reporter not in people
        ):
            continue
        winner = replay.get("winner")
        if not isinstance(winner, list) or not winner or not all(isinstance(actor_id, str) and actor_id for actor_id in winner):
            continue
        offers.append({
            "actorId": reporter,
            "kind": "confirm_arcade_replay_result",
            "intentId": intent_id,
            "eventId": request.get("eventId"),
            "reason": "designated_captain_has_grounded_arcade_replay_confirmation",
        })
    return offers


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


def record_completed_event(
    root: Path, participants: List[str], now: Optional[datetime] = None, *,
    team_id: Optional[str] = None, occurrence_key: Optional[str] = None,
) -> None:
    """Apply one verified shared-completion consequence, never a check-in guess.

    Training completion has two deliberately separate effects: the named
    teammates gain cohesion, while their captain becomes more interested in a
    *different* team challenge.  A short recovery window suppresses another
    immediate practice.  All effects are idempotent per stable occurrence.
    """
    storage = Storage(root)
    observed_at = isoformat(now or parse_instant(None))
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        completed = state.setdefault("completedOccurrences", {})
        completion_key = occurrence_key or "|".join(sorted(participants))
        if completion_key in completed:
            return
        completed[completion_key] = observed_at
        closeness = state.setdefault("closeness", {})
        scenario = storage.load_json(root / "scenarios" / SCENARIO_FILE)
        post = scenario.get("postSession", {}) if isinstance(scenario, dict) else {}
        desire_drop = int(post.get("trainingDesireDrop", 35))
        competition_boost = int(post.get("competitionBoost", 30))
        cohesion_boost = int(post.get("cohesionBoost", 10))
        for left in participants:
            for right in participants:
                if left < right:
                    key = f"{left}|{right}"
                    closeness[key] = min(100, int(closeness.get(key, 0)) + 10)
            motive = state.setdefault("motives", {}).setdefault(left, {"desireToPlay": 40, "curiosity": 0, "frustration": 0, "competition": 0})
            motive["desireToPlay"] = max(0, int(motive["desireToPlay"]) - desire_drop)
            motive["competition"] = min(100, int(motive.get("competition", 0)) + competition_boost)
        if isinstance(team_id, str) and team_id:
            teams = state.setdefault("teamDynamics", {})
            team = teams.setdefault(team_id, {"cohesion": 0, "competition": 0})
            team["cohesion"] = min(100, int(team.get("cohesion", 0)) + cohesion_boost)
            team["competition"] = min(100, int(team.get("competition", 0)) + competition_boost)
            team["lastTrainingCompletedAt"] = observed_at
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


def _apply_owner_promotions(storage: Storage, motives: Dict[str, Dict[str, Any]], state: Dict[str, Any]) -> None:
    """A Sofia training or agreed-challenge promotion raises desire once."""
    applied = state.setdefault("ownerPromotionsApplied", {})
    for row in storage.read_jsonl(storage.channel_path("club")):
        if not isinstance(row, dict) or row.get("kind") not in {"owner_team_training_nudge", "owner_challenge_promotion"}:
            continue
        message_id, targets = row.get("id"), row.get("to")
        if not isinstance(message_id, str) or message_id in applied or not isinstance(targets, list):
            continue
        for actor_id in targets:
            if actor_id in motives:
                boost = 15 if row.get("kind") == "owner_team_training_nudge" else 10
                motives[actor_id]["desireToPlay"] = min(100, int(motives[actor_id].get("desireToPlay", 0)) + boost)
        applied[message_id] = True


def _missed_session_offers(
    storage: Storage,
    motives: Dict[str, Dict[str, Any]],
    state: Dict[str, Any],
    scenario: Dict[str, Any],
    instant: datetime,
) -> List[Dict[str, Any]]:
    """Offer a fresh social proposal; never infer continued agreement."""
    from .session_retries import eligible_proposers, required_acceptor, retry_intent_id

    ledger = storage.load_json(storage.state / "booking-intents.json", default={})
    intents = ledger.get("intents", {}) if isinstance(ledger, dict) else {}
    if not isinstance(intents, dict):
        return []
    threshold = int(scenario["thresholds"]["desireToPlay"])
    cooldowns = state.setdefault("interactionCooldowns", {})
    offers: List[Dict[str, Any]] = []
    for source in intents.values():
        if not isinstance(source, dict) or source.get("status") != "missed_session":
            continue
        observation = source.get("eventObservation")
        if not isinstance(observation, dict) or observation.get("outcome") != "missed_session":
            continue
        try:
            retry_id = retry_intent_id(source)
        except ValueError:
            continue
        retry = intents.get(retry_id)
        if isinstance(retry, dict):
            if retry.get("status") != "captain_invited":
                continue
            proposer = retry.get("requestedBy")
            if not isinstance(proposer, str):
                continue
            try:
                acceptor = required_acceptor(source, proposer)
            except ValueError:
                continue
            if acceptor in motives:
                offers.append({
                    "actorId": acceptor,
                    "kind": "accept_session_retry",
                    "intentId": retry_id,
                    "sourceIntentId": source.get("id"),
                    "reason": "fresh_session_proposal_addressed_to_required_acceptor",
                })
            continue
        for proposer in eligible_proposers(source):
            motive = motives.get(proposer)
            if not isinstance(motive, dict) or int(motive.get("desireToPlay", 0)) < threshold:
                continue
            try:
                acceptor = required_acceptor(source, proposer)
            except ValueError:
                continue
            event_id = str(observation.get("eventId") or source.get("id") or "unknown")
            key = _cooldown_key(proposer, "propose_session_retry", event_id)
            if _cooling_down(cooldowns, key, instant, scenario):
                continue
            offers.append({
                "actorId": proposer,
                "kind": "propose_session_retry",
                "sourceIntentId": source.get("id"),
                "recipientActorId": acceptor,
                "reason": "missed_session_and_renewed_desire_to_play",
            })
    return offers


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


def _challenge_acceptance_offers(
    storage: Storage, people: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """An addressed captain challenge is a hard-gated response opportunity."""
    ledger = storage.load_json(storage.state / "booking-intents.json", default={})
    offers: List[Dict[str, Any]] = []
    for intent in ledger.get("intents", {}).values() if isinstance(ledger, dict) else []:
        if not isinstance(intent, dict) or intent.get("journey") != "customer_team_signup" or intent.get("status") != "captain_invited":
            continue
        opponent = intent.get("opponent")
        captain = opponent.get("captainActorId") if isinstance(opponent, dict) else None
        if isinstance(captain, str) and captain in people:
            offers.append({
                "actorId": captain,
                "kind": "accept_team_challenge",
                "intentId": intent.get("id"),
                "reason": "captain_challenge_addressed_to_opposing_captain",
            })
    return offers


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
        if isinstance(team_id, str) and _training_recovery_active(state, team_id, instant, scenario):
            continue
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


def _competition_challenge_offers(
    storage: Storage, people: Dict[str, Dict[str, Any]], roster: Dict[str, Any], motives: Dict[str, Any],
    state: Dict[str, Any], scenario: Dict[str, Any], instant: datetime,
) -> List[Dict[str, Any]]:
    """A completed practice makes a captain likely to challenge any ready rival.

    No pre-ranked rivalry is required: every other rostered team with a named,
    currently active captain and teammate is a potential rival.  This remains
    a captain-to-captain invitation, not an inferred match or booking.
    """
    threshold = int(scenario.get("thresholds", {}).get("competition", 20))
    if not isinstance(state.get("teamDynamics"), dict):
        return []
    offers: List[Dict[str, Any]] = []
    for actor_id, person in people.items():
        team_ids = person.get("captainOf", [])
        team_id = next(iter(team_ids), None) if isinstance(team_ids, list) else None
        if not isinstance(team_id, str) or not _competition_window_open(state, team_id, instant, scenario):
            continue
        team = state["teamDynamics"].get(team_id, {})
        if int(team.get("competition", 0)) < threshold or int(motives.get(actor_id, {}).get("competition", 0)) < threshold:
            continue
        rival = _available_rival_captain(roster, people, team_id)
        if rival is not None:
            offers.append({
                "actorId": actor_id, "kind": "challenge_rival_captain", "rivalCaptainId": rival,
                "reason": "recent_team_training_increased_competitive_appetite",
            })
    return offers


def _booked_share_invitation_offers(
    storage: Storage, people: Dict[str, Dict[str, Any]], instant: datetime,
) -> List[Dict[str, Any]]:
    """Advertise the owner-only next step for a current named-player booking.

    This is intentionally an advertisement, not a call to PodPlay. The share
    adapter remains unavailable until the owner makes a later durable choice.
    A retained link is private evidence that the offer has already been
    satisfied, while an ended window is history rather than a new invitation
    opportunity.
    """
    ledger = storage.load_json(storage.state / "booking-intents.json", default={})
    intents = ledger.get("intents", {}) if isinstance(ledger, dict) else {}
    sidecar = storage.load_json(storage.state / "share-invitations.json", default={})
    retained = sidecar.get("invitations", {}) if isinstance(sidecar, dict) else {}
    if not isinstance(intents, dict) or not isinstance(retained, dict):
        return []
    offers: List[Dict[str, Any]] = []
    for intent_id, intent in intents.items():
        if not isinstance(intent_id, str) or not isinstance(intent, dict):
            continue
        if intent.get("journey") not in {"customer_training", "customer_team_signup"}:
            continue
        if intent.get("status") not in {"booked", "team_signup_in_progress", "team_members_registered"}:
            continue
        owner = intent.get("bookerActorId")
        participants = intent.get("participants")
        booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
        plan = intent.get("previewPlan") if isinstance(intent.get("previewPlan"), dict) else {}
        occurrence = booking.get("occurrenceKey")
        event_id = booking.get("eventId")
        recipients = [
            actor_id for actor_id in participants
            if isinstance(actor_id, str) and actor_id != owner
        ] if isinstance(participants, list) else []
        if (
            not isinstance(owner, str) or owner not in people
            or not isinstance(occurrence, str) or not isinstance(event_id, str)
            or not recipients or occurrence in retained
        ):
            continue
        end_time = plan.get("endTime")
        if isinstance(end_time, str) and parse_instant(end_time) <= instant:
            continue
        offers.append({
            "actorId": owner,
            "kind": "capture_share_invitation",
            "intentId": intent_id,
            "occurrenceKey": occurrence,
            "recipientActorIds": recipients,
            "reason": "booked_named_players_need_private_invitation_handoff",
        })
    return offers


def _share_redemption_offers(
    storage: Storage, people: Dict[str, Dict[str, Any]], instant: datetime,
) -> List[Dict[str, Any]]:
    """Advertise redemption only to a named recipient after capture evidence."""
    ledger = storage.load_json(storage.state / "booking-intents.json", default={})
    intents = ledger.get("intents", {}) if isinstance(ledger, dict) else {}
    sidecar = storage.load_json(storage.state / "share-invitations.json", default={})
    retained = sidecar.get("invitations", {}) if isinstance(sidecar, dict) else {}
    if not isinstance(intents, dict) or not isinstance(retained, dict):
        return []
    offers: List[Dict[str, Any]] = []
    for intent_id, intent in intents.items():
        if not isinstance(intent_id, str) or not isinstance(intent, dict):
            continue
        if intent.get("journey") not in {"customer_training", "customer_team_signup"}:
            continue
        booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
        plan = intent.get("previewPlan") if isinstance(intent.get("previewPlan"), dict) else {}
        occurrence, owner = booking.get("occurrenceKey"), intent.get("bookerActorId")
        if not isinstance(occurrence, str) or not isinstance(owner, str) or not isinstance(retained.get(occurrence), dict):
            continue
        end_time = plan.get("endTime")
        if isinstance(end_time, str) and parse_instant(end_time) <= instant:
            continue
        accepted = {
            row.get("actorId") for row in intent.get("shareRedemptions", [])
            if isinstance(row, dict) and row.get("status") == "ACCEPTED"
        }
        for recipient in intent.get("participants", []) if isinstance(intent.get("participants"), list) else []:
            if isinstance(recipient, str) and recipient != owner and recipient in people and recipient not in accepted:
                offers.append({
                    "actorId": recipient, "kind": "redeem_share_invitation", "intentId": intent_id,
                    "occurrenceKey": occurrence, "ownerActorId": owner,
                    "reason": "named_player_has_private_share_handoff_available",
                })
    return offers


def _age_motive(motive: Dict[str, Any], minutes: float, clock: Dict[str, Any]) -> None:
    motive["desireToPlay"] = min(100, int(motive.get("desireToPlay", 0)) + round(minutes * int(clock["desirePerHour"]) / 60))
    motive["curiosity"] = max(0, int(motive.get("curiosity", 0)) - round(minutes * int(clock["curiosityDecayPerHour"]) / 60))
    motive["frustration"] = max(0, int(motive.get("frustration", 0)) - round(minutes * int(clock["frustrationDecayPerHour"]) / 60))
    motive["competition"] = max(0, int(motive.get("competition", 0)) - round(minutes * 6 / 60))


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
                if _training_recovery_active(state, team_id, instant, scenario):
                    continue
                teammate = _available_teammate(roster, people, actor_id, team_id)
                if teammate:
                    invite_key = _cooldown_key(actor_id, "invite_teammate", teammate)
                    if not _cooling_down(cooldowns, invite_key, instant, scenario):
                        offers.append({"actorId": actor_id, "kind": "invite_teammate", "teammateId": teammate, "reason": "desire_to_play_and_static_affinity"})
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


def _available_rival_captain(roster: Dict[str, Any], people: Dict[str, Dict[str, Any]], team_id: str) -> Optional[str]:
    """Select the first ready opposing captain without a rivalry ranking."""
    characters = {row.get("id"): row for row in roster.get("characters", []) if isinstance(row, dict)}
    for team in sorted((row for row in roster.get("teams", []) if isinstance(row, dict)), key=lambda row: str(row.get("id", ""))):
        if team.get("id") == team_id:
            continue
        captain = characters.get(team.get("captainId"), {})
        captain_id = captain.get("actorId") if isinstance(captain, dict) else None
        members = team.get("members", [])
        teammate_ids = [characters.get(member, {}).get("actorId") for member in members if member != team.get("captainId")]
        if isinstance(captain_id, str) and captain_id in people and any(isinstance(value, str) and value in people for value in teammate_ids):
            return captain_id
    return None


def _training_recovery_active(state: Dict[str, Any], team_id: str, instant: datetime, scenario: Dict[str, Any]) -> bool:
    team = state.get("teamDynamics", {}).get(team_id, {}) if isinstance(state.get("teamDynamics"), dict) else {}
    completed = team.get("lastTrainingCompletedAt") if isinstance(team, dict) else None
    if not isinstance(completed, str):
        return False
    minutes = int(scenario.get("postSession", {}).get("trainingRecoveryMinutes", 120))
    return (instant - parse_instant(completed)).total_seconds() < minutes * 60


def _competition_window_open(state: Dict[str, Any], team_id: str, instant: datetime, scenario: Dict[str, Any]) -> bool:
    team = state.get("teamDynamics", {}).get(team_id, {}) if isinstance(state.get("teamDynamics"), dict) else {}
    completed = team.get("lastTrainingCompletedAt") if isinstance(team, dict) else None
    if not isinstance(completed, str):
        return False
    minutes = int(scenario.get("postSession", {}).get("competitionWindowMinutes", 360))
    return (instant - parse_instant(completed)).total_seconds() <= minutes * 60


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
