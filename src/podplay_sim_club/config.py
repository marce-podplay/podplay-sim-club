"""Local runtime configuration with no implicit secret-file discovery."""

from dataclasses import dataclass
from enum import Enum
import os
from typing import Mapping, Optional, Sequence, Tuple


class ConfigurationError(ValueError):
    pass


class RunMode(str, Enum):
    FAKE = "fake"
    PREVIEW_READONLY = "preview-readonly"
    PREVIEW_WRITE = "preview-write"

    @classmethod
    def parse(cls, value: str) -> "RunMode":
        try:
            return cls(value.strip().lower())
        except ValueError as exc:
            choices = ", ".join(mode.value for mode in cls)
            raise ConfigurationError(
                f"unsupported mode {value!r}; expected one of: {choices}"
            ) from exc


@dataclass(frozen=True)
class ClubConfig:
    mode: RunMode = RunMode.FAKE
    preview_origin: Optional[str] = None
    allowed_preview_origins: Tuple[str, ...] = ()
    preview_write_confirmation: Optional[str] = None
    admin_email: Optional[str] = None

    @classmethod
    def from_environment(
        cls,
        environment: Optional[Mapping[str, str]] = None,
        mode_override: Optional[str] = None,
        preview_origin_override: Optional[str] = None,
        allowed_origins_override: Optional[Sequence[str]] = None,
        write_confirmation_override: Optional[str] = None,
    ) -> "ClubConfig":
        source = os.environ if environment is None else environment
        mode = RunMode.parse(mode_override or source.get("PODPLAY_SIM_MODE", "fake"))
        preview_origin = preview_origin_override or source.get(
            "PODPLAY_SIM_PREVIEW_ORIGIN"
        )
        if allowed_origins_override is None:
            allowed_origins = tuple(
                item.strip()
                for item in source.get(
                    "PODPLAY_SIM_ALLOWED_PREVIEW_ORIGINS", ""
                ).split(",")
                if item.strip()
            )
        else:
            allowed_origins = tuple(
                item.strip() for item in allowed_origins_override if item.strip()
            )
        return cls(
            mode=mode,
            preview_origin=preview_origin,
            allowed_preview_origins=allowed_origins,
            preview_write_confirmation=write_confirmation_override
            or source.get("PODPLAY_SIM_CONFIRM_PREVIEW_WRITE"),
            admin_email=source.get("PODPLAY_SIM_ADMIN_EMAIL"),
        )
