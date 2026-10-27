"""Launch one bounded economical Codex worker for a Riley staff contract."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Dict

from .staff_worker_dispatch import active_packet, claim_next, report
from .storage import Storage
from .time import isoformat, parse_instant


LAUNCHER_NAME = "codex-cli-economical-staff-worker"


def _reference(contract_key: str) -> str:
    return hashlib.sha256(contract_key.encode("utf-8")).hexdigest()[:16]


def worker_prompt(*, root: Path, worker_id: str, packet: Dict[str, Any]) -> str:
    """Build a bounded prompt for one non-browser staff interpretation."""
    return f"""You are Riley, one economical PodPlay Club staff worker.

Work only on this active local staff contract:
- worker: {worker_id}
- contract: {packet['contractKey']}
- initiative: {packet['intentId']}
- requested staff action: {packet.get('staffAction')}
- blocker: {packet.get('blocker')}

Read the current secret-free initiative state and Riley's existing staff watch.
If the normal Riley follow-up remains appropriate, run exactly:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m podplay_sim_club.cli staff-follow-up route --intent {packet['intentId']}`

That command may write one normal, non-coercive local community/staff follow-up. It does not book, quote, invite, pay, check in, claim attendance, or use any product API. If the situation is stale, ambiguous, already watched, or needs a coordinator review instead, do not route it.

End this one turn with exactly one command:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m podplay_sim_club.cli staff-dispatch report --contract {packet['contractKey']} --worker {worker_id} --outcome <routed|pass|blocked> --diary "<secret-free factual line>"`

Do not open a browser, use integrations, edit repository code, expose credentials/private URLs, or retry a different initiative.
"""


def _command(*, executable: str, root: Path, prompt: str) -> list[str]:
    return [executable, "exec", "--ephemeral", "--model", "gpt-6-luna", "--sandbox", "workspace-write", "--cd", str(root), prompt]


def launch(root: Path, *, worker_id: str, execute: bool) -> Dict[str, Any]:
    storage = Storage(root)
    if not execute:
        state = storage.load_json(storage.state / "staff-worker-contracts.json", default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        queued = sum(1 for item in contracts.values() if isinstance(item, dict) and item.get("status") == "queued") if isinstance(contracts, dict) else 0
        return {"action": "staff_worker_launch_preview", "queuedContracts": queued, "remoteWrites": 0}
    claimed = claim_next(root, worker_id=worker_id)
    if claimed.get("action") == "pass":
        return claimed
    packet = active_packet(root, worker_id=worker_id)
    if packet.get("action") != "staff_worker_active":
        raise ValueError("claimed staff contract did not produce an active packet")
    executable = shutil.which("codex")
    if executable is None:
        report(root, contract_key=packet["contractKey"], worker_id=worker_id, outcome="blocked", diary_line="Economical staff worker launcher was unavailable before Riley could review the initiative.")
        raise ValueError("local Codex CLI is unavailable")
    command = [sys.executable, str(root / "tools" / "sim-browser" / "codex-staff-worker-runner.py"), "--worker", worker_id, "--contract", packet["contractKey"]]
    try:
        process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        report(root, contract_key=packet["contractKey"], worker_id=worker_id, outcome="blocked", diary_line="Economical staff worker process could not start before Riley could review the initiative.")
        raise ValueError("economical staff worker process could not start") from exc
    with storage.writer_lock():
        state = storage.load_json(storage.state / "staff-worker-contracts.json", default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        contract = contracts.get(packet["contractKey"]) if isinstance(contracts, dict) else None
        if not isinstance(contract, dict) or contract.get("status") != "active" or contract.get("workerId") != worker_id:
            raise ValueError("staff contract changed while launcher was starting")
        contract["launcher"] = {"kind": LAUNCHER_NAME, "modelTier": "economical", "launchedAt": isoformat(parse_instant(None)), "processId": process.pid, "launchRef": _reference(packet["contractKey"])}
        storage.write_json(storage.state / "staff-worker-contracts.json", {"schemaVersion": 1, "contracts": contracts})
    return {"action": "economical_staff_worker_started", "contractKey": packet["contractKey"], "workerId": worker_id, "launchRef": _reference(packet["contractKey"]), "remoteWrites": 0}


def reap(root: Path) -> Dict[str, Any]:
    """Report, but never retry, an exited staff worker without a receipt."""
    storage = Storage(root)
    state = storage.load_json(storage.state / "staff-worker-contracts.json", default={})
    contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
    candidates = [item for item in contracts.values() if isinstance(item, dict) and item.get("status") == "active" and isinstance(item.get("workerId"), str) and isinstance(item.get("launcher"), dict) and item["launcher"].get("kind") == LAUNCHER_NAME and isinstance(item["launcher"].get("processId"), int)] if isinstance(contracts, dict) else []
    reaped = []
    for contract in candidates:
        try:
            os.kill(contract["launcher"]["processId"], 0)
        except ProcessLookupError:
            report(root, contract_key=contract["contractKey"], worker_id=contract["workerId"], outcome="blocked", diary_line="Economical staff worker exited without a final Riley receipt; no staff action is inferred.")
            reaped.append(contract["contractKey"])
        except PermissionError:
            continue
    return {"action": "staff_worker_processes_reaped", "contractKeys": reaped, "remoteWrites": 0}
