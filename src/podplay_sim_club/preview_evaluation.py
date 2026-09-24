"""Origin-locked, non-persisting evaluation of one baseline booking."""

import json
from typing import Any, Callable, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .preview_readonly import MAX_RESPONSE_BYTES, PreviewReadError
from .target_policy import TargetPolicy


class PreviewEvaluationError(RuntimeError):
    pass


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        raise PreviewEvaluationError("booking preview refused an HTTP redirect")


Transport = Callable[[Request, float], Any]


class PreviewBookingEvaluator:
    """Allow one exact PREVIEW payload shape and reject every persistent mode."""

    def __init__(
        self,
        policy: TargetPolicy,
        id_token: str,
        transport: Optional[Transport] = None,
    ):
        if policy.writes_allowed:
            raise PreviewEvaluationError("booking evaluation requires read-only target mode")
        self.policy = policy
        self._id_token = id_token
        if transport is None:
            opener = build_opener(_RejectRedirects())
            self._transport = lambda request, timeout: opener.open(request, timeout=timeout)
        else:
            self._transport = transport

    def evaluate(
        self,
        session_id: str,
        session_table_id: str,
        virtual_credits: float = 0,
    ) -> Dict[str, Any]:
        if not session_id or not session_table_id:
            raise PreviewEvaluationError("booking preview requires session and table IDs")
        payload = {
            "type": "PREVIEW",
            "items": [
                {
                    "session": {"id": session_id},
                    "sessionTable": {"id": session_table_id},
                }
            ],
            "chargeStrategy": "ONLY_OWNER",
            "passesStrategy": "USE_NONE",
            "virtualCredits": virtual_credits,
            "termsAgreed": True,
            "bookingMode": "USER_BOOKED",
        }
        return self._post_preview(payload)

    def _post_preview(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._validate_payload(payload)
        url = self.policy.target_origin + "/apis/v2/bookings"
        self.policy.assert_url(url)
        request = Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._id_token}",
                "User-Agent": "podplay-sim-club/0.1 booking-preview",
            },
            method="POST",
        )
        try:
            response = self._transport(request, 20)
            with response:
                self.policy.assert_url(response.geturl())
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except PreviewEvaluationError:
            raise
        except HTTPError as exc:
            if exc.code == 422:
                body = exc.read(MAX_RESPONSE_BYTES + 1)
                value = _decode_object(body)
                if value.get("type") == "PREVIEW":
                    return value
                codes = _nested_error_codes(value)
                detail = f" ({','.join(codes)})" if codes else ""
                raise PreviewEvaluationError(
                    f"booking preview rejected with HTTP 422{detail}"
                ) from exc
            raise PreviewEvaluationError(
                f"booking preview failed with HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise PreviewEvaluationError("booking preview network request failed") from exc
        if len(body) > MAX_RESPONSE_BYTES:
            raise PreviewEvaluationError("booking preview response exceeded the size limit")
        return _decode_object(body)

    @staticmethod
    def _validate_payload(payload: Dict[str, Any]) -> None:
        allowed_keys = {
            "type",
            "items",
            "chargeStrategy",
            "passesStrategy",
            "virtualCredits",
            "termsAgreed",
            "bookingMode",
        }
        if set(payload) != allowed_keys or payload.get("type") != "PREVIEW":
            raise PreviewEvaluationError("only the fixed PREVIEW booking payload is allowed")
        if payload.get("chargeStrategy") != "ONLY_OWNER":
            raise PreviewEvaluationError("booking preview charge strategy must be ONLY_OWNER")
        credits = payload.get("virtualCredits")
        if payload.get("passesStrategy") != "USE_NONE":
            raise PreviewEvaluationError("booking preview cannot consume passes")
        if not isinstance(credits, (int, float)) or credits < 0 or credits > 50:
            raise PreviewEvaluationError("booking preview credits must be between 0 and 50")
        if payload.get("termsAgreed") is not True or payload.get("bookingMode") != "USER_BOOKED":
            raise PreviewEvaluationError("booking preview baseline fields changed")
        items = payload.get("items")
        if not isinstance(items, list) or len(items) != 1:
            raise PreviewEvaluationError("booking preview requires exactly one item")
        item = items[0]
        if not isinstance(item, dict) or set(item) != {"session", "sessionTable"}:
            raise PreviewEvaluationError("booking preview item shape changed")
        for name in ("session", "sessionTable"):
            reference = item.get(name)
            if (
                not isinstance(reference, dict)
                or set(reference) != {"id"}
                or not isinstance(reference.get("id"), str)
                or not reference["id"]
            ):
                raise PreviewEvaluationError(f"booking preview {name} reference is invalid")


def summarize_booking_preview(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict) or value.get("type") != "PREVIEW":
        raise PreviewReadError("booking preview response has an unexpected type")
    summary = value.get("summary")
    if not isinstance(summary, dict):
        raise PreviewReadError("booking preview response is missing its summary")
    errors = _error_codes(summary.get("errors"))
    items = value.get("items") if isinstance(value.get("items"), list) else []
    for item in items:
        if isinstance(item, dict):
            errors.extend(_error_codes(item.get("errors")))
    errors = sorted(set(errors))
    total = summary.get("total")
    if not isinstance(total, (int, float)):
        raise PreviewReadError("booking preview total has an unexpected shape")
    return {
        "type": "PREVIEW",
        "status": value.get("status"),
        "total": float(total),
        "currency": summary.get("currency"),
        "virtualCredits": float(summary.get("virtualCredits") or 0),
        "maxChargableAmount": float(summary.get("maxChargableAmount") or 0),
        "errorCodes": errors,
        "readyForOrder": not errors and value.get("status") != "INVALID",
    }


def _error_codes(value: Any) -> list:
    if not isinstance(value, list):
        return []
    result = []
    for error in value:
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            result.append(error["code"])
        elif isinstance(error, str):
            result.append(error)
    return result


def _decode_object(body: bytes) -> Dict[str, Any]:
    if len(body) > MAX_RESPONSE_BYTES:
        raise PreviewEvaluationError("booking preview response exceeded the size limit")
    try:
        value = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PreviewEvaluationError("booking preview returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise PreviewEvaluationError("booking preview returned an unexpected shape")
    return value


def _nested_error_codes(value: Any) -> list:
    result = []
    if isinstance(value, dict):
        code = value.get("code")
        if isinstance(code, str) and code.isalnum() and len(code) <= 16:
            result.append(code)
        for child in value.values():
            result.extend(_nested_error_codes(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(_nested_error_codes(child))
    return sorted(set(result))
