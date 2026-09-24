"""Clock helpers shared by the fake world and future preview adapter."""

from datetime import datetime, timezone
from typing import Optional


UTC = timezone.utc


def parse_instant(value: Optional[str]) -> datetime:
    if value is None:
        return datetime.now(UTC)
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def hour_key(value: datetime) -> str:
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H"
    )
