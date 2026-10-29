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
import secrets
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


def _prepared_login_receipt(
    storage: Storage,
    *,
    packet: Dict[str, Any],
    handoff_id: str | None = None,
) -> bool:
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
        and (handoff_id is None or receipt.get("handoffId") == handoff_id)
    )


def _record_handoff_start(
    storage: Storage,
    *,
    contract_key: str,
    worker_id: str,
    handoff_id: str,
) -> None:
    """Persist the one launch marker before a browser is created.

    The detached runner reads this metadata back before it trusts a receipt.
    It is coordination metadata only: never a credential or product state.
    """
    with storage.writer_lock():
        state = storage.load_json(storage.state / "worker-contracts.json", default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        contract = contracts.get(contract_key) if isinstance(contracts, dict) else None
        if not isinstance(contract, dict) or contract.get("status") != "active" or contract.get("workerId") != worker_id:
            raise ValueError("worker contract changed before browser handoff")
        contract["launcher"] = {
            "kind": LAUNCHER_NAME,
            "modelTier": "economical",
            "handoffId": handoff_id,
            "handoffStartedAt": isoformat(parse_instant(None)),
        }
        storage.write_json(storage.state / "worker-contracts.json", {"schemaVersion": 1, "contracts": contracts})


def _active_handoff_id(storage: Storage, *, contract_key: str, worker_id: str) -> str | None:
    """Read the launcher-owned marker for the currently active contract."""
    state = storage.load_json(storage.state / "worker-contracts.json", default={})
    contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
    contract = contracts.get(contract_key) if isinstance(contracts, dict) else None
    launcher = contract.get("launcher") if isinstance(contract, dict) else None
    marker = launcher.get("handoffId") if isinstance(launcher, dict) else None
    return marker if isinstance(marker, str) and contract.get("workerId") == worker_id else None


def worker_prompt(*, root: Path, worker_id: str, runbook_payload: Dict[str, Any]) -> str:
    """Build a narrow, secret-free instruction for a single external worker."""
    packet = runbook_payload["launchPacket"]
    contract_key = packet["contractKey"]
    session_ref = _reference(contract_key)
    agreement = packet.get("agreement") if isinstance(packet.get("agreement"), dict) else {}
    participant_count = agreement.get("participantCount")
    duration_minutes = agreement.get("durationMinutes")
    agreement_instruction = ""
    if isinstance(participant_count, int) and participant_count > 0 and isinstance(duration_minutes, int) and duration_minutes > 0:
        agreement_instruction = (
            f" Agreement fact: {participant_count} named participants for {duration_minutes} minutes. "
            f"If the review shows another group size and the visible group controls can reach {participant_count}, "
            f"adjust only to {participant_count}, re-observe, and then continue the review."
        )
    availability_only = packet.get("journeyAction") == "review_training_availability"
    phase_instruction = (
        "This is an availability-observation phase: after the helper reaches live availability, record only what is visibly rendered. Do not select a slot, Continue, payment, or submit control."
        if availability_only else
        "This Preview booking contract has coordinator authorization for the named Sim to select the earliest future currently rendered allowlisted Open Pod slot, then wait one second and activate Continue when it visibly appears. If the current day says no availability but a rendered Next Available: Tomorrow control is available, activate it and re-observe before selecting a slot, provided that day remains inside the agreement's local window. If an earlier rendered row has no matching allowlisted control, continue scanning for the next eligible future allowlisted row instead of treating that unmapped row as a blocker. Do not treat the ordinary slot choice as an unresolved approval. For this explicitly authorized Preview run, when the agreement has no separate price cap, the rendered review total is the permitted price source; proceed through the displayed Preview payment flow only after the rendered participants, time, duration, and venue match the agreement."
        + agreement_instruction
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
1. Read `{packet['playbook']}` before any browser action.
2. Attach only to the prepared session with `tools/sim-browser/controller.mjs`.
3. Inspect the current rendered state. Use only a currently visible allowlisted control; wait and re-observe through normal loading transitions.
4. Before ending, you MUST run exactly one report command. If no visible outcome was recorded, use this safe fallback:
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m podplay_sim_club.cli worker-dispatch report --contract {contract_key} --worker {worker_id} --outcome blocked --diary "Stopped with no confirmed product outcome after the bounded visible-browser review."`

For a booking playbook, the detached runner has already completed the
allowlisted rendered region, West 37th venue, and Open Pod entry routine. It
stopped at live availability before selecting a date, slot, payment, or
booking confirmation. Begin by observing that current rendered state; do not
repeat the entry routine. The current evidence bundle was captured after that
preflight, not before it.

Normal page transitions may take a few seconds. If a needed rendered control
is initially absent or loading, wait and re-observe the current screen before
calling it unavailable. Report a blocker only after that bounded visible wait;
do not invent an underlying product or runtime error.

{phase_instruction}

The coordinator, not you, performed the visible one-time login. Use only the
isolated rendered browser and approved sim-browser controls. Do not use product APIs,
integrations, control-plane actions, direct HTTP requests, cookies,
page-script evaluation, credentials, or private URLs. Do not edit repository
code. Do not make a booking, payment, invitation, check-in, attendance, play,
score, or result claim unless that exact rendered confirmation is visible.

Record an observation only for a visible browser fact. The report command is mandatory even if the screen is ambiguous; use `blocked` rather than ending silently. Visible outcomes need the receipt created by worker-bootstrap. Do not retry an uncertain journey. Keep the diary line secret-free and factual.
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
    handoff_id = secrets.token_hex(12)
    _record_handoff_start(
        storage, contract_key=contract_key, worker_id=worker_id, handoff_id=handoff_id,
    )
    # Browser infrastructure is coordinator-owned: it starts the isolated
    # profile and completes the visible credential handoff before the cheaper
    # character worker receives its narrow playbook.
    bootstrap = subprocess.run(
        [
            sys.executable, str(root / "tools" / "sim-browser" / "worker-bootstrap.py"),
            "--worker", worker_id,
            "--handoff-id", handoff_id,
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
    if not _prepared_login_receipt(storage, packet=packet, handoff_id=handoff_id):
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
        launcher = contract.get("launcher") if isinstance(contract.get("launcher"), dict) else {}
        contract["launcher"] = {
            **launcher,
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
