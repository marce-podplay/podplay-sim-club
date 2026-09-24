"""Idempotent virtual-credit seed for the two Preview Club captains."""

from pathlib import Path
from typing import Any, Dict, Tuple

from .firebase_auth import FirebaseAuthenticator
from .identity_registry import ActorIdentity
from .preview_readiness import inspect_preview_readiness
from .preview_readonly import PreviewReadError, PreviewReadonlyClient
from .preview_seed import load_preview_seed
from .preview_write import PreviewCreditWriter, PreviewWriteError
from .target_policy import TargetPolicy


def build_funding_plan(
    root: Path,
    read_policy: TargetPolicy,
    admin_client: PreviewReadonlyClient,
    identities: Dict[str, ActorIdentity],
    firebase_api_key: str,
) -> Tuple[Dict[str, Any], Dict[str, PreviewReadonlyClient]]:
    readiness = inspect_preview_readiness(
        root,
        read_policy,
        admin_client,
        identities,
        firebase_api_key,
    )
    desired = load_preview_seed(root).get("virtualCredits")
    if not isinstance(desired, dict) or not desired:
        raise PreviewReadError("preview seed does not define virtual credits")
    rows = {}
    actor_clients = {}
    for actor_id, target in desired.items():
        if actor_id not in identities or not isinstance(target, (int, float)) or target <= 0:
            raise PreviewReadError("preview virtual-credit seed is invalid")
        identity = identities[actor_id]
        auth = FirebaseAuthenticator(
            root / "secrets" / "actor-auth" / f"{actor_id}.json"
        ).authenticate(identity.email, identity.password, firebase_api_key)
        actor_clients[actor_id] = PreviewReadonlyClient(read_policy, auth.id_token)
        current = float(readiness["actors"][actor_id]["virtualCredits"])
        increment = max(round(float(target) - current, 2), 0)
        rows[actor_id] = {
            "current": current,
            "desired": float(target),
            "increment": increment,
        }
    return {"readiness": readiness, "actors": rows}, actor_clients


def apply_funding_plan(
    root: Path,
    read_policy: TargetPolicy,
    write_policy: TargetPolicy,
    admin_client: PreviewReadonlyClient,
    admin_token: str,
    identities: Dict[str, ActorIdentity],
    firebase_api_key: str,
) -> Dict[str, Any]:
    plan, actor_clients = build_funding_plan(
        root, read_policy, admin_client, identities, firebase_api_key
    )
    writer = PreviewCreditWriter(write_policy, admin_token)
    writes = 0
    for actor_id, row in plan["actors"].items():
        if row["increment"] > 0:
            writer.credit(identities[actor_id].podplay_user_id, row["increment"])
            writes += 1
        payment = actor_clients[actor_id].get("/apis/v2/users/current/payment-method")
        balance = payment.get("virtualCredits") if isinstance(payment, dict) else None
        if not isinstance(balance, (int, float)) or float(balance) < row["desired"]:
            raise PreviewWriteError(f"virtual-credit read-back failed for {actor_id}")
        row["verified"] = float(balance)
    plan["writes"] = writes
    return plan
