"""Small Firebase password/refresh client with a private local token cache."""

import base64
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ID_TOKEN_SKEW_SECONDS = 120


class FirebaseAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthSession:
    email: str
    source: str
    id_token: str = field(repr=False)


JsonRequest = Callable[[str, Dict[str, Any]], Dict[str, Any]]


def _jwt_payload(token: str) -> Dict[str, Any]:
    try:
        part = token.split(".")[1]
        padded = part + "=" * (-len(part) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        value = json.loads(decoded.decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except (IndexError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _request_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            body = response.read(1024 * 1024)
    except HTTPError as exc:
        try:
            error = json.loads(exc.read(64 * 1024).decode("utf-8"))
            code = str(error.get("error", {}).get("message", "HTTP_ERROR"))
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            code = "HTTP_ERROR"
        safe_code = "".join(character for character in code if character.isupper() or character in "_-")
        raise FirebaseAuthError(f"Firebase authentication failed ({safe_code or exc.code})") from exc
    except URLError as exc:
        raise FirebaseAuthError("Firebase authentication network request failed") from exc
    try:
        result = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FirebaseAuthError("Firebase returned an invalid response") from exc
    if not isinstance(result, dict):
        raise FirebaseAuthError("Firebase returned an invalid response")
    return result


class FirebaseAuthenticator:
    def __init__(
        self,
        cache_path: Path,
        request_json: JsonRequest = _request_json,
        now: Callable[[], float] = time.time,
    ):
        self.cache_path = cache_path
        self.request_json = request_json
        self.now = now

    def authenticate(self, email: str, password: str, api_key: str) -> AuthSession:
        cache = self._read_cache()
        fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        if (
            cache.get("email") == email
            and cache.get("firebaseKeyFingerprint") == fingerprint
            and self._is_fresh(str(cache.get("idToken", "")), cache.get("expiresAt"))
        ):
            return AuthSession(email=email, source="cache", id_token=cache["idToken"])

        refresh_token = str(cache.get("refreshToken", "")) if cache.get("email") == email else ""
        if refresh_token and cache.get("firebaseKeyFingerprint") == fingerprint:
            try:
                refreshed = self.request_json(
                    f"https://securetoken.googleapis.com/v1/token?key={api_key}",
                    {"grant_type": "refresh_token", "refresh_token": refresh_token},
                )
                id_token = str(refreshed["id_token"])
                next_refresh = str(refreshed.get("refresh_token") or refresh_token)
                expires_at = self._expires_at(id_token, refreshed.get("expires_in"))
                self._write_cache(email, id_token, next_refresh, expires_at, fingerprint)
                return AuthSession(email=email, source="refresh", id_token=id_token)
            except (FirebaseAuthError, KeyError, TypeError, ValueError):
                pass

        signed_in = self.request_json(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}",
            {"email": email, "password": password, "returnSecureToken": True},
        )
        try:
            id_token = str(signed_in["idToken"])
        except (KeyError, TypeError) as exc:
            raise FirebaseAuthError("Firebase sign-in response did not contain an ID token") from exc
        signed_in_email = str(signed_in.get("email") or email)
        refresh_token = str(signed_in.get("refreshToken") or "")
        expires_at = self._expires_at(id_token, signed_in.get("expiresIn"))
        self._write_cache(signed_in_email, id_token, refresh_token, expires_at, fingerprint)
        return AuthSession(email=signed_in_email, source="password", id_token=id_token)

    def _expires_at(self, token: str, fallback_seconds: Optional[Any]) -> int:
        expiration = _jwt_payload(token).get("exp")
        if isinstance(expiration, (int, float)):
            return int(expiration)
        try:
            seconds = int(fallback_seconds)
        except (TypeError, ValueError):
            seconds = 3600
        return int(self.now()) + seconds

    def _is_fresh(self, token: str, cached_expiration: Any) -> bool:
        if not token:
            return False
        expiration = _jwt_payload(token).get("exp", cached_expiration)
        try:
            return float(expiration) - ID_TOKEN_SKEW_SECONDS > self.now()
        except (TypeError, ValueError):
            return False

    def _read_cache(self) -> Dict[str, Any]:
        if not self.cache_path.is_file():
            return {}
        try:
            value = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_cache(
        self,
        email: str,
        id_token: str,
        refresh_token: str,
        expires_at: int,
        fingerprint: str,
    ) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "email": email,
            "idToken": id_token,
            "refreshToken": refresh_token,
            "expiresAt": expires_at,
            "firebaseKeyFingerprint": fingerprint,
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.cache_path.name}.", dir=str(self.cache_path.parent)
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.cache_path)
            self.cache_path.chmod(0o600)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
