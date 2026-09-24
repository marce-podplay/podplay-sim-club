"""Read-only planning and narrowly bounded identity seeding for Preview Club."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import urlencode

from .firebase_auth import FirebaseAuthenticator
from .identity_registry import ActorIdentity, IdentityRegistry
from .preview_readonly import PreviewReadError, PreviewReadonlyClient, collection_items
from .preview_write import PreviewIdentityWriter, PreviewWriteError
from .target_policy import TargetPolicy


SIGNUP_SETTINGS = (
    "feature.areaLiabilityWaiver",
    "feature.liabilityWaiver",
    "feature.requireAreaOnSignup",
    "feature.requirePhoneNumberOnSignup",
    "stripe.testModeEnabled",
)


def load_preview_seed(root: Path) -> Dict[str, Any]:
    path = root / "seed" / "preview-club.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreviewReadError("seed/preview-club.json is missing or invalid") from exc
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise PreviewReadError("unsupported Preview Club seed definition")
    return value


def build_seed_plan(
    root: Path,
    client: PreviewReadonlyClient,
    identities: Dict[str, ActorIdentity],
) -> Dict[str, Any]:
    seed = load_preview_seed(root)
    tenant = client.get("/apis/v2/tenants/current")
    if not isinstance(tenant, dict) or not isinstance(tenant.get("id"), str):
        raise PreviewReadError("tenant response has an unexpected shape")

    settings = {
        item.get("id"): item.get("value")
        for item in collection_items(client.get("/apis/v2/tenants/current/settings"))
        if isinstance(item, dict) and item.get("id") in SIGNUP_SETTINGS
    }
    missing_settings = [key for key in SIGNUP_SETTINGS if key not in settings]
    if missing_settings:
        raise PreviewReadError("signup settings are missing: " + ", ".join(missing_settings))

    location = seed.get("location", {})
    area_id = location.get("areaId")
    pod_id = location.get("podId")
    areas = collection_items(client.get("/apis/v2/areas"))
    area = next(
        (item for item in areas if isinstance(item, dict) and item.get("id") == area_id),
        None,
    )
    if area is None:
        raise PreviewReadError(f"configured Preview Club area is missing: {area_id}")
    pods = collection_items(client.get(f"/apis/v2/areas/{area_id}/pods"))
    pod = next(
        (item for item in pods if isinstance(item, dict) and item.get("id") == pod_id),
        None,
    )
    if pod is None:
        raise PreviewReadError(f"configured Preview Club pod is missing: {pod_id}")

    now = datetime.now(timezone.utc)
    query = urlencode(
        {
            "podId": pod_id,
            "startTime": (now - timedelta(days=21)).isoformat(),
            "endTime": (now + timedelta(days=21)).isoformat(),
            "ipp": 1,
            "includeCanceled": "false",
        }
    )
    events = client.get("/apis/v2/events?" + query)
    if not isinstance(events, dict):
        raise PreviewReadError("event activity response has an unexpected shape")
    event_count = events.get("_total")
    if not isinstance(event_count, int):
        event_count = len(collection_items(events))

    actor_rows = {}
    for actor_id, identity in identities.items():
        matches = exact_users(client, identity.email)
        actor_rows[actor_id] = {
            "email": identity.email,
            "status": "exists" if matches else "missing",
            "podplayUserId": matches[0].get("id") if matches else "",
        }

    prerequisites_ok = (
        not _truthy(settings["feature.areaLiabilityWaiver"])
        and not _truthy(settings["feature.liabilityWaiver"])
        and not _truthy(settings["feature.requireAreaOnSignup"])
        and not _truthy(settings["feature.requirePhoneNumberOnSignup"])
        and _truthy(settings["stripe.testModeEnabled"])
    )
    return {
        "tenantId": tenant["id"],
        "tenantName": tenant.get("displayName") or tenant.get("name") or tenant["id"],
        "areaId": area_id,
        "areaName": area.get("displayName") or area.get("name") or area_id,
        "podId": pod_id,
        "podName": pod.get("displayName") or pod.get("name") or pod_id,
        "eventCount42Days": event_count,
        "settings": settings,
        "prerequisitesOk": prerequisites_ok,
        "actors": actor_rows,
    }


def exact_users(client: PreviewReadonlyClient, email: str) -> list:
    query = urlencode({"search": email, "page": 1, "ipp": 30})
    matches = [
        user
        for user in collection_items(client.get("/apis/v2/users?" + query))
        if isinstance(user, dict)
        and str(user.get("email", "")).lower() == email.lower()
    ]
    if len(matches) > 1:
        raise PreviewReadError(f"multiple exact users found for {email}")
    return matches


def apply_identity_seed(
    root: Path,
    read_policy: TargetPolicy,
    write_policy: TargetPolicy,
    admin_client: PreviewReadonlyClient,
    identities: Dict[str, ActorIdentity],
    firebase_api_key: str,
) -> Tuple[Dict[str, Any], list]:
    plan = build_seed_plan(root, admin_client, identities)
    if not plan["prerequisitesOk"]:
        raise PreviewWriteError("signup prerequisites are not safe for automatic seeding")
    writer = PreviewIdentityWriter(write_policy)
    registry = IdentityRegistry(root / "secrets" / "preview-actors.json")
    results = []

    for actor_id, identity in identities.items():
        matches = exact_users(admin_client, identity.email)
        action = "reused"
        if not matches:
            created = writer.signup(
                {
                    "email": identity.email,
                    "password": identity.password,
                    "firstName": identity.first_name,
                    "lastName": identity.last_name,
                    "emailMarketingOptIn": False,
                    "smsMarketingOptIn": False,
                    "acceptedAgreements": {"items": []},
                }
            )
            podplay_user_id = created["id"]
            action = "created"
        else:
            podplay_user_id = str(matches[0].get("id") or "")
        if not podplay_user_id:
            raise PreviewWriteError(f"PodPlay user ID is missing for {actor_id}")

        actor_auth = FirebaseAuthenticator(
            root / "secrets" / "actor-auth" / f"{actor_id}.json"
        ).authenticate(identity.email, identity.password, firebase_api_key)
        actor_profile = PreviewReadonlyClient(read_policy, actor_auth.id_token).get(
            "/apis/v2/users/current"
        )
        if (
            not isinstance(actor_profile, dict)
            or str(actor_profile.get("email", "")).lower() != identity.email.lower()
            or actor_profile.get("id") != podplay_user_id
        ):
            raise PreviewWriteError(f"identity verification failed for {actor_id}")
        registry.record_verified(
            actor_id,
            podplay_user_id,
            podplay_user_id,
            read_policy.target_origin,
            plan["tenantId"],
        )
        results.append(
            {
                "actorId": actor_id,
                "email": identity.email,
                "id": podplay_user_id,
                "action": action,
                "authSource": actor_auth.source,
            }
        )
    return plan, results


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
