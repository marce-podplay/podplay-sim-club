"""Local observatory server with one attested captain-result action."""

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Optional
from urllib.parse import quote

from .actor_runtime import run_tick as run_actor_tick
from .identity_registry import TEAM_ACTOR_NAMES
from .orchestrator import Orchestrator
from .preview_match_ledger import sanitized_match_index
from .tournament import acknowledge_result_request, report_winner
from .result_requests import report_finished_event_result


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
                    match_index = (
                        sanitized_match_index(ledger)
                        if isinstance(ledger, dict)
                        and isinstance(ledger.get("matches"), dict)
                        else {"activeOccurrenceKey": None, "matches": []}
                    )
                    tournament = _tournament_fixture(orchestrator)
                    preview_origin = _preview_origin(orchestrator)
                    if tournament is not None:
                        match_index["matches"] = [
                            match
                            for match in match_index["matches"]
                            if match.get("eventId") != tournament["match"]["eventId"]
                        ]
                        for match in match_index["matches"]:
                            match["active"] = False
                        match_index["matches"].append(tournament["match"])
                        match_index["activeOccurrenceKey"] = tournament["match"]["occurrenceKey"]
                        snapshot = tournament["snapshot"]
                    match_index = _with_observed_bookings(orchestrator, match_index)
                    match_index = _with_share_invitations(orchestrator, match_index)
                    match_index = _with_event_results(orchestrator, match_index)
                    match_index = _prefer_projected_events(orchestrator, match_index)
                    match_index = _with_social_lifecycle_offers(orchestrator, match_index)
                    match_index = _attach_share_invitation_links(orchestrator, match_index)
                    match_index = _with_known_venue(orchestrator, match_index)
                    _attach_preview_links(match_index, preview_origin)
                    self._json(HTTPStatus.OK, {
                        "previewOrigin": preview_origin,
                        "previewMatch": snapshot if isinstance(snapshot, dict) else None,
                        "previewMatches": match_index,
                        "messages": _recent_messages(orchestrator),
                        # Retained for API compatibility; the observatory now renders the
                        # smaller staff-initiative projection instead of raw storylines.
                        "storylines": _active_storylines(orchestrator),
                        "community": _community_snapshot(orchestrator),
                        "initiatives": _staff_initiatives(orchestrator),
                        "clubState": _club_state(orchestrator),
                        "arcade": _arcade_snapshot(orchestrator),
                    })
                    return
                if self.path == "/health":
                    self._json(
                        HTTPStatus.OK,
                        {"ok": True, "mode": "preview-observatory"},
                    )
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

            def do_POST(self):  # noqa: N802
                if self.path not in {"/api/tournament/report", "/api/tournament/acknowledge", "/api/events/result", "/api/events/arcade-replay-result"}:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 1 or length > 1024:
                        raise ValueError("invalid request length")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("invalid request")
                    if self.path == "/api/tournament/report":
                        state = report_winner(
                            orchestrator.storage.root,
                            payload.get("winner"),
                            payload.get("reporter"),
                        )
                        result = {"status": state["rounds"]["semi-final"]["status"], "winner": state["rounds"]["semi-final"]["winner"]}
                    elif self.path in {"/api/events/result", "/api/events/arcade-replay-result"}:
                        result = report_finished_event_result(
                            orchestrator.storage.root,
                            payload.get("intentId"), payload.get("reporter"), payload.get("winner"),
                        )
                    else:
                        result = acknowledge_result_request(
                            orchestrator.storage.root, payload.get("actorId")
                        )
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_captain_report"})
                    return
                self._json(HTTPStatus.OK, result)

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


def _preview_origin(orchestrator: Orchestrator) -> Optional[str]:
    """Return the approved preview origin already stored on local product state."""
    ledger = orchestrator.storage.load_json(
        orchestrator.storage.state / "preview-matches.json", default={}
    )
    origin = ledger.get("targetOrigin") if isinstance(ledger, dict) else None
    if isinstance(origin, str) and origin.startswith("https://"):
        return origin.rstrip("/")
    intents = orchestrator.storage.load_json(
        orchestrator.storage.state / "booking-intents.json", default={}
    )
    rows = intents.get("intents", {}) if isinstance(intents, dict) else {}
    if isinstance(rows, dict):
        for intent in rows.values():
            plan = intent.get("previewPlan") if isinstance(intent, dict) else None
            origin = plan.get("targetOrigin") if isinstance(plan, dict) else None
            if isinstance(origin, str) and origin.startswith("https://"):
                return origin.rstrip("/")
    return None


def _attach_preview_links(match_index, origin: Optional[str]) -> None:
    for match in match_index.get("matches", []):
        if not isinstance(match, dict):
            continue
        event_id = match.get("eventId")
        if isinstance(origin, str) and isinstance(event_id, str) and event_id:
            match["eventUrl"] = f"{origin}/admin/v2/events?eventId={quote(event_id, safe='')}"
        else:
            match["eventUrl"] = None


def _actor_directory(orchestrator: Orchestrator):
    directory = {
        "sofia": "Sofia Alvarez", "alex": "Alex Morgan", "riley": "Riley Chen",
        "lead": "Lead Coordinator", "andy-bogard": "Andy Bogard",
        "terry-bogard": "Terry Bogard", "kyo-captain": "Kyo Kusanagi",
        "benimaru": "Benimaru Nikaido",
    }
    directory.update({actor_id: " ".join(names) for actor_id, names in TEAM_ACTOR_NAMES.items()})
    directory.update({"king-captain": "King", "mai": "Mai Shiranui"})
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


def _active_storylines(orchestrator: Orchestrator):
    """Expose current character agreements separately from created product events."""
    ledger = orchestrator.storage.load_json(
        orchestrator.storage.state / "booking-intents.json", default={}
    )
    if not isinstance(ledger, dict) or not isinstance(ledger.get("intents"), dict):
        return []
    directory = _actor_directory(orchestrator)
    active_id = ledger.get("activeIntentId")
    rows = []
    for intent_id, intent in ledger["intents"].items():
        if not isinstance(intent, dict) or intent.get("status") in {"booked", "complete", "cancelled"}:
            continue
        participants = intent.get("participants")
        if not isinstance(participants, list):
            continue
        labels = [directory.get(actor_id, actor_id) for actor_id in participants if isinstance(actor_id, str)]
        rows.append(
            {
                "id": intent_id,
                "active": intent_id == active_id,
                "status": intent.get("status", "unknown"),
                "reason": intent.get("reason", "character agreement"),
                "journey": intent.get("journey", "planning"),
                "participants": labels,
                "durationMinutes": (
                    intent.get("constraints", {}).get("durationMinutes")
                    if isinstance(intent.get("constraints"), dict)
                    else None
                ),
                "nextAction": intent.get("nextAction", "await lead review"),
                "fallbackJourney": intent.get("fallbackJourney"),
            }
        )
    rows.sort(key=lambda row: (not row["active"], row["id"]))
    return rows


def _community_snapshot(orchestrator: Orchestrator):
    """Return a small, human-readable social view; never expose credentials.

    This is deliberately a projection rather than a dump of engine state.  The
    observatory needs to answer "who is here, how are they doing, and what is
    being offered?", not make operators decode ledgers and cooldown keys.
    """
    roster = orchestrator.storage.load_json(
        orchestrator.storage.root / "characters" / "roster.json", default={}
    )
    social = orchestrator.storage.load_json(
        orchestrator.storage.state / "social-engine.json", default={}
    )
    messages = _recent_messages(orchestrator)
    motives = social.get("motives", {}) if isinstance(social, dict) else {}
    offers = social.get("offers", []) if isinstance(social, dict) else []
    directory = _actor_directory(orchestrator)
    latest_message = {}
    for message in messages:
        sender = message.get("from")
        observed = message.get("observedAt")
        if isinstance(sender, str) and isinstance(observed, str):
            latest_message[sender] = {"kind": message.get("kind"), "observedAt": observed}

    people = []
    characters = roster.get("characters", []) if isinstance(roster, dict) else []
    for character in characters:
        if not isinstance(character, dict):
            continue
        actor_id = character.get("actorId")
        if not isinstance(actor_id, str):
            continue  # dormant roster names are not active community actors yet
        motive = motives.get(actor_id, {}) if isinstance(motives, dict) else {}
        actor_offers = [
            row.get("kind") for row in offers
            if isinstance(row, dict) and row.get("actorId") == actor_id and isinstance(row.get("kind"), str)
        ]
        teams = character.get("teamIds") if isinstance(character.get("teamIds"), list) else []
        people.append({
            "actorId": actor_id,
            "label": directory.get(actor_id, character.get("name", actor_id)),
            "role": character.get("role", "community"),
            "teams": teams,
            "captainOf": character.get("captainOf", []),
            "motives": {
                "play": motive.get("desireToPlay"),
                "curiosity": motive.get("curiosity"),
                "frustration": motive.get("frustration"),
            },
            "offers": actor_offers,
            "lastSignal": latest_message.get(actor_id),
        })
    people.sort(key=lambda row: (row["role"] not in {"club_owner", "club_staff"}, row["label"]))
    return {"people": people, "updatedAt": social.get("lastTickAt") if isinstance(social, dict) else None}


def _staff_initiatives(orchestrator: Orchestrator):
    """Project only actionable community initiatives, not every stale intent."""
    ledger = orchestrator.storage.load_json(
        orchestrator.storage.state / "booking-intents.json", default={}
    )
    if not isinstance(ledger, dict) or not isinstance(ledger.get("intents"), dict):
        return []
    directory = _actor_directory(orchestrator)
    commitment_projection = orchestrator.storage.load_json(
        orchestrator.storage.state / "projections" / "commitments.json", default={}
    )
    commitments = (
        commitment_projection.get("commitments", {})
        if isinstance(commitment_projection, dict)
        else {}
    )
    grounded_intents = {
        row.get("content", {}).get("intentRef")
        for row in commitments.values()
        if isinstance(row, dict)
        and row.get("status") == "accepted"
        and isinstance(row.get("content"), dict)
        and isinstance(row["content"].get("intentRef"), str)
    }
    active_id = ledger.get("activeIntentId")
    bridge_state = orchestrator.storage.load_json(
        orchestrator.storage.state / "lead-booking-bridge.json", default={}
    )
    last_bridge = bridge_state.get("lastAttempt") if isinstance(bridge_state, dict) else None
    rows = []
    terminal = {"complete", "cancelled", "missed_session", "expired"}
    for intent_id, intent in ledger["intents"].items():
        if not isinstance(intent, dict) or intent.get("status") in terminal:
            continue
        status = str(intent.get("status") or "unknown")
        if status not in {"agreed", "preview_ready", "requested", "planned"}:
            continue
        # An old intent may remain as history after a session.  It is useful to
        # the engine but not useful as a front-desk task unless it is the active
        # item or a current accepted commitment grounds it.
        if intent_id != active_id and intent_id not in grounded_intents:
            continue
        participants = intent.get("participants") if isinstance(intent.get("participants"), list) else []
        bridge = (
            last_bridge if isinstance(last_bridge, dict)
            and last_bridge.get("intentId") == intent_id else None
        )
        rows.append({
            "id": intent_id,
            "status": status,
            "reason": intent.get("reason", "community initiative"),
            "journey": intent.get("journey", "planning"),
            "participants": [directory.get(actor_id, actor_id) for actor_id in participants if isinstance(actor_id, str)],
            "nextAction": intent.get("nextAction") or ("plan a legal court" if status == "agreed" else "await community response"),
            "durationMinutes": (intent.get("constraints") or {}).get("durationMinutes") if isinstance(intent.get("constraints"), dict) else None,
            "leadBridge": {
                "action": bridge.get("action"), "detail": bridge.get("detail"),
                "observedAt": bridge.get("observedAt"),
            } if bridge else None,
        })
    rows.sort(key=lambda row: (row["status"] != "agreed", row["id"]))
    return rows[:8]


def _club_state(orchestrator: Orchestrator):
    """Expose the local engine clock separately from any stale remote snapshot."""
    rally = orchestrator.storage.load_json(orchestrator.storage.state / "rally-engine.json", default={})
    ledger = orchestrator.storage.load_json(orchestrator.storage.state / "booking-intents.json", default={})
    recovery = next((row for row in ledger.get("intents", {}).values()
                     if isinstance(row, dict) and row.get("reason") in {"missed_session_recovery", "missed_session_retry"}), None) if isinstance(ledger, dict) else None
    return {
        "lastTickAt": rally.get("lastTickAt") if isinstance(rally, dict) else None,
        "recovery": {
            "status": recovery.get("status"),
            "nextAction": recovery.get("nextAction"),
        } if isinstance(recovery, dict) else None,
    }


def _with_known_venue(orchestrator: Orchestrator, match_index):
    """Attach the latest known venue to every record using the same pod."""
    directory = _actor_directory(orchestrator)
    readiness = orchestrator.storage.load_json(
        orchestrator.storage.state / "preview-readiness.json", default={}
    )
    location = readiness.get("location") if isinstance(readiness, dict) else None
    venue = (
        {key: location.get(key) for key in ("areaName", "podName", "timezone")}
        if isinstance(location, dict) and isinstance(location.get("podId"), str)
        else None
    )
    for match in match_index.get("matches", []):
        if not isinstance(match, dict):
            continue
        if venue is not None and match.get("podId") == location["podId"]:
            match["venue"] = venue
        for participant in match.get("participants", []):
            if isinstance(participant, dict):
                actor_id = participant.get("actorId")
                participant["label"] = directory.get(actor_id, actor_id)
    return match_index


def _arcade_snapshot(orchestrator):
    """Expose replay-safe local Arcade records; they are never product evidence."""
    value = orchestrator.storage.load_json(orchestrator.storage.state / "arcade" / "matches.json", default={})
    matches = value.get("matches") if isinstance(value, dict) else {}
    rows = []
    for match in matches.values() if isinstance(matches, dict) else []:
        if not isinstance(match, dict):
            continue
        rows.append({key: match.get(key) for key in ("occurrenceKey", "game", "left", "right", "score", "winner", "winnerSide", "rallies", "status")})
    return {"kind": "arcade_simulation", "matches": sorted(rows, key=lambda row: str(row.get("occurrenceKey") or ""))}


def _with_observed_bookings(orchestrator: Orchestrator, match_index):
    """Merge UI-confirmed player bookings without inventing an event id or attendance."""
    observations = orchestrator.storage.load_json(
        orchestrator.storage.state / "observed-bookings.json", default={}
    )
    rows = observations.get("bookings") if isinstance(observations, dict) else None
    if not isinstance(rows, list):
        return match_index
    existing = {row.get("occurrenceKey") for row in match_index.get("matches", []) if isinstance(row, dict)}
    selected = None
    for row in rows:
        if not isinstance(row, dict) or row.get("occurrenceKey") in existing:
            continue
        match_index["matches"].append(row)
        if row.get("active") is True and isinstance(row.get("occurrenceKey"), str):
            selected = row["occurrenceKey"]
    if selected is not None and not match_index.get("activeOccurrenceKey"):
        for match in match_index["matches"]:
            if isinstance(match, dict):
                match["active"] = match.get("occurrenceKey") == selected
        match_index["activeOccurrenceKey"] = selected
    match_index["matches"].sort(key=lambda row: (str(row.get("startTime") or ""), str(row.get("occurrenceKey") or "")))
    return match_index


def _prefer_projected_events(orchestrator: Orchestrator, match_index):
    """Replace overlapping legacy cards with whole canonical projections.

    Pre-journal history remains visible as complete legacy cards. A projected
    occurrence is never field-merged with those ledgers: its event and joined
    per-actor participation projection form the entire lifecycle card.
    """
    event_store = orchestrator.storage.load_json(
        orchestrator.storage.state / "projections" / "events.json", default={}
    )
    participation_store = orchestrator.storage.load_json(
        orchestrator.storage.state / "projections" / "participation.json", default={}
    )
    events = event_store.get("events") if isinstance(event_store, dict) else None
    participation = participation_store.get("participation") if isinstance(participation_store, dict) else None
    if not isinstance(events, dict) or not events:
        return match_index
    participation = participation if isinstance(participation, dict) else {}
    directory = _actor_directory(orchestrator)
    projected = []
    for occurrence_key, event in events.items():
        if not isinstance(event, dict):
            continue
        window = event.get("plannedWindow") if isinstance(event.get("plannedWindow"), dict) else {}
        people = []
        for row in participation.values():
            if not isinstance(row, dict) or row.get("occurrenceKey") != occurrence_key:
                continue
            actor_id = row.get("actorId")
            roles = row.get("roles") if isinstance(row.get("roles"), list) else []
            people.append({
                "actorId": actor_id, "label": directory.get(actor_id, actor_id),
                "role": ", ".join(roles) or "participant",
                "bookingStatus": "BOOKED" if event.get("status") not in {"planned", "unknown"} else "PLANNED",
                "invitationStatus": str(row.get("invitation", "not_observed")).upper(),
                "attendance": str(row.get("attendance", "not_observed")).upper(),
                "checkIn": str(row.get("checkIn", "not_observed")).upper(),
                "resultReporter": row.get("resultReporter") is True,
                "evidenceRefs": row.get("evidenceRefs", []),
            })
        people.sort(key=lambda row: str(row.get("actorId") or ""))
        status = str(event.get("status") or "unknown")
        result = event.get("result") if isinstance(event.get("result"), dict) else {"status": "unreported"}
        invitees = [row for row in people if "booking_owner" not in str(row.get("role") or "").split(", ")]
        accepted_count = sum(row["invitationStatus"] == "ACCEPTED" for row in invitees)
        invitation_step = (
            "NOT_REQUIRED" if not invitees
            else "ACCEPTED" if accepted_count == len(invitees)
            else "PARTIAL" if accepted_count
            else "NOT_OBSERVED"
        )
        checked_in_count = sum(row["checkIn"] == "CHECKED_IN" for row in people)
        check_in_step = (
            "CHECKED_IN" if people and checked_in_count == len(people)
            else "PARTIAL" if checked_in_count
            else "NOT_OBSERVED"
        )
        play_step = (
            "RESULT_REPORTED" if result.get("status") == "reported"
            else "AWAITING_CAPTAIN_RESULT" if result.get("status") == "requested"
            else "NOT_PLAYED" if status == "missed_session"
            else "ATTENDANCE_OBSERVED" if status == "attendance_observed"
            else "INCONCLUSIVE" if status == "attendance_inconclusive"
            else "NOT_RECORDED"
        )
        steps = {
            "booking": "PLANNED" if status == "planned" else "BOOKED",
            "invitation": invitation_step,
            "acceptance": invitation_step,
            "checkIn": check_in_step,
            "play": play_step,
        }
        projected.append({
            "occurrenceKey": occurrence_key, "active": False,
            "phase": status, "eventId": event.get("productEventRef"),
            "eventCode": None, "label": str(event.get("kind") or "Club event").replace("_", " ").title(),
            "eventStatus": status.upper(), "startTime": window.get("startTime"),
            "endTime": window.get("endTime"), "podId": window.get("podRef"),
            "participants": people, "result": result,
            "lifecycle": {"state": status.upper(), "steps": steps},
            "socialIntentRef": event.get("socialIntentRef"),
            "productEvidenceRef": event.get("productEvidenceRef"),
        })
    projected_keys = {row["occurrenceKey"] for row in projected}
    legacy = [row for row in match_index.get("matches", []) if row.get("occurrenceKey") not in projected_keys]
    rows = legacy + projected
    rows.sort(key=lambda row: (str(row.get("startTime") or ""), str(row.get("occurrenceKey") or "")))
    active = match_index.get("activeOccurrenceKey")
    if active not in {row.get("occurrenceKey") for row in rows} and projected:
        active = max(
            projected,
            key=lambda row: (str(row.get("startTime") or ""), str(row.get("occurrenceKey") or "")),
        )["occurrenceKey"]
    for row in rows:
        row["active"] = row.get("occurrenceKey") == active
    return {"activeOccurrenceKey": active, "matches": rows}


def _attach_share_invitation_links(orchestrator: Orchestrator, match_index):
    """Attach the private invitation sidecar without changing lifecycle facts.

    The canonical event/participation card replaces overlapping compatibility
    cards wholesale. A retained Preview bearer link is an operator-approved
    sidecar, not event state, so it is attached only after that replacement.
    """
    value = orchestrator.storage.load_json(
        orchestrator.storage.state / "share-invitations.json", default={}
    )
    invitations = value.get("invitations") if isinstance(value, dict) else None
    if not isinstance(invitations, dict):
        return match_index
    for match in match_index.get("matches", []):
        if not isinstance(match, dict):
            continue
        invitation = invitations.get(match.get("occurrenceKey"))
        if not isinstance(invitation, dict):
            continue
        match["shareInvitation"] = {
            key: invitation.get(key)
            for key in ("redeemUrl", "maxRedeems", "redeemsRemaining", "capturedAt")
        }
    return match_index


def _with_social_lifecycle_offers(orchestrator: Orchestrator, match_index):
    """Show a legal local next step without presenting it as product evidence.

    A `capture_share_invitation` offer means that the booking owner may decide
    to open a private handoff. It does *not* mean a PodPlay invitation exists,
    so no participant invitation status is changed here.
    """
    social = orchestrator.storage.load_json(
        orchestrator.storage.state / "social-engine.json", default={}
    )
    offers = social.get("offers") if isinstance(social, dict) else None
    offers = offers if isinstance(offers, list) else []
    by_intent = {
        row.get("intentId"): row
        for row in offers
        if isinstance(row, dict)
        and row.get("kind") == "capture_share_invitation"
        and isinstance(row.get("intentId"), str)
    }
    if not by_intent:
        return match_index
    for match in match_index.get("matches", []):
        if not isinstance(match, dict):
            continue
        offer = by_intent.get(match.get("socialIntentRef"))
        if not isinstance(offer, dict):
            continue
        lifecycle = match.get("lifecycle") if isinstance(match.get("lifecycle"), dict) else {}
        steps = lifecycle.get("steps") if isinstance(lifecycle.get("steps"), dict) else {}
        if steps.get("invitation") in {None, "NOT_OBSERVED"}:
            steps["invitation"] = "OWNER_SHARE_OFFERED"
            lifecycle["state"] = "AWAITING_OWNER_SHARE"
        lifecycle["steps"] = steps
        match["lifecycle"] = lifecycle
        match["socialNextAction"] = {
            "kind": offer["kind"],
            "actorId": offer.get("actorId"),
            "recipientActorIds": offer.get("recipientActorIds", []),
        }
    return match_index


def _with_share_invitations(orchestrator: Orchestrator, match_index):
    """Attach explicitly retained preview share links and actual remaining seats."""
    value = orchestrator.storage.load_json(
        orchestrator.storage.state / "share-invitations.json", default={}
    )
    invitations = value.get("invitations") if isinstance(value, dict) else None
    if not isinstance(invitations, dict):
        return match_index
    intents = orchestrator.storage.load_json(orchestrator.storage.state / "booking-intents.json", default={})
    training_by_occurrence = {
        row.get("booking", {}).get("occurrenceKey"): row
        for row in intents.get("intents", {}).values()
        if isinstance(row, dict) and row.get("journey") == "customer_training" and isinstance(row.get("booking"), dict)
    } if isinstance(intents, dict) else {}
    messages = orchestrator.storage.read_jsonl(orchestrator.storage.channel_path("club"))
    for match in match_index.get("matches", []):
        if not isinstance(match, dict):
            continue
        invitation = invitations.get(match.get("occurrenceKey"))
        if not isinstance(invitation, dict):
            continue
        match["shareInvitation"] = {
            key: invitation.get(key)
            for key in ("redeemUrl", "maxRedeems", "redeemsRemaining", "capturedAt")
        }
        lifecycle = match.get("lifecycle") if isinstance(match.get("lifecycle"), dict) else {}
        steps = lifecycle.get("steps") if isinstance(lifecycle.get("steps"), dict) else {}
        # A retained link is useful evidence only until redemption.  Do not
        # downgrade a read-back SHARE_REDEEMED or checked-in lifecycle when a
        # dashboard snapshot is refreshed.
        steps.setdefault("invitation", "LINK_CAPTURED")
        lifecycle["steps"] = steps
        match["lifecycle"] = lifecycle
        training = training_by_occurrence.get(match.get("occurrenceKey"))
        if isinstance(training, dict):
            team_id = training.get("trainingTeamId")
            if isinstance(team_id, str) and team_id:
                team_label = " ".join(part.title() for part in team_id.split("-"))
            else:
                # The original King/Mai scenario predates trainingTeamId.
                team_label = "Women Fighters"
            match["label"] = f"{team_label} training"
            # The generic occurrence ledger falls back to Andy/Terry for old
            # two-player matches.  A known training intent is stronger local
            # evidence, so replace that fallback rather than blending teams.
            training_ids = {actor_id for actor_id in training.get("participants", []) if isinstance(actor_id, str)}
            participants = [row for row in match.get("participants", []) if isinstance(row, dict) and row.get("actorId") in training_ids]
            match["participants"] = participants
            actor_ids = {row.get("actorId") for row in participants}
            for actor_id in training.get("participants", []):
                if actor_id not in actor_ids:
                    participants.append({
                        "actorId": actor_id,
                        "role": "booking owner" if actor_id == training.get("bookerActorId") else "intended training partner",
                        "bookingStatus": "BOOKED" if actor_id == training.get("bookerActorId") else "INVITATION_READY",
                        "invitationStatus": "OWNER" if actor_id == training.get("bookerActorId") else "LINK_CAPTURED",
                        "attendance": "NOT_OBSERVED",
                    })
            accepted = any(
                isinstance(row, dict)
                and row.get("kind") == "share_invitation_accepted"
                and row.get("intentId") == training.get("id")
                and row.get("from") in training_ids
                and row.get("from") != training.get("bookerActorId")
                for row in messages
            )
            if accepted:
                if lifecycle.get("state") not in {"CHECKED_IN", "CHECK_IN_RECORDED"}:
                    lifecycle["state"] = "INVITATION_ACCEPTED"
                lifecycle["steps"]["acceptance"] = "ACCEPTED"
                for participant in participants:
                    if isinstance(participant, dict) and participant.get("actorId") != training.get("bookerActorId"):
                        participant["bookingStatus"] = "ACCEPTED"
                        participant["invitationStatus"] = "ACCEPTED"
            observation = training.get("eventObservation")
            if isinstance(observation, dict) and observation.get("outcome") == "missed_session":
                lifecycle["state"] = "MISSED_SESSION"
                lifecycle["steps"]["checkIn"] = "NO_CHECKINS_OBSERVED"
                lifecycle["steps"]["play"] = "NOT_PLAYED"
                observed_players = observation.get("participants")
                if isinstance(observed_players, dict):
                    for participant in participants:
                        if not isinstance(participant, dict):
                            continue
                        evidence = observed_players.get(participant.get("actorId"))
                        if isinstance(evidence, dict) and evidence.get("checkIn") == "NOT_CHECKED_IN":
                            participant["attendance"] = "NOT_CHECKED_IN"
            elif participants and all(
                row.get("attendance") == "CHECKED_IN" for row in participants if isinstance(row, dict)
            ):
                lifecycle["state"] = "CHECKED_IN"
                lifecycle["steps"]["checkIn"] = "CHECKED_IN"
                lifecycle["steps"].setdefault("play", "NOT_RECORDED")
    return match_index


def _with_event_results(orchestrator: Orchestrator, match_index):
    """Expose only attested result lifecycle data for matching local events."""
    ledger = orchestrator.storage.load_json(orchestrator.storage.state / "booking-intents.json", default={})
    intents = ledger.get("intents") if isinstance(ledger, dict) else None
    if not isinstance(intents, dict):
        return match_index
    by_occurrence = {}
    for intent_id, intent in intents.items():
        if not isinstance(intent, dict):
            continue
        for key in ("booking", "previewPlan"):
            value = intent.get(key)
            occurrence = value.get("occurrenceKey") if isinstance(value, dict) else None
            if isinstance(occurrence, str):
                by_occurrence[occurrence] = (intent_id, intent)
    for match in match_index.get("matches", []):
        if not isinstance(match, dict):
            continue
        intent_id = match.get("intentId")
        intent = intents.get(intent_id)
        if not isinstance(intent, dict):
            found = by_occurrence.get(match.get("occurrenceKey"))
            if found is not None:
                intent_id, intent = found
                match["intentId"] = intent_id
        replay = (
            intent.get("arcadeReplay")
            if isinstance(intent, dict) and isinstance(intent.get("arcadeReplay"), dict)
            else None
        )
        if replay is not None:
            match["arcadeReplay"] = {
                key: replay.get(key)
                for key in ("occurrenceKey", "status", "evidence", "reporterActorId", "winner", "winnerSide", "score", "game")
                if replay.get(key) is not None
            }
        request = intent.get("resultRequest") if isinstance(intent, dict) else None
        if not isinstance(request, dict):
            continue
        result = {
            key: request.get(key)
            for key in ("status", "reporterActorId", "requestedAt", "reportedAt", "winner", "evidence")
            if request.get(key) is not None
        }
        match["result"] = result
        lifecycle = match.get("lifecycle") if isinstance(match.get("lifecycle"), dict) else {}
        steps = lifecycle.get("steps") if isinstance(lifecycle.get("steps"), dict) else {}
        if request.get("status") == "reported":
            lifecycle["state"] = "RESULT_REPORTED"
            steps["play"] = "RESULT_REPORTED"
        elif request.get("status") == "requested":
            steps["play"] = "AWAITING_CAPTAIN_RESULT"
        lifecycle["steps"] = steps
        match["lifecycle"] = lifecycle
    return match_index


def _tournament_fixture(orchestrator):
    """Project the active tournament fixture without replacing historical matches."""
    ledger = orchestrator.storage.load_json(
        orchestrator.storage.state / "booking-intents.json", default={}
    )
    if not isinstance(ledger, dict):
        return None
    intents = ledger.get("intents", {})
    final = intents.get("intent:tournament:bogard-japan-women-fighters-001:final")
    semi = intents.get("intent:tournament:bogard-japan-women-fighters-001:semi-final")
    intent = final if isinstance(final, dict) and final.get("status") in {"event_created", "players_registered"} else semi
    if not isinstance(intent, dict) or intent.get("status") not in {
        "event_created", "players_registered"
    }:
        return None
    event = intent.get("event")
    plan = intent.get("previewPlan")
    if not isinstance(event, dict) or not isinstance(plan, dict):
        return None
    event_id = event.get("eventId")
    occurrence_key = plan.get("occurrenceKey")
    if not isinstance(event_id, str) or not isinstance(occurrence_key, str):
        return None
    participants = []
    for signup in intent.get("signups", []):
        if not isinstance(signup, dict) or not isinstance(signup.get("actorId"), str):
            continue
        participants.append(
            {
                "actorId": signup["actorId"],
                "role": "registered tournament player",
                "invitationStatus": "REGISTERED",
                "attendance": "not refreshed",
            }
        )
    venue = {
        "areaName": plan.get("areaName"),
        "podName": plan.get("podName"),
        "timezone": "America/New_York",
    }
    evidence = intent.get("evidence")
    round_name = evidence.get("round", "fixture") if isinstance(evidence, dict) else "fixture"
    match = {
        "occurrenceKey": occurrence_key,
        "occurrenceCode": f"TOURNAMENT-{str(round_name).upper()}",
        "active": True,
        "phase": "scheduled",
        "eventId": event_id,
        "eventCode": f"EVT-{''.join(char for char in event_id if char.isalnum())[:8].upper()}",
        "label": event.get("name") or "Tournament fixture",
        "eventStatus": event.get("status") or "PUBLISHED",
        "startTime": event.get("startTime") or plan.get("startTime"),
        "endTime": event.get("endTime") or plan.get("endTime"),
        "podId": plan.get("podId"),
        "venue": venue,
        "participants": participants,
    }
    return {
        "match": match,
        "snapshot": {
            "schemaVersion": 1,
            "result": "SCHEDULED",
            "pullRequest": plan.get("pullRequest"),
            "observedAt": plan.get("observedAt"),
            "event": {
                "eventId": event_id,
                "status": event.get("status") or "PUBLISHED",
            },
            "venue": venue,
        },
    }


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
