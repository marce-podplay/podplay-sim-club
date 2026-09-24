"""Repository command-line interface."""

import argparse
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import time
from typing import List, Optional, Tuple

from .config import ClubConfig, ConfigurationError, RunMode
from .credentials import CredentialsError, PreviewCredentials
from .firebase_auth import FirebaseAuthError, FirebaseAuthenticator
from .identity_registry import IdentityRegistry, IdentityRegistryError
from .orchestrator import Orchestrator
from .preview_readonly import PreviewReadError, PreviewReadonlyClient, collection_items
from .preview_readiness import inspect_preview_readiness
from .preview_seed import apply_identity_seed, build_seed_plan
from .preview_write import PreviewWriteError
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

    preview = subparsers.add_parser(
        "preview", help="perform bounded operations against an approved PR preview"
    )
    preview_subcommands = preview.add_subparsers(dest="preview_command", required=True)
    preview_subcommands.add_parser(
        "inspect", help="authenticate and summarize the preview using GET requests only"
    )
    preview_subcommands.add_parser(
        "readiness", help="inspect actors, payment state, and one legal future slot"
    )
    preview_seed = preview_subcommands.add_parser(
        "seed", help="plan or create the stable Preview Club identities"
    )
    preview_seed_mode = preview_seed.add_mutually_exclusive_group(required=True)
    preview_seed_mode.add_argument("--dry-run", action="store_true")
    preview_seed_mode.add_argument("--apply", action="store_true")
    preview_seed.add_argument(
        "--confirm-origin",
        help="required with --apply; must equal the exact preview origin",
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

    if args.command == "preview":
        if args.preview_command == "inspect":
            return inspect_preview(root)
        if args.preview_command == "readiness":
            return inspect_readiness(root)
        if args.preview_command == "seed":
            return seed_preview(
                root,
                apply=args.apply,
                confirmation=args.confirm_origin,
            )
        raise AssertionError(f"unhandled preview command {args.preview_command}")

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
                config.mode is RunMode.PREVIEW_READONLY,
                "GET-only adapter"
                if config.mode is RunMode.PREVIEW_READONLY
                else "preview-write adapter is not implemented",
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


def inspect_preview(root: Path) -> int:
    try:
        credentials = PreviewCredentials.load(root)
        policy = TargetPolicy.from_config(credentials.config)
        auth = FirebaseAuthenticator(
            root / "secrets" / "preview-auth-cache.json"
        ).authenticate(
            credentials.admin_email,
            credentials.admin_password,
            credentials.firebase_api_key,
        )
        client = PreviewReadonlyClient(policy, auth.id_token)
        tenant = client.get("/apis/v2/tenants/current")
        current_user = client.get("/apis/v2/users/current")
        areas = collection_items(client.get("/apis/v2/areas"))
        if len(areas) > 50:
            raise PreviewReadError("preview contains more than the 50-area inspection limit")
        area_rows = []
        for area in areas:
            if not isinstance(area, dict) or not isinstance(area.get("id"), str):
                raise PreviewReadError("preview area response has an unexpected shape")
            pods = collection_items(client.get(f"/apis/v2/areas/{area['id']}/pods"))
            area_rows.append((area.get("displayName") or area.get("name") or area["id"], len(pods)))
    except (
        CredentialsError,
        FirebaseAuthError,
        PreviewReadError,
        TargetPolicyError,
    ) as exc:
        print(f"ERROR preview inspection: {exc}", file=sys.stderr)
        return 2

    tenant_name = (
        tenant.get("displayName") or tenant.get("name") or tenant.get("id")
        if isinstance(tenant, dict)
        else "unknown"
    )
    roles = current_user.get("roles", []) if isinstance(current_user, dict) else []
    role_text = ", ".join(str(role) for role in roles) or "none"
    print(f"PREVIEW READ-ONLY // PR #{policy.pull_request_number}")
    print(f"target: {policy.target_origin}")
    print(f"auth: {auth.source} ({auth.email})")
    print(f"tenant: {tenant_name}")
    print(f"identity roles: {role_text}")
    print(f"areas: {len(area_rows)}")
    for name, pod_count in area_rows:
        print(f"- {name}: {pod_count} pods")
    print("writes: 0")
    return 0


def seed_preview(root: Path, apply: bool, confirmation: Optional[str]) -> int:
    try:
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        auth = FirebaseAuthenticator(
            root / "secrets" / "preview-auth-cache.json"
        ).authenticate(
            credentials.admin_email,
            credentials.admin_password,
            credentials.firebase_api_key,
        )
        client = PreviewReadonlyClient(read_policy, auth.id_token)
        registry = IdentityRegistry(root / "secrets" / "preview-actors.json")
        identities = registry.ensure()
        if apply:
            write_config = replace(
                credentials.config,
                mode=RunMode.PREVIEW_WRITE,
                preview_write_confirmation=confirmation,
            )
            write_policy = TargetPolicy.from_config(write_config)
            plan, results = apply_identity_seed(
                root,
                read_policy,
                write_policy,
                client,
                identities,
                credentials.firebase_api_key,
            )
        else:
            plan = build_seed_plan(root, client, identities)
            results = []
    except (
        CredentialsError,
        FirebaseAuthError,
        IdentityRegistryError,
        PreviewReadError,
        PreviewWriteError,
        TargetPolicyError,
    ) as exc:
        print(f"ERROR preview seed: {exc}", file=sys.stderr)
        return 2

    print(
        f"PREVIEW CLUB SEED // {'APPLIED' if apply else 'DRY RUN'} // "
        f"PR #{read_policy.pull_request_number}"
    )
    print(f"tenant: {plan['tenantName']} ({plan['tenantId']})")
    print(f"location: {plan['areaName']} / {plan['podName']}")
    print(f"activity: {plan['eventCount42Days']} events in the 42-day survey window")
    print(f"signup prerequisites: {'safe' if plan['prerequisitesOk'] else 'blocked'}")
    if apply:
        for result in results:
            print(f"- {result['actorId']}: {result['action']} and verified ({result['email']})")
        print(f"writes: {sum(1 for result in results if result['action'] == 'created')} user signups")
    else:
        for actor_id, actor in plan["actors"].items():
            print(f"- {actor_id}: {actor['status']} ({actor['email']})")
        missing = sum(1 for actor in plan["actors"].values() if actor["status"] == "missing")
        print(f"writes planned: {missing} user signups; no roles, credits, memberships, or bookings")
    return 0


def inspect_readiness(root: Path) -> int:
    try:
        credentials = PreviewCredentials.load(root)
        policy = TargetPolicy.from_config(credentials.config)
        admin_auth = FirebaseAuthenticator(
            root / "secrets" / "preview-auth-cache.json"
        ).authenticate(
            credentials.admin_email,
            credentials.admin_password,
            credentials.firebase_api_key,
        )
        admin_client = PreviewReadonlyClient(policy, admin_auth.id_token)
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure()
        report = inspect_preview_readiness(
            root,
            policy,
            admin_client,
            identities,
            credentials.firebase_api_key,
        )
    except (
        CredentialsError,
        FirebaseAuthError,
        IdentityRegistryError,
        PreviewReadError,
        TargetPolicyError,
    ) as exc:
        print(f"ERROR preview readiness: {exc}", file=sys.stderr)
        return 2

    print(f"PREVIEW CLUB READINESS // READ-ONLY // PR #{report['pullRequest']}")
    location = report["location"]
    print(
        f"location: {location['areaName']} / {location['podName']} "
        f"({location['podStatus']}, {location['availableCourtCount']} available court)"
    )
    for actor_id, actor in report["actors"].items():
        roles = ",".join(actor["roles"]) or "customer"
        payment = "saved" if actor["hasPaymentMethod"] else "missing"
        print(
            f"- {actor_id}: roles={roles}; membership={actor['membershipType']}; "
            f"payment={payment}; credits={actor['virtualCredits']:.2f}; "
            f"strategy={actor['booking']['strategy']}"
        )
    candidate = report["candidateSession"]
    if candidate:
        print(
            f"candidate: {candidate['startTime']} to {candidate['endTime']} "
            f"({candidate['periodType']}, rate={candidate['rate']:.2f})"
        )
    else:
        print("candidate: none found from day +2 through day +14")
    print(f"waiver: {'required' if report['waiverRequired'] else 'not required by tenant settings'}")
    status = "READY" if report["readyForManualMatch"] else "BLOCKED"
    print(f"manual match gate: {status}")
    if report["blockers"]:
        print("blockers: " + ", ".join(report["blockers"]))
    print("writes: 0 remote; sanitized report: state/preview-readiness.json")
    return 0 if report["readyForManualMatch"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
