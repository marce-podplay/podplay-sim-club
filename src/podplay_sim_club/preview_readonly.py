"""Origin-locked, GET-only client for an approved PodPlay PR preview."""

import json
from typing import Any, Callable, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .target_policy import TargetPolicy, TargetPolicyError


MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class PreviewReadError(RuntimeError):
    pass


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        raise PreviewReadError("preview request refused an HTTP redirect")


Transport = Callable[[Request, float], Any]


class PreviewReadonlyClient:
    def __init__(
        self,
        policy: TargetPolicy,
        id_token: str,
        transport: Optional[Transport] = None,
    ):
        self.policy = policy
        self._id_token = id_token
        if transport is None:
            opener = build_opener(_RejectRedirects())
            self._transport = lambda request, timeout: opener.open(request, timeout=timeout)
        else:
            self._transport = transport

    def get(self, path: str) -> Any:
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise TargetPolicyError("preview request path must be relative")
        if not parsed.path.startswith("/apis/v2/"):
            raise TargetPolicyError("preview reads are limited to /apis/v2/")
        url = self.policy.target_origin + path
        self.policy.assert_url(url)
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._id_token}",
                "User-Agent": "podplay-sim-club/0.1 preview-readonly",
            },
            method="GET",
        )
        try:
            response = self._transport(request, 15)
            with response:
                final_url = response.geturl()
                self.policy.assert_url(final_url)
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except PreviewReadError:
            raise
        except HTTPError as exc:
            raise PreviewReadError(f"preview GET failed with HTTP {exc.code}") from exc
        except URLError as exc:
            raise PreviewReadError("preview GET network request failed") from exc
        if len(body) > MAX_RESPONSE_BYTES:
            raise PreviewReadError("preview GET response exceeded the size limit")
        try:
            return json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PreviewReadError("preview GET returned invalid JSON") from exc


def collection_items(value: Any) -> list:
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise PreviewReadError("preview collection response has an unexpected shape")
    return value["items"]
