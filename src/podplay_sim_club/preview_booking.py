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
    ) -> Dict[str, Any]:
        if self._used:
            raise PreviewWriteError("booking write budget exhausted")
        if not session_id or not session_table_id:
            raise PreviewWriteError("booking order requires session and table IDs")
        if not isinstance(virtual_credits, (int, float)) or not 0 <= virtual_credits <= 25:
            raise PreviewWriteError("booking order credits must be between 0 and 25")
        items = [{"session": {"id": session_id}, "sessionTable": {"id": session_table_id}}]
        for item in additional_items or []:
            if not isinstance(item, dict):
                raise PreviewWriteError("booking order items must be objects")
            extra_session = item.get("sessionId")
            extra_table = item.get("tableId")
            if not isinstance(extra_session, str) or not isinstance(extra_table, str):
                raise PreviewWriteError("booking order items require session and table IDs")
            items.append({"session": {"id": extra_session}, "sessionTable": {"id": extra_table}})
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
        raise PreviewReadError("booking event owner does not match Red Captain")
    return {
        "eventId": event_id,
        "type": event.get("type"),
        "subtype": event.get("subtype"),
        "status": event.get("status"),
        "startTime": event.get("startTime"),
        "endTime": event.get("endTime"),
        "isCanceled": event.get("isCanceled"),
        "invitationCount": _collection_total(event.get("invitations")),
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
