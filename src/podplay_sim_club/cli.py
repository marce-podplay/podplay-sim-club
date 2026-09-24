"""Repository command-line interface."""

import argparse
from pathlib import Path
import subprocess
import sys
import time
from typing import List, Optional, Tuple

from .config import ClubConfig, ConfigurationError, RunMode
from .orchestrator import Orchestrator
from .server import serve
from .target_policy import TargetPolicy, TargetPolicyError
from .terminal import render
from .time import parse_instant


REPO_ROOT = Path(__file__).resolve().parents[2]


def parse_duration(value: str) -> float:
    units = {"s": 1, "m": 60, "h": 3600}
    normalized = value.strip().lower()
    if not normalized:
        raise argparse.ArgumentTypeError("duration cannot be empty")
    suffix = normalized[-1]
    if suffix in units:
        number = normalized[:-1]
        multiplier = units[suffix]
    else:
        number = normalized
        multiplier = 1
    try:
        result = float(number) * multiplier
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid duration: {value!r}") from exc
    if result <= 0:
        raise argparse.ArgumentTypeError("duration must be positive")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="club", description="Operate the local PodPlay Sim Club world."
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=REPO_ROOT,
        help="repository root (defaults to this checkout)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="print one world frame")

    watch = subparsers.add_parser("watch", help="refresh the world in the terminal")
    watch.add_argument("--interval", type=float, default=2.0)

    run = subparsers.add_parser("run", help="execute one bounded simulation beat")
    run.add_argument("--turns", type=int, default=10)
    run.add_argument("--now", help="override fake preview time with an ISO instant")

    seed = subparsers.add_parser("seed", help="initialize or reconcile fake seed state")
    seed.add_argument("--now", help="override fake preview time with an ISO instant")

    serve_parser = subparsers.add_parser("serve", help="serve the local observatory")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8787)
    serve_parser.add_argument(
        "--beat-every",
        type=parse_duration,
        help="optional local beat interval, for example 30m",
    )
    serve_parser.add_argument("--turns", type=int, default=10)

    doctor = subparsers.add_parser(
        "doctor", help="validate local mode, target policy, and required files"
    )
    doctor.add_argument(
        "--mode",
        choices=[mode.value for mode in RunMode],
        help="override PODPLAY_SIM_MODE for this check",
    )
    doctor.add_argument(
        "--preview-origin",
        help="override PODPLAY_SIM_PREVIEW_ORIGIN for this check",
    )
    doctor.add_argument(
        "--allow-preview-origin",
        action="append",
        help="exact approved origin; may be repeated",
    )
    doctor.add_argument(
        "--confirm-preview-write",
        help="must repeat the exact origin when checking preview-write mode",
    )

    reset = subparsers.add_parser(
        "demo-reset", help="erase only the fake preview database for reset testing"
    )
    reset.add_argument("--confirm", action="store_true", required=True)

    subparsers.add_parser("test", help="run the dependency-free unit tests")
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.home.resolve()

    if args.command == "test":
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=str(root),
            check=False,
        )
        return result.returncode

    try:
        if args.command == "doctor":
            config = ClubConfig.from_environment(
                mode_override=args.mode,
                preview_origin_override=args.preview_origin,
                allowed_origins_override=args.allow_preview_origin,
                write_confirmation_override=args.confirm_preview_write,
            )
            return run_doctor(root, config)
        config = ClubConfig.from_environment()
    except ConfigurationError as exc:
        print(f"ERROR configuration: {exc}", file=sys.stderr)
        return 2

    if config.mode is not RunMode.FAKE:
        print(
            "ERROR preview execution is not implemented; run `club doctor` "
            "to validate the target without contacting it",
            file=sys.stderr,
        )
        return 2

    orchestrator = Orchestrator(root)

    if args.command == "status":
        print(render(orchestrator.world_view()))
        return 0

    if args.command == "watch":
        try:
            while True:
                print("\033[2J\033[H" + render(orchestrator.world_view()), end="", flush=True)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print()
            return 0

    if args.command == "run":
        result = orchestrator.run_beat(
            turns=args.turns, now=parse_instant(args.now) if args.now else None
        )
        print(
            f"{result['occurrenceKey']}: {result['status']} "
            f"({result['turnsExecuted']} turns this beat)"
        )
        print()
        print(render(result["world"]))
        return 0

    if args.command == "seed":
        now = parse_instant(args.now) if args.now else None
        view = orchestrator.world_view(now)
        print(f"{view['season']} ready; fake preview seed is reconciled")
        return 0

    if args.command == "serve":
        serve(
            root=root,
            host=args.host,
            port=args.port,
            beat_every_seconds=args.beat_every,
            turns_per_beat=args.turns,
        )
        return 0

    if args.command == "demo-reset":
        orchestrator.reset_fake_preview()
        view = orchestrator.world_view()
        print(f"fake preview refreshed; simulator moved to {view['season']}")
        return 0

    raise AssertionError(f"unhandled command {args.command}")


def run_doctor(root: Path, config: ClubConfig) -> int:
    checks: List[Tuple[str, bool, str]] = []
    if config.mode is RunMode.FAKE:
        view = Orchestrator(root).world_view()
        checks.extend(
            [
                ("mode_is_fake", view["preview"]["mode"] == "fake", ""),
                ("preview_approved", view["preview"]["approved"] is True, ""),
                ("target_policy", True, "not required in fake mode"),
                ("preview_adapter_available", True, "fake adapter"),
            ]
        )
    else:
        checks.append(("mode_is_preview", True, config.mode.value))
        try:
            policy = TargetPolicy.from_config(config)
            checks.append(
                (
                    "target_policy",
                    True,
                    f"approved PR #{policy.pull_request_number}",
                )
            )
        except TargetPolicyError as exc:
            checks.append(("target_policy", False, str(exc)))
        checks.append(
            (
                "admin_identity_configured",
                bool(config.admin_email),
                "email only; credentials stay local",
            )
        )
        checks.append(
            (
                "preview_adapter_available",
                False,
                "network adapter is intentionally not implemented yet",
            )
        )

    ignore_path = root / ".gitignore"
    ignore_text = ignore_path.read_text(encoding="utf-8") if ignore_path.is_file() else ""
    checks.extend(
        [
            ("seed_exists", (root / "seed" / "tenant.json").is_file(), ""),
            (
                "scenario_exists",
                (root / "scenarios" / "hourly-match.json").is_file(),
                "",
            ),
            (
                "credentials_ignored",
                "secrets/*" in ignore_text and ".env" in ignore_text,
                "",
            ),
        ]
    )
    for name, passed, detail in checks:
        suffix = f" ({detail})" if detail else ""
        print(f"{'PASS' if passed else 'FAIL'} {name}{suffix}")
    return 0 if all(passed for _, passed, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
