"""Launch one bounded economical Sim worker through the local Codex CLI.

This is deliberately outside the Rally Engine.  The engine creates durable
contracts; this adapter is an opt-in outer-dispatcher bridge which may turn one
already-runnable contract into a short-lived Codex worker.  It never supplies
credentials, product URLs, or product APIs to that worker.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Dict

from .storage import Storage
from .time import isoformat, parse_instant
from .worker_dispatch import claim_next, report, runbook


LAUNCHER_NAME = "codex-cli-economical-worker"


def _reference(contract_key: str) -> str:
    return hashlib.sha256(contract_key.encode("utf-8")).hexdigest()[:16]


def worker_prompt(*, root: Path, worker_id: str, runbook_payload: Dict[str, Any]) -> str:
    """Build a narrow, secret-free instruction for a single external worker."""
    packet = runbook_payload["launchPacket"]
    contract_key = packet["contractKey"]
    return f"""You are one economical PodPlay Sim worker. Work only on the already-active contract below.

Contract (not product proof):
- worker: {worker_id}
- contract: {contract_key}
- actor: {packet['actorId']}
- browser job: {packet['browserJobKey']}
- playbook: {packet['playbook']}
- mode: headless=true, research=false

First run these commands from the repository root:
1. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m podplay_sim_club.cli worker-dispatch active --worker {worker_id}`
2. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m podplay_sim_club.cli worker-dispatch runbook --worker {worker_id}`
3. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python tools/sim-browser/worker-bootstrap.py --worker {worker_id}`
4. Read `{packet['playbook']}` before any browser action.

Use only the isolated rendered browser and approved sim-browser controls. Do not use product APIs, integrations, control-plane actions, direct HTTP requests, cookies, page-script evaluation, credentials, or private URLs. Do not edit repository code. Do not make a booking, payment, invitation, check-in, attendance, play, score, or result claim unless that exact rendered confirmation is visible.

Record an observation only for a visible browser fact. End this one bounded turn by calling `worker-dispatch report` exactly once for contract `{contract_key}`. Visible outcomes need the receipt created by worker-bootstrap. If the UI is ambiguous or the next action is not clearly authorized, report `blocked`; do not retry. Keep the diary line secret-free and factual.
"""


def _command(*, executable: str, root: Path, prompt: str) -> list[str]:
    """Return the explicit, isolated economical-worker process command."""
    return [
        executable,
        "exec",
        "--ephemeral",
        "--model", "gpt-6-luna",
        "--sandbox", "danger-full-access",
        "--ask-for-approval", "never",
        "--cd", str(root),
        prompt,
    ]


def launch(root: Path, *, worker_id: str, execute: bool) -> Dict[str, Any]:
    """Claim and optionally start exactly one routine Codex worker.

    ``execute=False`` is deliberately non-mutating: callers can safely check
    whether work is queued.  ``execute=True`` is an explicit operational act;
    after a process starts, the contract remains active until that worker gives
    a receipt.  A failed process start is the one safe case that is recorded as
    ``not_started`` because no worker process ever received a browser task.
    """
    storage = Storage(root)
    if not execute:
        state = storage.load_json(storage.state / "worker-contracts.json", default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        queued = sum(1 for item in contracts.values() if isinstance(item, dict) and item.get("status") == "queued")
        return {
            "action": "worker_launch_preview",
            "detail": "pass --execute to atomically claim and start one economical Codex worker",
            "queuedContracts": queued,
            "remoteWrites": 0,
        }

    claimed = claim_next(root, worker_id=worker_id)
    if claimed.get("action") == "pass":
        return claimed
    active_runbook = runbook(root, worker_id=worker_id)
    if active_runbook.get("action") != "worker_runbook_ready":
        raise ValueError("claimed worker contract did not produce a runbook")
    packet = active_runbook["launchPacket"]
    contract_key = packet["contractKey"]
    executable = shutil.which("codex")
    if executable is None:
        # No model process has started, so this is safely retryable.
        report(
            root, contract_key=contract_key, worker_id=worker_id, outcome="not_started",
            diary_line="Economical worker launcher was unavailable before the browser journey began.",
        )
        raise ValueError("local Codex CLI is unavailable")
    # The detached runner records only a safe exit code.  It reconstructs this
    # same prompt from the active contract and never retains model output.
    command = [
        sys.executable,
        str(root / "tools" / "sim-browser" / "codex-worker-runner.py"),
        "--worker", worker_id,
        "--contract", contract_key,
    ]
    try:
        process = subprocess.Popen(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        report(
            root, contract_key=contract_key, worker_id=worker_id, outcome="not_started",
            diary_line="Economical worker process could not start before the browser journey began.",
        )
        raise ValueError("economical worker process could not start") from exc

    observed_at = isoformat(parse_instant(None))
    launch_ref = _reference(contract_key)
    with storage.writer_lock():
        state = storage.load_json(storage.state / "worker-contracts.json", default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        contract = contracts.get(contract_key) if isinstance(contracts, dict) else None
        if not isinstance(contract, dict) or contract.get("status") != "active" or contract.get("workerId") != worker_id:
            raise ValueError("worker contract changed while the launcher was starting")
        contract["launcher"] = {
            "kind": LAUNCHER_NAME,
            "modelTier": "economical",
            "launchedAt": observed_at,
            "processId": process.pid,
            "launchRef": launch_ref,
        }
        storage.write_json(storage.state / "worker-contracts.json", {"schemaVersion": 1, "contracts": contracts})
    return {
        "action": "economical_worker_started",
        "contractKey": contract_key,
        "workerId": worker_id,
        "actorId": packet["actorId"],
        "launchRef": launch_ref,
        "remoteWrites": 0,
    }


def reap(root: Path) -> Dict[str, Any]:
    """Quarantine exited worker processes that never supplied a receipt.

    A dead launcher PID proves only that its Codex process is gone.  It cannot
    prove that a browser action did or did not happen, so the only safe state
    transition is the existing ``blocked`` review path.  It is deliberately
    not a retry mechanism.
    """
    storage = Storage(root)
    state = storage.load_json(storage.state / "worker-contracts.json", default={})
    contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
    candidates = []
    if isinstance(contracts, dict):
        for contract in contracts.values():
            launcher = contract.get("launcher") if isinstance(contract, dict) else None
            if (
                isinstance(contract, dict)
                and contract.get("status") == "active"
                and isinstance(launcher, dict)
                and launcher.get("kind") == LAUNCHER_NAME
                and isinstance(launcher.get("processId"), int)
                and isinstance(contract.get("contractKey"), str)
                and isinstance(contract.get("workerId"), str)
            ):
                candidates.append((contract["contractKey"], contract["workerId"], launcher["processId"]))
    reaped = []
    for contract_key, worker_id, process_id in candidates:
        try:
            os.kill(process_id, 0)
        except ProcessLookupError:
            status_ref = f"state/worker-launches/{_reference(contract_key)}.json"
            status = storage.load_json(storage.root / status_ref, default={})
            exit_detail = "" if not isinstance(status, dict) or status.get("status") != "exited" else " Detached worker exited without a final receipt."
            report(
                root, contract_key=contract_key, worker_id=worker_id, outcome="blocked",
                diary_line=("Economical worker process exited before returning its required browser receipt; no product outcome is inferred." + exit_detail),
            )
            reaped.append(contract_key)
        except PermissionError:
            # An existing process owned by another execution context is still
            # live enough to leave alone; a later coordinator can inspect it.
            continue
    return {"action": "worker_processes_reaped", "contractKeys": reaped, "remoteWrites": 0}
