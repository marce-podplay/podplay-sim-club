"""Durable, economical worker contracts for staff follow-up decisions.

Staff do not receive product authority from this queue.  A contract merely
asks one named staff Sim to decide whether to route a currently actionable
local follow-up, then record the result.  The actual route remains the normal
``staff_follow_up.tick`` protocol.
"""

from pathlib import Path
from typing import Any, Dict

from .storage import Storage
from .time import isoformat, parse_instant


STATE_FILE = "staff-worker-contracts.json"


def queue(root: Path, *, task: Dict[str, Any]) -> Dict[str, Any]:
    if task.get("action") == "pass":
        return task
    intent_id = task.get("intentId")
    signature = task.get("signature")
    actor_id = task.get("actorId")
    if not all(isinstance(value, str) and value for value in (intent_id, signature, actor_id)):
        return {"action": "pass", "detail": "staff task lacks a named intent, signature, or staff Sim", "remoteWrites": 0}
    key = f"staff:{actor_id}:{intent_id}:{signature}"
    storage = Storage(root)
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        contracts = contracts if isinstance(contracts, dict) else {}
        existing = contracts.get(key) if isinstance(contracts.get(key), dict) else None
        if existing is None:
            contracts[key] = {
                "contractKey": key, "status": "queued", "queuedAt": isoformat(parse_instant(None)),
                "actorId": actor_id, "intentId": intent_id, "signature": signature,
                "staffAction": task.get("staffAction"), "requestedFrom": task.get("requestedFrom"),
                "blocker": task.get("blocker"), "remoteWrites": 0,
            }
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "contracts": contracts})
    contract = contracts[key]
    return {"action": "staff_worker_contract_queued", "contractKey": key, "status": contract["status"], "actorId": actor_id, "remoteWrites": 0}


def claim_next(root: Path, *, worker_id: str) -> Dict[str, Any]:
    if not isinstance(worker_id, str) or not worker_id.startswith("economical:"):
        raise ValueError("staff worker must use the economical worker identity")
    storage = Storage(root)
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        queued = sorted((item for item in contracts.values() if isinstance(item, dict) and item.get("status") == "queued"), key=lambda item: (str(item.get("queuedAt") or ""), str(item.get("contractKey") or "")))
        if not queued:
            return {"action": "pass", "detail": "no queued staff worker contract", "remoteWrites": 0}
        contract = queued[0]
        contract.update({"status": "active", "workerId": worker_id, "claimedAt": isoformat(parse_instant(None))})
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "contracts": contracts})
    return {"action": "staff_worker_contract_claimed", "contractKey": contract["contractKey"], "actorId": contract["actorId"], "intentId": contract["intentId"], "remoteWrites": 0}


def active_packet(root: Path, *, worker_id: str) -> Dict[str, Any]:
    """Read one active staff contract without exposing unrelated state."""
    storage = Storage(root)
    state = storage.load_json(storage.state / STATE_FILE, default={})
    contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
    active = [item for item in contracts.values() if isinstance(item, dict) and item.get("status") == "active" and item.get("workerId") == worker_id] if isinstance(contracts, dict) else []
    if not active:
        return {"action": "pass", "detail": "staff worker has no active contract", "remoteWrites": 0}
    if len(active) != 1:
        raise ValueError("staff worker has more than one active contract")
    contract = active[0]
    return {
        "action": "staff_worker_active", "contractKey": contract["contractKey"],
        "actorId": contract["actorId"], "intentId": contract["intentId"],
        "staffAction": contract.get("staffAction"), "blocker": contract.get("blocker"),
        "requestedFrom": contract.get("requestedFrom"), "remoteWrites": 0,
    }


def report(root: Path, *, contract_key: str, worker_id: str, outcome: str, diary_line: str) -> Dict[str, Any]:
    if outcome not in {"routed", "pass", "blocked"}:
        raise ValueError("staff worker outcome is invalid")
    if not isinstance(diary_line, str) or not diary_line.strip():
        raise ValueError("staff worker diary line is required")
    storage = Storage(root)
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        contract = contracts.get(contract_key) if isinstance(contracts, dict) else None
        if not isinstance(contract, dict) or contract.get("status") != "active" or contract.get("workerId") != worker_id:
            raise ValueError("staff worker contract is unavailable")
        contract.update({"status": "reported", "outcome": outcome, "reportedAt": isoformat(parse_instant(None)), "diaryLine": diary_line.strip(), "remoteWrites": 0})
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "contracts": contracts})
        storage.append_jsonl(storage.state / "actors" / str(contract["actorId"]) / "journal.jsonl", {"kind": "staff_worker", "intentId": contract["intentId"], "outcome": outcome, "text": diary_line.strip(), "remoteWrites": 0})
    return {"action": "staff_worker_reported", "contractKey": contract_key, "status": "reported", "remoteWrites": 0}
