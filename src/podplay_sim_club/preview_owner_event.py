"""Guarded owner-created Open Play writes for Preview Club."""

import json
from typing import Any, Callable, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .preview_booking import _RejectRedirects
from .preview_readonly import MAX_RESPONSE_BYTES
from .preview_write import PreviewWriteError
from .target_policy import TargetPolicy


Transport = Callable[[Request, float], Any]


class PreviewOwnerEventWriter:
    """Create one free, published Open Play using the same booking endpoint as admin UI."""

    def __init__(self, policy: TargetPolicy, admin_token: str, transport: Optional[Transport] = None):
        if not policy.writes_allowed:
            raise PreviewWriteError("preview-write policy confirmation is required")
        self.policy = policy
        self._admin_token = admin_token
        self._used = False
        opener = build_opener(_RejectRedirects())
        self._transport = transport or (lambda request, timeout: opener.open(request, timeout=timeout))

    def create_open_play(self, items: List[Dict[str, str]], name: str, total_teams: int = 2) -> Dict[str, Any]:
        if self._used:
            raise PreviewWriteError("owner event write budget exhausted")
        if not isinstance(name, str) or not name.startswith("Preview Club ") or len(name) > 120:
            raise PreviewWriteError("owner event name must be a bounded Preview Club name")
        if not isinstance(total_teams, int) or not 1 <= total_teams <= 8:
            raise PreviewWriteError("owner event team capacity must be between 1 and 8")
        if not isinstance(items, list) or not items:
            raise PreviewWriteError("owner event requires one or more session items")
        normalized_items = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("sessionId"), str) or not isinstance(item.get("tableId"), str):
                raise PreviewWriteError("owner event items require session and table IDs")
            normalized_items.append({"session": {"id": item["sessionId"]}, "sessionTable": {"id": item["tableId"]}})
        self._used = True
        payload = {
            "type": "EVENT",
            "bookingMode": "FREE_OF_CHARGE",
            "eventSubtype": "OPEN_PLAY",
            "eventCustomType": "Open Play",
            "eventName": name,
            "eventDescription": "Preview Club simulated owner event.",
            "eventStatus": "PUBLISHED",
            "visibility": "LISTED",
            "admission": "OPEN",
            "admissionRate": {"regular": 0, "membersDefault": 0, "memberships": {"items": []}},
            "teamSize": 1,
            "totalTeams": total_teams,
            "maxGuests": 0,
            "createAsSeries": False,
            "termsAgreed": True,
            "liabilityWaiverAgreed": True,
            "items": normalized_items,
        }
        url = self.policy.target_origin + "/apis/v2/bookings"
        self.policy.assert_url(url)
        request = Request(url, data=json.dumps(payload, separators=(",", ":")).encode("utf-8"), headers={
            "Accept": "application/json", "Content-Type": "application/json",
            "Authorization": f"Bearer {self._admin_token}",
            "User-Agent": "podplay-sim-club/0.1 owner-open-play",
        }, method="POST")
        try:
            response = self._transport(request, 30)
            with response:
                self.policy.assert_url(response.geturl())
                body, status = response.read(MAX_RESPONSE_BYTES + 1), response.status
        except PreviewWriteError:
            raise
        except HTTPError as exc:
            raise PreviewWriteError(f"owner event create failed with HTTP {exc.code}") from exc
        except URLError as exc:
            raise PreviewWriteError("owner event create network request failed") from exc
        if status != 201:
            raise PreviewWriteError(f"owner event create returned HTTP {status}")
        if len(body) > MAX_RESPONSE_BYTES:
            raise PreviewWriteError("owner event response exceeded the size limit")
        try:
            value = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PreviewWriteError("owner event create returned invalid JSON") from exc
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise PreviewWriteError("owner event create response has an unexpected shape")
        return value
