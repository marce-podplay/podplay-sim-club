"""Small, independent public-app health observations for Club Lab.

This lane intentionally does not authenticate, book, quote, or inspect an
experiment.  It answers only whether the public login shell was reachable at
the observation instant.  A changed result is an incident observation, never
a regression conclusion.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .credentials import PreviewCredentials
from .storage import Storage
from .target_policy import TargetPolicy
from .time import isoformat, parse_instant


STATE_FILE = "ambient-health.json"


class AmbientHealthError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        raise AmbientHealthError("ambient health check refused an HTTP redirect")


def check(root: Path, *, fetch: Callable[[str], Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """Record a safe availability transition, if any, and return a compact card."""
    credentials = PreviewCredentials.load(root)
    policy = TargetPolicy.from_config(credentials.config)
    observed_at = isoformat(parse_instant(None))
    result = fetch(policy.target_origin) if fetch else _fetch_login_shell(policy)
    status = _classify(result)
    storage = Storage(root)
    with storage.writer_lock():
        previous = storage.load_json(storage.state / STATE_FILE, default={})
        previous_status = previous.get("status") if isinstance(previous, dict) else None
        current = {"schemaVersion": 1, "status": status, "observedAt": observed_at}
        storage.write_json(storage.state / STATE_FILE, current)
        if previous_status is not None and previous_status != status:
            _record_transition(storage, previous_status, status, observed_at)
        elif previous_status is None:
            _record_baseline(storage, status, observed_at)
    return {
        "lane": "ambient-health",
        "status": status,
        "changed": previous_status is not None and previous_status != status,
        "remoteWrites": 0,
        "doesNotMean": ["authenticated-journey-success", "booking", "attendance", "regression-conclusion"],
    }


def _fetch_login_shell(policy: TargetPolicy) -> Dict[str, Any]:
    url = policy.target_origin + "/login?loginMode=password"
    policy.assert_url(url)
    request = Request(url, headers={"Accept": "text/html", "User-Agent": "podplay-sim-club/0.1 ambient-health"}, method="GET")
    try:
        with build_opener(_NoRedirect()).open(request, timeout=15) as response:
            policy.assert_url(response.geturl())
            body = response.read(256 * 1024 + 1)
            return {"httpStatus": response.status, "bodyBytes": len(body), "html": body[:4096].decode("utf-8", errors="replace")}
    except HTTPError as exc:
        return {"httpStatus": exc.code, "bodyBytes": 0, "html": ""}
    except URLError as exc:
        return {"networkError": True}


def _classify(result: Dict[str, Any]) -> str:
    if result.get("networkError"):
        return "unreachable"
    if result.get("httpStatus") != 200:
        return "http_error"
    html = result.get("html")
    if not isinstance(html, str) or len(html.strip()) < 64 or "<" not in html:
        return "invalid_shell"
    return "reachable"


def _record_baseline(storage: Storage, status: str, observed_at: str) -> None:
    storage.append_jsonl(storage.channel_path("staff-planning"), {
        "id": "ambient-health:baseline", "channel": "staff-planning", "kind": "ambient_health_baseline",
        "from": "riley", "to": ["sofia"], "observedAt": observed_at,
        "text": f"Ambient health baseline recorded: public login shell is {status}. This does not verify an authenticated player journey.",
    })


def _record_transition(storage: Storage, previous: str, current: str, observed_at: str) -> None:
    storage.append_jsonl(storage.channel_path("staff-planning"), {
        "id": f"ambient-health:transition:{observed_at}", "channel": "staff-planning", "kind": "ambient_health_incident_observation",
        "from": "riley", "to": ["sofia"], "observedAt": observed_at,
        "text": f"Ambient health changed from {previous} to {current}. This is an incident observation; retry once before creating an issue candidate and do not infer a regression from this check alone.",
    })
