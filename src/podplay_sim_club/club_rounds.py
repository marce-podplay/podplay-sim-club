"""One deterministic player response to a freshly advertised club opportunity."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .storage import Storage
from .time import isoformat, parse_instant
from .team_challenges import propose_challenge


def tick(root: Path, actor_id: Optional[str] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Let one eligible captain ask Riley for the opportunity Sofia advertised.

    The social engine has already made the teammate-invitation offer legal. The
    player does not create an event or claim the teammate accepted; this is
    merely a durable request for Riley to open the next-session workflow.
    """
    storage = Storage(root)
    observed_at = isoformat(now or parse_instant(None))
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
        if offer.get("kind") not in {"invite_teammate", "challenge_rival_captain"}:
            continue
        offered_actor_id = offer.get("actorId")
        teammate_id = offer.get("teammateId")
        if not isinstance(offered_actor_id, str) or not isinstance(teammate_id, str):
            continue
        if actor_id is not None and offered_actor_id != actor_id:
            continue
        advertisement = next((row for row in advertisements.values() if offered_actor_id in row.get("to", [])), None)
        if not advertisement:
            continue
        if offer.get("kind") == "challenge_rival_captain":
            intent = propose_challenge(root, "andy-vs-kyo-doubles.json", now or parse_instant(None))
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
        return {"actorId": offered_actor_id, "action": "ask_for_new_event", "detail": "asked Riley to open the advertised session workflow", "messageId": message_id, "observedAt": observed_at, "remoteWrites": 0}
    return {"actorId": actor_id, "action": "pass", "detail": "no player has a fresh addressed opportunity", "observedAt": observed_at, "remoteWrites": 0}


def _prioritized_player_offers(offers: Any) -> list[Dict[str, Any]]:
    rows = [row for row in offers if isinstance(row, dict)] if isinstance(offers, list) else []
    return sorted(rows, key=lambda row: 0 if row.get("kind") == "challenge_rival_captain" else 1)
