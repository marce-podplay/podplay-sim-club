"""Reconciled invitation and acceptance operations for one Preview Club match."""

import json
from typing import Any, Callable, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .identity_registry import ActorIdentity
from .preview_readonly import MAX_RESPONSE_BYTES, PreviewReadError, PreviewReadonlyClient, collection_items
from .preview_write import PreviewWriteError
from .target_policy import TargetPolicy


ACCEPTED_STATUSES = {"ACCEPTED", "CHECKED_IN", "COMPLETE"}
INVITER_INVITATION_ID = "00000000-0000-0000-0000-000000000000"


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        raise PreviewWriteError("participation request refused an HTTP redirect")


Transport = Callable[[Request, float], Any]


class PreviewParticipationWriter:
    """Perform exactly one explicitly selected participation request."""

    def __init__(
        self,
        policy: TargetPolicy,
        actor_token: str,
        transport: Optional[Transport] = None,
    ):
        if not policy.writes_allowed:
            raise PreviewWriteError("preview-write policy confirmation is required")
        self.policy = policy
        self._actor_token = actor_token
        self._used = False
        if transport is None:
            opener = build_opener(_RejectRedirects())
            self._transport = lambda request, timeout: opener.open(request, timeout=timeout)
        else:
            self._transport = transport

    def invite(self, event_id: str, invitee: ActorIdentity) -> Dict[str, Any]:
        payload = {
            "type": "GUEST",
            "chargeType": "PAID_BY_INVITER",
            "invitee": {
                "fullName": f"{invitee.first_name} {invitee.last_name}",
                "email": invitee.email,
                "userId": invitee.podplay_user_id,
            },
        }
        return self._post(f"/apis/v2/events/{event_id}/invitations", payload)

    def accept(self, event_id: str, invitation_id: str) -> Dict[str, Any]:
        payload = {
            "type": "ORDER",
            "invitationType": "PARTICIPANT",
            "virtualCredits": 0,
            "passesStrategy": "USE_NONE",
        }
        return self._post(
            f"/apis/v2/events/{event_id}/invitations/{invitation_id}/acceptance",
            payload,
        )

    def check_in(self, event_id: str, invitation_id: str) -> Dict[str, Any]:
        return self._post(
            f"/apis/v2/events/{event_id}/invitations/{invitation_id}/check-in",
            {},
        )

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self._used:
            raise PreviewWriteError("participation write budget exhausted")
        self._used = True
        url = self.policy.target_origin + path
        self.policy.assert_url(url)
        request = Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._actor_token}",
                "User-Agent": "podplay-sim-club/0.1 participation",
            },
            method="POST",
        )
        try:
            response = self._transport(request, 30)
            with response:
                self.policy.assert_url(response.geturl())
                body = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
        except PreviewWriteError:
            raise
        except HTTPError as exc:
            raise PreviewWriteError(
                f"participation request failed with HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise PreviewWriteError("participation network request failed") from exc
        if status != 201:
            raise PreviewWriteError(f"participation request returned HTTP {status}")
        if len(body) > MAX_RESPONSE_BYTES:
            raise PreviewWriteError("participation response exceeded the size limit")
        try:
            value = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PreviewWriteError("participation response returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise PreviewWriteError("participation response has an unexpected shape")
        return value


class PreviewAcceptanceEvaluator:
    """Send only the non-persisting PREVIEW form of invitation acceptance."""

    def __init__(
        self,
        policy: TargetPolicy,
        actor_token: str,
        transport: Optional[Transport] = None,
    ):
        if policy.writes_allowed:
            raise PreviewWriteError("acceptance preview requires read-only target mode")
        self.policy = policy
        self._actor_token = actor_token
        if transport is None:
            opener = build_opener(_RejectRedirects())
            self._transport = lambda request, timeout: opener.open(request, timeout=timeout)
        else:
            self._transport = transport

    def evaluate(self, event_id: str, invitation_id: str) -> Dict[str, Any]:
        url = (
            self.policy.target_origin
            + f"/apis/v2/events/{event_id}/invitations/{invitation_id}/acceptance"
        )
        self.policy.assert_url(url)
        payload = {
            "type": "PREVIEW",
            "invitationType": "PARTICIPANT",
            "virtualCredits": 0,
            "passesStrategy": "USE_NONE",
        }
        request = Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._actor_token}",
                "User-Agent": "podplay-sim-club/0.1 acceptance-preview",
            },
            method="POST",
        )
        try:
            response = self._transport(request, 30)
            with response:
                self.policy.assert_url(response.geturl())
                body = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
        except PreviewWriteError:
            raise
        except HTTPError as exc:
            if exc.code != 422:
                raise PreviewWriteError(
                    f"acceptance preview failed with HTTP {exc.code}"
                ) from exc
            body = exc.read(MAX_RESPONSE_BYTES + 1)
            status = exc.code
        except URLError as exc:
            raise PreviewWriteError("acceptance preview network request failed") from exc
        if status not in {201, 422}:
            raise PreviewWriteError(f"acceptance preview returned HTTP {status}")
        if len(body) > MAX_RESPONSE_BYTES:
            raise PreviewWriteError("acceptance preview response exceeded the size limit")
        try:
            value = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PreviewWriteError("acceptance preview returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise PreviewWriteError("acceptance preview has an unexpected shape")
        return value


def find_actor_invitation(
    client: PreviewReadonlyClient,
    event_id: str,
    actor_user_id: str,
) -> Optional[Dict[str, Any]]:
    query = urlencode(
        [
            ("expand", "items._links.inviteeProfile"),
            ("expand", "items._links.checkIn"),
        ]
    )
    matches = []
    for invitation in collection_items(
        client.get(f"/apis/v2/events/{event_id}/invitations?{query}")
    ):
        if not isinstance(invitation, dict):
            continue
        profile = invitation.get("inviteeProfile")
        if isinstance(profile, dict) and profile.get("id") == actor_user_id:
            matches.append(invitation)
    if len(matches) > 1:
        raise PreviewReadError("multiple active invitations found for Preview Club actor")
    return matches[0] if matches else None


def find_owner_invitation(
    client: PreviewReadonlyClient,
    event_id: str,
) -> Dict[str, Any]:
    query = urlencode([("expand", "items._links.checkIn")])
    matches = [
        invitation
        for invitation in collection_items(
            client.get(f"/apis/v2/events/{event_id}/invitations?{query}")
        )
        if isinstance(invitation, dict)
        and invitation.get("id") == INVITER_INVITATION_ID
    ]
    if len(matches) != 1:
        raise PreviewReadError("booking owner invitation was not found exactly once")
    return matches[0]


def check_in_status(invitation: Dict[str, Any]) -> str:
    check_in = invitation.get("checkIn")
    if not isinstance(check_in, dict) or check_in.get("status") not in {
        "NOT_CHECKED_IN",
        "CHECKED_IN",
    }:
        raise PreviewReadError("invitation check-in read-back has an unexpected shape")
    return check_in["status"]


def summarize_invitation(invitation: Dict[str, Any], expected_user_id: str) -> Dict[str, Any]:
    invitation_id = invitation.get("id")
    status = invitation.get("status")
    profile = invitation.get("inviteeProfile")
    if (
        not isinstance(invitation_id, str)
        or not isinstance(status, str)
        or not isinstance(profile, dict)
        or profile.get("id") != expected_user_id
    ):
        raise PreviewReadError("invitation read-back has an unexpected shape")
    check_in = invitation.get("checkIn")
    return {
        "invitationId": invitation_id,
        "status": status,
        "accepted": status in ACCEPTED_STATUSES,
        "checkInStatus": check_in.get("status") if isinstance(check_in, dict) else None,
    }


def summarize_acceptance(value: Dict[str, Any]) -> Dict[str, Any]:
    summary = value.get("summary")
    invitation = value.get("invitation")
    if not isinstance(summary, dict) or not isinstance(invitation, dict):
        raise PreviewWriteError("invitation acceptance response has an unexpected shape")
    errors = summary.get("errors")
    if isinstance(errors, list) and errors:
        codes = [item.get("code") for item in errors if isinstance(item, dict)]
        raise PreviewWriteError(
            "invitation acceptance is blocked" + (f" ({', '.join(filter(None, codes))})" if codes else "")
        )
    invitation_id = invitation.get("id")
    if not isinstance(invitation_id, str):
        raise PreviewWriteError("invitation acceptance response is missing its invitation ID")
    return {
        "invitationId": invitation_id,
        "total": float(summary.get("total") or 0),
        "virtualCredits": float(summary.get("virtualCredits") or 0),
        "status": invitation.get("status"),
    }
