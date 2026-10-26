#!/usr/bin/env python3
"""Prepare one bounded, isolated browser for an already-active Sim worker.

This helper is deliberately not a product-journey automator. It reads the
worker's local launch packet, starts the declared headless browser, captures a
short sanitized observation, and writes a local receipt. A separate worker
still interprets the named playbook and must explicitly report its outcome.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from podplay_sim_club.storage import Storage  # noqa: E402
from podplay_sim_club.time import isoformat, parse_instant  # noqa: E402
from podplay_sim_club.worker_dispatch import active_packet  # noqa: E402


def _safe_ref(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _run(command: list[str], *, timeout_seconds: int) -> None:
    """Run a local helper without relaying potentially sensitive stdout."""
    try:
        completed = subprocess.run(
            command, cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("local browser helper timed out") from exc
    if completed.returncode != 0:
        raise RuntimeError("local browser helper failed")


def bootstrap(*, worker_id: str, timeout_seconds: int, observe_seconds: int) -> dict[str, Any]:
    if timeout_seconds < 5 or timeout_seconds > 60:
        raise ValueError("timeout seconds must be between 5 and 60")
    if observe_seconds < 1 or observe_seconds > min(15, timeout_seconds):
        raise ValueError("observe seconds must be between 1 and 15 and no greater than timeout")
    packet_result = active_packet(ROOT, worker_id=worker_id)
    if packet_result.get("action") == "pass":
        return packet_result
    packet = packet_result["launchPacket"]
    mode = packet.get("mode") if isinstance(packet.get("mode"), dict) else {}
    if mode.get("headless") is not True or mode.get("research") is not False:
        raise ValueError("worker bootstrap only supports default headless operations mode")
    actor_id = packet.get("actorId")
    contract_key = packet.get("contractKey")
    if not isinstance(actor_id, str) or not isinstance(contract_key, str):
        raise ValueError("active worker packet is incomplete")

    reference = _safe_ref(contract_key)
    storage = Storage(ROOT)
    session_path = storage.state / "worker-sessions" / f"{reference}.json"
    evidence_directory = storage.state / "worker-evidence" / reference
    _run([
        sys.executable, str(ROOT / "tools" / "sim-browser" / "actor-session.py"),
        "--actor", actor_id, "--session", str(session_path),
    ], timeout_seconds=timeout_seconds)
    _run([
        "node", str(ROOT / "tools" / "sim-browser" / "controller.mjs"), "observe",
        "--session", str(session_path), "--seconds", str(observe_seconds),
        "--out", str(evidence_directory),
    ], timeout_seconds=timeout_seconds)
    receipt = {
        "schemaVersion": 1,
        "observedAt": isoformat(parse_instant(None)),
        "workerId": worker_id,
        "actorId": actor_id,
        "contractRef": reference,
        "playbook": packet.get("playbook"),
        "mode": mode,
        "status": "browser_ready",
        "evidence": {"directory": str(evidence_directory.relative_to(ROOT)), "screenshot": "page.png", "summary": "evidence.json"},
        "doesNotMean": ["authenticated", "booking", "invitation", "payment", "check-in", "attendance", "play", "result"],
        "remoteWrites": 0,
    }
    storage.write_json(evidence_directory / "worker-receipt.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="prepare a bounded headless browser for one active economical Sim worker")
    parser.add_argument("--worker", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--observe-seconds", type=int, default=3)
    args = parser.parse_args()
    try:
        result = bootstrap(worker_id=args.worker, timeout_seconds=args.timeout_seconds, observe_seconds=args.observe_seconds)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR worker bootstrap: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
