"""Bounded self-signup adapter for a published Preview Club Open Play."""

import json
from typing import Any, Callable, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .preview_participation import _RejectRedirects, find_actor_invitation
from .preview_readonly import MAX_RESPONSE_BYTES, PreviewReadError, PreviewReadonlyClient, collection_items
from .preview_write import PreviewWriteError
from .target_policy import TargetPolicy


Transport = Callable[[Request, float], Any]


def find_event_signup(client: PreviewReadonlyClient, event_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    matches = [
        signup for signup in collection_items(client.get(f"/apis/v2/events/{event_id}/signups"))
        if isinstance(signup, dict) and isinstance(signup.get("owner"), dict)
        and signup["owner"].get("id") == user_id and signup.get("isCanceled") is not True
    ]
    if len(matches) > 1:
        raise PreviewReadError("multiple active Preview Club signups found for one player")
    return matches[0] if matches else None


def read_captain_attendance_invitation(
    client: PreviewReadonlyClient, event_id: str, signup_id: str
) -> Dict[str, Any]:
    """Resolve a captain's self-signup to its real attendance invitation.

    A team captain's signup ID identifies a ``SIGNUP`` open invitation, not the
    accepted ``EVENT_SIGNUP`` invitation used by the check-in endpoint.  The
    player-visible signup detail is the bounded bridge between those records.
    We then read the matching invitation collection item so check-in status is
    available for the normal read-back contract.
    """
    if not isinstance(signup_id, str) or not signup_id:
        raise PreviewReadError("captain signup reference is missing")
    detail = client.get(f"/apis/v2/events/{event_id}/signups/{signup_id}")
    if not isinstance(detail, dict) or detail.get("id") != signup_id:
        raise PreviewReadError("captain signup detail did not match the stored signup")
    summary = detail.get("summary")
    if not isinstance(summary, dict):
        raise PreviewReadError("captain signup detail has an unexpected shape")
    open_invitation = summary.get("openInvitation")
    invitation = summary.get("invitation")
    if (
        not isinstance(open_invitation, dict)
        or open_invitation.get("id") != signup_id
        or not isinstance(invitation, dict)
        or not isinstance(invitation.get("id"), str)
        or invitation.get("status") not in {"ACCEPTED", "CHECKED_IN", "COMPLETE"}
        or not isinstance(invitation.get("openInvitation"), dict)
        or invitation["openInvitation"].get("id") != signup_id
    ):
        raise PreviewReadError("captain signup did not resolve to an accepted attendance invitation")
    profile = invitation.get("inviteeProfile")
    if not isinstance(profile, dict) or not isinstance(profile.get("id"), str):
        raise PreviewReadError("captain attendance invitation has no named invitee")
    current = find_actor_invitation(client, event_id, profile["id"])
    if not isinstance(current, dict) or current.get("id") != invitation["id"]:
        raise PreviewReadError("captain attendance invitation read-back is ambiguous")
    return current


class PreviewEventSignupWriter:
    """Perform exactly one customer self-signup after the explicit write gate."""

    def __init__(self, policy: TargetPolicy, actor_token: str, transport: Optional[Transport] = None):
        if not policy.writes_allowed:
            raise PreviewWriteError("preview-write policy confirmation is required")
        self.policy = policy
        self._actor_token = actor_token
        self._used = False
        opener = build_opener(_RejectRedirects())
        self._transport = transport or (lambda request, timeout: opener.open(request, timeout=timeout))

    def signup(self, event_id: str, owner_user_id: str) -> Dict[str, Any]:
        if self._used:
            raise PreviewWriteError("event signup write budget exhausted")
        if not event_id or not owner_user_id:
            raise PreviewWriteError("event signup requires event and owner IDs")
        self._used = True
        # The actor's bearer token is the owner.  Supplying an ``owner`` field is
        # an admin-on-behalf flow and is rightly rejected for customer self-signup.
        preview = self._post(event_id, {
            "type": "PREVIEW", "termsAgreed": True, "liabilityWaiverAgreed": True,
            "kidsWaiverAgreed": True, "virtualCredits": 0, "passesStrategy": "USE_ALL",
        })
        errors = preview.get("errors") if isinstance(preview, dict) else None
        if isinstance(errors, list) and errors:
            codes = [row.get("code") for row in errors if isinstance(row, dict) and isinstance(row.get("code"), str)]
            suffix = f" ({','.join(codes)})" if codes else ""
            raise PreviewWriteError(f"event signup preview returned validation errors{suffix}")
        virtual_credits = preview.get("summary", {}).get("virtualCredits", 0)
        if not isinstance(virtual_credits, (int, float)):
            raise PreviewWriteError("event signup preview returned invalid virtual credits")
        return self._post(event_id, {
            "type": "ORDER", "termsAgreed": True,
            "virtualCredits": virtual_credits, "passesStrategy": "USE_ALL",
        })

    def _post(self, event_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self.policy.target_origin + f"/apis/v2/events/{event_id}/signups"
        self.policy.assert_url(url)
        request = Request(url, data=json.dumps(payload, separators=(",", ":")).encode("utf-8"), headers={
            "Accept": "application/json", "Content-Type": "application/json",
            "Authorization": f"Bearer {self._actor_token}", "User-Agent": "podplay-sim-club/0.1 event-signup",
        }, method="POST")
        try:
            response = self._transport(request, 30)
            with response:
                self.policy.assert_url(response.geturl())
                body, status = response.read(MAX_RESPONSE_BYTES + 1), response.status
        except PreviewWriteError:
            raise
        except HTTPError as exc:
            codes = _error_codes(exc.read(MAX_RESPONSE_BYTES + 1))
            suffix = f" ({','.join(codes)})" if codes else ""
            raise PreviewWriteError(f"event signup failed with HTTP {exc.code}{suffix}") from exc
        except URLError as exc:
            raise PreviewWriteError("event signup network request failed") from exc
        if status != 201:
            raise PreviewWriteError(f"event signup returned HTTP {status}")
        if len(body) > MAX_RESPONSE_BYTES:
            raise PreviewWriteError("event signup response exceeded the size limit")
        try:
            value = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PreviewWriteError("event signup returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise PreviewWriteError("event signup response has an unexpected shape")
        if payload.get("type") == "ORDER" and not isinstance(value.get("id"), str):
            raise PreviewWriteError("event signup response has an unexpected shape")
        return value


def _error_codes(body: bytes) -> list:
    """Expose only stable validation codes, never the response's free-form text."""
    try:
        value = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    errors = value.get("summary", {}).get("errors", []) if isinstance(value, dict) else []
    return [str(error["code"]) for error in errors if isinstance(error, dict) and isinstance(error.get("code"), str)][:5]
