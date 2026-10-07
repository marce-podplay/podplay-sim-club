"""Player-owned, GET-only discovery of customer-visible PodPlay assets.

Raw product responses are kept in memory only.  Each endpoint is reduced to
one or more strict subject observations before evidence or facts are written.
"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional
from urllib.parse import urlencode

from .credentials import PreviewCredentials
from .facts import reject_private_data
from .firebase_auth import FirebaseAuthenticator
from .identity_registry import IdentityRegistry, IdentityRegistryError
from .players import actor_auth_cache, canonical_actor_id
from .preview_readonly import PreviewReadError, PreviewReadonlyClient, collection_items
from .protocol import append_protocol_facts_locked
from .self_assets import (
    SelfAssetValidationError,
    make_belief_fact,
    make_evidence,
    persist_evidence_locked,
    sanitize_observation,
)
from .storage import Storage
from .target_policy import TargetPolicy
from .time import isoformat


CAPABILITY_ID = "actor.inspect_self_assets"
CONTRACT_VERSION = 1
SUBJECT_TTLS = {
    "virtual_credit_balance": timedelta(minutes=15),
    "payment_readiness": timedelta(minutes=15),
    "owned_package_entitlements": timedelta(minutes=30),
    "membership_assignment": timedelta(hours=6),
}
PACKAGE_ITEM_TYPES = {
    "BOOKING", "EVENT_SIGNUP", "GUEST_SIGNUP", "SERIES_SIGNUP",
    "REPLAY", "VIRTUAL_CREDITS", "REPLAY_CREDITS", "LESSON",
}
PACKAGE_STATUSES = {"ACTIVE", "EXHAUSTED", "EXPIRED", "CANCELED"}


class AssetDiscoveryError(RuntimeError):
    """Raised when discovery cannot safely identify or persist an actor read."""


def discover_player_assets(
    root: Path,
    actor_ids: Iterable[str],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Authenticate as existing actors and perform only actor-owned GETs."""
    credentials = PreviewCredentials.load(root)
    policy = TargetPolicy.from_config(credentials.config)
    identities = IdentityRegistry(root / "secrets" / "preview-actors.json").stored()
    selected = _selected_actor_ids(actor_ids)
    observed_at = _utc(now or datetime.now(timezone.utc))
    results = []
    for actor_id in selected:
        identity = identities.get(actor_id)
        if identity is None:
            raise IdentityRegistryError(
                f"actor is missing from the private registry: {actor_id}"
            )
        session = FirebaseAuthenticator(actor_auth_cache(root, actor_id)).authenticate(
            identity.email, identity.password, credentials.firebase_api_key
        )
        if session.email.lower() != identity.email.lower():
            raise AssetDiscoveryError(
                f"Firebase session does not match the registry user: {actor_id}"
            )
        client = PreviewReadonlyClient(policy, session.id_token)
        results.append(
            discover_player_assets_with_client(
                Storage(root),
                actor_id=actor_id,
                expected_email=identity.email,
                client=client,
                target_fingerprint=_target_fingerprint(policy),
                observed_at=observed_at,
            )
        )
    return {
        "schemaVersion": 1,
        "pullRequest": policy.pull_request_number,
        "targetFingerprint": _target_fingerprint(policy),
        "observedAt": isoformat(observed_at),
        "actors": results,
        "remoteWrites": 0,
    }


def discover_player_assets_with_client(
    storage: Storage,
    *,
    actor_id: str,
    expected_email: str,
    client: PreviewReadonlyClient,
    target_fingerprint: str,
    observed_at: datetime,
) -> Dict[str, Any]:
    """Discover one actor's assets using an already authenticated GET client.

    This seam makes the contract testable without network access.  The current
    user read is the identity gate.  Once it passes, payment and package
    failures are isolated per subject and successful observations survive.
    """
    actor_id = canonical_actor_id(actor_id)
    when = _utc(observed_at)
    timestamp = isoformat(when)
    correlation_id = f"journey:self-assets:{actor_id}:{timestamp}"
    probe_id = f"probe:self-assets:{actor_id}:{timestamp}"

    profile_query = urlencode(
        [("expand", "_links.membershipEnrollment._links.membership")]
    )
    profile = client.get(f"/apis/v2/users/current?{profile_query}")
    _verify_current_user(actor_id, expected_email, profile)

    subject_results: Dict[str, Dict[str, Any]] = {}
    subject_results["membership_assignment"] = _observe(
        "membership_assignment",
        lambda: _membership_observation(profile)
    )

    try:
        payment = client.get("/apis/v2/users/current/payment-method")
    except PreviewReadError:
        subject_results["virtual_credit_balance"] = _error("request_failed")
    else:
        subject_results["virtual_credit_balance"] = _observe(
            "virtual_credit_balance",
            lambda: _credit_observation(payment)
        )

    payment_elements_query = urlencode(
        [("expand", "items._links.contextAvailability")]
    )
    try:
        payment_elements = client.get(
            f"/apis/v2/users/current/payment-elements?{payment_elements_query}"
        )
    except PreviewReadError:
        subject_results["payment_readiness"] = _error("request_failed")
    else:
        subject_results["payment_readiness"] = _observe(
            "payment_readiness",
            lambda: _payment_readiness_observation(payment_elements)
        )

    package_query = urlencode([("expand", "items._links.items")])
    try:
        packages = client.get(f"/apis/v2/users/current/packages?{package_query}")
    except PreviewReadError:
        subject_results["owned_package_entitlements"] = _error("request_failed")
    else:
        subject_results["owned_package_entitlements"] = _observe(
            "owned_package_entitlements",
            lambda: _package_observation(packages)
        )

    evidences = []
    facts = []
    summaries: Dict[str, Any] = {}
    for subject, result in subject_results.items():
        evidence = make_evidence(
            probe_id=probe_id,
            target_fingerprint=target_fingerprint,
            capability_id=CAPABILITY_ID,
            actor_id=actor_id,
            subject=subject,
            observed_at=timestamp,
            result=result["result"],
            observation=result.get("observation"),
            error_code=result.get("errorCode"),
            contract_version=CONTRACT_VERSION,
        )
        status = "observed" if evidence["result"] == "observed" else "unknown"
        expiry = (
            isoformat(when + SUBJECT_TTLS[subject]) if status == "observed" else None
        )
        facts.append(
            make_belief_fact(
                actor_id=actor_id,
                subject=subject,
                status=status,
                occurred_at=timestamp,
                correlation_id=correlation_id,
                observed_value=evidence["safeObservation"],
                evidence_ref=evidence["evidenceId"],
                expires_at=expiry,
                source_kind="preview_readback",
            )
        )
        evidences.append(evidence)
        summaries[subject] = {
            "status": status,
            "result": evidence["result"],
            "value": evidence["safeObservation"],
            "errorCode": evidence["errorCode"],
            "evidenceRef": evidence["evidenceId"],
            "observedAt": timestamp,
            "expiresAt": expiry,
        }

    with storage.writer_lock():
        for evidence in evidences:
            persist_evidence_locked(storage, evidence)
        appended = append_protocol_facts_locked(storage, facts)

    return {
        "actorId": actor_id,
        "correlationId": correlation_id,
        "subjects": summaries,
        "localFactsAppended": appended,
        "remoteWrites": 0,
    }


def _selected_actor_ids(actor_ids: Iterable[str]) -> list[str]:
    selected = []
    for value in actor_ids:
        actor_id = canonical_actor_id(value)
        if actor_id not in selected:
            selected.append(actor_id)
    if not selected:
        raise AssetDiscoveryError("at least one existing actor must be selected")
    return selected


def _verify_current_user(actor_id: str, expected_email: str, profile: Any) -> None:
    if not isinstance(profile, dict):
        raise AssetDiscoveryError(
            f"current user response has an unexpected shape: {actor_id}"
        )
    email = profile.get("email")
    if not isinstance(email, str) or email.lower() != expected_email.lower():
        raise AssetDiscoveryError(
            f"current user does not match the registry user: {actor_id}"
        )
    if not isinstance(profile.get("id"), str) or not profile["id"]:
        raise AssetDiscoveryError(f"current user id is missing: {actor_id}")


def _credit_observation(payment: Any) -> Dict[str, Any]:
    if not isinstance(payment, dict):
        raise SelfAssetValidationError("payment response is not an object")
    if "virtualCredits" not in payment:
        raise _MissingField("virtualCredits")
    credits = payment["virtualCredits"]
    if isinstance(credits, bool) or not isinstance(credits, (int, float)):
        raise SelfAssetValidationError("virtualCredits is not numeric")
    return {"amount": float(credits), "unit": "virtual_credit"}


def _payment_readiness_observation(response: Any) -> Dict[str, Any]:
    """Mirror the customer Payment Methods screen without retaining details.

    The current UI defines its default booking method by finding a payment
    element whose expanded context-availability collection contains the
    `booking` context with `isDefault: true`.  An explicit empty collection is
    therefore observed not-ready.  Missing expansions remain unknown.
    """
    payment_elements = collection_items(response)
    ready = False
    for payment_element in payment_elements:
        if not isinstance(payment_element, dict):
            raise SelfAssetValidationError("payment element is not an object")
        contexts = payment_element.get("contextAvailability")
        if not isinstance(contexts, dict) or not isinstance(contexts.get("items"), list):
            raise _MissingField("payment element context availability expansion")
        for context in contexts["items"]:
            if not isinstance(context, dict):
                raise SelfAssetValidationError(
                    "payment element context availability is not an object"
                )
            context_id = context.get("id")
            is_default = context.get("isDefault")
            if not isinstance(context_id, str):
                raise _MissingField("payment element context id")
            if not isinstance(is_default, bool):
                raise _MissingField("payment element context default flag")
            if context_id == "booking" and is_default:
                ready = True
    return {"ready": ready}


def _membership_observation(profile: Any) -> Dict[str, Any]:
    if not isinstance(profile, dict):
        raise SelfAssetValidationError("profile response is not an object")
    if "membershipEnrollment" not in profile:
        # Deliberately conservative: an omitted expansion is not proof that the
        # customer has no membership.
        raise _MissingField("membershipEnrollment")
    enrollment = profile["membershipEnrollment"]
    if enrollment is None:
        return {"assigned": False}
    if not isinstance(enrollment, dict):
        raise SelfAssetValidationError("membershipEnrollment is not an object")
    membership = enrollment.get("membership")
    if not isinstance(membership, dict):
        raise _MissingField("membershipEnrollment.membership")
    membership_type = membership.get("type", enrollment.get("type"))
    display_name = membership.get("displayName", enrollment.get("displayName"))
    if not isinstance(membership_type, str) or not membership_type:
        raise _MissingField("membership type")
    if not isinstance(display_name, str) or not display_name:
        raise _MissingField("membership display name")
    observation: Dict[str, Any] = {
        "assigned": True,
        "type": membership_type,
        "displayName": display_name,
    }
    if "isOwner" in enrollment:
        if not isinstance(enrollment["isOwner"], bool):
            raise SelfAssetValidationError("membership owner flag is not boolean")
        observation["isOwner"] = enrollment["isOwner"]
    if enrollment.get("startDate") is not None:
        if not isinstance(enrollment["startDate"], str):
            raise SelfAssetValidationError("membership start date is not a timestamp")
        observation["startDate"] = enrollment["startDate"]
    return observation


def _package_observation(response: Any) -> list[Dict[str, Any]]:
    packages = collection_items(response)
    observations = []
    for package in packages:
        if not isinstance(package, dict):
            raise SelfAssetValidationError("package is not an object")
        status = package.get("status")
        if status not in PACKAGE_STATUSES:
            raise SelfAssetValidationError("package status is unsupported")
        if status != "ACTIVE":
            continue
        items = package.get("items")
        if not isinstance(items, dict) or not isinstance(items.get("items"), list):
            raise _MissingField("package items expansion")
        expiration = package.get("expirationDate")
        if expiration is not None and not isinstance(expiration, str):
            raise SelfAssetValidationError("package expiration is not a timestamp")
        for item in items["items"]:
            if not isinstance(item, dict):
                raise SelfAssetValidationError("package item is not an object")
            item_type = item.get("type")
            label = item.get("displayName")
            remaining = item.get("remainingItems")
            if item_type not in PACKAGE_ITEM_TYPES:
                raise SelfAssetValidationError("package item type is unsupported")
            if not isinstance(label, str) or not label:
                raise _MissingField("package item displayName")
            if isinstance(remaining, bool) or not isinstance(remaining, (int, float)):
                raise _MissingField("package item remainingItems")
            observations.append({
                "kind": item_type.lower(),
                "label": label,
                "remainingQuantity": float(remaining),
                "unit": _package_unit(item_type),
                "expiresAt": expiration,
                "status": status.lower(),
            })
    return observations


def _package_unit(item_type: str) -> str:
    if item_type in {"VIRTUAL_CREDITS", "REPLAY_CREDITS"}:
        return "credit"
    if item_type == "REPLAY":
        return "replay"
    if item_type == "LESSON":
        return "lesson"
    return "pass"


class _MissingField(SelfAssetValidationError):
    pass


def _observe(subject: str, reader: Callable[[], Any]) -> Dict[str, Any]:
    try:
        observation = sanitize_observation(subject, reader())
        reject_private_data(observation, f"self_assets.{subject}")
        return {
            "result": "observed",
            "observation": observation,
        }
    except _MissingField:
        return {"result": "unknown", "errorCode": "missing_field"}
    except (SelfAssetValidationError, PreviewReadError, ValueError, TypeError):
        return {"result": "error", "errorCode": "unexpected_shape"}


def _error(code: str) -> Dict[str, Any]:
    return {"result": "error", "errorCode": code}


def _target_fingerprint(policy: TargetPolicy) -> str:
    digest = sha256(policy.target_origin.encode("utf-8")).hexdigest()[:12]
    return f"pr-{policy.pull_request_number}:origin-{digest}"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
