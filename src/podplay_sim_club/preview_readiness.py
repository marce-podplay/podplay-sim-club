"""Read-only readiness inventory for the first controlled preview match."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .firebase_auth import FirebaseAuthenticator
from .identity_registry import ActorIdentity
from .preview_readonly import PreviewReadError, PreviewReadonlyClient, collection_items
from .preview_seed import build_seed_plan
from .storage import Storage
from .target_policy import TargetPolicy


READINESS_STATE = "preview-readiness.json"
PLAYER_ACTORS = ("red-captain", "blue-captain")


def inspect_preview_readiness(
    root: Path,
    policy: TargetPolicy,
    admin_client: PreviewReadonlyClient,
    identities: Dict[str, ActorIdentity],
    firebase_api_key: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    observed_at = _utc(now or datetime.now(timezone.utc))
    seed_plan = build_seed_plan(root, admin_client, identities)
    pod_id = seed_plan["podId"]
    area_id = seed_plan["areaId"]
    pod_query = urlencode(
        [("expand", "_links.tables"), ("expand", "_links.area")]
    )
    pod = admin_client.get(f"/apis/v2/pods/{pod_id}?{pod_query}")
    if not isinstance(pod, dict) or pod.get("id") != pod_id:
        raise PreviewReadError("configured pod detail has an unexpected shape")

    actors: Dict[str, Any] = {}
    actor_clients: Dict[str, PreviewReadonlyClient] = {}
    for actor_id, identity in identities.items():
        auth = FirebaseAuthenticator(
            root / "secrets" / "actor-auth" / f"{actor_id}.json"
        ).authenticate(identity.email, identity.password, firebase_api_key)
        client = PreviewReadonlyClient(policy, auth.id_token)
        actor_clients[actor_id] = client
        profile_query = urlencode(
            [("expand", "_links.membershipEnrollment._links.membership")]
        )
        profile = client.get(f"/apis/v2/users/current?{profile_query}")
        payment = client.get("/apis/v2/users/current/payment-method")
        booking_settings = client.get(
            f"/apis/v2/users/current/settings/booking/{area_id}/pods"
        )
        actors[actor_id] = _actor_readiness(
            actor_id, identity, profile, payment, booking_settings, pod_id
        )

    red_client = actor_clients.get("red-captain")
    if red_client is None:
        raise PreviewReadError("red-captain identity is required for readiness inspection")
    candidate = find_candidate_session(
        red_client,
        pod_id,
        str(pod.get("timezone") or "UTC"),
        observed_at,
    )
    settings = seed_plan["settings"]
    waiver_required = _truthy(settings.get("feature.areaLiabilityWaiver")) or _truthy(
        settings.get("feature.liabilityWaiver")
    )
    player_profiles_ready = all(
        actors.get(actor_id, {}).get("profileVerified") is True
        for actor_id in PLAYER_ACTORS
    )
    gates = {
        "targetApproved": True,
        "locationAvailable": str(pod.get("status") or "") == "AVAILABLE",
        "playerProfilesVerified": player_profiles_ready,
        "waiverNotRequired": not waiver_required,
        "futureSlotFound": candidate is not None,
        "paymentStateKnown": all(
            isinstance(actor.get("virtualCredits"), (int, float))
            and isinstance(actor.get("hasPaymentMethod"), bool)
            for actor in actors.values()
        ),
    }
    blockers = [name for name, passed in gates.items() if not passed]
    report = {
        "schemaVersion": 1,
        "observedAt": observed_at.isoformat(),
        "pullRequest": policy.pull_request_number,
        "targetOrigin": policy.target_origin,
        "writes": 0,
        "location": {
            "areaId": area_id,
            "areaName": seed_plan["areaName"],
            "podId": pod_id,
            "podName": seed_plan["podName"],
            "podStatus": pod.get("status"),
            "timezone": pod.get("timezone"),
            "openingHours": pod.get("openingHours"),
            "availableCourtCount": _available_court_count(pod),
        },
        "waiverRequired": waiver_required,
        "actors": actors,
        "candidateSession": candidate,
        "candidateSelection": {
            "strategy": "NEAREST_FUTURE",
            "safetyLeadMinutes": 30,
            "firstDayOffset": 0,
            "lastDayOffset": 14,
        },
        "gates": gates,
        "blockers": blockers,
        "readyForBookingPreview": not blockers,
        "notes": [
            "Customer roles are sufficient for the captain booking path; no role assignment was attempted.",
            "The session-grid rate excludes possible locking fees and tax; only booking preview is checkout-authoritative.",
            "Sofia does not need an owner role for the first captain-only match.",
        ],
    }
    storage = Storage(root)
    storage.write_json(storage.state / READINESS_STATE, report)
    return report


def summarize_payment(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise PreviewReadError("payment-method response has an unexpected shape")
    payment_element = value.get("paymentElement")
    has_payment_method = bool(
        isinstance(payment_element, dict) and payment_element.get("id")
    ) or bool(value.get("preferredCard")) or bool(value.get("preferredBankAccount"))
    credits = value.get("virtualCredits", 0)
    if not isinstance(credits, (int, float)):
        raise PreviewReadError("virtual-credit balance has an unexpected shape")
    return {
        "hasPaymentMethod": has_payment_method,
        "virtualCredits": float(credits),
    }


def find_candidate_session(
    client: PreviewReadonlyClient,
    pod_id: str,
    timezone_name: str,
    now: datetime,
    first_day_offset: int = 0,
    last_day_offset: int = 14,
    safety_lead_minutes: int = 30,
    allowed_local_windows: Optional[List[str]] = None,
    required_duration_minutes: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    try:
        local_now = _utc(now).astimezone(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError as exc:
        raise PreviewReadError(f"pod timezone is not recognized: {timezone_name}") from exc
    not_before = _utc(now) + timedelta(minutes=safety_lead_minutes)
    for offset in range(first_day_offset, last_day_offset + 1):
        operating_date = (local_now.date() + timedelta(days=offset)).isoformat()
        query = urlencode({"podId": pod_id, "operatingDate": operating_date})
        sessions = collection_items(client.get(f"/apis/v2/sessions?{query}"))
        candidate = select_candidate_session(
            sessions,
            not_before=not_before,
            timezone_name=timezone_name,
            allowed_local_windows=allowed_local_windows,
            required_duration_minutes=required_duration_minutes,
        )
        if candidate is not None:
            return candidate
    return None


def select_candidate_session(
    sessions: list,
    not_before: Optional[datetime] = None,
    timezone_name: Optional[str] = None,
    allowed_local_windows: Optional[List[str]] = None,
    required_duration_minutes: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if required_duration_minutes is not None:
        _validate_half_hour_duration(required_duration_minutes)
    local_zone = None
    windows: List[Tuple[int, int]] = []
    if allowed_local_windows:
        if not timezone_name:
            raise PreviewReadError("timezone is required for local availability windows")
        try:
            local_zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise PreviewReadError(
                f"pod timezone is not recognized: {timezone_name}"
            ) from exc
        windows = [_parse_local_window(value) for value in allowed_local_windows]
    candidates = []
    for session in sessions:
        if (
            not isinstance(session, dict)
            or session.get("status") != "AVAILABLE"
            or not isinstance(session.get("tablesLeft"), int)
            or session["tablesLeft"] < 1
        ):
            continue
        start_time = session.get("startTime")
        if not isinstance(start_time, str):
            continue
        try:
            start_instant = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except ValueError:
            continue
        if not_before is not None and _utc(start_instant) < _utc(not_before):
            continue
        end_time = session.get("endTime")
        try:
            end_instant = (
                datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                if isinstance(end_time, str)
                else None
            )
        except ValueError:
            end_instant = None
        if end_instant is None or end_instant <= start_instant:
            continue
        available_tables = session.get("availableTables")
        table_items = (
            available_tables.get("items", [])
            if isinstance(available_tables, dict)
            else []
        )
        valid_tables = [
            table
            for table in table_items
            if isinstance(table, dict) and isinstance(table.get("rate"), (int, float))
        ]
        if not valid_tables:
            continue
        default_table = session.get("defaultTable")
        default_id = default_table.get("id") if isinstance(default_table, dict) else None
        selected_table = next(
            (table for table in valid_tables if table.get("id") == default_id),
            valid_tables[0],
        )
        item = {
            "sessionId": session.get("id"),
            "operatingDate": session.get("operatingDate"),
            "startTime": session.get("startTime"),
            "endTime": session.get("endTime"),
            "periodType": session.get("periodType"),
            "tablesLeft": session.get("tablesLeft"),
            "tableId": selected_table.get("id"),
            "tableType": selected_table.get("type"),
            "rate": float(selected_table["rate"]),
        }
        candidates.append((start_instant, item))
    if not candidates:
        return None
    candidates.sort(key=lambda item: _utc(item[0]))
    if required_duration_minutes is None:
        for _, candidate in candidates:
            start = _parse_session_time(candidate["startTime"])
            end = _parse_session_time(candidate["endTime"])
            if not windows or _inside_local_window(start, end, local_zone, windows):
                return _candidate_from_slots([candidate])
        return None

    # The product grid exposes half-hour sessions.  A 60- or 90-minute booking
    # therefore comprises adjacent session/table items, rather than a fictional
    # single long session.  Keep the full item list so a later guarded write can
    # reproduce precisely what the customer selected in the UI.
    for start_index in range(len(candidates)):
        slots = []
        total_minutes = 0
        previous_end = None
        for _, candidate in candidates[start_index:]:
            start = _parse_session_time(candidate["startTime"])
            end = _parse_session_time(candidate["endTime"])
            if previous_end is not None and start != previous_end:
                break
            slots.append(candidate)
            total_minutes += int((end - start).total_seconds() / 60)
            previous_end = end
            if total_minutes == required_duration_minutes:
                if not windows or _inside_local_window(
                    _parse_session_time(slots[0]["startTime"]), end, local_zone, windows
                ):
                    return _candidate_from_slots(slots)
                break
            if total_minutes > required_duration_minutes:
                break
    return None


def _candidate_from_slots(slots: List[Dict[str, Any]]) -> Dict[str, Any]:
    first = dict(slots[0])
    first["startTime"] = slots[0]["startTime"]
    first["endTime"] = slots[-1]["endTime"]
    first["items"] = [
        {"sessionId": slot["sessionId"], "tableId": slot["tableId"]}
        for slot in slots
    ]
    first["durationMinutes"] = sum(
        int((_parse_session_time(slot["endTime"]) - _parse_session_time(slot["startTime"])).total_seconds() / 60)
        for slot in slots
    )
    first["rate"] = sum(float(slot["rate"]) for slot in slots)
    return first


def _parse_session_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise PreviewReadError("session has an invalid timestamp") from exc


def _validate_half_hour_duration(value: int) -> None:
    if not isinstance(value, int) or value < 30 or value % 30:
        raise PreviewReadError("duration must be a positive 30-minute increment")


def _parse_local_window(value: str) -> Tuple[int, int]:
    try:
        start, end = value.split("-", 1)
        start_hour, start_minute = (int(part) for part in start.split(":"))
        end_hour, end_minute = (int(part) for part in end.split(":"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise PreviewReadError(f"invalid local availability window: {value!r}") from exc
    start_total = start_hour * 60 + start_minute
    end_total = end_hour * 60 + end_minute
    if not (0 <= start_total < end_total <= 24 * 60):
        raise PreviewReadError(f"invalid local availability window: {value!r}")
    return start_total, end_total


def _inside_local_window(
    start: datetime,
    end: datetime,
    local_zone: ZoneInfo,
    windows: List[Tuple[int, int]],
) -> bool:
    local_start = _utc(start).astimezone(local_zone)
    local_end = _utc(end).astimezone(local_zone)
    if local_start.date() != local_end.date():
        return False
    start_minute = local_start.hour * 60 + local_start.minute
    end_minute = local_end.hour * 60 + local_end.minute
    return any(
        window_start <= start_minute and end_minute <= window_end
        for window_start, window_end in windows
    )


def _actor_readiness(
    actor_id: str,
    identity: ActorIdentity,
    profile: Any,
    payment: Any,
    booking_settings: Any,
    pod_id: str,
) -> Dict[str, Any]:
    if not isinstance(profile, dict):
        raise PreviewReadError(f"profile response has an unexpected shape for {actor_id}")
    if profile.get("id") != identity.podplay_user_id:
        raise PreviewReadError(f"profile identity changed for {actor_id}")
    pod_settings = _pod_booking_settings(booking_settings, pod_id)
    result = {
        "profileVerified": True,
        "roles": profile.get("roles") if isinstance(profile.get("roles"), list) else [],
        "membershipType": profile.get("membershipType") or "NONE",
        "booking": pod_settings,
    }
    result.update(summarize_payment(payment))
    return result


def _pod_booking_settings(value: Any, pod_id: str) -> Dict[str, Any]:
    item = next(
        (
            candidate
            for candidate in collection_items(value)
            if isinstance(candidate, dict)
            and isinstance(candidate.get("pod"), dict)
            and candidate["pod"].get("id") == pod_id
        ),
        None,
    )
    if item is None:
        raise PreviewReadError(f"booking settings are missing for pod {pod_id}")
    quotas = item.get("quotas")
    if not isinstance(quotas, dict):
        raise PreviewReadError("pod booking quotas have an unexpected shape")
    return {
        "strategy": item.get("strategy"),
        "selectableUnit": quotas.get("selectableUnit"),
        "selectableBlock": quotas.get("selectableBlock"),
        "allowMultiReservationBooking": quotas.get("allowMultiReservationBooking"),
        "minDurationHours": quotas.get("minDurationHours"),
        "maxDurationHours": quotas.get("maxDurationHours"),
        "remainingFuturePrivateReservations": quotas.get(
            "remainingFuturePrivateReservations"
        ),
    }


def _available_court_count(pod: Dict[str, Any]) -> int:
    tables = pod.get("tables")
    items = tables.get("items", []) if isinstance(tables, dict) else []
    return sum(
        1
        for table in items
        if isinstance(table, dict) and table.get("status") == "AVAILABLE"
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
