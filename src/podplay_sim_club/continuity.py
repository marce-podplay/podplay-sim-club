"""Explicit, secret-free portability for the fictional Preview Club community.

Bundles carry only the roster fingerprint, a deliberately small fictional
progression projection, and local social facts.  They do not carry any Preview
database state and cannot recreate a booking, account, role, or credential.
"""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Dict

from .facts import append_fact_locked, reject_private_data, validate_fact
from .projections import rebuild_projections_locked
from .storage import Storage


BUNDLE_VERSION = 1
PORTABLE_SOURCE_KINDS = {
    "actor_expectation",
    "captain_report",
    "club_message",
    "intent_policy",
    "protocol_reducer",
}


class ContinuityError(ValueError):
    pass


def export_bundle(root: Path, output: Path) -> Dict[str, Any]:
    """Create a new portable community bundle; refuse to overwrite a file."""
    destination = output.resolve()
    if destination.exists():
        raise ContinuityError("continuity export destination already exists")
    roster, fingerprint, actor_ids = _roster(root)
    storage = Storage(root)
    facts = storage.read_jsonl(storage.state / "journal" / "facts.jsonl")
    portable = []
    for fact in facts:
        validate_fact(fact)
        if fact.get("source", {}).get("kind") not in PORTABLE_SOURCE_KINDS:
            continue
        reject_private_data(fact, "continuity.fact")
        portable.append(deepcopy(fact))
    progression = _progression(storage, actor_ids)
    bundle = {
        "schemaVersion": BUNDLE_VERSION,
        "kind": "preview-club-community",
        "roster": {"fingerprint": fingerprint, "actorIds": actor_ids},
        "progression": progression,
        "facts": portable,
        "excluded": {
            "previewScopedFacts": len(facts) - len(portable),
            "reason": "Preview observations, product references, bookings, identities, roles, and credentials are season-scoped and never portable",
        },
    }
    reject_private_data(bundle, "continuity.bundle")
    storage.write_json(destination, bundle)
    return _summary(bundle, destination)


def inspect_bundle(path: Path) -> Dict[str, Any]:
    bundle = _read_bundle(path)
    return _summary(bundle, path.resolve())


def import_bundle(root: Path, source: Path, *, replace_progression: bool = False) -> Dict[str, Any]:
    """Merge compatible historical facts and optionally replace safe motives.

    Import does not make Preview facts current: they are excluded from the
    format entirely. Existing facts retain precedence by deterministic fact ID.
    """
    bundle = _read_bundle(source)
    _, expected_fingerprint, actor_ids = _roster(root)
    if bundle["roster"]["fingerprint"] != expected_fingerprint:
        raise ContinuityError("bundle roster fingerprint does not match this repository")
    if bundle["roster"]["actorIds"] != actor_ids:
        raise ContinuityError("bundle actor manifest does not match this repository")
    storage = Storage(root)
    with storage.writer_lock():
        appended = sum(1 for fact in bundle["facts"] if append_fact_locked(storage, fact))
        progression_applied = False
        if replace_progression:
            state = storage.load_json(storage.state / "social-engine.json", default={})
            if not isinstance(state, dict):
                state = {}
            state["motives"] = deepcopy(bundle["progression"]["motives"])
            storage.write_json(storage.state / "social-engine.json", state)
            progression_applied = True
        rebuild_projections_locked(storage)
    return {
        "kind": bundle["kind"], "factsAppended": appended,
        "progressionApplied": progression_applied,
        "previewStateRestored": False,
    }


def _read_bundle(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContinuityError("continuity bundle is missing or invalid JSON") from exc
    if not isinstance(value, dict) or value.get("schemaVersion") != BUNDLE_VERSION or value.get("kind") != "preview-club-community":
        raise ContinuityError("unsupported continuity bundle")
    roster = value.get("roster")
    progression = value.get("progression")
    facts = value.get("facts")
    if (
        not isinstance(roster, dict)
        or not isinstance(roster.get("fingerprint"), str)
        or not isinstance(roster.get("actorIds"), list)
        or not all(isinstance(actor_id, str) for actor_id in roster["actorIds"])
        or not isinstance(progression, dict)
        or not isinstance(facts, list)
    ):
        raise ContinuityError("continuity bundle shape is invalid")
    _validate_progression(progression, roster["actorIds"])
    for fact in facts:
        validate_fact(fact)
        if fact.get("source", {}).get("kind") not in PORTABLE_SOURCE_KINDS:
            raise ContinuityError("bundle contains a non-portable product or unknown fact")
    reject_private_data(value, "continuity.bundle")
    return value


def _roster(root: Path) -> tuple[Dict[str, Any], str, list[str]]:
    try:
        roster = json.loads((root / "characters" / "roster.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContinuityError("roster is missing or invalid") from exc
    rows = roster.get("characters") if isinstance(roster, dict) else None
    actor_ids = sorted(
        row["actorId"] for row in rows if isinstance(row, dict) and isinstance(row.get("actorId"), str)
    ) if isinstance(rows, list) else []
    if not actor_ids or len(actor_ids) != len(set(actor_ids)):
        raise ContinuityError("roster actor manifest is invalid")
    canonical = json.dumps(roster, sort_keys=True, separators=(",", ":"))
    return roster, sha256(canonical.encode("utf-8")).hexdigest(), actor_ids


def _progression(storage: Storage, actor_ids: list[str]) -> Dict[str, Any]:
    state = storage.load_json(storage.state / "social-engine.json", default={})
    source = state.get("motives") if isinstance(state, dict) else {}
    motives = {
        actor_id: {key: value for key, value in row.items() if key in {"desireToPlay", "curiosity", "frustration", "competition", "competence", "autonomy", "relatedness"}}
        for actor_id, row in source.items() if actor_id in actor_ids and isinstance(row, dict)
    } if isinstance(source, dict) else {}
    progression = {"motives": motives}
    _validate_progression(progression, actor_ids)
    return progression


def _validate_progression(progression: Dict[str, Any], actor_ids: list[str]) -> None:
    motives = progression.get("motives")
    if not isinstance(motives, dict):
        raise ContinuityError("continuity progression is invalid")
    permitted = {"desireToPlay", "curiosity", "frustration", "competition", "competence", "autonomy", "relatedness"}
    for actor_id, row in motives.items():
        if actor_id not in actor_ids or not isinstance(row, dict) or not set(row).issubset(permitted):
            raise ContinuityError("continuity motives are invalid")
        if not all(isinstance(value, (int, float)) and 0 <= value <= 100 for value in row.values()):
            raise ContinuityError("continuity motive values must be between 0 and 100")


def _summary(bundle: Dict[str, Any], path: Path) -> Dict[str, Any]:
    return {
        "path": str(path), "kind": bundle["kind"],
        "actorCount": len(bundle["roster"]["actorIds"]),
        "factCount": len(bundle["facts"]),
        "progressionActors": len(bundle["progression"]["motives"]),
        "previewStateIncluded": False,
    }
