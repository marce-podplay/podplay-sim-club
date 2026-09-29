"""Named players. Retired color-captain ids remain readable aliases."""

import json
import os
from pathlib import Path


LEGACY_ACTOR_IDS = {
    "red-captain": "andy-bogard",
    "blue-captain": "terry-bogard",
}

# Andy books when his desire to play crosses the threshold. Terry is the
# teammate he invites. Sofia's events use the players named on the campaign.
BOOKER = "andy-bogard"
TEAMMATE = "terry-bogard"


def canonical_actor_id(actor_id: str) -> str:
    return LEGACY_ACTOR_IDS.get(actor_id, actor_id)


def rewrite_legacy_ids(value: str) -> str:
    for old, new in LEGACY_ACTOR_IDS.items():
        value = value.replace(old, new)
    return value


def actor_auth_cache(root: Path, actor_id: str) -> Path:
    """Prefer the named-player cache, and still open a retired filename."""
    actor_id = canonical_actor_id(actor_id)
    directory = root / "secrets" / "actor-auth"
    current = directory / f"{actor_id}.json"
    if current.is_file():
        return current
    legacy = next((old for old, new in LEGACY_ACTOR_IDS.items() if new == actor_id), None)
    if legacy is None:
        return current
    previous = directory / f"{legacy}.json"
    return previous if previous.is_file() else current


def migrate_legacy_actor_files(root: Path) -> None:
    """Rename durable actor ids without touching email addresses."""
    state = root / "state"
    if state.is_dir():
        for path in state.rglob("*"):
            if not path.is_file() or path.suffix not in {".json", ".jsonl", ".md"}:
                continue
            text = path.read_text(encoding="utf-8")
            if (
                "red-captain" not in text
                and "blue-captain" not in text
                and "red-blue-001" not in text
            ):
                continue
            if "@" in text and ("red-captain@" in text or "+red-captain" in text or "+blue-captain" in text):
                continue
            path.write_text(
                rewrite_legacy_ids(text).replace("red-blue-001", "andy-terry-001"),
                encoding="utf-8",
            )
        actors = state / "actors"
        for old, new in LEGACY_ACTOR_IDS.items():
            source = actors / old
            destination = actors / new
            if source.is_dir() and not destination.exists():
                source.rename(destination)
        old_run = state / "runs" / "needs-match-season-001-red-blue-001"
        new_run = state / "runs" / "needs-match-season-001-andy-terry-001"
        if old_run.is_dir() and not new_run.exists():
            old_run.rename(new_run)
    registry = root / "secrets" / "preview-actors.json"
    if registry.is_file() and not (registry.stat().st_mode & 0o077):
        data = json.loads(registry.read_text(encoding="utf-8"))
        actors = data.get("actors") if isinstance(data, dict) else None
        if isinstance(actors, dict):
            changed = False
            for old, new in LEGACY_ACTOR_IDS.items():
                if old in actors and new not in actors:
                    actors[new] = actors.pop(old)
                    changed = True
            if changed:
                temporary = registry.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                os.chmod(temporary, 0o600)
                os.replace(temporary, registry)
                os.chmod(registry, 0o600)
    auth = root / "secrets" / "actor-auth"
    if auth.is_dir():
        for old, new in LEGACY_ACTOR_IDS.items():
            source = auth / f"{old}.json"
            destination = auth / f"{new}.json"
            if source.is_file() and not destination.exists():
                source.rename(destination)
