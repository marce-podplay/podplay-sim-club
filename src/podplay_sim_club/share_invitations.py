"""Private local records for player-created PodPlay share invitation links."""

from datetime import datetime, timezone
import json
import os
from typing import Any, Callable, Mapping, Optional
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .target_policy import TargetPolicy
from .storage import Storage
from .time import isoformat, parse_instant
from .preview_write import PreviewWriteError
from .booking_intents import load_intent_ledger, save_intent
from .protocol import append_protocol_facts_locked, make_message, reply_causation


SHARE_INVITATIONS_FILE = "share-invitations.json"


class ShareInvitationError(ValueError):
    """The product did not expose a safe share invitation URL."""


def record_share_choice(
    root, *, intent_id: str, actor_id: str, action: str,
    now: Optional[datetime] = None,
) -> Mapping[str, Any]:
    """Persist a player decision before any invitation product write.

    Choices are deliberately local and secret-free.  The lead bridge consumes a
    capture choice only on an explicitly enabled Preview apply tick; recipient
    redemption choices are similarly inert until the retained private link and
    read-back adapter are available.
    """
    if action not in {"capture", "redeem"}:
        raise ShareInvitationError("share invitation action is unsupported")
    storage = Storage(root)
    observed_at = isoformat(now or datetime.now(timezone.utc))
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        intent = ledger["intents"].get(intent_id)
        if not isinstance(intent, dict):
            raise ShareInvitationError("share invitation intent is unavailable")
        owner = intent.get("bookerActorId")
        participants = intent.get("participants") if isinstance(intent.get("participants"), list) else []
        if action == "capture":
            if actor_id != owner:
                raise ShareInvitationError("only the booking owner may capture a share invitation")
            choice = intent.get("shareInvitationChoice") if isinstance(intent.get("shareInvitationChoice"), dict) else {}
            choice.update({"actorId": actor_id, "status": "accepted", "requestedAt": observed_at})
            intent["shareInvitationChoice"] = choice
            intent["nextAction"] = "lead_may_capture_private_share_invitation"
        else:
            if actor_id == owner or actor_id not in participants:
                raise ShareInvitationError("only a named non-owner participant may redeem a share invitation")
            attention = intent.get("shareInvitationAttention") if isinstance(intent.get("shareInvitationAttention"), dict) else {}
            acknowledgement = attention.get(actor_id) if isinstance(attention.get(actor_id), dict) else {}
            if acknowledgement.get("status") != "acknowledged":
                raise ShareInvitationError("the named recipient must acknowledge the invitation handoff before deciding")
            choices = intent.get("shareRedemptionChoices") if isinstance(intent.get("shareRedemptionChoices"), dict) else {}
            choices[actor_id] = {"status": "accepted", "requestedAt": observed_at}
            intent["shareRedemptionChoices"] = choices
            intent["nextAction"] = "lead_may_redeem_named_player_share_invitation"
        save_intent(storage, ledger, intent, make_active=False)
    return {"intentId": intent_id, "actorId": actor_id, "action": action, "observedAt": observed_at}


def record_share_attention(
    root, *, intent_id: str, actor_id: str, now: Optional[datetime] = None,
) -> Mapping[str, Any]:
    """Record that a named recipient noticed a private-invitation handoff.

    Acknowledgement establishes common ground about the *next decision* only.
    It neither exposes the private redeem URL nor authorizes redemption; the
    recipient still has to make a distinct durable accept/pass/ask choice.
    """
    storage = Storage(root)
    observed_at = isoformat(now or datetime.now(timezone.utc))
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        intent = ledger["intents"].get(intent_id)
        if not isinstance(intent, dict):
            raise ShareInvitationError("share invitation intent is unavailable")
        owner = intent.get("bookerActorId")
        participants = intent.get("participants") if isinstance(intent.get("participants"), list) else []
        if actor_id == owner or actor_id not in participants:
            raise ShareInvitationError("only a named non-owner participant may acknowledge a share invitation")
        if intent.get("status") not in {"booked", "team_signup_in_progress", "team_members_registered"}:
            raise ShareInvitationError("share invitation is not currently actionable")
        attention = intent.get("shareInvitationAttention") if isinstance(intent.get("shareInvitationAttention"), dict) else {}
        prior = attention.get(actor_id) if isinstance(attention.get(actor_id), dict) else {}
        if prior.get("status") == "acknowledged":
            return {"intentId": intent_id, "actorId": actor_id, "status": "acknowledged", "observedAt": prior.get("acknowledgedAt")}
        attention[actor_id] = {"status": "acknowledged", "acknowledgedAt": observed_at}
        intent["shareInvitationAttention"] = attention
        intent["nextAction"] = "await_named_player_share_decision"
        save_intent(storage, ledger, intent, make_active=False)
        message_id = f"{intent_id}:share-invitation-acknowledged:{actor_id}"
        row = {
            "id": message_id, "channel": "club", "from": actor_id, "to": [owner],
            "kind": "share_invitation_acknowledged", "speechAct": "acknowledge",
            "intentId": intent_id,
            "inReplyTo": f"{intent_id}:share-invitation-ready:{actor_id}",
            "text": "I received the private invitation handoff. I will now decide whether to accept, ask a question, or pass.",
            "observedAt": observed_at, "remoteWrites": 0,
        }
        if not any(existing.get("id") == message_id for existing in storage.read_jsonl(storage.channel_path("club")) if isinstance(existing, dict)):
            storage.append_jsonl(storage.channel_path("club"), row)
            conversation_id = intent.get("conversationId") if isinstance(intent.get("conversationId"), str) else f"conversation:{intent_id}"
            append_protocol_facts_locked(storage, [make_message(
                message_id=message_id, conversation_id=conversation_id,
                correlation_id=f"journey:{intent_id}", sender_actor_id=actor_id,
                recipient_actor_ids=[owner], speech_act="acknowledge",
                content_code="share_invitation.handoff_acknowledged", occurred_at=observed_at,
                in_reply_to=row["inReplyTo"], causation_id=reply_causation(row["inReplyTo"]),
                grounding="acknowledged", parameters={"intentRef": intent_id, "nextStep": "recipient_decision"},
            )])
    return {"intentId": intent_id, "actorId": actor_id, "status": "acknowledged", "observedAt": observed_at}


def next_share_invitation_task(root, *, now: Optional[datetime] = None) -> Mapping[str, Any]:
    """Choose one current, player-authorized invitation bridge task.

    This is a selection helper only.  It does not read credentials, expose a
    URL, or contact Preview.
    """
    storage = Storage(root)
    instant = now or datetime.now(timezone.utc)
    ledger = load_intent_ledger(storage)
    sidecar = storage.load_json(storage.state / SHARE_INVITATIONS_FILE, default={})
    retained = sidecar.get("invitations", {}) if isinstance(sidecar, dict) else {}
    if not isinstance(retained, dict):
        retained = {}
    for intent_id, intent in ledger["intents"].items():
        if not isinstance(intent, dict) or intent.get("journey") not in {"customer_training", "customer_team_signup"}:
            continue
        if intent.get("status") not in {"booked", "team_signup_in_progress", "team_members_registered"}:
            continue
        booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
        plan = intent.get("previewPlan") if isinstance(intent.get("previewPlan"), dict) else {}
        occurrence = booking.get("occurrenceKey")
        if not isinstance(occurrence, str):
            continue
        end_time = plan.get("endTime")
        if isinstance(end_time, str) and parse_instant(end_time) <= instant:
            continue
        owner = intent.get("bookerActorId")
        choice = intent.get("shareInvitationChoice") if isinstance(intent.get("shareInvitationChoice"), dict) else {}
        if (
            isinstance(owner, str) and occurrence not in retained
            and choice.get("actorId") == owner and choice.get("status") == "accepted"
        ):
            return {"action": "capture_share_invitation", "intentId": intent_id, "ownerActorId": owner}
        record = retained.get(occurrence)
        choices = intent.get("shareRedemptionChoices") if isinstance(intent.get("shareRedemptionChoices"), dict) else {}
        participants = intent.get("participants") if isinstance(intent.get("participants"), list) else []
        accepted_rows = {
            row.get("actorId") for row in intent.get("shareRedemptions", [])
            if isinstance(row, dict) and row.get("status") == "ACCEPTED"
        }
        if isinstance(record, dict):
            for actor_id in participants:
                decision = choices.get(actor_id) if isinstance(actor_id, str) else None
                if (
                    isinstance(actor_id, str) and actor_id != owner and actor_id not in accepted_rows
                    and isinstance(decision, dict) and decision.get("status") == "accepted"
                ):
                    return {"action": "redeem_share_invitation", "intentId": intent_id, "recipientActorId": actor_id}
    return {"action": "pass", "detail": "no current player-authorized share invitation task"}


class _RejectRedirects(HTTPRedirectHandler):
    """Do not carry a player bearer token across an unexpected redirect."""

    def redirect_request(self, request, fp, code, msg, headers, new_url):
        raise PreviewWriteError("share invitation request refused an HTTP redirect")


def open_share_invitation(policy: TargetPolicy, actor_token: str, event_id: str) -> Mapping[str, Any]:
    """Open one PodPlay share invitation as the booking owner and validate it."""
    if not policy.writes_allowed:
        raise PreviewWriteError("preview-write policy confirmation is required")
    if not event_id:
        raise ShareInvitationError("event id is required")
    url = policy.target_origin + f"/apis/v2/events/{event_id}/open-invitations"
    policy.assert_url(url)
    request = Request(url, data=b'{"visibility":"UNLISTED"}', headers={
        "Accept": "application/json", "Content-Type": "application/json",
        "Authorization": f"Bearer {actor_token}", "User-Agent": "podplay-sim-club/0.1 share-invitation",
    }, method="POST")
    try:
        with build_opener().open(request, timeout=30) as response:
            policy.assert_url(response.geturl())
            body, status = response.read(), response.status
    except HTTPError as exc:
        raise PreviewWriteError(f"open invitation failed with HTTP {exc.code}") from exc
    except URLError as exc:
        raise PreviewWriteError("open invitation network request failed") from exc
    if status not in {200, 201}:
        raise PreviewWriteError(f"open invitation returned HTTP {status}")
    try:
        response = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ShareInvitationError("open invitation returned invalid JSON") from exc
    if not isinstance(response, dict):
        raise ShareInvitationError("open invitation returned an unexpected response")
    capture_share_invitation_link(response, policy)
    return response


def capture_share_invitation_link(
    response: Mapping[str, Any], policy: TargetPolicy
) -> str:
    """Return a same-preview ``redeemUrl`` without persisting it anywhere."""
    redeem_url = response.get("redeemUrl")
    if not isinstance(redeem_url, str) or not redeem_url:
        raise ShareInvitationError("open invitation response is missing redeemUrl")
    parsed = urlparse(redeem_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ShareInvitationError("redeemUrl must be an absolute URL")
    try:
        policy.assert_url(redeem_url)
    except Exception as exc:
        raise ShareInvitationError("redeemUrl is outside the approved preview origin") from exc
    return redeem_url


def redeem_share_invitation(
    policy: TargetPolicy, actor_token: str, redeem_url: str,
    transport: Optional[Callable[[Request, float], Any]] = None,
) -> Mapping[str, Any]:
    """Redeem a retained same-preview link as the signed-in recipient.

    PodPlay's player handoff posts only the opaque ``key`` from the link.  The
    URL stays in the 0600 local store and never reaches the club channel,
    dashboard, or command output.
    """
    if not policy.writes_allowed:
        raise PreviewWriteError("preview-write policy confirmation is required")
    capture_share_invitation_link({"redeemUrl": redeem_url}, policy)
    keys = parse_qs(urlparse(redeem_url).query).get("key", [])
    if len(keys) != 1 or not isinstance(keys[0], str) or not keys[0]:
        raise ShareInvitationError("redeemUrl is missing its sharing key")
    url = policy.target_origin + "/apis/v2/events/invitations"
    policy.assert_url(url)
    request = Request(url, data=json.dumps({"sharingKey": keys[0]}).encode("utf-8"), headers={
        "Accept": "application/json", "Content-Type": "application/json",
        "Authorization": f"Bearer {actor_token}", "User-Agent": "podplay-sim-club/0.1 share-redemption",
    }, method="POST")
    try:
        sender = transport or (lambda item, timeout: build_opener(_RejectRedirects()).open(item, timeout=timeout))
        with sender(request, 30) as response:
            policy.assert_url(response.geturl())
            body, status = response.read(), response.status
    except PreviewWriteError:
        raise
    except HTTPError as exc:
        raise PreviewWriteError(f"share invitation redemption failed with HTTP {exc.code}") from exc
    except URLError as exc:
        raise PreviewWriteError("share invitation redemption network request failed") from exc
    if status != 201:
        raise PreviewWriteError(f"share invitation redemption returned HTTP {status}")
    try:
        value = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ShareInvitationError("share invitation redemption returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ShareInvitationError("share invitation redemption returned an unexpected response")
    return value


def record_share_invitation(
    storage: Storage,
    policy: TargetPolicy,
    *,
    key: str,
    owner_actor_id: str,
    response: Mapping[str, Any],
    observed_at: Optional[str] = None,
) -> Mapping[str, Any]:
    """Persist an explicitly captured preview link and its live redeem budget.

    This file is ignored by git and mode 0600.  Callers must obtain ``response``
    from PodPlay's open-invitation response; no URL or capacity is guessed.
    """
    if not key or not owner_actor_id:
        raise ShareInvitationError("share invitation key and owner are required")
    redeem_url = capture_share_invitation_link(response, policy)
    max_redeems, remaining = response.get("maxRedeems"), response.get("redeemsRemaining")
    if max_redeems is not None or remaining is not None:
        if not isinstance(max_redeems, int) or max_redeems < 1:
            raise ShareInvitationError("open invitation response has invalid maxRedeems")
        if not isinstance(remaining, int) or not 0 <= remaining <= max_redeems:
            raise ShareInvitationError("open invitation response has invalid redeemsRemaining")
    invitation_id = response.get("id")
    if not isinstance(invitation_id, str) or not invitation_id:
        raise ShareInvitationError("open invitation response is missing id")
    record = {
        "key": key,
        "ownerActorId": owner_actor_id,
        "invitationId": invitation_id,
        "redeemUrl": redeem_url,
        "maxRedeems": max_redeems if isinstance(max_redeems, int) else None,
        "redeemsRemaining": remaining if isinstance(remaining, int) else None,
        "capturedAt": observed_at or datetime.now(timezone.utc).isoformat(),
    }
    with storage.writer_lock():
        path = storage.state / SHARE_INVITATIONS_FILE
        value = storage.load_json(path, default={"schemaVersion": 1, "invitations": {}})
        if not isinstance(value, dict) or value.get("schemaVersion") != 1 or not isinstance(value.get("invitations"), dict):
            raise ShareInvitationError("share invitation store has an unsupported schema")
        value["invitations"][key] = record
        storage.write_json(path, value)
        os.chmod(path, 0o600)
    return record


def expire_unsent_booking_invitation(
    root, *, owner_actor_id: str, now: Optional[datetime] = None
) -> Mapping[str, Any]:
    """Record the one honest outcome for a retained link whose booking ended.

    A captured URL is not evidence that a teammate received or redeemed it.
    When the booked window has passed, retain the booking as history, notify the
    intended teammate and ask the front desk for a *new* session.  This never
    sends a product invitation or marks a person as having attended.
    """
    storage = Storage(root)
    instant = now or datetime.now(timezone.utc)
    observed_at = isoformat(instant)
    with storage.writer_lock():
        invitations = storage.load_json(
            storage.state / SHARE_INVITATIONS_FILE,
            default={"schemaVersion": 1, "invitations": {}},
        )
        links = invitations.get("invitations", {}) if isinstance(invitations, dict) else {}
        bookings_path = storage.state / "observed-bookings.json"
        observed = storage.load_json(bookings_path, default={"schemaVersion": 1, "bookings": []})
        bookings = observed.get("bookings", []) if isinstance(observed, dict) else []
        messages = storage.read_jsonl(storage.channel_path("club"))
        existing = {row.get("id") for row in messages if isinstance(row, dict)}
        for booking in bookings:
            if not isinstance(booking, dict) or not isinstance(booking.get("occurrenceKey"), str):
                continue
            key = booking["occurrenceKey"]
            link = links.get(key) if isinstance(links, dict) else None
            lifecycle = booking.get("lifecycle") if isinstance(booking.get("lifecycle"), dict) else {}
            steps = lifecycle.get("steps") if isinstance(lifecycle.get("steps"), dict) else {}
            if not isinstance(link, dict) or link.get("ownerActorId") != owner_actor_id:
                continue
            if steps.get("invitation") != "LINK_CAPTURED" or steps.get("acceptance") != "NOT_SENT":
                continue
            end_time = booking.get("endTime")
            if not isinstance(end_time, str) or parse_instant(end_time) > instant:
                continue
            message_id = f"{key}:share-invitation-expired"
            if message_id not in existing:
                storage.append_jsonl(storage.channel_path("club"), {
                    "id": message_id,
                    "channel": "club",
                    "from": owner_actor_id,
                    "to": ["mai", "riley"],
                    "kind": "share_invitation_expired",
                    "bookingOccurrenceKey": key,
                    "shareInvitationKey": key,
                    "participants": [owner_actor_id, "mai"],
                    "durationMinutes": 30,
                    "text": "Mai, I captured our invitation but the W37 booking window passed before I sent it. Riley, please log a request for a new 30-minute Women Fighters session.",
                    "observedAt": observed_at,
                    "remoteWrites": 0,
                })
                existing.add(message_id)
            booking["phase"] = "expired"
            booking["lifecycle"] = {
                "state": "EXPIRED_UNSENT",
                "steps": {
                    "booking": "BOOKED",
                    "invitation": "EXPIRED_UNSENT",
                    "acceptance": "NOT_AVAILABLE",
                    "checkIn": "NOT_OBSERVED",
                    "play": "NOT_RECORDED",
                },
            }
            for participant in booking.get("participants", []):
                if isinstance(participant, dict) and participant.get("actorId") == "mai":
                    participant["invitationStatus"] = "EXPIRED_UNSENT"
            storage.write_json(bookings_path, observed)
            return {
                "actorId": owner_actor_id,
                "action": "report_expired_share_invitation",
                "messageId": message_id,
                "detail": "the booking ended before the captured share invitation was sent; requested a new session",
                "observedAt": observed_at,
                "remoteWrites": 0,
            }
    return {"actorId": owner_actor_id, "action": "pass", "detail": "no captured unsent invitation has expired", "observedAt": observed_at, "remoteWrites": 0}
