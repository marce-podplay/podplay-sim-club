"""Small private handoff for browser-created player invitation links.

The Club feed is deliberately observable by the local dashboard, so it is not
an appropriate place for a bearer-style product link.  This module keeps the
link in an actor-scoped local mailbox and writes only a human-readable delivery
notice to the regular community channel.  Delivery is immediate; opening or
accepting an invitation remains a separate player action.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from .booking_intents import load_intent_ledger, save_intent
from .storage import Storage
from .time import isoformat


class SecureChannelError(ValueError):
    """A private invitation handoff could not be safely recorded."""


def _now(value: datetime | None = None) -> str:
    return isoformat(value or datetime.now(timezone.utc))


def _directory(storage: Storage) -> Path:
    path = storage.state / "secure-channel"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def _write_private(storage: Storage, path: Path, value: Mapping[str, Any]) -> None:
    storage.write_json(path, dict(value))
    os.chmod(path, 0o600)


def _load_private(storage: Storage, path: Path) -> dict[str, Any]:
    value = storage.load_json(path, default={})
    if not isinstance(value, dict):
        raise SecureChannelError("private invitation mailbox has an unsupported shape")
    return value


def _intent(storage: Storage, intent_id: str, owner_actor_id: str) -> dict[str, Any]:
    intent = load_intent_ledger(storage).get("intents", {}).get(intent_id)
    if not isinstance(intent, dict):
        raise SecureChannelError("booking intent is unavailable")
    if intent.get("bookerActorId") != owner_actor_id:
        raise SecureChannelError("only the named booking owner may hand off this invitation")
    participants = intent.get("participants")
    if not isinstance(participants, list) or owner_actor_id not in participants:
        raise SecureChannelError("booking intent has no valid named participants")
    return intent


def _validate_link(value: str, expected_origin: str) -> str:
    try:
        invite = urlparse(value.strip())
        origin = urlparse(expected_origin)
    except ValueError as exc:
        raise SecureChannelError("invitation link is malformed") from exc
    if not invite.scheme or not invite.netloc or invite.username or invite.password:
        raise SecureChannelError("invitation link is malformed")
    if (invite.scheme, invite.netloc) != (origin.scheme, origin.netloc):
        raise SecureChannelError("invitation link is outside the approved product origin")
    if invite.params or invite.query or invite.fragment or not invite.path.startswith("/invites/"):
        raise SecureChannelError("invitation link does not have the expected product route")
    token = invite.path.removeprefix("/invites/")
    if not token or "/" in token:
        raise SecureChannelError("invitation link does not have one invitation token")
    return value.strip()


def retain_invitation_link(
    root: Path, *, intent_id: str, owner_actor_id: str, invitation_url: str,
    expected_origin: str, observed_at: datetime | None = None,
) -> Mapping[str, Any]:
    """Store a visibly copied product invitation link in the owner's mailbox.

    The returned projection intentionally contains no URL, token, or opaque
    product identifier so it is safe for CLI output and the local dashboard.
    """
    storage = Storage(root)
    when = _now(observed_at)
    with storage.writer_lock():
        _intent(storage, intent_id, owner_actor_id)
        url = _validate_link(invitation_url, expected_origin)
        records = _load_private(storage, _directory(storage) / "owner-invitations.json")
        prior = records.get(intent_id) if isinstance(records.get(intent_id), dict) else {}
        records[intent_id] = {
            "intentId": intent_id,
            "ownerActorId": owner_actor_id,
            "invitationUrl": url,
            "capturedAt": when,
            "deliveryCount": int(prior.get("deliveryCount", 0) or 0),
        }
        _write_private(storage, _directory(storage) / "owner-invitations.json", records)
    return {"intentId": intent_id, "ownerActorId": owner_actor_id, "status": "retained", "observedAt": when}


def deliver_invitation(
    root: Path, *, intent_id: str, owner_actor_id: str, recipient_actor_ids: Iterable[str],
    observed_at: datetime | None = None,
) -> Mapping[str, Any]:
    """Immediately deliver the retained link to named private mailboxes.

    The normal community message is deliberately URL-free.  It is a simple
    notification, not an acknowledgement protocol or an acceptance claim.
    """
    storage = Storage(root)
    when = _now(observed_at)
    recipients = list(dict.fromkeys(recipient_actor_ids))
    if not recipients:
        raise SecureChannelError("at least one named recipient is required")
    with storage.writer_lock():
        intent = _intent(storage, intent_id, owner_actor_id)
        named = set(intent["participants"])
        invalid = [actor for actor in recipients if actor == owner_actor_id or actor not in named]
        if invalid:
            raise SecureChannelError("invitation recipients must be named non-owner participants")
        directory = _directory(storage)
        owner_records = _load_private(storage, directory / "owner-invitations.json")
        retained = owner_records.get(intent_id)
        if not isinstance(retained, dict) or not isinstance(retained.get("invitationUrl"), str):
            raise SecureChannelError("the owner has not retained a product invitation link")
        mailboxes = _load_private(storage, directory / "recipient-mailboxes.json")
        for recipient in recipients:
            mailbox_key = f"{recipient}:{intent_id}"
            mailboxes[mailbox_key] = {
                "intentId": intent_id,
                "recipientActorId": recipient,
                "ownerActorId": owner_actor_id,
                "invitationUrl": retained["invitationUrl"],
                "deliveredAt": when,
                "status": "delivered",
            }
        _write_private(storage, directory / "recipient-mailboxes.json", mailboxes)
        retained["deliveryCount"] = int(retained.get("deliveryCount", 0) or 0) + len(recipients)
        retained["lastDeliveredAt"] = when
        owner_records[intent_id] = retained
        _write_private(storage, directory / "owner-invitations.json", owner_records)

        message_id = f"{intent_id}:private-invitation-delivered:{','.join(sorted(recipients))}"
        existing = {row.get("id") for row in storage.read_jsonl(storage.channel_path("club")) if isinstance(row, dict)}
        if message_id not in existing:
            storage.append_jsonl(storage.channel_path("club"), {
                "id": message_id, "channel": "club", "from": owner_actor_id, "to": recipients,
                "kind": "private_invitation_delivered", "speechAct": "inform", "intentId": intent_id,
                "observedAt": when, "remoteWrites": 0,
                "text": "I sent the player invitation to your private local invite channel. Open it in your player browser when you are ready.",
            })
        # The private mailbox is durable evidence of delivery. Make the normal
        # social engine's next step explicit without copying the private link
        # into the public intent ledger.
        ledger = load_intent_ledger(storage)
        current = ledger.get("intents", {}).get(intent_id) if isinstance(ledger.get("intents"), dict) else None
        if isinstance(current, dict):
            current["nextAction"] = "await_named_player_share_decision"
            save_intent(storage, ledger, current, make_active=False)
    return {"intentId": intent_id, "ownerActorId": owner_actor_id, "recipients": recipients, "status": "delivered", "observedAt": when}
