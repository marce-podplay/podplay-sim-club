"""Durable, secret-safe local file storage."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Iterator, List, Optional


JsonObject = Dict[str, Any]


class Storage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.state = self.root / "state"
        self.state.mkdir(parents=True, exist_ok=True)

    @property
    def world_path(self) -> Path:
        return self.state / "world.json"

    @property
    def fake_preview_path(self) -> Path:
        return self.state / "fake-preview.json"

    @property
    def lock_path(self) -> Path:
        return self.state / "writer.lock"

    def load_json(self, path: Path, default: Optional[Any] = None) -> Any:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def write_json(self, path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(path))
        finally:
            if temporary.exists():
                temporary.unlink()

    def append_jsonl(self, path: Path, value: JsonObject) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(value, sort_keys=True, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def read_jsonl(self, path: Path) -> List[JsonObject]:
        if not path.exists():
            return []
        values = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    values.append(json.loads(line))
        return values

    @contextmanager
    def writer_lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def archive_world(self, world: JsonObject) -> None:
        season = world.get("season", {}).get("id", "unknown-season")
        path = self.state / "seasons" / season / "world-final.json"
        self.write_json(path, world)

    def actor_journal_path(self, actor_id: str) -> Path:
        return self.state / "actors" / actor_id / "journal.jsonl"

    def channel_path(self, channel: str) -> Path:
        return self.state / "channels" / f"{channel}.jsonl"

    def run_path(self, occurrence_key: str, name: str) -> Path:
        safe_key = occurrence_key.replace("/", "_").replace(":", "-")
        return self.state / "runs" / safe_key / name
