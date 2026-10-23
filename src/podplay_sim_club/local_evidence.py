"""Sanitized, local-only visual evidence receipts for Club Lab.

These images are evidence *artifacts*, not product screenshots and not proof
of attendance, a result, a regression, or a customer entitlement.  A live-play
receipt is created only from an explicit local ``PLAYING`` lifecycle state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from .storage import Storage
from .identity_registry import ACTOR_NAMES, TEAM_ACTOR_NAMES


INDEX_FILE = "evidence/index.json"
EVIDENCE_DIR = "evidence/images"


def capture_live_play_receipts(root: Path, *, observed_at: datetime | None = None) -> dict[str, Any]:
    """Store one local scene image per explicitly-playing observed match.

    This intentionally has no Preview I/O.  It is safe to call from every
    engine tick: an occurrence is captured at most once.
    """
    storage = Storage(root)
    instant = observed_at or datetime.now(timezone.utc)
    matches = _matches(storage)
    captured = []
    with storage.writer_lock():
        index = storage.load_json(storage.state / INDEX_FILE, default={"schemaVersion": 1, "items": []})
        items = index.get("items") if isinstance(index, dict) else None
        if not isinstance(items, list):
            items = []
        known = {row.get("occurrenceKey") for row in items if isinstance(row, dict) and row.get("kind") == "live_play_scene"}
        for match in matches:
            occurrence = match.get("occurrenceKey")
            if not isinstance(occurrence, str) or occurrence in known or not _is_playing(match):
                continue
            participants = _participants(match)
            if len(participants) < 2:
                continue
            filename = _filename(occurrence)
            _write_scene(storage.state / EVIDENCE_DIR / filename, match, participants, instant.isoformat())
            item = {
                "kind": "live_play_scene",
                "occurrenceKey": occurrence,
                "label": str(match.get("label") or "Club game"),
                "participants": participants,
                "capturedAt": instant.isoformat(),
                "image": filename,
                "boundary": "local scene image; explicit live-play state only; not product attendance or a result",
            }
            items.append(item)
            for actor in _participant_ids(match):
                storage.append_jsonl(storage.actor_journal_path(actor), {
                    "id": f"evidence:{_safe_key(occurrence)}:{actor}",
                    "occurrenceKey": occurrence,
                    "actorId": actor,
                    "step": "local_live_play_scene_captured",
                    "detail": "A local evidence scene was captured after explicit live-play state. It does not prove attendance, a score, or a product result.",
                    "evidenceRef": f"local-evidence:{filename}",
                    "observedAt": instant.isoformat(),
                })
            captured.append(item)
        index = {"schemaVersion": 1, "items": items[-80:]}
        storage.write_json(storage.state / INDEX_FILE, index)
    return {"action": "captured" if captured else "pass", "items": captured, "remoteWrites": 0}


def evidence_index(root: Path) -> dict[str, Any]:
    storage = Storage(root)
    value = storage.load_json(storage.state / INDEX_FILE, default={})
    items = value.get("items") if isinstance(value, dict) and isinstance(value.get("items"), list) else []
    safe = []
    for item in items:
        if not isinstance(item, dict):
            continue
        image = item.get("image")
        if not isinstance(image, str) or Path(image).name != image or not image.endswith(".svg"):
            continue
        safe.append({key: item.get(key) for key in ("kind", "occurrenceKey", "label", "participants", "capturedAt", "image", "boundary")})
    return {"items": safe[-20:]}


def _matches(storage: Storage) -> list[dict[str, Any]]:
    rows = []
    for relative in ("preview-matches.json", "observed-bookings.json"):
        value = storage.load_json(storage.state / relative, default={})
        candidates = value.get("matches") if isinstance(value, dict) else None
        if isinstance(candidates, dict):
            rows.extend(row for row in candidates.values() if isinstance(row, dict))
        bookings = value.get("bookings") if isinstance(value, dict) else None
        if isinstance(bookings, list):
            rows.extend(row for row in bookings if isinstance(row, dict))
    return rows


def _is_playing(match: dict[str, Any]) -> bool:
    lifecycle = match.get("lifecycle") if isinstance(match.get("lifecycle"), dict) else {}
    steps = lifecycle.get("steps") if isinstance(lifecycle.get("steps"), dict) else {}
    return str(steps.get("play") or "").upper() == "PLAYING"


def _participants(match: dict[str, Any]) -> list[str]:
    rows = match.get("participants") if isinstance(match.get("participants"), list) else []
    values = [
        row.get("label") or _label_for_actor(row.get("actorId"))
        for row in rows if isinstance(row, dict)
    ]
    return [value for value in values if isinstance(value, str)][:4]


def _participant_ids(match: dict[str, Any]) -> list[str]:
    rows = match.get("participants") if isinstance(match.get("participants"), list) else []
    return [row["actorId"] for row in rows if isinstance(row, dict) and isinstance(row.get("actorId"), str)]


def _safe_key(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value).strip("-")[:72]


def _filename(occurrence: str) -> str:
    return f"play-{_safe_key(occurrence)}.svg"


def _label_for_actor(actor_id: Any) -> str:
    """Never fall back to an internal actor id in a visual receipt."""
    names = {**ACTOR_NAMES, **TEAM_ACTOR_NAMES}.get(actor_id)
    return " ".join(names) if names else "Named Sim"


def _write_scene(path: Path, match: dict[str, Any], participants: list[str], captured_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    venue = match.get("venue") if isinstance(match.get("venue"), dict) else {}
    area = escape(str(venue.get("areaName") or "Observed area"))
    pod = escape(str(venue.get("podName") or "Observed pod"))
    names = " · ".join(escape(name) for name in participants)
    label = escape(str(match.get("label") or "Club game"))
    text = f'''<svg xmlns="http://www.w3.org/2000/svg" width="920" height="360" viewBox="0 0 920 360" role="img" aria-label="Local Club Lab play evidence scene">
<rect width="920" height="360" fill="#11122f"/><rect x="24" y="24" width="872" height="312" fill="#1d1e4a" stroke="#78e3d4" stroke-width="3"/>
<text x="48" y="66" fill="#78e3d4" font-family="monospace" font-size="20">CLUB LAB · LOCAL PLAY EVIDENCE</text>
<text x="48" y="100" fill="#bdb9dc" font-family="monospace" font-size="14">{label}</text>
<rect x="48" y="128" width="824" height="142" fill="#244b54" stroke="#5b58a8" stroke-width="2"/>
<text x="72" y="162" fill="#fff7d1" font-family="monospace" font-size="18">{area} / {pod}</text>
<text x="72" y="208" fill="#fff7d1" font-family="monospace" font-size="19">{names}</text>
<text x="48" y="304" fill="#bdb9dc" font-family="monospace" font-size="12">captured {escape(captured_at)} · explicit local PLAYING state · not attendance, score, or result proof</text>
</svg>'''
    path.write_text(text, encoding="utf-8")
