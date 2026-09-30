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


SHARE_INVITATIONS_FILE = "share-invitations.json"


class ShareInvitationError(ValueError):
    """The product did not expose a safe share invitation URL."""


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
