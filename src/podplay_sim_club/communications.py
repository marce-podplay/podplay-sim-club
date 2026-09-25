"""Deterministic parallel communication records for product actions."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .storage import Storage
from .target_policy import TargetPolicy


PROTOCOL_VERSION = 1
NOTIFICATION_FILE = "notifications.jsonl"


def record_invite_sent(
    storage: Storage,
    policy: TargetPolicy,
    *,
    occurrence_key: str,
    event_id: str,
    invitation: Dict[str, Any],
    sender_actor_id: str,
    recipient_actor_id: str,
    observed_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Mirror a read-back invitation locally without claiming email delivery.

    The product invitation is the source of truth.  This sidecar record provides
    an idempotent actor-visible signal and preserves an invite URL only when the
    product response actually contains a same-origin absolute URL.
    """
    invitation_id = invitation.get("id")
    status = invitation.get("status")
    if not isinstance(invitation_id, str) or not invitation_id:
        raise ValueError("invite_sent record requires a product invitation ID")
    if not isinstance(status, str) or not status:
        raise ValueError("invite_sent record requires a product invitation status")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("invite_sent record requires an event ID")
    invite_url = _product_invite_url(invitation, policy)
    record = {
        "protocolVersion": PROTOCOL_VERSION,
        "id": f"notification:invite_sent:{event_id}:{invitation_id}",
        "kind": "invite_sent",
        "occurrenceKey": occurrence_key,
        "observedAt": observed_at or datetime.now(timezone.utc).isoformat(),
        "product": {
            "eventId": event_id,
            "invitationId": invitation_id,
            "invitationStatus": status,
            "readBack": True,
        },
        "sender": {"actorId": sender_actor_id},
        "recipient": {"actorId": recipient_actor_id},
        "delivery": {
            "email": "unobserved",
            "parallelChannel": "recorded",
        },
        "inviteLink": (
            {"status": "available", "url": invite_url}
            if invite_url
            else {"status": "not_exposed_by_product"}
        ),
        "remoteWrites": 0,
    }
    with storage.writer_lock():
        path = storage.state / NOTIFICATION_FILE
        if any(row.get("id") == record["id"] for row in storage.read_jsonl(path)):
            return record
        storage.append_jsonl(path, record)
        storage.append_jsonl(
            storage.channel_path("club"),
            {
                "protocolVersion": PROTOCOL_VERSION,
                "id": record["id"],
                "channel": "club",
                "kind": "invite_sent",
                "occurrenceKey": occurrence_key,
                "from": sender_actor_id,
                "to": [recipient_actor_id],
                "payload": record,
                "observedAt": record["observedAt"],
                "remoteWrites": 0,
            },
        )
    return record


def _product_invite_url(invitation: Dict[str, Any], policy: TargetPolicy) -> Optional[str]:
    """Return a URL only when it was explicitly supplied by the product."""
    candidates = [
        invitation.get("inviteUrl"),
        invitation.get("invitationUrl"),
        invitation.get("url"),
    ]
    links = invitation.get("_links")
    if isinstance(links, dict):
        for key in ("invite", "invitation", "accept"):
            value = links.get(key)
            candidates.append(value.get("href") if isinstance(value, dict) else value)
    for value in candidates:
        if not isinstance(value, str) or not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            try:
                policy.assert_url(value)
            except Exception:
                continue
            return value
    return None
