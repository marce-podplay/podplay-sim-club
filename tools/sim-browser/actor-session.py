#!/usr/bin/env python3
"""Start an isolated actor browser without printing the configured origin."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from podplay_sim_club.credentials import PreviewCredentials  # noqa: E402
from podplay_sim_club.target_policy import TargetPolicy  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actor", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--path", default="/login?loginMode=password")
    parser.add_argument(
        "--invite-intent",
        help="open this actor's privately delivered invitation in a fresh browser without printing its URL",
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="open a visible supervised research window; default is headless isolated rendering",
    )
    args = parser.parse_args()
    origin = TargetPolicy.from_config(PreviewCredentials.load(ROOT).config).target_origin
    path = args.path
    if args.invite_intent:
        from podplay_sim_club.storage import Storage

        storage = Storage(ROOT)
        mailboxes = storage.load_json(storage.state / "secure-channel" / "recipient-mailboxes.json", default={})
        key = f"{args.actor}:{args.invite_intent}"
        mailbox = mailboxes.get(key) if isinstance(mailboxes, dict) else None
        invitation = mailbox.get("invitationUrl") if isinstance(mailbox, dict) else None
        if (
            not isinstance(mailbox, dict)
            or mailbox.get("status") != "delivered"
            or mailbox.get("recipientActorId") != args.actor
            or mailbox.get("intentId") != args.invite_intent
            or not isinstance(invitation, str)
        ):
            raise SystemExit("sim-browser: no delivered private invitation is available for this actor")
        parsed = urlparse(invitation)
        expected = urlparse(origin)
        if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc) or not parsed.path.startswith("/invites/"):
            raise SystemExit("sim-browser: private invitation is not on the approved product origin")
        path = parsed.path
    command = [
        "node", str(ROOT / "tools" / "sim-browser" / "controller.mjs"), "start",
        "--actor", args.actor, "--origin", origin, "--path", path,
        "--session", args.session,
    ]
    if args.headed:
        command.append("--headed")
    result = subprocess.run(
        command,
        cwd=str(ROOT),
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
