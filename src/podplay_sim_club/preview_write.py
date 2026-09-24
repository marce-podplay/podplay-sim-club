"""Narrow write client for creating Preview Club user identities."""

import json
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .target_policy import TargetPolicy


MAX_RESPONSE_BYTES = 1024 * 1024


class PreviewWriteError(RuntimeError):
    pass


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        raise PreviewWriteError("preview write refused an HTTP redirect")


class PreviewIdentityWriter:
    def __init__(self, policy: TargetPolicy):
        if not policy.writes_allowed:
            raise PreviewWriteError("preview-write policy confirmation is required")
        self.policy = policy
        self._opener = build_opener(_RejectRedirects())

    def signup(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Use the public product signup so the chosen password remains valid."""
        url = self.policy.target_origin + "/apis/v2/users"
        self.policy.assert_url(url)
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "podplay-sim-club/0.1 identity-seed",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=20) as response:
                self.policy.assert_url(response.geturl())
                body = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
        except PreviewWriteError:
            raise
        except HTTPError as exc:
            raise PreviewWriteError(f"preview user signup failed with HTTP {exc.code}") from exc
        except URLError as exc:
            raise PreviewWriteError("preview user signup network request failed") from exc
        if status != 201:
            raise PreviewWriteError(f"preview user signup returned HTTP {status}")
        if len(body) > MAX_RESPONSE_BYTES:
            raise PreviewWriteError("preview user signup response exceeded the size limit")
        try:
            value = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PreviewWriteError("preview user signup returned invalid JSON") from exc
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise PreviewWriteError("preview user signup response did not contain an ID")
        return value
