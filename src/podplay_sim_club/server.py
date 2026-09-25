"""Read-only local observatory server."""

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Optional

from .actor_runtime import run_tick as run_actor_tick
from .orchestrator import Orchestrator
from .preview_match_ledger import sanitized_match_index


class ObservatoryServer:
    def __init__(
        self,
        orchestrator: Orchestrator,
        host: str = "127.0.0.1",
        port: int = 8787,
        beat_every_seconds: Optional[float] = None,
        turns_per_beat: int = 10,
    ):
        self.orchestrator = orchestrator
        self.host = host
        self.port = port
        self.beat_every_seconds = beat_every_seconds
        self.turns_per_beat = turns_per_beat
        self._stop = threading.Event()
        self._scheduler: Optional[threading.Thread] = None
        self._server = ThreadingHTTPServer((host, port), self._handler())

    def _handler(self):
        orchestrator = self.orchestrator
        template_path = Path(__file__).resolve().parent / "templates" / "observatory.html"

        class Handler(BaseHTTPRequestHandler):
            server_version = "PodPlaySimClub/0.1"

            def do_GET(self):  # noqa: N802
                if self.path == "/":
                    self._send(
                        HTTPStatus.OK,
                        template_path.read_bytes(),
                        "text/html; charset=utf-8",
                    )
                    return
                if self.path == "/api/preview":
                    snapshot = orchestrator.storage.load_json(
                        orchestrator.storage.state / "preview-match-status.json",
                        default=None,
                    )
                    ledger = orchestrator.storage.load_json(
                        orchestrator.storage.state / "preview-matches.json",
                        default=None,
                    )
                    self._json(HTTPStatus.OK, {
                        "previewMatch": snapshot if isinstance(snapshot, dict) else None,
                        "previewMatches": (
                            sanitized_match_index(ledger)
                            if isinstance(ledger, dict)
                            and isinstance(ledger.get("matches"), dict)
                            else {"activeOccurrenceKey": None, "matches": []}
                        ),
                        "messages": _recent_messages(orchestrator),
                    })
                    return
                if self.path == "/health":
                    self._json(
                        HTTPStatus.OK,
                        {"ok": True, "mode": "preview-observatory"},
                    )
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

            def log_message(self, fmt, *args):
                print(f"observatory: {fmt % args}")

            def _json(self, status: HTTPStatus, value):
                payload = json.dumps(value, sort_keys=True).encode("utf-8")
                self._send(status, payload, "application/json; charset=utf-8")

            def _send(self, status: HTTPStatus, payload: bytes, content_type: str):
                self.send_response(status.value)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

        return Handler

    def serve_forever(self) -> None:
        if self.beat_every_seconds is not None:
            self._scheduler = threading.Thread(
                target=self._scheduler_loop, name="club-scheduler", daemon=True
            )
            self._scheduler.start()
        try:
            self._server.serve_forever()
        finally:
            self._stop.set()
            self._server.server_close()

    @property
    def address(self):
        return self._server.server_address

    def shutdown(self) -> None:
        self._stop.set()
        self._server.shutdown()

    def _scheduler_loop(self) -> None:
        assert self.beat_every_seconds is not None
        while not self._stop.wait(self.beat_every_seconds):
            try:
                result = run_actor_tick(
                    self.orchestrator.storage.root,
                    max_turns=self.turns_per_beat,
                )
                print(
                    f"actor tick {result['tick']}: "
                    f"{len(result['turns'])} turns; remote writes 0"
                )
            except Exception as exc:  # scheduler stays alive; evidence remains in files
                print(f"actor scheduler failed: {type(exc).__name__}: {exc}")


def _recent_messages(orchestrator: Orchestrator):
    """Return the shared actor channel in chronological, bounded order."""
    rows = orchestrator.storage.read_jsonl(orchestrator.storage.channel_path("club"))
    directory = _actor_directory(orchestrator)
    messages = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        message = dict(row)
        sender = message.get("from")
        recipients = message.get("to")
        message["fromLabel"] = directory.get(sender, sender or "System")
        message["toLabels"] = [
            directory.get(actor_id, actor_id)
            for actor_id in recipients
            if isinstance(actor_id, str)
        ] if isinstance(recipients, list) else []
        messages.append(message)
    messages.sort(key=lambda row: (str(row.get("observedAt") or ""), str(row.get("id") or "")))
    return messages[-80:]


def _actor_directory(orchestrator: Orchestrator):
    directory = {
        "sofia": "Sofia Alvarez", "alex": "Alex Morgan", "riley": "Riley Chen",
        "lead": "Lead Coordinator", "red-captain": "Andy Bogard",
        "blue-captain": "Terry Bogard", "kyo-captain": "Kyo Kusanagi",
        "benimaru": "Benimaru Nikaido",
    }
    roster = orchestrator.storage.load_json(
        orchestrator.storage.root / "characters" / "roster.json", default={}
    )
    if isinstance(roster, dict):
        for character in roster.get("characters", []):
            if isinstance(character, dict):
                actor_id, name = character.get("actorId"), character.get("name")
                if isinstance(actor_id, str) and isinstance(name, str):
                    directory[actor_id] = name
    return directory


def serve(
    root: Path,
    host: str,
    port: int,
    beat_every_seconds: Optional[float],
    turns_per_beat: int,
) -> None:
    app = ObservatoryServer(
        Orchestrator(root),
        host=host,
        port=port,
        beat_every_seconds=beat_every_seconds,
        turns_per_beat=turns_per_beat,
    )
    print(f"Preview Club observatory: http://{host}:{port}")
    if beat_every_seconds is None:
        print("Actor scheduler: disabled")
    else:
        print(f"Actor scheduler: every {beat_every_seconds:g}s, {turns_per_beat} turns")
    app.serve_forever()
