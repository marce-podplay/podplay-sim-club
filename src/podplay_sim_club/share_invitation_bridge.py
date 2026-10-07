"""Lead bridge for previously authorized player share-invitation actions."""

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .storage import Storage
from .time import isoformat


def advance(
    root: Path, task: Dict[str, Any], *, now: datetime,
    apply_preview: bool = False, confirmation: Optional[str] = None,
    capture: Optional[Callable[[Path, str, str], int]] = None,
    redeem: Optional[Callable[[Path, str, str, Optional[str]], int]] = None,
) -> Dict[str, Any]:
    """Use an existing adapter only after a durable player choice.

    No product write happens without the engine's explicit apply mode and the
    exact Preview confirmation. The called adapter is responsible for its
    product read-back and for never emitting the private URL.
    """
    if not isinstance(task, dict) or task.get("action") == "pass":
        return {"action": "pass", "detail": "no player-authorized share invitation awaits the lead", "remoteWrites": 0}
    action, intent_id = task.get("action"), task.get("intentId")
    if action not in {"capture_share_invitation", "redeem_share_invitation"} or not isinstance(intent_id, str):
        return {"action": "pass", "detail": "share invitation task is invalid", "remoteWrites": 0}
    if not apply_preview:
        return _record(root, now, intent_id, action, "awaiting_preview_apply", "player decision is durable; private invitation handoff awaits an explicit apply tick")
    if not isinstance(confirmation, str) or not confirmation.strip():
        return _record(root, now, intent_id, action, "apply_refused", "share invitation apply requires the exact Preview origin")
    if capture is None or redeem is None:
        from .cli import open_preview_share_invitation, redeem_preview_training_invitation
        capture = capture or open_preview_share_invitation
        redeem = redeem or redeem_preview_training_invitation
    exit_code = (
        capture(root, intent_id, confirmation)
        if action == "capture_share_invitation"
        else redeem(root, intent_id, confirmation, task.get("recipientActorId"))
    )
    return _record(
        root, now, intent_id, action,
        "read_back_complete" if exit_code == 0 else "apply_failed",
        "player share invitation adapter completed and persisted its read-back" if exit_code == 0 else "player share invitation adapter did not complete",
        # CLI adapters deliberately return only an exit status. Do not turn a
        # successful idempotent read-back into a fabricated write count.
        remote_writes=0,
    )


def _record(root: Path, now: datetime, intent_id: str, task_action: str, action: str, detail: str, *, remote_writes: int = 0) -> Dict[str, Any]:
    storage = Storage(root)
    row = {"observedAt": isoformat(now), "intentId": intent_id, "task": task_action, "action": action, "detail": detail, "remoteWrites": remote_writes}
    with storage.writer_lock():
        state = storage.load_json(storage.state / "share-invitation-bridge.json", default={})
        attempts = state.get("attempts", []) if isinstance(state, dict) else []
        storage.write_json(storage.state / "share-invitation-bridge.json", {"schemaVersion": 1, "lastAttempt": row, "attempts": (attempts + [row])[-100:]})
    return {"actorId": "lead", **row}
