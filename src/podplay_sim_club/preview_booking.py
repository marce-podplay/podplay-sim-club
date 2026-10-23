"""Single-write booking adapter and read-back reconciliation for Preview Club."""

from datetime import datetime, timedelta
import hashlib
import json
from typing import Any, Callable, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .preview_readonly import (
    MAX_RESPONSE_BYTES,
    PreviewReadError,
    PreviewReadonlyClient,
    collection_items,
)
from .preview_write import PreviewWriteError
from .target_policy import TargetPolicy


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        raise PreviewWriteError("booking order refused an HTTP redirect")


Transport = Callable[[Request, float], Any]


class PreviewBookingWriter:
    def __init__(
        self,
        policy: TargetPolicy,
        actor_token: str,
        transport: Optional[Transport] = None,
    ):
        if not policy.writes_allowed:
            raise PreviewWriteError("preview-write policy confirmation is required")
        self.policy = policy
        self._actor_token = actor_token
        self._used = False
        if transport is None:
            opener = build_opener(_RejectRedirects())
            self._transport = lambda request, timeout: opener.open(request, timeout=timeout)
        else:
            self._transport = transport

    def order(
        self,
        session_id: str,
        session_table_id: str,
        virtual_credits: float,
        additional_items: Optional[list] = None,
        session_guests_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self._used:
            raise PreviewWriteError("booking write budget exhausted")
        if not session_id or not session_table_id:
            raise PreviewWriteError("booking order requires session and table IDs")
        if not isinstance(virtual_credits, (int, float)) or not 0 <= virtual_credits <= 25:
            raise PreviewWriteError("booking order credits must be between 0 and 25")
        first_item = {"session": {"id": session_id}, "sessionTable": {"id": session_table_id}}
        if session_guests_id is not None:
            if not isinstance(session_guests_id, str) or not session_guests_id:
                raise PreviewWriteError("booking guest option must be a non-empty ID")
            first_item["sessionGuests"] = {"id": session_guests_id}
        items = [first_item]
        for extra in additional_items or []:
            if not isinstance(extra, dict):
                raise PreviewWriteError("booking order items must be objects")
            extra_session = extra.get("sessionId")
            extra_table = extra.get("tableId")
            if not isinstance(extra_session, str) or not isinstance(extra_table, str):
                raise PreviewWriteError("booking order items require session and table IDs")
            item = {"session": {"id": extra_session}, "sessionTable": {"id": extra_table}}
            guest_id = extra.get("sessionGuestsId")
            if guest_id is not None:
                if not isinstance(guest_id, str) or not guest_id:
                    raise PreviewWriteError("booking guest option must be a non-empty ID")
                item["sessionGuests"] = {"id": guest_id}
            items.append(item)
        self._used = True
        payload = {
            "type": "ORDER",
            "items": items,
            "chargeStrategy": "ONLY_OWNER",
            "passesStrategy": "USE_NONE",
            "virtualCredits": virtual_credits,
            "termsAgreed": True,
            "bookingMode": "USER_BOOKED",
        }
        url = self.policy.target_origin + "/apis/v2/bookings"
        self.policy.assert_url(url)
        request = Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._actor_token}",
                "User-Agent": "podplay-sim-club/0.1 manual-booking",
            },
            method="POST",
        )
        try:
            response = self._transport(request, 30)
            with response:
                self.policy.assert_url(response.geturl())
                body = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
        except PreviewWriteError:
            raise
        except HTTPError as exc:
            raise PreviewWriteError(f"booking order failed with HTTP {exc.code}") from exc
        except URLError as exc:
            raise PreviewWriteError("booking order network request failed") from exc
        if status != 201:
            raise PreviewWriteError(f"booking order returned HTTP {status}")
        if len(body) > MAX_RESPONSE_BYTES:
            raise PreviewWriteError("booking order response exceeded the size limit")
        try:
            value = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PreviewWriteError("booking order returned invalid JSON") from exc
        if (
            not isinstance(value, dict)
            or value.get("type") != "ORDER"
            or not isinstance(value.get("id"), str)
        ):
            raise PreviewWriteError("booking order response has an unexpected shape")
        return value


class UnifiedBookingWriter:
    """One guarded Unified quote → confirm transaction for an approved player.

    The quote is intentionally refreshed immediately before confirmation.  Its
    opaque identifier stays in-process; durable community evidence records
    only the sanitized outcome.
    """

    def __init__(self, policy: TargetPolicy, actor_token: str, transport: Optional[Transport] = None):
        if not policy.writes_allowed:
            raise PreviewWriteError("preview-write policy confirmation is required")
        self.policy = policy
        self._actor_token = actor_token
        self._used = False
        if transport is None:
            opener = build_opener(_RejectRedirects())
            self._transport = lambda request, timeout: opener.open(request, timeout=timeout)
        else:
            self._transport = transport

    def order(self, pod_id: str, starts_at: str, ends_at: str, group_size: int, payment_method_id: Optional[str]) -> Dict[str, Any]:
        if self._used:
            raise PreviewWriteError("booking write budget exhausted")
        if not all(isinstance(value, str) and value for value in (pod_id, starts_at, ends_at)):
            raise PreviewWriteError("unified booking order slot is invalid")
        if not isinstance(group_size, int) or group_size < 1:
            raise PreviewWriteError("unified booking order group size is invalid")
        quote = self._request(
            "/apis/v2/bookings/quotes",
            {"slot": {"pod": {"id": pod_id}, "startsAt": starts_at, "endsAt": ends_at}, "groupSize": group_size},
            expected_status=201,
            label="unified booking quote",
        )
        quote_id = quote.get("id")
        links = quote.get("_links")
        confirm = links.get("confirm") if isinstance(links, dict) else None
        href = confirm.get("href") if isinstance(confirm, dict) else None
        if not isinstance(quote_id, str) or not isinstance(href, str) or href != f"/bookings/quotes/{quote_id}/confirm":
            raise PreviewWriteError("unified booking quote confirm link is invalid")
        requirements = quote.get("requirements")
        if isinstance(requirements, list):
            blocking = [row for row in requirements if isinstance(row, dict) and row.get("code") != "agree_terms"]
            if blocking:
                raise PreviewWriteError("unified booking quote has unresolved account requirements")
        due = ((quote.get("pricing") or {}).get("due") or {}).get("total")
        due_amount = due.get("amount") if isinstance(due, dict) else None
        if not isinstance(due_amount, int) or due_amount < 0:
            raise PreviewWriteError("unified booking quote total is invalid")
        if due_amount > 0 and not isinstance(payment_method_id, str):
            raise PreviewWriteError("unified booking confirmation requires the player's existing payment method")
        self._used = True
        body: Dict[str, Any] = {"termsAgreed": True}
        if due_amount > 0:
            body["paymentMethod"] = {"id": payment_method_id}
        return self._request("/apis/v2" + href, body, expected_status=201, label="unified booking confirmation")

    def _request(self, path: str, body: Dict[str, Any], *, expected_status: int, label: str) -> Dict[str, Any]:
        url = self.policy.target_origin + path
        self.policy.assert_url(url)
        request = Request(
            url,
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {self._actor_token}", "User-Agent": "podplay-sim-club/0.1 unified-booking"},
            method="POST",
        )
        try:
            response = self._transport(request, 30)
            with response:
                self.policy.assert_url(response.geturl())
                value = _decode_booking_object(response.read(MAX_RESPONSE_BYTES + 1), label)
                status = response.status
        except PreviewWriteError:
            raise
        except HTTPError as exc:
            raise PreviewWriteError(f"{label} failed with HTTP {exc.code}") from exc
        except URLError as exc:
            raise PreviewWriteError(f"{label} network request failed") from exc
        if status != expected_status:
            raise PreviewWriteError(f"{label} returned HTTP {status}")
        return value


def occurrence_key(pod_id: str, start_time: str, actor_id: str) -> str:
    digest = hashlib.sha256(
        f"PP-7444|{pod_id}|{start_time}|{actor_id}".encode("utf-8")
    ).hexdigest()[:16]
    return f"pp7444-{digest}"


def find_matching_events(
    client: PreviewReadonlyClient,
    pod_id: str,
    user_id: str,
    start_time: str,
    end_time: str,
) -> list:
    start = _parse_time(start_time) - timedelta(minutes=1)
    end = _parse_time(end_time) + timedelta(minutes=1)
    query = urlencode(
        [
            ("podId", pod_id),
            ("userId", user_id),
            ("startTime", start.isoformat()),
            ("endTime", end.isoformat()),
            ("includePending", "true"),
            ("includeCanceled", "false"),
            ("excludeUnlisted", "false"),
            ("ipp", "30"),
            ("expand", "items._links.pods"),
            ("expand", "items._links.bookedBy"),
        ]
    )
    matches = []
    for event in collection_items(client.get("/apis/v2/events?" + query)):
        if not isinstance(event, dict):
            continue
        pod_ids = _reference_ids(event.get("pods"))
        booked_by = event.get("bookedBy")
        booked_by_id = booked_by.get("id") if isinstance(booked_by, dict) else None
        if (
            event.get("startTime") == start_time
            and event.get("endTime") == end_time
            and pod_id in pod_ids
            and booked_by_id == user_id
            and event.get("isCanceled") is not True
        ):
            matches.append(event)
    return matches


def read_back_event(
    client: PreviewReadonlyClient,
    event_id: str,
    plan: Dict[str, Any],
) -> Dict[str, Any]:
    query = urlencode(
        [
            ("expand", "_links.pods"),
            ("expand", "_links.tables"),
            ("expand", "_links.bookedBy"),
            ("expand", "_links.invitations"),
        ]
    )
    event = client.get(f"/apis/v2/events/{event_id}?{query}")
    if not isinstance(event, dict) or event.get("id") != event_id:
        raise PreviewReadError("booking event read-back has an unexpected shape")
    if (
        event.get("startTime") != plan["startTime"]
        or event.get("endTime") != plan["endTime"]
        or plan["podId"] not in _reference_ids(event.get("pods"))
        or event.get("isCanceled") is True
    ):
        raise PreviewReadError("booking event read-back does not match the planned occurrence")
    booked_by = event.get("bookedBy")
    if not isinstance(booked_by, dict) or booked_by.get("id") != plan["ownerUserId"]:
        raise PreviewReadError("booking event owner does not match the player who booked")
    return {
        "eventId": event_id,
        "type": event.get("type"),
        "subtype": event.get("subtype"),
        "status": event.get("status"),
        "startTime": event.get("startTime"),
        "endTime": event.get("endTime"),
        "isCanceled": event.get("isCanceled"),
        "invitationCount": _collection_total(event.get("invitations")),
        "teamSize": event.get("teamSize"),
    }


def summarize_order(value: Dict[str, Any]) -> Dict[str, Any]:
    summary = value.get("summary")
    if not isinstance(summary, dict):
        raise PreviewWriteError("booking order response is missing its summary")
    errors = summary.get("errors")
    if isinstance(errors, list) and errors:
        raise PreviewWriteError("booking order response contains validation errors")
    event_id = value.get("id")
    if value.get("status") != "CONFIRMED" or not isinstance(event_id, str):
        raise PreviewWriteError("booking order was not confirmed")
    order = value.get("order")
    return {
        "eventId": event_id,
        "orderId": order.get("id") if isinstance(order, dict) else None,
        "status": value.get("status"),
        "total": float(summary.get("total") or 0),
        "virtualCredits": float(summary.get("virtualCredits") or 0),
        "currency": summary.get("currency"),
    }


def summarize_unified_order(value: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a confirmed Unified Booking to safe lifecycle evidence."""
    event = value.get("event")
    event_id = event.get("id") if isinstance(event, dict) else None
    if value.get("status") != "CONFIRMED" or not isinstance(event_id, str):
        raise PreviewWriteError("unified booking was not confirmed")
    return {"eventId": event_id, "orderId": None, "status": "CONFIRMED", "total": None, "virtualCredits": 0.0, "currency": None}


def payment_method_id(value: Any) -> Optional[str]:
    """Extract the current player's method only for an in-process confirmation."""
    if not isinstance(value, dict):
        return None
    for key in ("paymentElement", "preferredCard", "preferredBankAccount"):
        candidate = value.get(key)
        if isinstance(candidate, dict) and isinstance(candidate.get("id"), str) and candidate["id"]:
            return candidate["id"]
    return None


def _decode_booking_object(body: bytes, label: str) -> Dict[str, Any]:
    if len(body) > MAX_RESPONSE_BYTES:
        raise PreviewWriteError(f"{label} response exceeded the size limit")
    try:
        value = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PreviewWriteError(f"{label} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise PreviewWriteError(f"{label} returned an unexpected shape")
    return value


def _reference_ids(value: Any) -> set:
    if not isinstance(value, dict):
        return set()
    items = value.get("items")
    if not isinstance(items, list):
        return set()
    return {
        item["id"]
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _collection_total(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    total = value.get("_total")
    return total if isinstance(total, int) else 0


def _parse_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PreviewReadError("planned booking time is invalid") from exc
