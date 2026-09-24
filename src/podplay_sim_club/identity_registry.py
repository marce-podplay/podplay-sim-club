"""Private, durable registry for simulator Firebase identities."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import string
import tempfile
from typing import Any, Dict, Iterable


class IdentityRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ActorIdentity:
    actor_id: str
    email: str
    first_name: str
    last_name: str
    password: str = field(repr=False)
    firebase_uid: str = ""
    podplay_user_id: str = ""


ACTOR_NAMES = {
    "sofia": ("Sofia", "Preview Club"),
    "red-captain": ("Red", "Captain PP-7444"),
    "blue-captain": ("Blue", "Captain PP-7444"),
}


class IdentityRegistry:
    def __init__(self, path: Path):
        self.path = path

    def ensure(self, actor_ids: Iterable[str] = ACTOR_NAMES.keys()) -> Dict[str, ActorIdentity]:
        data = self._read()
        actors = data.setdefault("actors", {})
        changed = False
        for actor_id in actor_ids:
            if actor_id not in ACTOR_NAMES:
                raise IdentityRegistryError(f"unsupported actor identity: {actor_id}")
            if actor_id not in actors:
                first_name, last_name = ACTOR_NAMES[actor_id]
                suffix = secrets.token_hex(3)
                actors[actor_id] = {
                    "email": f"marcelo+sim-pp7444-{actor_id}-{suffix}@podplay.app",
                    "firstName": first_name,
                    "lastName": last_name,
                    "password": _password(),
                    "firebaseUid": "",
                    "podplayUserId": "",
                }
                changed = True
        if changed or not self.path.is_file():
            self._write(data)
        return self._identities(data)

    def record_verified(
        self,
        actor_id: str,
        firebase_uid: str,
        podplay_user_id: str,
        preview_origin: str,
        tenant_id: str,
    ) -> None:
        data = self._read()
        actor = data.get("actors", {}).get(actor_id)
        if not isinstance(actor, dict):
            raise IdentityRegistryError(f"actor is missing from registry: {actor_id}")
        actor.update(
            {
                "firebaseUid": firebase_uid,
                "podplayUserId": podplay_user_id,
                "lastVerifiedAt": datetime.now(timezone.utc).isoformat(),
                "lastPreviewOrigin": preview_origin,
                "lastTenantId": tenant_id,
            }
        )
        self._write(data)

    def _read(self) -> Dict[str, Any]:
        if not self.path.is_file():
            return {"schemaVersion": 1, "ticket": "PP-7444", "actors": {}}
        if self.path.stat().st_mode & 0o077:
            raise IdentityRegistryError(
                "secrets/preview-actors.json must not be readable by group or others"
            )
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IdentityRegistryError("preview actor registry is not valid JSON") from exc
        if not isinstance(value, dict) or value.get("schemaVersion") != 1:
            raise IdentityRegistryError("unsupported preview actor registry")
        return value

    def _identities(self, data: Dict[str, Any]) -> Dict[str, ActorIdentity]:
        result = {}
        for actor_id, actor in data.get("actors", {}).items():
            if not isinstance(actor, dict):
                raise IdentityRegistryError("preview actor registry has an invalid actor")
            required = ("email", "firstName", "lastName", "password")
            if any(not isinstance(actor.get(key), str) or not actor[key] for key in required):
                raise IdentityRegistryError(f"preview actor registry is incomplete: {actor_id}")
            result[actor_id] = ActorIdentity(
                actor_id=actor_id,
                email=actor["email"],
                first_name=actor["firstName"],
                last_name=actor["lastName"],
                password=actor["password"],
                firebase_uid=str(actor.get("firebaseUid") or ""),
                podplay_user_id=str(actor.get("podplayUserId") or ""),
            )
        return result

    def _write(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=str(self.path.parent)
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
            self.path.chmod(0o600)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise


def _password() -> str:
    alphabet = string.ascii_letters + string.digits + "-_"
    return "S7" + "".join(secrets.choice(alphabet) for _ in range(30))
