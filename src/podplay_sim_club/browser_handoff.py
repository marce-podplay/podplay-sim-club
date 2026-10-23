"""One-time, local-only credential handoff for a Preview player browser.

The simulator normally keeps actor credentials in a 0600 registry and uses
them for API reads.  A player-facing browser needs the same existing identity,
but a CLI must never print its email or password.  This narrowly scoped helper
lets the *approved Preview origin only* request one field at a time from a
loopback server.  It is intentionally short-lived and retains nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .credentials import PreviewCredentials
from .identity_registry import IdentityRegistry, IdentityRegistryError
from .players import canonical_actor_id
from .target_policy import TargetPolicy


class BrowserHandoffError(RuntimeError):
    """Raised when a browser credential handoff cannot be safely started."""


@dataclass
class _HandoffState:
    actor_id: str
    email: str
    password: str
    allowed_origin: str
    fields_served: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def claim(self, actor_id: str, field_name: str) -> Optional[str]:
        """Return a field at most once; never return an unsupported value."""
        if actor_id != self.actor_id or field_name not in {"email", "password"}:
            return None
        with self.lock:
            if field_name in self.fields_served:
                return None
            self.fields_served.add(field_name)
            return self.email if field_name == "email" else self.password

    @property
    def complete(self) -> bool:
        with self.lock:
            return self.fields_served == {"email", "password"}


def serve_browser_credential_handoff(
    root: Path,
    actor_id: Optional[str] = None,
    *,
    use_admin: bool = False,
    port: int = 0,
    lifetime_seconds: int = 120,
) -> int:
    """Expose one approved browser identity to the exact Preview origin briefly.

    Returns the selected loopback port after serving either both fields or the
    bounded lifetime.  It prints only actor id, origin, port, and completion
    state.  Credentials never enter output or durable state.
    """
    if lifetime_seconds <= 0:
        raise BrowserHandoffError("handoff lifetime must be positive")
    if use_admin and actor_id is not None:
        raise BrowserHandoffError("choose either an actor or the approved administrator")
    if not use_admin and not actor_id:
        raise BrowserHandoffError("an actor or the approved administrator is required")
    credentials = PreviewCredentials.load(root)
    policy = TargetPolicy.from_config(credentials.config)
    if use_admin:
        handoff_actor_id = "podplay-admin"
        email, password = credentials.admin_email, credentials.admin_password
    else:
        handoff_actor_id = canonical_actor_id(str(actor_id))
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").stored()
        identity = identities.get(handoff_actor_id)
        if identity is None:
            raise IdentityRegistryError(f"actor is missing from the private registry: {handoff_actor_id}")
        email, password = identity.email, identity.password
    state = _HandoffState(
        actor_id=handoff_actor_id,
        email=email,
        password=password,
        allowed_origin=policy.target_origin,
    )

    class Handler(BaseHTTPRequestHandler):
        server_version = "PreviewClubBrowserHandoff/1"

        def log_message(self, _format: str, *_args: object) -> None:
            # Standard handler logging may include query strings.  Keep it quiet.
            return

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", state.allowed_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")
            self.send_header("Cache-Control", "no-store")

        def do_OPTIONS(self) -> None:  # noqa: N802
            if self.headers.get("Origin") != state.allowed_origin:
                self.send_error(403)
                return
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            requested_actor = query.get("actor", [""])[0]
            field_name = query.get("field", [""])[0]
            if (
                parsed.path != "/v1/credential"
                or self.headers.get("Origin") != state.allowed_origin
            ):
                self.send_error(403)
                return
            value = state.claim(requested_actor, field_name)
            if value is None:
                self.send_error(404)
                return
            body = json.dumps({"value": value}).encode("utf-8")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise BrowserHandoffError("could not bind the loopback handoff server") from exc
    server.timeout = 0.25
    selected_port = int(server.server_address[1])
    print("PREVIEW BROWSER HANDOFF // LOCAL ONLY", flush=True)
    print(f"actor: {handoff_actor_id}", flush=True)
    print(f"origin: {state.allowed_origin}", flush=True)
    print(f"port: {selected_port}", flush=True)
    print(f"expiresInSeconds: {lifetime_seconds}", flush=True)
    deadline = time.monotonic() + lifetime_seconds
    try:
        while time.monotonic() < deadline and not state.complete:
            server.handle_request()
    finally:
        server.server_close()
    print(f"complete: {'yes' if state.complete else 'no'}", flush=True)
    return selected_port
