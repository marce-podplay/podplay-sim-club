"""Player-owned research recognition and Preview-gated virtual-credit rewards."""

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .credentials import PreviewCredentials
from .config import RunMode
from .firebase_auth import FirebaseAuthenticator
from .identity_registry import IdentityRegistry, IdentityRegistryError
from .preview_readonly import PreviewReadonlyClient
from .preview_write import PreviewCreditWriter, PreviewWriteError
from .product_research import ProductResearchError, research_probe
from .protocol import append_protocol_facts_locked, conversation_id_for, make_message, reply_causation
from .storage import Storage
from .target_policy import TargetPolicy
from .time import isoformat


STATE_FILE = "research-rewards.json"
AMOUNTS = {"participation": 50, "finding": 100}
BADGES = {"participation": "research-participant", "finding": "club-scout"}


class ResearchRewardError(ValueError):
    pass


def offer(root: Path, *, probe_id: str, actor_id: str, kind: str) -> Dict[str, Any]:
    """Offer a bounded reward after eligible evidence; no product credit is granted."""
    if kind not in AMOUNTS:
        raise ResearchRewardError("reward kind must be participation or finding")
    probe = _eligible_probe(root, probe_id, actor_id, kind)
    season = _season(root)
    reward_id = f"reward:{season}:{probe_id}:{actor_id}:{kind}"
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        _ensure_season_cap(state, season, actor_id, kind)
        reward = state["rewards"].get(reward_id)
        if not isinstance(reward, dict):
            observed_at = _now()
            reward = {
                "rewardId": reward_id, "season": season, "probeId": probe_id, "actorId": actor_id,
                "kind": kind, "amount": AMOUNTS[kind], "unit": "virtual_credit", "status": "offered",
                "offeredBy": "sofia", "offeredAt": observed_at, "respondedAt": None,
                "approvedAt": None, "appliedAt": None, "readBack": None, "remoteWrites": 0,
            }
            state["rewards"][reward_id] = reward
            _award_recognition(state, season, actor_id, reward_id, kind, observed_at)
            _append_club_once(storage, {
                "id": f"message:{reward_id}:offer", "channel": "club", "kind": "research_reward_offered",
                "from": "sofia", "to": [actor_id], "probeId": probe_id,
                "text": f"Sofia offers {AMOUNTS[kind]} virtual-credit units for {kind} research work. Accept or pass; this is not granted until product read-back.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            append_protocol_facts_locked(storage, [make_message(
                message_id=f"message:{reward_id}:offer", conversation_id=conversation_id_for(reward_id),
                correlation_id=probe_id, sender_actor_id="sofia", recipient_actor_ids=[actor_id],
                speech_act="offer", content_code="research_virtual_credit_reward", occurred_at=observed_at,
                parameters={"kind": kind, "amount": AMOUNTS[kind], "unit": "virtual_credit"},
            )])
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_reward(reward)


def respond(root: Path, *, reward_id: str, actor_id: str, decision: str) -> Dict[str, Any]:
    if decision not in {"accept", "pass"}:
        raise ResearchRewardError("reward response must be accept or pass")
    storage = Storage(root)
    with storage.writer_lock():
        state, reward = _reward(storage, reward_id)
        if reward["actorId"] != actor_id:
            raise ResearchRewardError("only the offered player may respond")
        if reward["status"] != "offered":
            raise ResearchRewardError("reward offer is no longer awaiting a response")
        observed_at = _now()
        reward.update({"status": "accepted" if decision == "accept" else "passed", "respondedAt": observed_at})
        message_id = f"message:{reward_id}:{decision}"
        _append_club_once(storage, {
            "id": message_id, "channel": "club", "kind": "research_reward_response",
            "from": actor_id, "to": ["sofia"], "probeId": reward["probeId"],
            "text": f"{actor_id} {decision}s the research reward offer. No product credit has been asserted.",
            "observedAt": observed_at, "remoteWrites": 0,
        })
        append_protocol_facts_locked(storage, [make_message(
            message_id=message_id, conversation_id=conversation_id_for(reward_id), correlation_id=reward["probeId"],
            sender_actor_id=actor_id, recipient_actor_ids=["sofia"], speech_act="accept" if decision == "accept" else "reject",
            content_code="research_virtual_credit_reward", occurred_at=observed_at,
            in_reply_to=f"message:{reward_id}:offer", causation_id=reply_causation(f"message:{reward_id}:offer"),
            parameters={"decision": decision, "amount": reward["amount"], "unit": "virtual_credit"},
        )])
        storage.write_json(storage.state / STATE_FILE, state)
        return _public_reward(reward)


def approve(root: Path, *, reward_id: str, actor_id: str) -> Dict[str, Any]:
    """Sofia approves an accepted offer locally; Preview application stays gated."""
    if actor_id != "sofia":
        raise ResearchRewardError("only Sofia may approve a research reward grant")
    storage = Storage(root)
    with storage.writer_lock():
        state, reward = _reward(storage, reward_id)
        if reward["status"] != "accepted":
            raise ResearchRewardError("only an accepted reward offer may be approved")
        reward.update({"status": "approved", "approvedAt": _now()})
        storage.write_json(storage.state / STATE_FILE, state)
        return _public_reward(reward)


def apply(root: Path, *, reward_id: str, confirmation: str) -> Dict[str, Any]:
    """Apply an approved reward as <=50-credit writes and require player read-back."""
    storage = Storage(root)
    with storage.writer_lock():
        _, reward = _reward(storage, reward_id)
        if reward["status"] != "approved":
            raise ResearchRewardError("Sofia approval is required before a reward grant")
        actor_id, amount = reward["actorId"], reward["amount"]
    credentials = PreviewCredentials.load(root)
    read_policy = TargetPolicy.from_config(credentials.config)
    write_policy = TargetPolicy.from_config(replace(
        credentials.config, mode=RunMode.PREVIEW_WRITE, preview_write_confirmation=confirmation,
    ))
    identities = IdentityRegistry(root / "secrets" / "preview-actors.json").stored()
    identity = identities.get(actor_id)
    if identity is None or not identity.podplay_user_id:
        raise IdentityRegistryError("reward recipient lacks a private Preview identity")
    admin = FirebaseAuthenticator(root / "secrets" / "preview-admin-auth-cache.json").authenticate(
        credentials.admin_email, credentials.admin_password, credentials.firebase_api_key
    )
    recipient = FirebaseAuthenticator(root / "secrets" / f"preview-{actor_id}-auth-cache.json").authenticate(
        identity.email, identity.password, credentials.firebase_api_key
    )
    before = _balance(PreviewReadonlyClient(read_policy, recipient.id_token).get("/apis/v2/users/current/payment-method"))
    writer = PreviewCreditWriter(write_policy, admin.id_token)
    remaining = float(amount)
    writes = 0
    while remaining > 0:
        increment = min(50.0, remaining)
        writer.credit(identity.podplay_user_id, increment, reason="Preview Club research reward")
        remaining = round(remaining - increment, 2)
        writes += 1
    after = _balance(PreviewReadonlyClient(read_policy, recipient.id_token).get("/apis/v2/users/current/payment-method"))
    if after < before + float(amount):
        raise PreviewWriteError("research reward credit read-back did not reach the offered amount")
    with storage.writer_lock():
        state, reward = _reward(storage, reward_id)
        reward.update({"status": "applied", "appliedAt": _now(), "readBack": "verified", "remoteWrites": writes})
        storage.write_json(storage.state / STATE_FILE, state)
        return _public_reward(reward)


def snapshot(root: Path) -> Dict[str, Any]:
    state = _load(Storage(root))
    rewards = [_public_reward(value) for value in state["rewards"].values() if isinstance(value, dict)]
    rewards.sort(key=lambda row: row["rewardId"])
    return {"rewards": rewards, "recognition": state["recognition"]}


def _eligible_probe(root: Path, probe_id: str, actor_id: str, kind: str) -> Dict[str, Any]:
    try:
        probe = research_probe(root, probe_id)
    except ProductResearchError as exc:
        raise ResearchRewardError(str(exc)) from exc
    if not any(row.get("actorId") == actor_id and row.get("decision") == "accept" for row in probe.get("verifiers", [])):
        raise ResearchRewardError("only an accepting named verifier may receive this research reward")
    if kind == "participation" and probe.get("status") not in {"observed", "inconclusive"}:
        raise ResearchRewardError("participation reward requires a completed bounded read-back")
    if kind == "finding" and not isinstance(probe.get("issueCandidate"), dict):
        raise ResearchRewardError("finding reward requires a recorded issue candidate")
    return probe


def _season(root: Path) -> str:
    credentials = PreviewCredentials.load(root)
    return f"pr-{TargetPolicy.from_config(credentials.config).pull_request_number}"


def _load(storage: Storage) -> Dict[str, Any]:
    value = storage.load_json(storage.state / STATE_FILE, default={"schemaVersion": 1, "rewards": {}, "recognition": {}})
    if not isinstance(value, dict) or value.get("schemaVersion") != 1 or not isinstance(value.get("rewards"), dict) or not isinstance(value.get("recognition"), dict):
        raise ResearchRewardError("research reward state is invalid")
    return value


def _reward(storage: Storage, reward_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    state = _load(storage)
    reward = state["rewards"].get(reward_id)
    if not isinstance(reward, dict):
        raise ResearchRewardError("research reward is unavailable")
    return state, reward


def _ensure_season_cap(state: Dict[str, Any], season: str, actor_id: str, kind: str) -> None:
    for reward in state["rewards"].values():
        if isinstance(reward, dict) and reward.get("season") == season and reward.get("actorId") == actor_id and reward.get("kind") == kind:
            raise ResearchRewardError("this player already has this reward kind for the current Preview season")


def _award_recognition(state: Dict[str, Any], season: str, actor_id: str, reward_id: str, kind: str, observed_at: str) -> None:
    profile = state["recognition"].setdefault(actor_id, {"researchXp": 0, "badges": [], "awards": []})
    if reward_id in profile["awards"]:
        return
    profile["researchXp"] += AMOUNTS[kind]
    profile["badges"] = sorted(set([*profile["badges"], BADGES[kind]]))
    profile["awards"].append(reward_id)
    profile["lastAwardedAt"] = observed_at


def _append_club_once(storage: Storage, row: Dict[str, Any]) -> None:
    path = storage.channel_path("club")
    if not any(existing.get("id") == row["id"] for existing in storage.read_jsonl(path)):
        storage.append_jsonl(path, row)


def _balance(value: Any) -> float:
    amount = value.get("virtualCredits") if isinstance(value, dict) else None
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
        raise PreviewWriteError("reward recipient virtual-credit balance is invalid")
    return float(amount)


def _public_reward(reward: Dict[str, Any]) -> Dict[str, Any]:
    return {key: reward.get(key) for key in (
        "rewardId", "season", "probeId", "actorId", "kind", "amount", "unit", "status",
        "offeredBy", "offeredAt", "respondedAt", "approvedAt", "appliedAt", "readBack", "remoteWrites",
    )}


def _now() -> str:
    return isoformat(datetime.now(timezone.utc))
