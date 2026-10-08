"""One deterministic player response to a freshly advertised club opportunity."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .storage import Storage
from .time import isoformat, parse_instant
from .team_challenges import accept_challenge, defer_challenge_for_capacity, propose_captain_challenge
from .training_sessions import accept_intent as accept_training_intent, propose_pair as propose_training_pair
from .social_engine import record_interaction_cooldown
from .share_invitations import expire_unsent_booking_invitation
from .share_invitations import record_share_attention, record_share_choice
from .check_in_bridge import record_choice as record_check_in_choice
from .session_retries import accept_retry, propose_retry
from .result_requests import report_finished_event_result
from .payer_approval import accept as accept_payer_approval


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
        if offer.get("kind") not in {"invite_teammate", "challenge_rival_captain", "accept_team_challenge", "ask_riley_for_challenge_deferral", "approve_booking_plan", "propose_team_training", "accept_team_training", "propose_session_retry", "accept_session_retry", "capture_share_invitation", "acknowledge_share_invitation", "redeem_share_invitation", "check_in_training", "confirm_arcade_replay_result"}:
            continue
        offered_actor_id = offer.get("actorId")
        teammate_id = offer.get("teammateId")
        if not isinstance(offered_actor_id, str):
            continue
        if actor_id is not None and offered_actor_id != actor_id:
            continue
        if offer.get("kind") == "confirm_arcade_replay_result":
            intent_id = offer.get("intentId")
            if not isinstance(intent_id, str):
                continue
            ledger = storage.load_json(storage.state / "booking-intents.json", default={})
            intent = ledger.get("intents", {}).get(intent_id) if isinstance(ledger, dict) else None
            replay = intent.get("arcadeReplay") if isinstance(intent, dict) else None
            winner = replay.get("winner") if isinstance(replay, dict) else None
            if not isinstance(winner, list) or not winner or not isinstance(winner[0], str):
                continue
            result = report_finished_event_result(
                root, intent_id, offered_actor_id, winner[0], now or parse_instant(None)
            )
            return {"actorId": offered_actor_id, "action": "confirm_arcade_replay_result", "detail": "confirmed the grounded Club Arcade replay result", "intentId": intent_id, "winner": result["winner"], "observedAt": result["reportedAt"], "remoteWrites": 0}
        if offer.get("kind") == "approve_booking_plan":
            booking_plan_id = offer.get("bookingPlanId")
            if not isinstance(booking_plan_id, str):
                continue
            approval = accept_payer_approval(root, booking_plan_id, offered_actor_id, observed_at)
            return {"actorId": offered_actor_id, "action": "approve_booking_plan", "detail": "accepted the fresh, expectation-matched booking quote as its named payer", "bookingPlanId": booking_plan_id, "observedAt": observed_at, "remoteWrites": 0, "approval": approval["status"]}
        if offer.get("kind") == "check_in_training":
            intent_id = offer.get("intentId")
            if not isinstance(intent_id, str):
                continue
            choice = record_check_in_choice(root, intent_id=intent_id, actor_id=offered_actor_id, now=now or parse_instant(None))
            return {"actorId": offered_actor_id, "action": "check_in_training", "detail": "made a durable arrival choice; product check-in remains lead-gated", "intentId": intent_id, "observedAt": choice["observedAt"], "remoteWrites": 0}
        if offer.get("kind") == "acknowledge_share_invitation":
            intent_id = offer.get("intentId")
            if not isinstance(intent_id, str):
                continue
            attention = record_share_attention(root, intent_id=intent_id, actor_id=offered_actor_id, now=now)
            return {"actorId": offered_actor_id, "action": "acknowledge_share_invitation", "detail": "acknowledged the private invitation handoff; a separate recipient decision remains required", "intentId": intent_id, "observedAt": attention["observedAt"], "remoteWrites": 0}
        if offer.get("kind") in {"capture_share_invitation", "redeem_share_invitation"}:
            intent_id = offer.get("intentId")
            if not isinstance(intent_id, str):
                continue
            action = "capture" if offer.get("kind") == "capture_share_invitation" else "redeem"
            choice = record_share_choice(root, intent_id=intent_id, actor_id=offered_actor_id, action=action, now=now)
            return {"actorId": offered_actor_id, "action": f"{action}_share_invitation", "detail": "made a durable player-owned share invitation choice; Preview handoff remains lead-gated", "intentId": intent_id, "observedAt": choice["observedAt"], "remoteWrites": 0}
        if offer.get("kind") == "accept_session_retry":
            intent_id = offer.get("intentId")
            if not isinstance(intent_id, str):
                continue
            intent = accept_retry(root, intent_id, offered_actor_id, now or parse_instant(None))
            return {"actorId": offered_actor_id, "action": "accept_session_retry", "detail": "accepted a fresh session proposal after the prior missed event", "intentId": intent["id"], "observedAt": observed_at, "remoteWrites": 0}
        if offer.get("kind") == "propose_session_retry":
            source_id = offer.get("sourceIntentId")
            recipient = offer.get("recipientActorId")
            if not isinstance(source_id, str) or not isinstance(recipient, str):
                continue
            intent = propose_retry(root, source_id, offered_actor_id, now or parse_instant(None))
            event_id = intent.get("evidence", {}).get("missedEventId")
            record_interaction_cooldown(root, offered_actor_id, "propose_session_retry", str(event_id or source_id), now)
            return {"actorId": offered_actor_id, "action": "propose_session_retry", "detail": "proposed a fresh session to the required teammate or opposing captain", "intentId": intent["id"], "observedAt": observed_at, "remoteWrites": 0}
        if offer.get("kind") == "accept_team_training":
            intent_id = offer.get("intentId")
            if not isinstance(intent_id, str):
                continue
            intent = accept_training_intent(root, intent_id, offered_actor_id, now or parse_instant(None))
            return {"actorId": offered_actor_id, "action": "accept_team_training", "detail": "accepted a captain-led team practice invitation", "intentId": intent["id"], "observedAt": observed_at, "remoteWrites": 0}
        if offer.get("kind") == "accept_team_challenge":
            intent_id = offer.get("intentId")
            if not isinstance(intent_id, str):
                continue
            intent = accept_challenge(root, intent_id, offered_actor_id, now or parse_instant(None))
            return {"actorId": offered_actor_id, "action": "accept_team_challenge", "detail": "accepted an addressed captain-to-captain challenge", "intentId": intent["id"], "observedAt": observed_at, "remoteWrites": 0}
        if offer.get("kind") == "ask_riley_for_challenge_deferral":
            intent_id = offer.get("intentId")
            if not isinstance(intent_id, str):
                continue
            intent = defer_challenge_for_capacity(root, intent_id, offered_actor_id, now or parse_instant(None))
            return {"actorId": offered_actor_id, "action": "ask_riley_for_challenge_deferral", "detail": "asked Riley to retain an overlapping captain challenge as a later request", "intentId": intent["id"], "observedAt": observed_at, "remoteWrites": 0}
        if offer.get("kind") == "challenge_rival_captain":
            rival = offer.get("rivalCaptainId")
            if not isinstance(rival, str):
                continue
            intent = propose_captain_challenge(root, offered_actor_id, rival, now or parse_instant(None))
            # A historical/agreed challenge is not a fresh player action. Let
            # the captain consider the distinct teammate invitation below.
            if intent.get("status") != "captain_invited":
                continue
            record_interaction_cooldown(
                root, offered_actor_id, "challenge_rival_captain",
                str(offer.get("rivalCaptainId") or ""), now,
            )
            return {"actorId": offered_actor_id, "action": "challenge_rival_captain", "detail": "invited the rival captain to an unbooked doubles challenge", "intentId": intent["id"], "observedAt": observed_at, "remoteWrites": 0}
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
    priority = {"confirm_arcade_replay_result": 0, "check_in_training": 1, "acknowledge_share_invitation": 2, "redeem_share_invitation": 3, "capture_share_invitation": 4, "approve_booking_plan": 5, "accept_session_retry": 6, "ask_riley_for_challenge_deferral": 7, "accept_team_challenge": 8, "accept_team_training": 9, "propose_session_retry": 10, "propose_team_training": 11, "challenge_rival_captain": 12, "invite_teammate": 13}
    return sorted(rows, key=lambda row: priority.get(row.get("kind"), 9))
