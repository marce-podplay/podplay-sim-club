"""Load the minimum ignored configuration needed for preview inspection."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import stat
from typing import Any, Dict

from .config import ClubConfig, RunMode


class CredentialsError(ValueError):
    pass


@dataclass(frozen=True)
class PreviewCredentials:
    preview_origin: str
    allowed_preview_origins: tuple
    admin_email: str
    admin_password: str = field(repr=False)
    firebase_api_key: str = field(repr=False)

    @property
    def config(self) -> ClubConfig:
        return ClubConfig(
            mode=RunMode.PREVIEW_READONLY,
            preview_origin=self.preview_origin,
            allowed_preview_origins=self.allowed_preview_origins,
            admin_email=self.admin_email,
        )

    @classmethod
    def load(cls, root: Path) -> "PreviewCredentials":
        path = root / "secrets" / "preview-credentials.json"
        if not path.is_file():
            raise CredentialsError(
                "missing secrets/preview-credentials.json; see README preview setup"
            )
        permissions = stat.S_IMODE(path.stat().st_mode)
        if permissions & 0o077:
            raise CredentialsError(
                "secrets/preview-credentials.json must not be readable by group or others"
            )
        try:
            payload: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialsError("preview credentials file is not valid JSON") from exc

        admin = payload.get("admin")
        if not isinstance(admin, dict):
            raise CredentialsError("preview credentials must contain an admin object")
        allowed = payload.get("allowedPreviewOrigins")
        if not isinstance(allowed, list) or not allowed or not all(
            isinstance(value, str) and value.strip() for value in allowed
        ):
            raise CredentialsError("allowedPreviewOrigins must be a non-empty string list")

        values = {
            "previewOrigin": payload.get("previewOrigin"),
            "admin.email": admin.get("email"),
            "admin.password": admin.get("password"),
            "firebaseApiKey": payload.get("firebaseApiKey"),
        }
        missing = [
            name
            for name, value in values.items()
            if not isinstance(value, str) or not value.strip()
        ]
        if missing:
            raise CredentialsError("missing preview credential fields: " + ", ".join(missing))
        if payload.get("mode") != RunMode.PREVIEW_READONLY.value:
            raise CredentialsError("credential mode must be preview-readonly")

        return cls(
            preview_origin=values["previewOrigin"].strip(),
            allowed_preview_origins=tuple(value.strip() for value in allowed),
            admin_email=values["admin.email"].strip(),
            admin_password=values["admin.password"],
            firebase_api_key=values["firebaseApiKey"].strip(),
        )
