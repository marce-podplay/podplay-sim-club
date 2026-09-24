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
from .preview_evaluation import (
    PreviewBookingEvaluator,
    PreviewEvaluationError,
    summarize_booking_preview,
)
from .preview_funding import apply_funding_plan, build_funding_plan
from .preview_payment import (
    PreviewPaymentMethodWriter,
    load_test_stripe_secret,
    wait_for_payment_method,
)
from .preview_readonly import PreviewReadError, PreviewReadonlyClient, collection_items
from .preview_readiness import inspect_preview_readiness
from .preview_seed import apply_identity_seed, build_seed_plan
from .preview_write import PreviewWriteError
from .server import serve
from .storage import Storage
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
    preview_subcommands.add_parser(
        "booking-preview", help="calculate one non-persisting baseline booking preview"
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
    preview_fund = preview_subcommands.add_parser(
        "fund", help="plan or reconcile bounded virtual credits for the captains"
    )
    preview_fund_mode = preview_fund.add_mutually_exclusive_group(required=True)
    preview_fund_mode.add_argument("--dry-run", action="store_true")
    preview_fund_mode.add_argument("--apply", action="store_true")
    preview_fund.add_argument(
        "--confirm-origin",
        help="required with --apply; must equal the exact preview origin",
    )
    preview_payment = preview_subcommands.add_parser(
        "payment-method", help="plan or attach Stripe test Visa methods to the captains"
    )
    preview_payment_mode = preview_payment.add_mutually_exclusive_group(required=True)
    preview_payment_mode.add_argument("--dry-run", action="store_true")
    preview_payment_mode.add_argument("--apply", action="store_true")
    preview_payment.add_argument(
        "--confirm-origin",
        help="required with --apply; must equal the exact preview origin",
    )
    preview_payment.add_argument(
        "--stripe-env",
        type=Path,
        help="ignored env file containing STRIPE_SECRET_KEY; required with --apply",
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
        if args.preview_command == "booking-preview":
            return evaluate_booking_preview(root)
        if args.preview_command == "fund":
            return fund_preview(root, args.apply, args.confirm_origin)
        if args.preview_command == "payment-method":
            return seed_payment_methods(
                root, args.apply, args.confirm_origin, args.stripe_env
            )
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
    status = "READY" if report["readyForBookingPreview"] else "BLOCKED"
    print(f"booking preview gate: {status}")
    if report["blockers"]:
        print("blockers: " + ", ".join(report["blockers"]))
    print("writes: 0 remote; sanitized report: state/preview-readiness.json")
    return 0 if report["readyForBookingPreview"] else 1


def evaluate_booking_preview(root: Path) -> int:
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
        if not report["readyForBookingPreview"] or not report["candidateSession"]:
            raise PreviewEvaluationError("readiness gate must pass before booking preview")
        red = identities["red-captain"]
        red_auth = FirebaseAuthenticator(
            root / "secrets" / "actor-auth" / "red-captain.json"
        ).authenticate(red.email, red.password, credentials.firebase_api_key)
        candidate = report["candidateSession"]
        requested_credits = min(
            float(report["actors"]["red-captain"]["virtualCredits"]), 25.0
        )
        response = PreviewBookingEvaluator(policy, red_auth.id_token).evaluate(
            candidate["sessionId"], candidate["tableId"], requested_credits
        )
        evaluation = summarize_booking_preview(response)
        output = {
            "schemaVersion": 1,
            "observedAt": report["observedAt"],
            "pullRequest": policy.pull_request_number,
            "location": report["location"],
            "candidateSession": candidate,
            "evaluation": evaluation,
            "remoteMutations": 0,
            "evaluationPosts": 1,
        }
        storage = Storage(root)
        storage.write_json(storage.state / "preview-booking-evaluation.json", output)
    except (
        CredentialsError,
        FirebaseAuthError,
        IdentityRegistryError,
        PreviewEvaluationError,
        PreviewReadError,
        TargetPolicyError,
    ) as exc:
        print(f"ERROR booking preview: {exc}", file=sys.stderr)
        return 2

    print(f"PREVIEW BOOKING CALCULATION // PR #{policy.pull_request_number}")
    print(f"session: {candidate['startTime']} to {candidate['endTime']}")
    print(
        f"result: status={evaluation['status']}; total={evaluation['total']:.2f} "
        f"{evaluation['currency'] or ''}; errors={','.join(evaluation['errorCodes']) or 'none'}"
    )
    print(f"manual order gate: {'READY' if evaluation['readyForOrder'] else 'BLOCKED'}")
    print("remote mutations: 0; evaluation POSTs: 1")
    print("sanitized report: state/preview-booking-evaluation.json")
    return 0 if evaluation["readyForOrder"] else 1


def fund_preview(root: Path, apply: bool, confirmation: Optional[str]) -> int:
    try:
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        admin_auth = FirebaseAuthenticator(
            root / "secrets" / "preview-auth-cache.json"
        ).authenticate(
            credentials.admin_email,
            credentials.admin_password,
            credentials.firebase_api_key,
        )
        admin_client = PreviewReadonlyClient(read_policy, admin_auth.id_token)
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure()
        if apply:
            write_config = replace(
                credentials.config,
                mode=RunMode.PREVIEW_WRITE,
                preview_write_confirmation=confirmation,
            )
            write_policy = TargetPolicy.from_config(write_config)
            plan = apply_funding_plan(
                root,
                read_policy,
                write_policy,
                admin_client,
                admin_auth.id_token,
                identities,
                credentials.firebase_api_key,
            )
        else:
            plan, _ = build_funding_plan(
                root,
                read_policy,
                admin_client,
                identities,
                credentials.firebase_api_key,
            )
            plan["writes"] = 0
    except (
        CredentialsError,
        FirebaseAuthError,
        IdentityRegistryError,
        PreviewReadError,
        PreviewWriteError,
        TargetPolicyError,
    ) as exc:
        print(f"ERROR preview funding: {exc}", file=sys.stderr)
        return 2

    print(
        f"PREVIEW CLUB CREDITS // {'APPLIED' if apply else 'DRY RUN'} // "
        f"PR #{read_policy.pull_request_number}"
    )
    for actor_id, row in plan["actors"].items():
        suffix = f"; verified={row['verified']:.2f}" if "verified" in row else ""
        print(
            f"- {actor_id}: current={row['current']:.2f}; desired={row['desired']:.2f}; "
            f"increment={row['increment']:.2f}{suffix}"
        )
    planned = sum(1 for row in plan["actors"].values() if row["increment"] > 0)
    print(f"writes: {plan['writes']}; writes planned: {planned if not apply else 0}")
    return 0


def seed_payment_methods(
    root: Path,
    apply: bool,
    confirmation: Optional[str],
    stripe_env: Optional[Path],
) -> int:
    try:
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure()
        actor_rows = {}
        actor_sessions = {}
        for actor_id in ("red-captain", "blue-captain"):
            identity = identities[actor_id]
            auth = FirebaseAuthenticator(
                root / "secrets" / "actor-auth" / f"{actor_id}.json"
            ).authenticate(identity.email, identity.password, credentials.firebase_api_key)
            client = PreviewReadonlyClient(read_policy, auth.id_token)
            payment = client.get("/apis/v2/users/current/payment-method")
            element = payment.get("paymentElement") if isinstance(payment, dict) else None
            present = bool(isinstance(element, dict) and element.get("id")) or bool(
                isinstance(payment, dict)
                and (payment.get("preferredCard") or payment.get("preferredBankAccount"))
            )
            actor_rows[actor_id] = {"present": present, "action": "reuse" if present else "add"}
            actor_sessions[actor_id] = (auth, client)
        writes = 0
        if apply:
            if stripe_env is None:
                raise PreviewWriteError("--stripe-env is required with --apply")
            stripe_secret = load_test_stripe_secret(stripe_env.resolve())
            write_config = replace(
                credentials.config,
                mode=RunMode.PREVIEW_WRITE,
                preview_write_confirmation=confirmation,
            )
            write_policy = TargetPolicy.from_config(write_config)
            for actor_id, row in actor_rows.items():
                if row["present"]:
                    row["verified"] = True
                    continue
                auth, client = actor_sessions[actor_id]
                PreviewPaymentMethodWriter(
                    write_policy, auth.id_token, stripe_secret
                ).add_visa()
                writes += 1
                row["verified"] = wait_for_payment_method(client)
                if not row["verified"]:
                    raise PreviewWriteError(
                        f"payment-method read-back timed out for {actor_id}"
                    )
    except (
        CredentialsError,
        FirebaseAuthError,
        IdentityRegistryError,
        PreviewReadError,
        PreviewWriteError,
        TargetPolicyError,
    ) as exc:
        print(f"ERROR preview payment method: {exc}", file=sys.stderr)
        return 2

    print(
        f"PREVIEW CLUB PAYMENT METHODS // {'APPLIED' if apply else 'DRY RUN'} // "
        f"PR #{read_policy.pull_request_number}"
    )
    for actor_id, row in actor_rows.items():
        verified = f"; verified={row['verified']}" if "verified" in row else ""
        print(f"- {actor_id}: {row['action']}{verified}")
    planned = sum(1 for row in actor_rows.values() if row["action"] == "add")
    print(f"writes: {writes}; writes planned: {planned if not apply else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
