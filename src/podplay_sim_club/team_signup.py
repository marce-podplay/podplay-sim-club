"""Narrow customer-owned team-member adapter for a booked team event."""

import json
from typing import Any, Callable, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .preview_participation import _RejectRedirects
from .preview_readonly import MAX_RESPONSE_BYTES
from .preview_write import PreviewWriteError
from .target_policy import TargetPolicy


Transport = Callable[[Request, float], Any]


class PreviewTeamMemberWriter:
    """Add one named teammate through product PREVIEW then ORDER.

    The booking owner is the sole authenticated caller. Each ORDER is bounded
    to one explicitly named teammate and returns a product-created invitation
    that is already redeemed/accepted by the product contract.
    """

    def __init__(self, policy: TargetPolicy, owner_token: str, transport: Optional[Transport] = None):
        if not policy.writes_allowed:
            raise PreviewWriteError("preview-write policy confirmation is required")
        if not isinstance(owner_token, str) or not owner_token:
            raise PreviewWriteError("team signup owner token is required")
        self.policy = policy
        self._token = owner_token
        self._used = False
        opener = build_opener(_RejectRedirects())
        self._transport = transport or (lambda request, timeout: opener.open(request, timeout=timeout))

    def add_paid_by_leader(self, event_id: str, leader_user_id: str, invitee: Dict[str, str]) -> Dict[str, Any]:
        if self._used:
            raise PreviewWriteError("team-member write budget exhausted")
        if not isinstance(event_id, str) or not event_id or not isinstance(leader_user_id, str) or not leader_user_id:
            raise PreviewWriteError("team-member signup needs event and leader IDs")
        required = ("fullName", "email", "userId")
        if any(not isinstance(invitee.get(key), str) or not invitee[key] for key in required):
            raise PreviewWriteError("team-member invitee is incomplete")
        base = {
            "invitee": {key: invitee[key] for key in required},
            "chargeType": "PAID_BY_INVITER",
            "teamLeader": {"user": {"id": leader_user_id}},
            "passesStrategy": "USE_NONE",
        }
        preview = self._post(event_id, {"type": "PREVIEW", **base})
        errors = preview.get("errors") if isinstance(preview, dict) else None
        if isinstance(errors, list) and errors:
            raise PreviewWriteError("team-member preview returned validation errors")
        self._used = True
        ordered = self._post(event_id, {"type": "ORDER", **base})
        invitation = ordered.get("invitation") if isinstance(ordered, dict) else None
        if not isinstance(invitation, dict) or not isinstance(invitation.get("id"), str):
            raise PreviewWriteError("team-member order did not return an invitation")
        return ordered

    def _post(self, event_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self.policy.target_origin + f"/apis/v2/events/{event_id}/team-members"
        self.policy.assert_url(url)
        request = Request(url, data=json.dumps(payload, separators=(",", ":")).encode(), headers={
            "Accept": "application/json", "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}", "User-Agent": "podplay-sim-club/0.1 team-signup",
        }, method="POST")
        try:
            response = self._transport(request, 30)
            with response:
                self.policy.assert_url(response.geturl())
                body, status = response.read(MAX_RESPONSE_BYTES + 1), response.status
        except PreviewWriteError:
            raise
        except HTTPError as exc:
            raise PreviewWriteError(f"team-member request failed with HTTP {exc.code}") from exc
        except URLError as exc:
            raise PreviewWriteError("team-member network request failed") from exc
        if status not in {200, 201}:
            raise PreviewWriteError(f"team-member request returned HTTP {status}")
        if len(body) > MAX_RESPONSE_BYTES:
            raise PreviewWriteError("team-member response exceeded the size limit")
        try:
            value = json.loads(body.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PreviewWriteError("team-member response returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise PreviewWriteError("team-member response has an unexpected shape")
        return value
