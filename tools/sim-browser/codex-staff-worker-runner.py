#!/usr/bin/env python3
"""Detached supervisor for one economical Riley staff worker."""

import argparse
import subprocess
import sys
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from podplay_sim_club.codex_staff_worker_launcher import _command, worker_prompt  # noqa: E402
from podplay_sim_club.staff_worker_dispatch import active_packet  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", required=True)
    parser.add_argument("--contract", required=True)
    args = parser.parse_args()
    packet = active_packet(ROOT, worker_id=args.worker)
    if packet.get("action") != "staff_worker_active" or packet.get("contractKey") != args.contract:
        return 2
    executable = shutil.which("codex")
    if executable is None:
        return 2
    return subprocess.run(_command(executable=executable, root=ROOT, prompt=worker_prompt(root=ROOT, worker_id=args.worker, packet=packet)), cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
