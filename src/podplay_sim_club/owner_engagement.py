"""Sofia's bounded response when Preview Club has no active interaction.

The owner can advertise a future free session.  The advertisement is local
club speech: it cannot publish an event, issue a product promotion, or create
a booking.  A player must subsequently request a session through Riley.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .booking_intents import load_intent_ledger, save_intent
from .storage import Storage
from .time import isoformat, parse_instant


def tick(root: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    storage = Storage(root)
    instant = now or parse_instant(None)
    observed_at = isoformat(instant)
    follow_up = _encourage_team_training(storage, observed_at)
    if follow_up is not None:
        return follow_up
    challenge_promotion = _promote_agreed_team_challenge(storage, observed_at)
    if challenge_promotion is not None:
        return challenge_promotion
    scenario = storage.load_json(root / "scenarios" / "social-engine.json")
    promotion = scenario.get("quietClubPromotion") if isinstance(scenario, dict) else None
    if not isinstance(promotion, dict):
        return _pass(observed_at, "quiet-club promotion is not configured")

    with storage.writer_lock():
        social = storage.load_json(storage.state / "social-engine.json", default={})
        offers = social.get("offers", []) if isinstance(social, dict) else []
        offer = next((row for row in offers if isinstance(row, dict) and row.get("actorId") == "sofia" and row.get("kind") == "stimulate_quiet_club"), None)
        if not offer:
            return _pass(observed_at, "no quiet-club owner offer")
        motives = social.get("motives", {})
        if not isinstance(motives, dict):
            return _pass(observed_at, "player motives are unavailable")
        targets = [actor_id for actor_id, motive in motives.items() if isinstance(actor_id, str) and isinstance(motive, dict)]
        if not targets:
            return _pass(observed_at, "no enabled players to address")
        boost = int(promotion.get("desireBoost", 0))
        for actor_id in targets:
            motive = motives[actor_id]
            motive["desireToPlay"] = min(100, int(motive.get("desireToPlay", 0)) + boost)
        count = int(social.get("quietClubPromotionCount", 0)) + 1
        social["quietClubPromotionAt"] = observed_at
        social["quietClubPromotionCount"] = count
        storage.write_json(storage.state / "social-engine.json", social)
        message_id = f"sofia:quiet-club:{count}"
        storage.append_jsonl(storage.channel_path("club"), {
            "id": message_id,
            "occurrenceKey": "owner:quiet-club",
            "channel": "club",
            "from": "sofia",
            "to": targets,
            "kind": "owner_interest_check",
            "text": "The club is quiet. I can promote a free W37 session when a player asks Riley for a new event—who wants the next match?",
            "observedAt": observed_at,
            "remoteWrites": 0,
        })
    return {
        "actorId": "sofia", "action": "advertise_interest_check",
        "detail": "addressed quiet-club players with a local interest check",
        "messageId": message_id, "targets": targets, "observedAt": observed_at,
        "remoteWrites": 0,
    }


def _pass(observed_at: str, detail: str) -> Dict[str, Any]:
    return {"actorId": "sofia", "action": "pass", "detail": detail, "observedAt": observed_at, "remoteWrites": 0}


def _encourage_team_training(storage: Storage, observed_at: str) -> Optional[Dict[str, Any]]:
    """Let Sofia turn an evidenced session into a local training nudge once.

    This is promotion, not a booking or a challenge.  Captains must still
    create any future agreement and the normal player booking flow applies.
    """
    with storage.writer_lock():
        activation_state = storage.load_json(storage.state / "team-activations.json", default={})
        activations = activation_state.get("activations", {}) if isinstance(activation_state, dict) else {}
        roster = storage.load_json(storage.root / "characters" / "roster.json", default={})
        teams = {
            row.get("id"): row for row in roster.get("teams", [])
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        } if isinstance(roster, dict) else {}
        if isinstance(activations, dict):
            for team_id, activation in activations.items():
                if not isinstance(team_id, str) or not isinstance(activation, dict) or activation.get("announcedAt"):
                    continue
                team = teams.get(team_id)
                targets = activation.get("actorIds")
                captain = None
                if isinstance(team, dict):
                    captain_character = team.get("captainId")
                    characters = {
                        row.get("id"): row for row in roster.get("characters", []) if isinstance(row, dict)
                    }
                    captain_row = characters.get(captain_character)
                    captain = captain_row.get("actorId") if isinstance(captain_row, dict) else None
                if not isinstance(team, dict) or not isinstance(targets, list) or not all(isinstance(actor, str) for actor in targets) or not isinstance(captain, str) or captain not in targets:
                    continue
                activation["announcedAt"] = observed_at
                storage.write_json(storage.state / "team-activations.json", activation_state)
                team_name = str(team.get("name") or team_id)
                storage.append_jsonl(storage.channel_path("club"), {
                    "id": f"team-activation:{team_id}:training-welcome",
                    "occurrenceKey": f"team-activation:{team_id}",
                    "channel": "club", "from": "sofia", "to": targets,
                    "kind": "owner_team_training_nudge",
                    "motiveBoost": 45,
                    "text": f"Welcome, {team_name}. Your player accounts are ready. A short training session is available whenever your captain and one teammate choose to arrange one.",
                    "observedAt": observed_at, "remoteWrites": 0,
                })
                return {
                    "actorId": "sofia", "action": "welcome_verified_team",
                    "detail": "advertised a local training opportunity to a newly verified team",
                    "teamId": team_id, "targets": targets, "observedAt": observed_at,
                    "remoteWrites": 0,
                }
        ledger = load_intent_ledger(storage)
        source = next((intent for intent in ledger["intents"].values() if isinstance(intent, dict)
                       and intent.get("journey") == "customer_training"
                       and isinstance(intent.get("resultRequest"), dict)
                       and intent["resultRequest"].get("status") == "requested"
                       and not intent.get("teamTrainingPromotionAt")), None)
        if source is None:
            return None
        targets = ["andy-bogard", "terry-bogard", "kyo-captain", "benimaru"]
        source["teamTrainingPromotionAt"] = observed_at
        source["teamTrainingPromotionTargets"] = targets
        save_intent(storage, ledger, source, make_active=False)
        storage.append_jsonl(storage.channel_path("club"), {
            "id": f"{source['id']}:team-training-promotion",
            "occurrenceKey": "owner:post-training-promotion",
            "channel": "club", "from": "sofia", "to": targets,
            "kind": "owner_team_training_nudge",
            "inReplyTo": f"event:{source['resultRequest'].get('eventId')}:result-requested",
            "text": "While Women Fighters' result is awaiting a report, Bogard-Higashi and Japan Team should each arrange a training session. After the current training result is reported, I can promote a team challenge—but captains decide whether to arrange it.",
            "observedAt": observed_at, "remoteWrites": 0,
        })
    return {
        "actorId": "sofia", "action": "encourage_team_training",
        "detail": "asked Bogard-Higashi and Japan Team to arrange training before any promoted challenge",
        "targets": targets, "observedAt": observed_at, "remoteWrites": 0,
    }


def _promote_agreed_team_challenge(storage: Storage, observed_at: str) -> Optional[Dict[str, Any]]:
    """Let Sofia amplify one captain-agreed challenge without arranging it.

    The challenge must already exist because two captains communicated their
    agreement.  Sofia's promotion makes that social opportunity visible to its
    named players; the guarded lead bridge still owns availability, booking,
    and any product invitation writes.
    """
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        source = next((
            intent for intent in ledger["intents"].values()
            if isinstance(intent, dict)
            and intent.get("journey") == "customer_team_signup"
            and intent.get("status") in {"agreed", "preview_ready"}
            and not intent.get("ownerChallengePromotionAt")
        ), None)
        if source is None:
            return None
        participants = [actor for actor in source.get("participants", []) if isinstance(actor, str)]
        challenger = source.get("challenger") if isinstance(source.get("challenger"), dict) else {}
        opponent = source.get("opponent") if isinstance(source.get("opponent"), dict) else {}
        challenger_captain = challenger.get("captainActorId")
        opponent_captain = opponent.get("captainActorId")
        if len(participants) < 2 or not isinstance(challenger_captain, str) or not isinstance(opponent_captain, str):
            return None
        source["ownerChallengePromotionAt"] = observed_at
        source["ownerChallengePromotionTargets"] = participants
        save_intent(storage, ledger, source, make_active=False)
        challenger_name = challenger.get("teamName", "the challenger")
        opponent_name = opponent.get("teamName", "the opponent")
        storage.append_jsonl(storage.channel_path("club"), {
            "id": f"{source['id']}:owner-challenge-promotion",
            "occurrenceKey": "owner:captain-agreed-challenge",
            "channel": "club", "from": "sofia", "to": participants,
            "kind": "owner_challenge_promotion",
            "intentId": source["id"],
            "inReplyTo": f"{source['id']}:agreement",
            "text": f"{challenger_name} and {opponent_name} have captain agreement for a team match. Keep your partners ready: once the lead confirms one legal court, the initiating captain can book and the named teammates will be added.",
            "observedAt": observed_at, "remoteWrites": 0,
        })
    return {
        "actorId": "sofia", "action": "promote_captain_agreed_challenge",
        "detail": "amplified a captain-agreed team challenge without creating a booking",
        "intentId": source["id"], "targets": participants,
        "observedAt": observed_at, "remoteWrites": 0,
    }
