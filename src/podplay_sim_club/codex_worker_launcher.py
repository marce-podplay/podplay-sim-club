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
from .worker_dispatch import claim_next, claim_specific, report, runbook


LAUNCHER_NAME = "codex-cli-economical-worker"


def _reference(contract_key: str) -> str:
    return hashlib.sha256(contract_key.encode("utf-8")).hexdigest()[:16]


def _prepared_login_receipt(storage: Storage, *, packet: Dict[str, Any]) -> bool:
    """Accept only the coordinator receipt for a visibly settled player login.

    A browser can render the regional landing dialog before a player is signed
    in.  Handing that page to an economical worker makes the worker appear to
    have reached booking while it cannot legitimately continue.  The launcher
    therefore treats the receipt as a strict handoff boundary, not merely an
    evidence attachment.
    """
    contract_key = packet.get("contractKey")
    actor_id = packet.get("actorId")
    if not isinstance(contract_key, str) or not isinstance(actor_id, str):
        return False
    reference = _reference(contract_key)
    receipt = storage.load_json(
        storage.state / "worker-evidence" / reference / "worker-receipt.json",
        default={},
    )
    return (
        isinstance(receipt, dict)
        and receipt.get("schemaVersion") == 1
        and receipt.get("contractRef") == reference
        and receipt.get("actorId") == actor_id
        and receipt.get("status") == "browser_ready"
        and receipt.get("login") == "visible_login_settled"
        and receipt.get("remoteWrites") == 0
    )


def worker_prompt(*, root: Path, worker_id: str, runbook_payload: Dict[str, Any]) -> str:
    """Build a narrow, secret-free instruction for a single external worker."""
    packet = runbook_payload["launchPacket"]
    contract_key = packet["contractKey"]
    session_ref = _reference(contract_key)
    availability_only = packet.get("journeyAction") == "review_training_availability"
    phase_instruction = (
        "This is an availability-observation phase: after the helper reaches live availability, record only what is visibly rendered. Do not select a slot, Continue, payment, or submit control."
        if availability_only else
        "After the helper reaches live availability, follow the bounded booking playbook and stop whenever the next decision is not clearly authorized."
    )
    return f"""You are one economical PodPlay Sim worker. Work only on the already-active contract below.

Contract (not product proof):
- worker: {worker_id}
- contract: {contract_key}
- actor: {packet['actorId']}
- browser job: {packet['browserJobKey']}
- playbook: {packet['playbook']}
- mode: headless=true, research=false
- prepared browser session: state/worker-sessions/{session_ref}.json

First run these commands from the repository root:
1. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m podplay_sim_club.cli worker-dispatch active --worker {worker_id}`
2. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m podplay_sim_club.cli worker-dispatch runbook --worker {worker_id}`
3. Read `{packet['playbook']}` before any browser action.
4. Attach only to the prepared session with `tools/sim-browser/controller.mjs`.

For a booking playbook, the detached runner has already completed the
allowlisted rendered region, West 37th venue, and Open Pod entry routine. It
stopped at live availability before selecting a date, slot, payment, or
booking confirmation. Begin by observing that current rendered state; do not
repeat the entry routine.

{phase_instruction}

The coordinator, not you, performed the visible one-time login. Use only the
isolated rendered browser and approved sim-browser controls. Do not use product APIs,
integrations, control-plane actions, direct HTTP requests, cookies,
page-script evaluation, credentials, or private URLs. Do not edit repository
code. Do not make a booking, payment, invitation, check-in, attendance, play,
score, or result claim unless that exact rendered confirmation is visible.

Record an observation only for a visible browser fact. End this one bounded turn by calling `worker-dispatch report` exactly once for contract `{contract_key}`. Visible outcomes need the receipt created by worker-bootstrap. If the UI is ambiguous or the next action is not clearly authorized, report `blocked`; do not retry. Keep the diary line secret-free and factual.
"""


def _command(*, executable: str, root: Path, prompt: str) -> list[str]:
    """Return the explicit, isolated economical-worker process command."""
    return [
        executable,
        "exec",
        "--ephemeral",
        "--model", "gpt-6-luna",
        # The worker requires loopback access only to the coordinator-created
        # isolated browser. This mode is explicitly enabled for the approved
        # character-runtime boundary; it never carries credentials or URLs in
        # the prompt.
        "--sandbox", "danger-full-access",
        "--cd", str(root),
        prompt,
    ]


def launch(root: Path, *, worker_id: str, execute: bool, contract_key: str | None = None) -> Dict[str, Any]:
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

    claimed = claim_specific(root, contract_key=contract_key, worker_id=worker_id) if contract_key else claim_next(root, worker_id=worker_id)
    if claimed.get("action") == "pass":
        return claimed
    active_runbook = runbook(root, worker_id=worker_id)
    if active_runbook.get("action") != "worker_runbook_ready":
        raise ValueError("claimed worker contract did not produce a runbook")
    packet = active_runbook["launchPacket"]
    contract_key = packet["contractKey"]
    # Browser infrastructure is coordinator-owned: it starts the isolated
    # profile and completes the visible credential handoff before the cheaper
    # character worker receives its narrow playbook.
    bootstrap = subprocess.run(
        [
            sys.executable, str(root / "tools" / "sim-browser" / "worker-bootstrap.py"),
            "--worker", worker_id,
        ],
        cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=False, timeout=190,
    )
    if bootstrap.returncode != 0:
        report(
            root, contract_key=contract_key, worker_id=worker_id, outcome="not_started",
            diary_line="Coordinator browser login did not become ready before the economical character turn.",
        )
        return {"action": "coordinator_browser_not_ready", "contractKey": contract_key, "remoteWrites": 0}
    if not _prepared_login_receipt(storage, packet=packet):
        report(
            root, contract_key=contract_key, worker_id=worker_id, outcome="not_started",
            diary_line="Coordinator did not provide a visibly settled player-login receipt before the economical character turn.",
        )
        return {"action": "coordinator_browser_not_ready", "contractKey": contract_key, "remoteWrites": 0}
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


def dispatch_once(root: Path, *, worker_id: str, execute: bool) -> Dict[str, Any]:
    """Run one observable, single-flight outer-dispatcher cycle.

    The Rally Engine only creates contracts.  A scheduler can call this
    adapter after an engine turn without teaching the engine how to spawn
    processes.  In execute mode it first reaps exited workers that supplied no
    receipt, then starts *at most one* worker if there is no active launcher.
    Preview mode is entirely read-only and answers whether the lane is ready.
    """
    storage = Storage(root)
    state = storage.load_json(storage.state / "worker-contracts.json", default={})
    contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
    contracts = contracts if isinstance(contracts, dict) else {}
    active = [
        item for item in contracts.values()
        if isinstance(item, dict) and item.get("status") == "active"
    ]
    queued = [
        item for item in contracts.values()
        if isinstance(item, dict) and item.get("status") == "queued"
    ]
    if not execute:
        return {
            "action": "dispatcher_cycle_preview",
            "detail": "pass --execute to reap one completed worker and launch at most one queued worker",
            "activeContracts": len(active),
            "queuedContracts": len(queued),
            "remoteWrites": 0,
        }

    reaped = reap(root)
    # Re-read after reaping, which may have moved a contract to coordinator
    # review.  A single-flight lane avoids silently overlapping two character
    # turns against the same local Chrome runtime.
    refreshed = storage.load_json(storage.state / "worker-contracts.json", default={})
    refreshed_contracts = refreshed.get("contracts", {}) if isinstance(refreshed, dict) else {}
    active_after_reap = [
        item for item in refreshed_contracts.values()
        if isinstance(item, dict) and item.get("status") == "active"
    ] if isinstance(refreshed_contracts, dict) else []
    if active_after_reap:
        return {
            "action": "dispatcher_waiting_for_active_worker",
            "detail": "an economical worker is still active; no overlapping worker was started",
            "activeContracts": len(active_after_reap),
            "reapedContractKeys": reaped.get("contractKeys", []),
            "remoteWrites": 0,
        }
    launched = launch(root, worker_id=worker_id, execute=True)
    return {
        "action": "dispatcher_cycle_executed",
        "reapedContractKeys": reaped.get("contractKeys", []),
        "launch": launched,
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
