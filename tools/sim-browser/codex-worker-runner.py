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
