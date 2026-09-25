"""Bounded self-signup adapter for a published Preview Club Open Play."""

import json
from typing import Any, Callable, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .preview_participation import _RejectRedirects
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
        payload = {
            "type": "ORDER", "mode": "USER_BOOKED", "owner": {"id": owner_user_id},
            "paymentMethod": "FREE", "passesStrategy": "USE_NONE", "kids": {"items": []},
            "virtualCredits": 0,
        }
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
            raise PreviewWriteError(f"event signup failed with HTTP {exc.code}") from exc
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
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise PreviewWriteError("event signup response has an unexpected shape")
        return value
