"""Secret-free handoff from a browser job to one economical Sim worker.

The Rally Engine chooses work; a separate orchestrator may later launch the
worker.  Keeping the contract durable makes that model/provider interchangeable
and prevents the Python engine from silently opening a browser or a model.
"""

from pathlib import Path
from typing import Any, Dict

from .storage import Storage
from .time import isoformat, parse_instant


STATE_FILE = "worker-contracts.json"


def queue(root: Path, *, browser_job: Dict[str, Any]) -> Dict[str, Any]:
    """Queue one idempotent operations-mode worker contract for a browser job."""
    job_key = browser_job.get("jobKey")
    actor_id = browser_job.get("actorId")
    if not isinstance(job_key, str) or not isinstance(actor_id, str):
        return {"action": "pass", "detail": "browser job has no named worker contract", "remoteWrites": 0}
    storage = Storage(root)
    observed_at = isoformat(parse_instant(None))
    contract_key = f"worker:{job_key}"
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        contracts = contracts if isinstance(contracts, dict) else {}
        existing = contracts.get(contract_key) if isinstance(contracts.get(contract_key), dict) else None
        if existing is None:
            contracts[contract_key] = {
                "contractKey": contract_key,
                "status": "queued",
                "queuedAt": observed_at,
                "actorId": actor_id,
                "browserJobKey": job_key,
                "intentId": browser_job.get("intentId"),
                "journeyAction": browser_job.get("journeyAction"),
                "playbook": browser_job.get("playbook"),
                "mode": {"research": False, "headless": True, "modelTier": "economical"},
                "allowedEvidence": ["visible_ui", "sanitized_console_network", "journey_packet", "diary_line"],
                "remoteWrites": 0,
            }
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "contracts": contracts})
    contract = contracts[contract_key]
    return {"action": "worker_contract_queued", "contractKey": contract_key, "status": contract["status"], "actorId": actor_id, "remoteWrites": 0}


def claim(root: Path, *, contract_key: str, worker_id: str) -> Dict[str, Any]:
    """Let one economical worker take only its named Sim's contract."""
    storage = Storage(root)
    observed_at = isoformat(parse_instant(None))
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        contract = contracts.get(contract_key) if isinstance(contracts, dict) else None
        if not isinstance(contract, dict) or contract.get("status") != "queued":
            raise ValueError("worker contract is unavailable")
        if not isinstance(worker_id, str) or not worker_id.startswith("economical:"):
            raise ValueError("worker must use the economical worker identity")
        contract.update({"status": "active", "workerId": worker_id, "claimedAt": observed_at})
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "contracts": contracts})
    return {"contractKey": contract_key, "actorId": contract["actorId"], "status": "active", "remoteWrites": 0}


def report(root: Path, *, contract_key: str, worker_id: str, outcome: str, diary_line: str) -> Dict[str, Any]:
    """Close one worker turn with a bounded, secret-free diary outcome."""
    if outcome not in {"visible_progress", "unexpected_ui", "blocked", "pass"}:
        raise ValueError("worker outcome is not allowed")
    if not isinstance(diary_line, str) or not diary_line.strip() or len(diary_line) > 280:
        raise ValueError("worker diary line must be concise")
    storage = Storage(root)
    observed_at = isoformat(parse_instant(None))
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        contract = contracts.get(contract_key) if isinstance(contracts, dict) else None
        if not isinstance(contract, dict) or contract.get("status") != "active" or contract.get("workerId") != worker_id:
            raise ValueError("only the claiming worker may report")
        contract.update({"status": "reported", "reportedAt": observed_at, "outcome": outcome, "diaryLine": diary_line.strip()})
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "contracts": contracts})
        storage.append_jsonl(storage.actor_journal_path(str(contract["actorId"])), {
            "observedAt": observed_at, "kind": "worker_journey", "contractKey": contract_key,
            "outcome": outcome, "text": diary_line.strip(), "remoteWrites": 0,
        })
    return {"contractKey": contract_key, "status": "reported", "remoteWrites": 0}
