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
import re
import select
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from podplay_sim_club.storage import Storage  # noqa: E402
from podplay_sim_club.time import isoformat, parse_instant  # noqa: E402
from podplay_sim_club.worker_dispatch import active_packet  # noqa: E402


def _safe_ref(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _run(command: list[str], *, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
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
    return completed


def _start_handoff(*, actor_id: str, lifetime_seconds: int) -> tuple[subprocess.Popen[str], int]:
    """Start the existing one-time credential server without relaying its data."""
    process = subprocess.Popen(
        [
            sys.executable, "-u", "-m", "podplay_sim_club.cli", "preview", "browser-credential-handoff",
            "--actor", actor_id, "--lifetime", str(lifetime_seconds),
        ],
        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    port = None
    saw_output = False
    try:
        while time.monotonic() < deadline:
            if process.stdout is None:
                break
            # The handoff announces a short human-readable banner before its
            # machine-readable port.  Once the first line is buffered,
            # select() no longer reports the pipe as readable even though
            # TextIO still holds the remaining banner lines.  Child unbuffered
            # output plus direct reads after the first line avoids treating a
            # healthy handoff as a login failure.
            ready = [process.stdout] if saw_output else select.select([process.stdout], [], [], 0.25)[0]
            if ready:
                line = process.stdout.readline()
                saw_output = True
                matched = re.fullmatch(r"port: ([0-9]{1,5})\n?", line)
                if matched:
                    port = int(matched.group(1))
                    break
            if process.poll() is not None:
                break
    except Exception:
        process.terminate()
        process.wait(timeout=5)
        raise
    if port is None:
        process.terminate()
        process.wait(timeout=5)
        raise RuntimeError("local credential handoff did not become ready")
    return process, port


def _stop_handoff(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def bootstrap(
    *,
    worker_id: str,
    timeout_seconds: int,
    observe_seconds: int,
    login: bool,
    handoff_id: str | None,
) -> dict[str, Any]:
    if timeout_seconds < 30 or timeout_seconds > 180:
        raise ValueError("timeout seconds must be between 30 and 180")
    if observe_seconds < 1 or observe_seconds > min(15, timeout_seconds):
        raise ValueError("observe seconds must be between 1 and 15 and no greater than timeout")
    if handoff_id is not None and (not handoff_id.isalnum() or len(handoff_id) < 12):
        raise ValueError("handoff id must be an alphanumeric launch marker of at least 12 characters")
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
    journey_action = packet.get("journeyAction")
    invitation_intent = packet.get("intentId")
    needs_private_invitation = journey_action == "redeem_share_invitation"
    if needs_private_invitation and not isinstance(invitation_intent, str):
        raise ValueError("invitation worker is missing its local intent reference")

    reference = _safe_ref(contract_key)
    storage = Storage(ROOT)
    session_path = storage.state / "worker-sessions" / f"{reference}.json"
    evidence_directory = storage.state / "worker-evidence" / reference
    _run([
        sys.executable, str(ROOT / "tools" / "sim-browser" / "actor-session.py"),
        "--actor", actor_id, "--session", str(session_path),
    ], timeout_seconds=timeout_seconds)
    login_status = "not_requested"
    if login:
        handoff, handoff_port = _start_handoff(actor_id=actor_id, lifetime_seconds=timeout_seconds)
        try:
            _run([
                "node", str(ROOT / "tools" / "sim-browser" / "login.mjs"),
                "--session", str(session_path), "--handoff-port", str(handoff_port),
            ], timeout_seconds=timeout_seconds)
            login_status = "visible_login_settled"
        finally:
            _stop_handoff(handoff)
    # The product may complete sign-in at its ordinary root route, which can
    # expose first-use onboarding before the recipient ever sees the private
    # invitation.  Reopen the owner-delivered local mailbox item only after
    # visible login settles, using the controller's origin-checked private
    # handoff rather than retaining or printing the bearer URL here.
    if needs_private_invitation:
        _run([
            "node", str(ROOT / "tools" / "sim-browser" / "controller.mjs"), "open-private-invite",
            "--session", str(session_path), "--intent", invitation_intent,
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
        "login": login_status,
        # A public-shell diagnostic may use the same actor and contract shape.
        # The launcher-generated marker binds this receipt to the one worker
        # launch that created it, so such a diagnostic cannot satisfy a later
        # operational handoff by accident.
        "handoffId": handoff_id,
        "evidence": {"directory": str(evidence_directory.relative_to(ROOT)), "screenshot": "page.png", "summary": "evidence.json"},
        "doesNotMean": ["booking", "invitation", "payment", "check-in", "attendance", "play", "result"],
        "remoteWrites": 0,
    }
    storage.write_json(evidence_directory / "worker-receipt.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="prepare a bounded headless browser for one active economical Sim worker")
    parser.add_argument("--worker", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=150)
    parser.add_argument("--observe-seconds", type=int, default=3)
    parser.add_argument("--handoff-id", help="launcher-generated marker binding this receipt to one worker launch")
    parser.add_argument("--skip-login", action="store_true", help="capture only the public login shell; do not start a credential handoff")
    args = parser.parse_args()
    try:
        result = bootstrap(
            worker_id=args.worker, timeout_seconds=args.timeout_seconds,
            observe_seconds=args.observe_seconds, login=not args.skip_login,
            handoff_id=args.handoff_id,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR worker bootstrap: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
