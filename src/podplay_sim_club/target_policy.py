"""Fail-closed policy for PodPlay PR-preview origins."""

from dataclasses import dataclass
import re
from urllib.parse import SplitResult, urlsplit

from .config import ClubConfig, RunMode


PR_PREVIEW_HOST = re.compile(
    r"^podify-pr(?:-(?P<modern>[0-9]+)-staging|(?P<legacy>[0-9]+)staging)"
    r"-main-service-[a-z0-9-]+[.]a[.]run[.]app$"
)


class TargetPolicyError(ValueError):
    pass


def _parts(value: str) -> SplitResult:
    candidate = value.strip()
    if not candidate:
        raise TargetPolicyError("preview origin is required")
    parsed = urlsplit(candidate)
    if parsed.scheme != "https":
        raise TargetPolicyError("preview origin must use https")
    if not parsed.hostname:
        raise TargetPolicyError("preview origin must include a hostname")
    if parsed.username or parsed.password:
        raise TargetPolicyError("preview origin cannot contain credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise TargetPolicyError("preview origin contains an invalid port") from exc
    if port not in (None, 443):
        raise TargetPolicyError("preview origin cannot use a non-HTTPS port")
    return parsed


def origin_of(value: str) -> str:
    parsed = _parts(value)
    return f"https://{parsed.hostname.lower()}"


def canonical_origin(value: str) -> str:
    parsed = _parts(value)
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise TargetPolicyError("preview origin must not contain a path, query, or fragment")
    return origin_of(value)


@dataclass(frozen=True)
class TargetPolicy:
    target_origin: str
    pull_request_number: int
    writes_allowed: bool = False

    @classmethod
    def from_config(cls, config: ClubConfig) -> "TargetPolicy":
        if config.mode is RunMode.FAKE:
            raise TargetPolicyError("fake mode does not have a remote target")
        target = canonical_origin(config.preview_origin or "")
        hostname = urlsplit(target).hostname or ""
        match = PR_PREVIEW_HOST.fullmatch(hostname)
        if match is None:
            raise TargetPolicyError(
                "target is not a recognized PodPlay PR-preview Cloud Run origin"
            )
        allowed = {
            canonical_origin(origin) for origin in config.allowed_preview_origins
        }
        if target not in allowed:
            raise TargetPolicyError("target is not present in the exact-origin allowlist")
        if config.mode is RunMode.PREVIEW_WRITE:
            confirmation = canonical_origin(config.preview_write_confirmation or "")
            if confirmation != target:
                raise TargetPolicyError(
                    "preview-write confirmation must equal the exact target origin"
                )
        pr_number = int(match.group("modern") or match.group("legacy"))
        return cls(
            target_origin=target,
            pull_request_number=pr_number,
            writes_allowed=config.mode is RunMode.PREVIEW_WRITE,
        )

    def assert_url(self, candidate: str) -> None:
        if origin_of(candidate) != self.target_origin:
            raise TargetPolicyError("request or redirect crossed the approved origin")
