#!/usr/bin/env python3
"""Detached, secret-free process supervisor for one Codex Sim worker.

The worker's model/browser output is intentionally discarded.  The only
durable process diagnostic is an exit code, allowing the coordinator to
distinguish an active worker from an exited worker that failed to report.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

# A booking screen can render its date/pod shell before the sessions request
# settles.  This is deliberately a small human-scale pause, not a polling
# assertion: the worker receives a stable post-entry snapshot and still must
# verify the actual controls it plans to use.
AVAILABILITY_SETTLE_SECONDS = 8
FINAL_OBSERVATION_SECONDS = 3
# Keep economical character turns bounded. This is intentionally longer than
# the normal UI settle period because the model must read its playbook, inspect
# the rendered screen, and write one report; tune it only with worker timing
# evidence rather than treating it as a product timeout.
ECONOMICAL_TURN_TIMEOUT_SECONDS = 120

from podplay_sim_club.codex_worker_launcher import (  # noqa: E402
    _active_handoff_id,
    _command,
    _prepared_login_receipt,
    worker_prompt,
)
from podplay_sim_club.storage import Storage  # noqa: E402
from podplay_sim_club.worker_dispatch import report, runbook  # noqa: E402
from podplay_sim_club.time import isoformat, parse_instant  # noqa: E402


def _reference(contract_key: str) -> str:
    return hashlib.sha256(contract_key.encode("utf-8")).hexdigest()[:16]


def _prepare_booking_entry(*, storage: Storage, packet: dict[str, object]) -> bool:
    """Reach the stable, rendered availability screen before model handoff.

    Region, club, and experience selection are durable context from the
    contract, not a character decision about a volatile slot or purchase.
    Leaving this routine as a prose instruction made the cheap worker liable
    to stop on the first onboarding dialog.  Run the existing visible-browser
    helper here so both research and operations workers begin from the same
    inspected screen.  The helper deliberately stops before date, slot,
    payment, invitation, or booking confirmation.
    """
    action = packet.get("journeyAction")
    if action not in {"plan_training_booking", "review_training_availability"}:
        return True
    contract_key = packet.get("contractKey")
    actor_id = packet.get("actorId")
    worker_id = packet.get("workerId")
    if not all(isinstance(value, str) for value in (contract_key, actor_id, worker_id)):
        return False
    session = storage.state / "worker-sessions" / f"{_reference(contract_key)}.json"
    if not session.is_file():
        report(
            ROOT, contract_key=contract_key, worker_id=worker_id, outcome="blocked",
            diary_line="Coordinator could not provide the isolated rendered browser session for booking entry.",
        )
        return False
    try:
        completed = subprocess.run(
            ["node", str(ROOT / "tools" / "sim-browser" / "booking-entry.mjs"), "--session", str(session)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=100,
        )
    except (OSError, subprocess.TimeoutExpired):
        completed = None
    if completed is None or completed.returncode != 0:
        report(
            ROOT, contract_key=contract_key, worker_id=worker_id, outcome="blocked",
            diary_line="The rendered booking-entry routine did not reach live availability; no slot or booking action was attempted.",
        )
        return False
    # The bootstrap evidence necessarily precedes this entry routine. Refresh
    # it before the worker can inspect the bundle: otherwise a truthful
    # screenshot of the initial Select Region dialog can make a worker stop
    # even though the live browser has advanced to availability.
    reference = _reference(contract_key)
    evidence_directory = storage.state / "worker-evidence" / reference
    try:
        observed = subprocess.run(
            [
                "node", str(ROOT / "tools" / "sim-browser" / "controller.mjs"), "observe",
                "--session", str(session), "--seconds", str(AVAILABILITY_SETTLE_SECONDS), "--out", str(evidence_directory),
                "--card-screen", "booking live availability",
                "--card-observed", "rendered region, West 37th venue, and Open Pod entry completed",
                "--card-next", "character reviews current availability",
                "--card-outcome", "entry only; no slot, payment, or booking selected",
            ],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        observed = None
    if observed is None or observed.returncode != 0:
        report(
            ROOT, contract_key=contract_key, worker_id=worker_id, outcome="blocked",
            diary_line="Rendered booking entry reached availability but its current evidence could not be captured; no slot or booking action was attempted.",
        )
        return False
    receipt_path = evidence_directory / "worker-receipt.json"
    receipt = storage.load_json(receipt_path, default={})
    if isinstance(receipt, dict):
        receipt["entry"] = "rendered_live_availability"
        storage.write_json(receipt_path, receipt)
    return True


def _capture_final_observation(*, storage: Storage, contract_key: str) -> bool:
    """Keep a separate, sanitized end-of-turn screen for coordinator review."""
    session = storage.state / "worker-sessions" / f"{_reference(contract_key)}.json"
    if not session.is_file():
        return False
    completion_dir = storage.state / "worker-evidence" / _reference(contract_key) / "completion"
    try:
        completed = subprocess.run(
            [
                "node", str(ROOT / "tools" / "sim-browser" / "controller.mjs"), "observe",
                "--session", str(session), "--seconds", str(FINAL_OBSERVATION_SECONDS),
                "--out", str(completion_dir),
                "--card-screen", "economical worker completion",
                "--card-observed", "final bounded rendered observation after the character turn",
                "--card-next", "coordinator reads the worker report or review state",
                "--card-outcome", "observation only; no product outcome inferred",
            ],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _finalize_if_unreported(*, storage: Storage, worker_id: str, contract_key: str) -> None:
    """Close a finished model turn that omitted its mandatory report.

    The model process ending is not evidence of a booking or even of a browser
    action.  Leaving that contract active, however, prevents the dispatcher
    from making progress and eventually forces a less specific reaper path.
    Record the narrowest truthful outcome immediately: a visible journey may
    have occurred, but the character did not provide a final report.
    """
    final_observation = _capture_final_observation(storage=storage, contract_key=contract_key)
    try:
        report(
            ROOT,
            contract_key=contract_key,
            worker_id=worker_id,
            outcome="blocked",
            diary_line=(
                "Economical worker ended without its required final browser report; a final rendered observation "
                "was captured." if final_observation else
                "Economical worker ended without its required final browser report; no product outcome is inferred."
            ),
        )
    except ValueError:
        # The normal worker may have reported during the tiny interval between
        # process exit and this supervisor check.  In that case there is
        # nothing left for the supervisor to close.
        return


def run(*, worker_id: str, contract_key: str) -> int:
    storage = Storage(ROOT)
    reference = _reference(contract_key)
    status_path = storage.state / "worker-launches" / f"{reference}.json"
    result = {"schemaVersion": 1, "contractRef": reference, "workerId": worker_id, "startedAt": isoformat(parse_instant(None))}
    try:
        active = runbook(ROOT, worker_id=worker_id)
        packet = active.get("launchPacket") if isinstance(active, dict) else None
        if not isinstance(packet, dict) or packet.get("contractKey") != contract_key:
            raise ValueError("active worker contract is unavailable")
        # Defense in depth: the launcher normally verifies this before it
        # starts us, but the detached process must independently refuse a
        # public-shell-only receipt.  That prevents a stale subprocess or an
        # unexpected bootstrap path from handing a cheap character worker an
        # unauthenticated page that merely resembles Booking.
        handoff_id = _active_handoff_id(
            storage, contract_key=contract_key, worker_id=worker_id,
        )
        if handoff_id is None or not _prepared_login_receipt(
            storage, packet=packet, handoff_id=handoff_id,
        ):
            report(
                ROOT, contract_key=contract_key, worker_id=worker_id,
                outcome="not_started",
                diary_line="Coordinator did not provide this launch's visibly settled player-login receipt before the economical character turn.",
            )
            result.update({"status": "login_not_settled", "finishedAt": isoformat(parse_instant(None))})
            storage.write_json(status_path, result)
            return 2
        # Make the routine entry deterministic.  The economical model still
        # owns the character turn after this point, but never has to infer or
        # remember a region-onboarding branch before it can see availability.
        packet = {**packet, "workerId": worker_id}
        if not _prepare_booking_entry(storage=storage, packet=packet):
            result.update({"status": "booking_entry_blocked", "finishedAt": isoformat(parse_instant(None))})
            storage.write_json(status_path, result)
            return 2
        executable = shutil.which("codex")
        if executable is None:
            raise RuntimeError("local Codex CLI is unavailable")
        try:
            completed = subprocess.run(
                _command(executable=executable, root=ROOT, prompt=worker_prompt(root=ROOT, worker_id=worker_id, runbook_payload=active)),
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False, timeout=ECONOMICAL_TURN_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            _finalize_if_unreported(storage=storage, worker_id=worker_id, contract_key=contract_key)
            result.update({"status": "model_timeout", "finishedAt": isoformat(parse_instant(None))})
            storage.write_json(status_path, result)
            return 2
        _finalize_if_unreported(storage=storage, worker_id=worker_id, contract_key=contract_key)
        result.update({"status": "exited", "exitCode": completed.returncode, "finishedAt": isoformat(parse_instant(None))})
        storage.write_json(status_path, result)
        return completed.returncode
    except Exception:
        result.update({"status": "launcher_error", "finishedAt": isoformat(parse_instant(None))})
        storage.write_json(status_path, result)
        return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="supervise one detached economical Codex Sim worker")
    parser.add_argument("--worker", required=True)
    parser.add_argument("--contract", required=True)
    args = parser.parse_args()
    return run(worker_id=args.worker, contract_key=args.contract)


if __name__ == "__main__":
    raise SystemExit(main())
