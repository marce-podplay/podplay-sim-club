"""One deterministic player response to a freshly advertised club opportunity."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .storage import Storage
from .time import isoformat, parse_instant
from .team_challenges import propose_challenge
from .training_sessions import accept_intent as accept_training_intent, propose_pair as propose_training_pair
from .social_engine import record_interaction_cooldown
from .share_invitations import expire_unsent_booking_invitation


def tick(root: Path, actor_id: Optional[str] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Let one eligible captain ask Riley for the opportunity Sofia advertised.

    The social engine has already made the teammate-invitation offer legal. The
    player does not create an event or claim the teammate accepted; this is
    merely a durable request for Riley to open the next-session workflow.
    """
    storage = Storage(root)
    observed_at = isoformat(now or parse_instant(None))
    # A retained link is only a possible invitation.  Let its owner report the
    # missed window before considering any new social offer.
    if actor_id == "king-captain":
        expired = expire_unsent_booking_invitation(root, owner_actor_id=actor_id, now=now)
        if expired.get("action") != "pass":
            return expired
    social = storage.load_json(storage.state / "social-engine.json", default={})
    offers = social.get("offers", []) if isinstance(social, dict) else []
    messages = storage.read_jsonl(storage.channel_path("club"))
    advertisements = {
        row.get("id"): row
        for row in messages
        if isinstance(row, dict) and row.get("kind") == "owner_interest_check" and isinstance(row.get("id"), str)
    }
    existing = {row.get("id") for row in messages if isinstance(row, dict)}
    for offer in _prioritized_player_offers(offers):
        if offer.get("kind") not in {"invite_teammate", "challenge_rival_captain", "propose_team_training", "accept_team_training"}:
            continue
        offered_actor_id = offer.get("actorId")
        teammate_id = offer.get("teammateId")
        if not isinstance(offered_actor_id, str):
            continue
        if actor_id is not None and offered_actor_id != actor_id:
            continue
        if offer.get("kind") == "accept_team_training":
            intent_id = offer.get("intentId")
            if not isinstance(intent_id, str):
                continue
            intent = accept_training_intent(root, intent_id, offered_actor_id, now or parse_instant(None))
            return {"actorId": offered_actor_id, "action": "accept_team_training", "detail": "accepted a captain-led team practice invitation", "intentId": intent["id"], "observedAt": observed_at, "remoteWrites": 0}
        if not isinstance(teammate_id, str):
            continue
        if offer.get("kind") == "propose_team_training":
            intent = propose_training_pair(root, offered_actor_id, teammate_id, now or parse_instant(None))
            if intent.get("status") != "captain_invited":
                continue
            record_interaction_cooldown(root, offered_actor_id, "propose_team_training", teammate_id, now)
            return {"actorId": offered_actor_id, "action": "propose_team_training", "detail": "invited a compatible teammate to a 30-minute W37 practice", "intentId": intent["id"], "observedAt": observed_at, "remoteWrites": 0}
        # Use the newest addressed interest check that has not already produced
        # this player's request. Older announcements remain history, not a
        # permanent blocker for a later club round.
        advertisement = next(
            (
                row for row in reversed(list(advertisements.values()))
                if offered_actor_id in row.get("to", [])
                and f"{row['id']}:{offered_actor_id}:new-event-request" not in existing
            ),
            None,
        )
        if not advertisement:
            continue
        if offer.get("kind") == "challenge_rival_captain":
            intent = propose_challenge(root, "andy-vs-kyo-doubles.json", now or parse_instant(None))
            # A historical/agreed challenge is not a fresh player action. Let
            # the captain consider the distinct teammate invitation below.
            if intent.get("status") != "captain_invited":
                continue
            record_interaction_cooldown(
                root, offered_actor_id, "challenge_rival_captain",
                str(offer.get("rivalCaptainId") or ""), now,
            )
            return {"actorId": offered_actor_id, "action": "challenge_rival_captain", "detail": "invited the rival captain to an unbooked doubles challenge", "intentId": intent["id"], "observedAt": observed_at, "remoteWrites": 0}
        message_id = f"{advertisement['id']}:{offered_actor_id}:new-event-request"
        if message_id in existing:
            continue
        with storage.writer_lock():
            if message_id in {row.get("id") for row in storage.read_jsonl(storage.channel_path("club")) if isinstance(row, dict)}:
                continue
            storage.append_jsonl(storage.channel_path("club"), {
                "id": message_id,
                "channel": "club",
                "from": offered_actor_id,
                "to": ["riley"],
                "kind": "new_event_request",
                "inReplyTo": advertisement["id"],
                "teammateId": teammate_id,
                "text": "Sofia's interest check got my attention. Please open a new W37 session request; I will invite my teammate if it is promoted.",
                "observedAt": observed_at,
                "remoteWrites": 0,
            })
        record_interaction_cooldown(root, offered_actor_id, "invite_teammate", teammate_id, now)
        return {"actorId": offered_actor_id, "action": "ask_for_new_event", "detail": "asked Riley to open the advertised session workflow", "messageId": message_id, "observedAt": observed_at, "remoteWrites": 0}
    return {"actorId": actor_id, "action": "pass", "detail": "no player has a fresh addressed opportunity", "observedAt": observed_at, "remoteWrites": 0}


def _prioritized_player_offers(offers: Any) -> list[Dict[str, Any]]:
    rows = [row for row in offers if isinstance(row, dict)] if isinstance(offers, list) else []
    priority = {"accept_team_training": 0, "propose_team_training": 1, "challenge_rival_captain": 2, "invite_teammate": 3}
    return sorted(rows, key=lambda row: priority.get(row.get("kind"), 9))
