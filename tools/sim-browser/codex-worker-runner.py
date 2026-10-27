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

from podplay_sim_club.codex_worker_launcher import _command, _prepared_login_receipt, worker_prompt  # noqa: E402
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
                "--session", str(session), "--seconds", "2", "--out", str(evidence_directory),
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
        if not _prepared_login_receipt(storage, packet=packet):
            report(
                ROOT, contract_key=contract_key, worker_id=worker_id,
                outcome="not_started",
                diary_line="Coordinator did not provide a visibly settled player-login receipt before the economical character turn.",
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
        completed = subprocess.run(
            _command(executable=executable, root=ROOT, prompt=worker_prompt(root=ROOT, worker_id=worker_id, runbook_payload=active)),
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
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
