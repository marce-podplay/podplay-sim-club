"""Local Club Lab active-session inference.

PodPlay does not currently provide a product ``PLAYING`` state for these
bookings.  Club Lab may therefore model a local active session only when the
scheduled window is open and every named player has a visibly confirmed staff
check-in.  It is a dashboard/simulation fact, never product attendance,
scoring, or result evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .facts import append_fact_locked, make_fact
from .identity_registry import ACTOR_NAMES, TEAM_ACTOR_NAMES
from .storage import Storage
from .time import isoformat, parse_instant


STATE_FILE = "active-sessions.json"


def tick(root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Start and end local active sessions from durable, visible evidence."""
    storage = Storage(root)
    instant = now or datetime.now(timezone.utc)
    observed_at = isoformat(instant)
    started: list[dict[str, Any]] = []
    ended: list[dict[str, Any]] = []
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={"schemaVersion": 1, "sessions": {}})
        sessions = state.get("sessions") if isinstance(state, dict) and isinstance(state.get("sessions"), dict) else {}
        ledger = storage.load_json(storage.state / "booking-intents.json", default={})
        intents = ledger.get("intents") if isinstance(ledger, dict) and isinstance(ledger.get("intents"), dict) else {}

        for occurrence, session in sessions.items():
            if not isinstance(occurrence, str) or not isinstance(session, dict):
                continue
            if session.get("status") != "PLAYING":
                continue
            end_time = session.get("endTime")
            if isinstance(end_time, str) and instant >= parse_instant(end_time):
                session["status"] = "ENDED"
                session["endedAt"] = observed_at
                session["lifecycle"] = {"steps": {"play": "ENDED"}}
                sessions[occurrence] = session
                append_fact_locked(storage, make_fact(
                    fact_type="event.local_play_ended",
                    natural_key=f"{occurrence}:local-play-ended",
                    subject_ref=occurrence,
                    correlation_id=f"journey:{session.get('intentId', occurrence)}",
                    source_kind="local_session_inference",
                    occurred_at=observed_at,
                    actor_id="system",
                    payload={"reason": "scheduled_window_ended", "doesNotMean": ["attendance", "score", "result"]},
                ))
                ended.append(_public_session(session))

        for intent_id, intent in intents.items():
            candidate = _session_candidate(intent_id, intent, instant)
            if candidate is None:
                continue
            occurrence = candidate["occurrenceKey"]
            existing = sessions.get(occurrence)
            if isinstance(existing, dict):
                continue
            sessions[occurrence] = candidate
            append_fact_locked(storage, make_fact(
                fact_type="event.local_play_started",
                natural_key=f"{occurrence}:local-play-started",
                subject_ref=occurrence,
                correlation_id=f"journey:{intent_id}",
                source_kind="local_session_inference",
                occurred_at=observed_at,
                actor_id="system",
                payload={
                    "basis": ["scheduled_window_open", "all_named_visible_staff_check_ins"],
                    "doesNotMean": ["product_reported_play", "attendance", "score", "result"],
                },
            ))
            started.append(_public_session(candidate))
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "sessions": sessions})
    return {"action": "started" if started else "ended" if ended else "pass", "started": started, "ended": ended, "remoteWrites": 0}


def sessions(root: Path) -> list[dict[str, Any]]:
    """Return only safe local session projections for the dashboard/evidence."""
    storage = Storage(root)
    state = storage.load_json(storage.state / STATE_FILE, default={})
    rows = state.get("sessions") if isinstance(state, dict) and isinstance(state.get("sessions"), dict) else {}
    return [_public_session(row) for row in rows.values() if isinstance(row, dict)]


def _session_candidate(intent_id: Any, intent: Any, now: datetime) -> dict[str, Any] | None:
    if not isinstance(intent_id, str) or not isinstance(intent, dict):
        return None
    booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
    start_time, end_time = booking.get("startTime"), booking.get("endTime")
    participants = [actor for actor in intent.get("participants", []) if isinstance(actor, str)]
    checks = intent.get("browserCheckIns") if isinstance(intent.get("browserCheckIns"), dict) else {}
    if (
        len(participants) < 2
        or not isinstance(start_time, str)
        or not isinstance(end_time, str)
        or not start_time <= end_time
        or not (parse_instant(start_time) <= now < parse_instant(end_time))
        or not all(_is_visible_check_in(checks.get(actor)) for actor in participants)
    ):
        return None
    occurrence = booking.get("occurrenceKey")
    if not isinstance(occurrence, str) or not occurrence:
        occurrence = intent_id
    venue_label = booking.get("venueLabel") if isinstance(booking.get("venueLabel"), str) else "Club venue / booked pod"
    area, separator, pod = venue_label.partition(" / ")
    return {
        "occurrenceKey": occurrence,
        "intentId": intent_id,
        "label": str(intent.get("label") or intent.get("title") or "Club training"),
        "startTime": start_time,
        "endTime": end_time,
        "startedAt": isoformat(now),
        "status": "PLAYING",
        "venue": {"areaName": area or "Club venue", "podName": pod if separator and pod else "Booked pod"},
        "participants": [{"actorId": actor, "label": _label(actor)} for actor in participants],
        "lifecycle": {"steps": {"booking": "BOOKED", "checkIn": "CONFIRMED", "play": "PLAYING"}},
        "boundary": "local active-session inference from open booking window and visible staff check-ins; not product-reported play, attendance, score, or result",
    }


def _is_visible_check_in(value: Any) -> bool:
    return isinstance(value, dict) and value.get("status") == "CHECKED_IN" and value.get("source") == "visible_staff_browser"


def _label(actor_id: str) -> str:
    value = {**ACTOR_NAMES, **TEAM_ACTOR_NAMES}.get(actor_id)
    return " ".join(value) if value else "Named Sim"


def _public_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        key: session.get(key)
        for key in ("occurrenceKey", "intentId", "label", "startTime", "endTime", "startedAt", "endedAt", "status", "venue", "participants", "lifecycle", "boundary")
    }
