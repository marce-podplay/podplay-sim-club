"""Bounded, normal-use Unified Booking research campaigns.

This is deliberately separate from the fictional social runtime.  A campaign
may create a real Preview booking, but only for its fixed roster and price
band.  Product correlation data stays in ``secrets/``; journals receive only
safe, human-readable facts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from .credentials import PreviewCredentials
from .config import ClubConfig, RunMode
from .firebase_auth import FirebaseAuthenticator
from .identity_registry import IdentityRegistry
from .players import actor_auth_cache
from .storage import Storage
from .target_policy import TargetPolicy


PRIVATE_FILE = "unified-research-campaigns.json"
CAMPAIGNS = (
    {"id": "rate-cards-off-peak", "band": "OFF_PEAK", "hour": 14, "owner": "andy-bogard", "teammate": "terry-bogard"},
    {"id": "rate-cards-peak", "band": "PEAK", "hour": 18, "owner": "kyo-captain", "teammate": "benimaru"},
)


class ContinuousResearchError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        raise ContinuousResearchError("Unified research request refused an HTTP redirect")


class _Client:
    def __init__(self, policy: TargetPolicy, token: str):
        self.policy, self.token = policy, token
        self.opener = build_opener(_NoRedirect())

    def request(self, method: str, path: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not path.startswith("/apis/v2/"):
            raise ContinuousResearchError("Unified research path is invalid")
        url = self.policy.target_origin + path
        self.policy.assert_url(url)
        request = Request(
            url,
            data=None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Accept": "application/json", "Authorization": f"Bearer {self.token}", "User-Agent": "podplay-sim-club/0.1 unified-research", **({"Content-Type": "application/json"} if payload is not None else {})},
            method=method,
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                self.policy.assert_url(response.geturl())
                body = response.read(2 * 1024 * 1024 + 1)
                status = response.status
        except HTTPError as exc:
            raise ContinuousResearchError(f"Unified research request failed with HTTP {exc.code}") from exc
        except URLError as exc:
            raise ContinuousResearchError("Unified research network request failed") from exc
        if status not in {200, 201} or len(body) > 2 * 1024 * 1024:
            raise ContinuousResearchError("Unified research response was unexpected")
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContinuousResearchError("Unified research response was not JSON") from exc
        if not isinstance(value, dict):
            raise ContinuousResearchError("Unified research response had an invalid shape")
        return value


def run_campaigns(root: Path, confirmation: str, *, now: datetime | None = None) -> Dict[str, Any]:
    """Create at most one booking per declared band, then accept its invite.

    Repeated calls only observe the private campaign ledger.  They cannot
    create an unbounded stream of bookings.
    """
    credentials, policy = _write_context(root, confirmation)
    identities = IdentityRegistry(root / "secrets" / "preview-actors.json").stored()
    private = _load_private(root)
    pod_id, zone_name = _private_location(root, private)
    instant = now or datetime.now(timezone.utc)
    results = []
    for campaign in CAMPAIGNS:
        previous = private["campaigns"].get(campaign["id"])
        if isinstance(previous, dict) and previous.get("status") == "booked":
            owner = _client_for(root, credentials, identities, policy, campaign["owner"])
            changed = _observe_existing(root, owner, campaign, previous, instant)
            results.append({"campaign": campaign["id"], "action": "observed_changed" if changed else "observed_unchanged", "remoteWrites": 0})
            continue
        owner = _client_for(root, credentials, identities, policy, campaign["owner"])
        teammate = _client_for(root, credentials, identities, policy, campaign["teammate"])
        quote, starts_at, ends_at = _find_quote(owner, pod_id, zone_name, campaign["hour"], instant)
        confirmation_path = _confirm_path(quote)
        payment = owner.request("GET", "/apis/v2/users/current/payment-method")
        method_id = _payment_id(payment)
        booking = owner.request("POST", confirmation_path, {"termsAgreed": True, "paymentMethod": {"id": method_id}})
        booking_id = _id(booking, "booking")
        invite = owner.request("POST", f"/apis/v2/bookings/{booking_id}/invites", {"mode": "BOOKER_COVERS"})
        invite_token = _id(invite, "invite token", keys=("token",))
        teammate.request("POST", f"/apis/v2/invites/{invite_token}/accept", {"termsAgreed": True})
        total = _money_cents(quote)
        private["campaigns"][campaign["id"]] = {
            "status": "booked", "bookingId": booking_id, "inviteToken": invite_token,
            "owner": campaign["owner"], "teammate": campaign["teammate"],
            "band": campaign["band"], "startsAt": starts_at, "endsAt": ends_at,
            "observedTotalCents": total, "targetOrigin": policy.target_origin,
        }
        _record_safe_booking(root, campaign, starts_at, total, instant)
        results.append({"campaign": campaign["id"], "action": "booked_and_invited", "pricingBand": campaign["band"], "durationMinutes": 30, "remoteWrites": 3})
    _write_private(root, private)
    return {"campaigns": results, "remoteWrites": sum(row["remoteWrites"] for row in results), "attendance": "not_observed"}


def check_in_arrivals(root: Path, confirmation: str, *, now: datetime | None = None) -> Dict[str, Any]:
    """Let declared players check in only when their own product view opens it.

    This is deliberately a separate command from booking creation.  A local
    arrival acceptance is journaled immediately before that player's own
    product check-in.  No action is taken before the UI exposes the arrival
    affordance, and a missing/disabled affordance is a wait, not a failure.
    """
    credentials, policy = _write_context(root, confirmation)
    identities = IdentityRegistry(root / "secrets" / "preview-actors.json").stored()
    private = _load_private(root)
    instant = now or datetime.now(timezone.utc)
    rows = []
    for campaign in CAMPAIGNS:
        record = private["campaigns"].get(campaign["id"])
        if not isinstance(record, dict) or record.get("status") != "booked":
            continue
        if record.get("lastObservedStatus") == "COMPLETED":
            rows.append({"campaign": campaign["id"], "action": "session_completed_without_check_in_evidence", "remoteWrites": 0})
            continue
        checkins = record.setdefault("checkIns", {})
        for actor in (campaign["owner"], campaign["teammate"]):
            if checkins.get(actor) == "confirmed":
                rows.append({"campaign": campaign["id"], "actor": actor, "action": "already_checked_in", "remoteWrites": 0})
                continue
            identity = identities.get(actor)
            if identity is None or not identity.podplay_user_id:
                raise ContinuousResearchError(f"missing verified product identity for {actor}")
            client = _client_for(root, credentials, identities, policy, actor)
            booking = client.request("GET", f"/apis/v2/bookings/{record['bookingId']}")
            spot = _own_spot(booking, identity.podplay_user_id)
            if spot is None or not _checkin_open(spot):
                rows.append({"campaign": campaign["id"], "actor": actor, "action": "waiting_for_player_check_in_window", "remoteWrites": 0})
                continue
            _record_arrival_acceptance(root, campaign, actor, instant)
            client.request("POST", f"/apis/v2/bookings/{record['bookingId']}/spots/{spot['id']}/check-in", {})
            read_back = client.request("GET", f"/apis/v2/bookings/{record['bookingId']}")
            confirmed_spot = _own_spot(read_back, identity.podplay_user_id)
            if confirmed_spot is None:
                raise ContinuousResearchError("check-in read-back did not retain the player spot")
            checkins[actor] = "confirmed"
            _record_check_in(root, campaign, actor, instant)
            rows.append({"campaign": campaign["id"], "actor": actor, "action": "checked_in", "remoteWrites": 1})
    _write_private(root, private)
    return {"campaigns": rows, "remoteWrites": sum(row["remoteWrites"] for row in rows), "attendance": "not_observed", "doesNotMean": ["play", "result", "benefit", "regression-conclusion"]}


def _write_context(root: Path, confirmation: str) -> tuple[PreviewCredentials, TargetPolicy]:
    credentials = PreviewCredentials.load(root)
    policy = TargetPolicy.from_config(ClubConfig(
        mode=RunMode.PREVIEW_WRITE,
        preview_origin=credentials.preview_origin,
        allowed_preview_origins=credentials.allowed_preview_origins,
        preview_write_confirmation=confirmation,
        admin_email=credentials.admin_email,
    ))
    if not policy.writes_allowed:
        raise ContinuousResearchError("an exact Preview write confirmation is required")
    return credentials, policy


def _own_spot(booking: Dict[str, Any], product_user_id: str) -> Dict[str, Any] | None:
    group = booking.get("group")
    spots = group.get("spots") if isinstance(group, dict) else None
    if not isinstance(spots, list):
        return None
    for spot in spots:
        player = spot.get("player") if isinstance(spot, dict) else None
        if isinstance(player, dict) and player.get("id") == product_user_id and isinstance(spot.get("id"), str):
            return spot
    return None


def _checkin_open(spot: Dict[str, Any]) -> bool:
    actions = spot.get("actions")
    if not isinstance(actions, list):
        return False
    return any(isinstance(action, dict) and action.get("key") == "show_check_in_code" and action.get("enabled") is True for action in actions)


def _record_arrival_acceptance(root: Path, campaign: Dict[str, Any], actor: str, now: datetime) -> None:
    storage = Storage(root)
    observed_at = now.astimezone(timezone.utc).isoformat()
    key = f"continuous:{campaign['id']}"
    storage.append_jsonl(storage.actor_journal_path(actor), {"id": f"{key}:arrival-accept:{actor}", "occurrenceKey": key, "actorId": actor, "scenario": "continuous-unified-research", "step": "arrival_check_in_accepted", "detail": "The named player explicitly accepted their own arrival check-in after the product exposed the arrival action.", "observedAt": observed_at})


def _record_check_in(root: Path, campaign: Dict[str, Any], actor: str, now: datetime) -> None:
    storage = Storage(root)
    observed_at = now.astimezone(timezone.utc).isoformat()
    key = f"continuous:{campaign['id']}"
    storage.append_jsonl(storage.actor_journal_path(actor), {"id": f"{key}:check-in:{actor}", "occurrenceKey": key, "actorId": actor, "scenario": "continuous-unified-research", "step": "product_check_in_confirmed", "detail": "The product accepted this player’s own check-in after explicit arrival acceptance. Check-in is not evidence of play or a result.", "observedAt": observed_at})
    storage.append_jsonl(storage.channel_path("staff-planning"), {"id": f"{key}:check-in:{actor}", "channel": "staff-planning", "kind": "continuous_player_check_in", "from": "riley", "to": [actor], "observedAt": observed_at, "text": "A named player’s own check-in was accepted by the product after the arrival window opened. Do not infer play, a result, a benefit, or a regression."})


def _observe_existing(root: Path, owner: _Client, campaign: Dict[str, Any], private: Dict[str, Any], now: datetime) -> bool:
    """Read a known booking and journal only a meaningful safe state change."""
    booking_id = private.get("bookingId")
    if not isinstance(booking_id, str) or not booking_id:
        raise ContinuousResearchError("private campaign booking correlation is missing")
    booking = owner.request("GET", f"/apis/v2/bookings/{booking_id}")
    status = booking.get("status")
    if not isinstance(status, str) or not status:
        status = "present_without_exposed_status"
    previous = private.get("lastObservedStatus")
    private["lastObservedStatus"] = status
    private["lastObservedAt"] = now.astimezone(timezone.utc).isoformat()
    if previous == status:
        return False
    if previous is None:
        return False
    storage = Storage(root)
    observed_at = private["lastObservedAt"]
    key = f"continuous:{campaign['id']}"
    storage.append_jsonl(storage.actor_journal_path(campaign["owner"]), {"id": f"{key}:status:{observed_at}", "occurrenceKey": key, "actorId": campaign["owner"], "scenario": "continuous-unified-research", "step": "booking_status_changed", "detail": "A direct owner booking read-back changed its exposed status. This is an observation only; it does not establish attendance, play, or a regression.", "observedAt": observed_at})
    storage.append_jsonl(storage.channel_path("staff-planning"), {"id": f"{key}:status:{observed_at}", "channel": "staff-planning", "kind": "continuous_unified_status_changed", "from": "riley", "to": [campaign["owner"], campaign["teammate"]], "observedAt": observed_at, "text": "A direct Unified Booking read-back changed its exposed booking status. Preserve the fact for follow-up; do not infer attendance, play, cancellation cause, or a regression from this observation."})
    return True


def _client_for(root: Path, credentials: PreviewCredentials, identities: Dict[str, Any], policy: TargetPolicy, actor: str) -> _Client:
    identity = identities.get(actor)
    if identity is None:
        raise ContinuousResearchError(f"missing existing actor identity for {actor}")
    auth = FirebaseAuthenticator(actor_auth_cache(root, actor))
    session = auth.authenticate(identity.email, identity.password, credentials.firebase_api_key)
    return _Client(policy, session.id_token)


def _find_quote(client: _Client, pod_id: str, zone_name: str, hour: int, now: datetime) -> tuple[Dict[str, Any], str, str]:
    zone = ZoneInfo(zone_name)
    local_now = now.astimezone(zone)
    for offset in range(1, 9):
        candidate = (local_now + timedelta(days=offset)).replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate.weekday() >= 5:
            continue
        end = candidate + timedelta(minutes=30)
        try:
            quote = client.request("POST", "/apis/v2/bookings/quotes", {"slot": {"pod": {"id": pod_id}, "startsAt": candidate.astimezone(timezone.utc).isoformat(), "endsAt": end.astimezone(timezone.utc).isoformat()}, "groupSize": 2, "passes": {"strategy": "USE_NONE"}, "virtualCredits": {"strategy": "USE_ALL"}})
        except ContinuousResearchError:
            continue
        if isinstance(quote.get("_links"), dict):
            return quote, candidate.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()
    raise ContinuousResearchError("no legal future 30-minute weekday slot was available")


def _confirm_path(quote: Dict[str, Any]) -> str:
    href = quote.get("_links", {}).get("confirm", {}).get("href")
    # The API deliberately returns a client-facing relative link, while this
    # adapter's origin lock works with API-relative paths.
    if isinstance(href, str) and href.startswith("/bookings/"):
        href = "/apis/v2" + href
    if not isinstance(href, str) or not href.startswith("/apis/v2/"):
        raise ContinuousResearchError("quote did not provide a valid confirmation path")
    return href


def _payment_id(payment: Dict[str, Any]) -> str:
    for key in ("paymentElement", "preferredCard", "preferredBankAccount"):
        value = payment.get(key)
        if isinstance(value, dict) and isinstance(value.get("id"), str) and value["id"]:
            return value["id"]
    raise ContinuousResearchError("booking owner has no reusable test payment method")


def _id(value: Dict[str, Any], label: str, keys: tuple[str, ...] = ("id",)) -> str:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    raise ContinuousResearchError(f"{label} response did not contain an identifier")


def _money_cents(quote: Dict[str, Any]) -> int | None:
    for key in ("total", "amount", "due"):
        value = quote.get(key)
        if isinstance(value, dict) and isinstance(value.get("amount"), int):
            return value["amount"]
    return None


def _private_location(root: Path, private: Dict[str, Any]) -> tuple[str, str]:
    location = private.get("location")
    if not isinstance(location, dict):
        # One-time migration from the pre-existing local readiness cache.  The
        # raw pod identifier is immediately moved to ignored private state and
        # is never written to a diary, channel, dashboard, or source file.
        legacy = Storage(root).load_json(root / "state" / "preview-readiness.json", default={})
        location = legacy.get("location") if isinstance(legacy, dict) else None
        if not isinstance(location, dict):
            raise ContinuousResearchError("private campaign location is not initialized; run Preview readiness first")
        private["location"] = {"podId": location.get("podId"), "timezone": location.get("timezone")}
        location = private["location"]
    pod_id, zone = location.get("podId"), location.get("timezone")
    if not isinstance(pod_id, str) or not pod_id or not isinstance(zone, str) or not zone:
        raise ContinuousResearchError("private campaign location is incomplete")
    try:
        ZoneInfo(zone)
    except Exception as exc:
        raise ContinuousResearchError("private campaign timezone is invalid") from exc
    return pod_id, zone


def _load_private(root: Path) -> Dict[str, Any]:
    path = root / "secrets" / PRIVATE_FILE
    if not path.is_file():
        return {"schemaVersion": 1, "campaigns": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContinuousResearchError("private campaign ledger is invalid") from exc
    if not isinstance(value, dict) or value.get("schemaVersion") != 1 or not isinstance(value.get("campaigns"), dict):
        raise ContinuousResearchError("private campaign ledger has an invalid shape")
    return value


def _write_private(root: Path, value: Dict[str, Any]) -> None:
    path = root / "secrets" / PRIVATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".unified-research-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _record_safe_booking(root: Path, campaign: Dict[str, Any], starts_at: str, total: int | None, now: datetime) -> None:
    storage = Storage(root)
    observed_at = now.astimezone(timezone.utc).isoformat()
    key = f"continuous:{campaign['id']}"
    amount = "an observed checkout amount" if total is None else f"an observed {total} cent checkout amount"
    storage.append_jsonl(storage.actor_journal_path(campaign["owner"]), {"id": f"{key}:booking", "occurrenceKey": key, "actorId": campaign["owner"], "scenario": "continuous-unified-research", "step": "unified_booking_confirmed", "detail": f"A future 30-minute {campaign['band'].lower()} training booking was confirmed for the declared research campaign, with {amount}.", "observedAt": observed_at})
    storage.append_jsonl(storage.actor_journal_path(campaign["teammate"]), {"id": f"{key}:acceptance", "occurrenceKey": key, "actorId": campaign["teammate"], "scenario": "continuous-unified-research", "step": "product_invitation_accepted", "detail": "The named teammate explicitly accepted the declared booker-covers invitation. This is not check-in, attendance, or play.", "observedAt": observed_at})
    storage.append_jsonl(storage.channel_path("staff-planning"), {"id": f"{key}:readback", "channel": "staff-planning", "kind": "continuous_unified_booking", "from": "riley", "to": [campaign["owner"], campaign["teammate"]], "observedAt": observed_at, "text": f"The declared {campaign['band'].lower()} research booking has a confirmed booking and accepted teammate invitation. The session starts later; attendance, play, result, benefit, and regression status are not observed."})
