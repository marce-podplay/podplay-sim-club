"""Repository command-line interface."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from .booking_intents import (
    BookingIntentError,
    load_intent_ledger,
    save_intent,
    select_intent,
)
from .actor_runtime import run_tick as run_actor_tick
from .tournament import report_winner, run_tick as run_tournament_tick
from .rally_engine import tick as run_rally_tick
from .config import ClubConfig, ConfigurationError, RunMode
from .credentials import CredentialsError, PreviewCredentials
from .communications import record_invite_sent, record_training_check_in
from .firebase_auth import FirebaseAuthError, FirebaseAuthenticator
from .identity_registry import ACTOR_NAMES, TEAM_ACTOR_NAMES, IdentityRegistry, IdentityRegistryError, TEAM_ACTORS
from .orchestrator import Orchestrator
from .players import BOOKER, TEAMMATE, actor_auth_cache, canonical_actor_id, migrate_legacy_actor_files
from .player_login import session_check_record, verify_player_session
from .preview_evaluation import (
    PreviewBookingEvaluator,
    PreviewEvaluationError,
    summarize_booking_preview,
)
from .preview_booking import (
    PreviewBookingWriter,
    find_matching_events,
    occurrence_key,
    read_back_event,
    summarize_order,
)
from .preview_funding import apply_funding_plan, build_funding_plan
from .preview_match_ledger import (
    PreviewMatchLedgerError,
    load_match_ledger,
    sanitized_match_index,
    save_match,
    select_active,
    select_match,
)
from .preview_payment import (
    PreviewPaymentMethodWriter,
    load_test_stripe_secret,
    wait_for_payment_method,
)
from .preview_owner_event import PreviewOwnerEventWriter
from .preview_owner_event import PreviewEventRescheduleWriter
from .preview_event_signup import PreviewEventSignupWriter, find_event_signup
from .preview_participation import (
    ACCEPTED_STATUSES,
    PreviewAcceptanceEvaluator,
    PreviewParticipationWriter,
    check_in_window,
    check_in_status,
    classify_match_status,
    find_actor_invitation,
    find_owner_invitation,
    summarize_acceptance,
    summarize_invitation,
)
from .preview_readonly import PreviewReadError, PreviewReadonlyClient, collection_items
from .preview_readiness import (
    EARLY_START_WINDOW_MINUTES,
    early_agreed_start,
    find_candidate_session,
    inspect_preview_readiness,
    may_start_early,
)
from .preview_seed import apply_identity_seed, build_seed_plan, load_preview_seed, seed_actor_ids
from .preview_write import PreviewWriteError
from .server import serve
from .storage import Storage
from .target_policy import TargetPolicy, TargetPolicyError
from .team_challenges import TeamChallengeError, plan_challenge, resolve_booking_owner
from .result_requests import report_finished_event_result
from .training_sessions import TrainingSessionError, accept as accept_training, propose as propose_training
from .share_invitations import (
    ShareInvitationError,
    open_share_invitation,
    record_share_invitation,
    redeem_share_invitation,
)
from .front_desk import mark_rescheduled, pending_reschedule
from .arcade import ArcadeError, attach_training_replay, simulate_and_store
from .asset_discovery import AssetDiscoveryError, discover_player_assets
from .browser_handoff import BrowserHandoffError, serve_browser_credential_handoff
from .team_signup import PreviewTeamMemberWriter
from .facts import load_facts
from .event_lifecycle import (
    ensure_planned_locked,
    record_booked_locked,
    record_invitation_locked,
    record_window_locked,
)
from .projections import rebuild_projections
from .self_assets import SelfAssetValidationError
from .terminal import render, render_needs
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

    needs = subparsers.add_parser(
        "needs", help="run or inspect the deterministic needs-driven interaction"
    )
    needs_subcommands = needs.add_subparsers(dest="needs_command", required=True)
    needs_run = needs_subcommands.add_parser(
        "run", help="advance need, proposal, agreement, and booking intent turns"
    )
    needs_run.add_argument("--turns", type=int, default=4)
    needs_run.add_argument("--now", help="override fake preview time with an ISO instant")
    needs_promote = needs_subcommands.add_parser(
        "promote", help="let Sofia announce a priority session and collect responses"
    )
    needs_promote.add_argument("--turns", type=int, default=4)
    needs_promote.add_argument("--now", help="override fake preview time with an ISO instant")
    needs_subcommands.add_parser("status", help="show needs and booking intents")

    runtime = subparsers.add_parser(
        "runtime", help="run bounded, durable actor turns (Preview writes require an explicit confirmed flag)"
    )
    runtime_subcommands = runtime.add_subparsers(dest="runtime_command", required=True)
    runtime_tick = runtime_subcommands.add_parser(
        "tick", help="run queued actor turns and save compact actor memories"
    )
    runtime_tick.add_argument("--max-turns", type=int, default=10)
    runtime_tick.add_argument("--now", help="override runtime clock with an ISO instant")
    tournament_tick = runtime_subcommands.add_parser("tournament-tick", help="advance one durable tournament decision without preview writes")
    tournament_tick.add_argument("--now", help="override runtime clock with an ISO instant")
    tournament_report = runtime_subcommands.add_parser("report-winner", help="record a designated captain's no-score tournament winner report")
    tournament_report.add_argument("--winner", required=True, choices=["Bogard-Higashi", "Japan Team"])
    tournament_report.add_argument("--reporter", required=True, choices=["andy-bogard"], help="player attesting to the result")
    tournament_report.add_argument("--now", help="override runtime clock with an ISO instant")
    event_report = runtime_subcommands.add_parser(
        "report-event-result", help="record the requested booking captain's no-score event result attestation"
    )
    event_report.add_argument("--intent", required=True, help="completed training or challenge intent ID")
    event_report.add_argument("--reporter", required=True, help="the captain named in the result request")
    event_report.add_argument("--winner", required=True, help="one named participant or roster team")
    event_report.add_argument("--now", help="override runtime clock with an ISO instant")
    rally_tick = runtime_subcommands.add_parser("rally-tick", help="run one bounded Rally Engine actor tick")
    rally_tick.add_argument("--hours", type=int, default=2, help="session lifetime on first tick (default: 2)")
    rally_tick.add_argument("--now", help="override runtime clock with an ISO instant")
    rally_tick.add_argument("--plan-preview", action="store_true", help="allow one authenticated live availability and booking evaluation")
    rally_tick.add_argument("--apply-preview", action="store_true", help="allow one guarded player-owned booking adapter invocation")
    rally_tick.add_argument("--confirm-origin", help="required with --apply-preview; exact Preview origin")

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
    preview_login = preview_subcommands.add_parser(
        "login",
        help="sign in a stored player through Firebase and verify /users/current",
    )
    preview_login.add_argument(
        "--actor",
        action="append",
        default=None,
        help="actor id to verify; repeat for more than one (default: Andy and Terry)",
    )
    preview_session_check = preview_subcommands.add_parser(
        "session-check",
        help="verify each seeded Preview Club identity with a stateless Firebase session",
    )
    preview_session_check.add_argument(
        "--actor",
        action="append",
        default=None,
        help="actor id to verify; repeat to narrow the default seeded owner, players, and staff set",
    )
    preview_subcommands.add_parser(
        "readiness", help="inspect actors, payment state, and one legal future slot"
    )
    preview_assets = preview_subcommands.add_parser(
        "discover-assets",
        help="read and persist sanitized customer assets using each player's own identity",
    )
    preview_assets.add_argument(
        "--actor",
        action="append",
        required=True,
        help="existing actor id to inspect; repeat for more than one",
    )
    preview_browser_handoff = preview_subcommands.add_parser(
        "browser-credential-handoff",
        help="serve one existing actor's login fields locally for the exact Preview browser origin",
    )
    preview_browser_handoff.add_argument("--actor", required=True, help="existing actor id")
    preview_browser_handoff.add_argument(
        "--port", type=int, default=0, help="loopback port; default selects an ephemeral port"
    )
    preview_browser_handoff.add_argument(
        "--lifetime", type=int, default=120, help="one-time handoff lifetime in seconds"
    )
    preview_venues = preview_subcommands.add_parser(
        "scan-venues", help="read-only scan of today's venues for contiguous available sessions"
    )
    preview_venues.add_argument("--duration", type=int, default=60, help="required contiguous duration in minutes")
    preview_venues.add_argument("--within", type=int, default=30, help="highlight starts within this many minutes")
    preview_subcommands.add_parser(
        "booking-preview", help="calculate one non-persisting baseline booking preview"
    )
    preview_plan_intent = preview_subcommands.add_parser(
        "plan-intent", help="map an agreed character intent to a non-persisting preview slot"
    )
    preview_plan_intent.add_argument(
        "--intent", help="booking intent ID; defaults to the active intent"
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
    preview_reschedule = preview_subcommands.add_parser(
        "reschedule", help="plan or move one Riley-approved event without creating another"
    )
    preview_reschedule_mode = preview_reschedule.add_mutually_exclusive_group(required=True)
    preview_reschedule_mode.add_argument("--dry-run", action="store_true")
    preview_reschedule_mode.add_argument("--apply", action="store_true")
    preview_reschedule.add_argument("--confirm-origin", help="required with --apply; exact preview origin")
    preview_reschedule.add_argument("--request", help="Riley request ID; defaults only when one is pending")
    preview_seed.add_argument(
        "--team", choices=sorted(TEAM_ACTORS),
        help="also seed the two current product identities for this dormant team",
    )
    preview_fund = preview_subcommands.add_parser(
        "fund", help="plan or reconcile bounded virtual credits for the players"
    )
    preview_fund_mode = preview_fund.add_mutually_exclusive_group(required=True)
    preview_fund_mode.add_argument("--dry-run", action="store_true")
    preview_fund_mode.add_argument("--apply", action="store_true")
    preview_fund.add_argument(
        "--confirm-origin",
        help="required with --apply; must equal the exact preview origin",
    )
    preview_payment = preview_subcommands.add_parser(
        "payment-method", help="plan or attach Stripe test Visa methods to the players"
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
    preview_payment.add_argument(
        "--actor", action="append", choices=sorted({**ACTOR_NAMES, **TEAM_ACTOR_NAMES}),
        help="actor to reconcile; repeat for an event roster (defaults to Andy and Terry)",
    )
    preview_book = preview_subcommands.add_parser(
        "book", help="plan or create exactly one reconciled Preview Club booking"
    )
    preview_book_mode = preview_book.add_mutually_exclusive_group(required=True)
    preview_book_mode.add_argument("--dry-run", action="store_true")
    preview_book_mode.add_argument("--apply", action="store_true")
    preview_book.add_argument(
        "--confirm-origin",
        help="required with --apply; must equal the exact preview origin",
    )
    preview_open_play = preview_subcommands.add_parser(
        "create-open-play", help="plan or create one owner-published free Open Play from an owner intent"
    )
    preview_open_play_mode = preview_open_play.add_mutually_exclusive_group(required=True)
    preview_open_play_mode.add_argument("--dry-run", action="store_true")
    preview_open_play_mode.add_argument("--apply", action="store_true")
    preview_open_play.add_argument("--confirm-origin", help="required with --apply; must equal the exact preview origin")
    preview_open_play.add_argument("--intent", help="owner Open Play intent ID; defaults to the active intent")
    preview_signup = preview_subcommands.add_parser(
        "signup-open-play", help="reconcile the two named player self-signups after an owner Open Play is promoted"
    )
    preview_signup_mode = preview_signup.add_mutually_exclusive_group(required=True)
    preview_signup_mode.add_argument("--dry-run", action="store_true")
    preview_signup_mode.add_argument("--apply", action="store_true")
    preview_signup.add_argument("--confirm-origin", help="required with --apply; must equal the exact preview origin")
    preview_signup.add_argument("--intent", help="owner Open Play intent ID; defaults to the active intent")
    preview_team_signup = preview_subcommands.add_parser("team-signup", help="book and reconcile one agreed captain-led team challenge")
    preview_team_signup_mode = preview_team_signup.add_mutually_exclusive_group(required=True)
    preview_team_signup_mode.add_argument("--dry-run", action="store_true")
    preview_team_signup_mode.add_argument("--apply", action="store_true")
    preview_team_signup.add_argument("--confirm-origin", help="required with --apply; exact preview origin")
    preview_team_signup.add_argument("--intent", required=True, help="agreed customer_team_signup intent ID")
    preview_book.add_argument(
        "--intent",
        help="execute this preview_ready character intent instead of an operator-created plan",
    )
    preview_share = preview_subcommands.add_parser("share-invitation", help="open and retain one player-owned share invitation for a booked intent")
    preview_share.add_argument("--apply", action="store_true", required=True)
    preview_share.add_argument("--confirm-origin", required=True, help="exact preview origin")
    preview_share.add_argument("--intent", required=True, help="booked customer-training intent")
    preview_redeem_share = preview_subcommands.add_parser(
        "redeem-training-invitation",
        help="redeem and accept a retained player share invitation for a booked intent",
    )
    preview_redeem_share.add_argument("--apply", action="store_true", required=True)
    preview_redeem_share.add_argument("--confirm-origin", required=True, help="exact preview origin")
    preview_redeem_share.add_argument("--intent", required=True, help="booked intent with a retained share invitation")
    preview_redeem_share.add_argument("--recipient", help="named recipient; required when the booking has multiple teammates")
    preview_join = preview_subcommands.add_parser(
        "join", help="invite and reconcile Terry Bogard into the selected match"
    )
    preview_join_mode = preview_join.add_mutually_exclusive_group(required=True)
    preview_join_mode.add_argument("--dry-run", action="store_true")
    preview_join_mode.add_argument("--apply", action="store_true")
    preview_join.add_argument(
        "--confirm-origin",
        help="required with --apply; must equal the exact preview origin",
    )
    preview_join.add_argument(
        "--occurrence", help="ledger occurrence to join; defaults to the active match"
    )
    preview_check_in = preview_subcommands.add_parser(
        "check-in", help="reconcile player check-ins when the selected match starts"
    )
    preview_check_in_mode = preview_check_in.add_mutually_exclusive_group(required=True)
    preview_check_in_mode.add_argument("--dry-run", action="store_true")
    preview_check_in_mode.add_argument("--apply", action="store_true")
    preview_check_in.add_argument(
        "--confirm-origin",
        help="required with --apply; must equal the exact preview origin",
    )
    preview_check_in.add_argument(
        "--occurrence", help="ledger occurrence to check in; defaults to the active match"
    )
    preview_training_check_in = preview_subcommands.add_parser(
        "training-check-in", help="check one accepted training player in during the local arrival window"
    )
    preview_training_check_in_mode = preview_training_check_in.add_mutually_exclusive_group(required=True)
    preview_training_check_in_mode.add_argument("--dry-run", action="store_true")
    preview_training_check_in_mode.add_argument("--apply", action="store_true")
    preview_training_check_in.add_argument("--confirm-origin", help="required with --apply; must equal the exact preview origin")
    preview_training_check_in.add_argument("--intent", required=True, help="booked customer-training intent")
    preview_training_check_in.add_argument("--actor", required=True, help="accepted training player who is arriving")
    preview_status = preview_subcommands.add_parser(
        "match-status", help="read and assert the selected preview-match state"
    )
    preview_status.add_argument(
        "--occurrence", help="ledger occurrence to refresh; defaults to the active match"
    )
    preview_subcommands.add_parser("matches", help="list preview matches in the local ledger")
    preview_select = preview_subcommands.add_parser(
        "select", help="choose the active preview match for subsequent commands"
    )
    preview_select.add_argument("--occurrence", required=True)
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
    state = subparsers.add_parser("state", help="validate facts or rebuild capability projections")
    state_subcommands = state.add_subparsers(dest="state_command", required=True)
    state_subcommands.add_parser("rebuild", help="replay the fact journal into deterministic projections")
    state_subcommands.add_parser("facts", help="validate and summarize the secret-free fact journal")
    challenge = subparsers.add_parser(
        "challenge", help="record a durable no-write team challenge from a roster scenario"
    )
    challenge.add_argument(
        "--scenario", default="andy-vs-kyo-doubles.json", help="scenario file under scenarios/"
    )
    training = subparsers.add_parser(
        "training", help="record a captain-led two-player training proposal or acceptance locally"
    )
    training.add_argument(
        "--scenario", default="women-fighters-training.json", help="training scenario file under scenarios/"
    )
    training.add_argument(
        "--accept", action="store_true", help="record the named teammate's acceptance after the captain proposal")
    arcade = subparsers.add_parser("arcade", help="run a local-only deterministic Club Arcade match")
    arcade.add_argument("--occurrence", required=True, help="local Arcade occurrence key; never a product event mutation")
    arcade.add_argument("--left", required=True, help="one or two comma-separated player actor IDs")
    arcade.add_argument("--right", required=True, help="one or two comma-separated player actor IDs")
    arcade.add_argument("--season", default="season-001", help="public local season seed component")
    arcade_replay = subparsers.add_parser("arcade-replay", help="attach a fictional local Arcade replay to a booked training")
    arcade_replay.add_argument("--intent", required=True, help="booked customer-training intent ID")
    arcade_replay.add_argument("--season", default="season-001", help="public local season seed component")
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.home.resolve()
    if args.command != "test":
        migrate_legacy_actor_files(root)

    if args.command == "test":
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=str(root),
            check=False,
        )
        return result.returncode

    if args.command == "state":
        if args.state_command == "facts":
            facts = load_facts(root)
            print(f"PREVIEW CLUB FACT JOURNAL // {len(facts)} VALID FACTS")
            if facts:
                print(f"last: {facts[-1]['factId']} // {facts[-1]['type']}")
            return 0
        if args.state_command == "rebuild":
            result = rebuild_projections(root)
            print(
                "PREVIEW CLUB PROJECTIONS // REBUILT // "
                f"{result['metadata']['factCount']} FACTS // "
                f"{len(result['conversations']['conversations'])} CONVERSATIONS // "
                f"{len(result['commitments']['commitments'])} COMMITMENTS // "
                f"{len(result['events']['events'])} EVENTS // "
                f"{len(result['participation']['participation'])} PARTICIPATIONS // "
                f"{len(result['beliefs']['beliefs'])} BELIEFS"
            )
            return 0
        raise AssertionError(f"unhandled state command {args.state_command}")

    if args.command == "challenge":
        try:
            intent = plan_challenge(root, args.scenario, datetime.now(timezone.utc))
        except (TeamChallengeError, ValueError) as exc:
            print(f"ERROR team challenge: {exc}", file=sys.stderr)
            return 2
        print("PREVIEW CLUB TEAM CHALLENGE // AGREED // LOCAL ONLY")
        print(f"intent: {intent['id']}")
        print(f"match: {intent['challenger']['teamName']} vs {intent['opponent']['teamName']}")
        print(f"players: {', '.join(intent['participantCharacters'])}")
        print(f"duration: {intent['constraints']['durationMinutes']} minutes")
        print(f"next: {intent['nextAction']}")
        print("writes: 0")
        return 0

    if args.command == "training":
        try:
            now = datetime.now(timezone.utc)
            intent = propose_training(root, args.scenario, now)
            action = "captain_invited"
            if args.accept:
                intent = accept_training(root, args.scenario, now)
                action = "agreed"
        except TrainingSessionError as exc:
            print(f"ERROR training: {exc}", file=sys.stderr)
            return 2
        print(f"PREVIEW CLUB TRAINING // {action.upper()} // LOCAL ONLY")
        print(f"intent: {intent['id']}")
        print(f"players: {', '.join(intent['participants'])}")
        print(f"duration: {intent['constraints']['durationMinutes']} minutes")
        print(f"next: {intent['nextAction']}")
        print("writes: 0")
        return 0

    if args.command == "arcade":
        try:
            left = [value.strip() for value in args.left.split(",") if value.strip()]
            right = [value.strip() for value in args.right.split(",") if value.strip()]
            match = simulate_and_store(root, occurrence_key=args.occurrence, left=left, right=right, season_id=args.season)
        except ArcadeError as exc:
            print(f"ERROR arcade: {exc}", file=sys.stderr)
            return 2
        print("PREVIEW CLUB ARCADE // SIMULATED // LOCAL ONLY")
        print(f"occurrence: {match['occurrenceKey']}; game: {match['game']}")
        print(f"score: {match['score']['left']}–{match['score']['right']}; winner: {', '.join(match['winner'])}")
        print(f"rallies: {len(match['rallies'])}; writes: 0")
        return 0

    if args.command == "arcade-replay":
        try:
            match = attach_training_replay(root, intent_id=args.intent, season_id=args.season)
        except ArcadeError as exc:
            print(f"ERROR arcade replay: {exc}", file=sys.stderr)
            return 2
        print("PREVIEW CLUB ARCADE REPLAY // ATTACHED // LOCAL FICTION ONLY")
        print(f"intent: {args.intent}; occurrence: {match['occurrenceKey']}")
        print(f"score: {match['score']['left']}–{match['score']['right']}; winner: {', '.join(match['winner'])}")
        print("next: after the booked end, the named captain can confirm this replay result")
        print("writes: 0")
        return 0

    if args.command == "preview":
        if args.preview_command == "inspect":
            return inspect_preview(root)
        if args.preview_command == "login":
            return login_preview_players(root, args.actor)
        if args.preview_command == "session-check":
            return check_preview_sessions(root, args.actor)
        if args.preview_command == "readiness":
            return inspect_readiness(root)
        if args.preview_command == "discover-assets":
            return discover_preview_assets(root, args.actor)
        if args.preview_command == "browser-credential-handoff":
            try:
                serve_browser_credential_handoff(
                    root, args.actor, port=args.port, lifetime_seconds=args.lifetime
                )
            except (BrowserHandoffError, IdentityRegistryError, TargetPolicyError) as exc:
                print(f"ERROR browser credential handoff: {exc}", file=sys.stderr)
                return 2
            return 0
        if args.preview_command == "scan-venues":
            return scan_preview_venues(root, args.duration, args.within)
        if args.preview_command == "booking-preview":
            return evaluate_booking_preview(root)
        if args.preview_command == "plan-intent":
            return plan_preview_intent(root, args.intent)
        if args.preview_command == "fund":
            return fund_preview(root, args.apply, args.confirm_origin)
        if args.preview_command == "payment-method":
            return seed_payment_methods(root, args.apply, args.confirm_origin, args.stripe_env, args.actor)
        if args.preview_command == "book":
            return book_preview_match(
                root, args.apply, args.confirm_origin, args.intent
            )
        if args.preview_command == "share-invitation":
            return open_preview_share_invitation(root, args.intent, args.confirm_origin)
        if args.preview_command == "redeem-training-invitation":
            return redeem_preview_training_invitation(root, args.intent, args.confirm_origin, args.recipient)
        if args.preview_command == "create-open-play":
            return create_preview_open_play(root, args.apply, args.confirm_origin, args.intent)
        if args.preview_command == "signup-open-play":
            return signup_preview_open_play(root, args.apply, args.confirm_origin, args.intent)
        if args.preview_command == "team-signup":
            return signup_preview_team_challenge(root, args.apply, args.confirm_origin, args.intent)
        if args.preview_command == "join":
            return join_preview_match(
                root, args.apply, args.confirm_origin, args.occurrence
            )
        if args.preview_command == "check-in":
            return check_in_preview_match(
                root, args.apply, args.confirm_origin, args.occurrence
            )
        if args.preview_command == "training-check-in":
            return check_in_training_player(
                root, args.apply, args.confirm_origin, args.intent, args.actor
            )
        if args.preview_command == "match-status":
            return preview_match_status(root, args.occurrence)
        if args.preview_command == "matches":
            return list_preview_matches(root)
        if args.preview_command == "select":
            return select_preview_match(root, args.occurrence)
        if args.preview_command == "seed":
            return seed_preview(
                root,
                apply=args.apply,
                confirmation=args.confirm_origin,
                team=args.team,
            )
        if args.preview_command == "reschedule":
            return reschedule_preview(root, args.apply, args.confirm_origin, args.request)
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

    if args.command == "needs":
        if args.needs_command == "run":
            result = orchestrator.run_needs_beat(
                turns=args.turns,
                now=parse_instant(args.now) if args.now else None,
            )
            print(
                f"{result['interactionKey']}: {result['status']} "
                f"({result['turnsExecuted']} turns this beat)"
            )
            print()
            print(render_needs(result["world"]))
            return 0
        if args.needs_command == "status":
            print(render_needs(orchestrator.world_view()))
            return 0
        if args.needs_command == "promote":
            result = orchestrator.run_promotion_beat(
                turns=args.turns,
                now=parse_instant(args.now) if args.now else None,
            )
            print(
                f"{result['interactionKey']}: {result['status']} "
                f"({result['turnsExecuted']} turns this beat)"
            )
            print()
            print(render_needs(result["world"]))
            return 0
        raise AssertionError(f"unhandled needs command {args.needs_command}")

    if args.command == "runtime":
        if args.runtime_command == "tick":
            result = run_actor_tick(
                root, max_turns=args.max_turns, now=parse_instant(args.now)
            )
            print(
                f"PREVIEW CLUB ACTOR RUNTIME // tick {result['tick']} // "
                f"{len(result['turns'])} turns // remote writes 0"
            )
            for turn in result["turns"]:
                print(f"- {turn['actorId']}: {turn['action']} — {turn['detail']}")
            return 0
        if args.runtime_command == "tournament-tick":
            result = run_tournament_tick(root, now=parse_instant(args.now) if args.now else None)
            print(f"PREVIEW CLUB TOURNAMENT // tick {result['tick']} // remote writes 0")
            print(f"- {result['action']['actorId']}: {result['action']['action']} — {result['action']['detail']}")
            return 0
        if args.runtime_command == "report-winner":
            state = report_winner(root, args.winner, args.reporter, now=parse_instant(args.now) if args.now else None)
            print(f"PREVIEW CLUB TOURNAMENT RESULT // {state['rounds']['semi-final']['winner']} // no score")
            return 0
        if args.runtime_command == "report-event-result":
            result = report_finished_event_result(
                root, args.intent, args.reporter, args.winner,
                now=parse_instant(args.now) if args.now else None,
            )
            print(f"PREVIEW CLUB EVENT RESULT // {result['winner']} // captain attestation; no score")
            return 0
        if args.runtime_command == "rally-tick":
            if args.apply_preview and not args.confirm_origin:
                parser.error("runtime rally-tick --apply-preview requires --confirm-origin")
            result = run_rally_tick(
                root, hours=args.hours, now=parse_instant(args.now) if args.now else None,
                plan_preview=args.plan_preview, apply_preview=args.apply_preview,
                confirmation=args.confirm_origin,
            )
            print(f"RALLY ENGINE // {result['status'].upper()} // remote writes {result['remoteWrites']}")
            if result.get("action"):
                action = result["action"]
                if isinstance(action, dict):
                    print(f"- {action['actorId']}: {action['action']} — {action['detail']}")
                else:
                    detail = (
                        result.get("training", {}) if action == "plan_training_booking"
                        else result.get("teamSignup", {}) if action in {"plan_team_signup_booking", "apply_team_signup_booking"}
                        else result.get("bookingBridge", {})
                    )
                    actor = detail.get("actorId", "lead") if isinstance(detail, dict) else "lead"
                    text = detail.get("detail", "no additional detail") if isinstance(detail, dict) else "no additional detail"
                    print(f"- {actor}: {action} — {text}")
            return 0
        raise AssertionError(f"unhandled runtime command {args.runtime_command}")

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
                (root / "scenarios" / "hourly-match.json").is_file()
                and (root / "scenarios" / "needs-match.json").is_file()
                and (root / "scenarios" / "owner-promotion.json").is_file(),
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


def login_preview_players(root: Path, actor_ids: Optional[List[str]]) -> int:
    """Sign in stored players. The printed summary contains no email or password."""
    selected = [canonical_actor_id(actor_id) for actor_id in (actor_ids or [BOOKER, TEAMMATE])]
    try:
        credentials = PreviewCredentials.load(root)
        policy = TargetPolicy.from_config(credentials.config)
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").stored()
        summaries = []
        for actor_id in selected:
            identity = identities.get(actor_id)
            if identity is None:
                raise IdentityRegistryError(f"actor is missing from the private registry: {actor_id}")
            session = FirebaseAuthenticator(actor_auth_cache(root, actor_id)).authenticate(
                identity.email, identity.password, credentials.firebase_api_key
            )
            profile = PreviewReadonlyClient(policy, session.id_token).get("/apis/v2/users/current")
            summaries.append(verify_player_session(actor_id, identity, session, profile))
    except (
        CredentialsError,
        FirebaseAuthError,
        IdentityRegistryError,
        PreviewReadError,
        TargetPolicyError,
    ) as exc:
        print(f"ERROR preview login: {exc}", file=sys.stderr)
        return 2

    print(f"PLAYER LOGIN // PR #{policy.pull_request_number}")
    print(f"target: {policy.target_origin}")
    for summary in summaries:
        roles = ",".join(summary["roles"]) or "none"
        print(
            f"- {summary['actorId']}: verified user {summary['userId']} "
            f"roles={roles} auth={summary['authSource']}"
        )
    print("writes: 0")
    return 0


def discover_preview_assets(root: Path, actor_ids: List[str]) -> int:
    """Run the player-owned 4B probes and print only sanitized observations."""
    try:
        report = discover_player_assets(root, actor_ids)
    except (
        AssetDiscoveryError,
        CredentialsError,
        FirebaseAuthError,
        IdentityRegistryError,
        PreviewReadError,
        SelfAssetValidationError,
        TargetPolicyError,
    ) as exc:
        print(f"ERROR preview asset discovery: {exc}", file=sys.stderr)
        return 2

    print(f"PLAYER SELF-ASSETS // PR #{report['pullRequest']} // READ ONLY")
    print(f"observed: {report['observedAt']}")
    for actor in report["actors"]:
        print(f"- {actor['actorId']}")
        for subject, row in actor["subjects"].items():
            suffix = f" ({row['errorCode']})" if row.get("errorCode") else ""
            print(f"  {subject}: {row['status']}{suffix}")
    print("remote writes: 0")
    return 0


def check_preview_sessions(root: Path, actor_ids: Optional[List[str]]) -> int:
    """Run one stateless login/readback check for each seeded Club identity.

    This is deliberately separate from browser automation.  It authenticates,
    verifies ``/users/current``, then lets the short-lived token go out of
    scope.  It neither creates a browser session nor performs a preview write.
    """
    try:
        credentials = PreviewCredentials.load(root)
        policy = TargetPolicy.from_config(credentials.config)
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").stored()
        selected = [canonical_actor_id(actor_id) for actor_id in (
            actor_ids or list(seed_actor_ids(load_preview_seed(root)))
        )]
        summaries = []
        for actor_id in selected:
            identity = identities.get(actor_id)
            if identity is None:
                raise IdentityRegistryError(f"actor is missing from the private registry: {actor_id}")
            session = FirebaseAuthenticator(actor_auth_cache(root, actor_id)).authenticate(
                identity.email, identity.password, credentials.firebase_api_key
            )
            profile = PreviewReadonlyClient(policy, session.id_token).get("/apis/v2/users/current")
            summaries.append(verify_player_session(actor_id, identity, session, profile))
            # Do not retain a token beyond this actor's verification.
            del session
        record = session_check_record(summaries)
        observed_at = datetime.now(timezone.utc)
        record["observedAt"] = observed_at.isoformat()
        Storage(root).write_json(
            root / "state" / "runs" / f"preview-session-check-{observed_at.strftime('%Y-%m-%dT%H-%M-%SZ')}.json",
            record,
        )
    except (
        CredentialsError,
        FirebaseAuthError,
        IdentityRegistryError,
        PreviewReadError,
        TargetPolicyError,
    ) as exc:
        print(f"ERROR preview session check: {exc}", file=sys.stderr)
        return 2

    print(f"PREVIEW SESSION CHECK // PR #{policy.pull_request_number}")
    print(f"target: {policy.target_origin}")
    for summary in summaries:
        roles = ",".join(summary["roles"]) or "none"
        print(f"- {summary['actorId']}: verified roles={roles}")
    print("browser session: not created or modified")
    print("writes: 0")
    return 0


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


def scan_preview_venues(root: Path, duration: int, within_minutes: int) -> int:
    """Compare today's actual session grids, not merely pod availability flags."""
    if duration < 30 or duration % 30 or within_minutes < 0:
        print("ERROR venue scan: duration must be a 30-minute increment; within must be non-negative", file=sys.stderr)
        return 2
    try:
        credentials = PreviewCredentials.load(root)
        policy = TargetPolicy.from_config(credentials.config)
        admin_auth = FirebaseAuthenticator(root / "secrets" / "preview-auth-cache.json").authenticate(credentials.admin_email, credentials.admin_password, credentials.firebase_api_key)
        admin_client = PreviewReadonlyClient(policy, admin_auth.id_token)
        booker = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure(["andy-bogard"])["andy-bogard"]
        booker_auth = FirebaseAuthenticator(actor_auth_cache(root, "andy-bogard")).authenticate(booker.email, booker.password, credentials.firebase_api_key)
        player_client = PreviewReadonlyClient(policy, booker_auth.id_token)
        observed_at = datetime.now(timezone.utc)
        areas = collection_items(admin_client.get("/apis/v2/areas"))
        pods_to_scan = []
        for area in areas[:50]:
            if not isinstance(area, dict) or not isinstance(area.get("id"), str):
                continue
            for pod_stub in collection_items(admin_client.get(f"/apis/v2/areas/{area['id']}/pods")):
                if not isinstance(pod_stub, dict) or not isinstance(pod_stub.get("id"), str):
                    continue
                pods_to_scan.append((area, pod_stub))

        def scan_one(entry):
            area, pod_stub = entry
            pod = admin_client.get(f"/apis/v2/pods/{pod_stub['id']}")
            if not isinstance(pod, dict) or pod.get("status") != "AVAILABLE":
                return None
            timezone_name = str(pod.get("timezone") or "UTC")
            candidate = find_candidate_session(player_client, pod_stub["id"], timezone_name, observed_at, first_day_offset=0, last_day_offset=0, required_duration_minutes=duration)
            return {"area": area.get("displayName") or area.get("name") or area["id"], "areaId": area["id"], "pod": pod.get("displayName") or pod.get("name") or pod_stub["id"], "podId": pod_stub["id"], "timezone": timezone_name, "openingHours": pod.get("openingHours"), "candidate": candidate}

        rows = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            for future in as_completed([pool.submit(scan_one, entry) for entry in pods_to_scan]):
                row = future.result()
                if row is not None:
                    rows.append(row)
    except (CredentialsError, FirebaseAuthError, IdentityRegistryError, PreviewReadError, TargetPolicyError, ValueError) as exc:
        print(f"ERROR venue scan: {exc}", file=sys.stderr)
        return 2
    immediate_cutoff = observed_at.timestamp() + within_minutes * 60
    candidates = [row for row in rows if isinstance(row["candidate"], dict)]
    candidates.sort(key=lambda row: row["candidate"]["startTime"])
    immediate = [row for row in candidates if datetime.fromisoformat(row["candidate"]["startTime"].replace("Z", "+00:00")).timestamp() <= immediate_cutoff]
    print(f"PREVIEW VENUE SCAN // PR #{policy.pull_request_number} // {duration} MINUTES")
    print(f"observed: {observed_at.isoformat()}; available pods scanned: {len(rows)}")
    print(f"starts within {within_minutes} minutes: {len(immediate)}")
    for row in candidates[:12]:
        candidate = row["candidate"]
        marker = "NOW" if row in immediate else "LATER"
        print(f"- {marker} {row['area']} / {row['pod']} ({row['timezone']}): {candidate['startTime']} to {candidate['endTime']}; tables={candidate['tablesLeft']}; ids={row['areaId']}/{row['podId']}")
    if not candidates:
        print("no contiguous candidate today")
    print("writes: 0")
    return 0


def seed_preview(root: Path, apply: bool, confirmation: Optional[str], team: Optional[str] = None) -> int:
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
        actor_ids = list(seed_actor_ids(load_preview_seed(root)))
        if team:
            actor_ids.extend(TEAM_ACTORS[team])
        identities = registry.ensure(actor_ids)
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
                auth.id_token,
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
            roles = f"; roles={','.join(result['roles'])}" if result["roles"] else ""
            print(f"- {result['actorId']}: {result['action']} and verified ({result['email']}){roles}")
        signups = sum(1 for result in results if result['action'] == 'created')
        print(f"writes: {signups} user signups; Riley roles reconciled")
    else:
        for actor_id, actor in plan["actors"].items():
            print(f"- {actor_id}: {actor['status']} ({actor['email']})")
        missing = sum(1 for actor in plan["actors"].values() if actor["status"] == "missing")
        fixture = plan["staffFixture"]
        print(f"writes planned: {missing} user signups; {fixture['actorId']} roles={','.join(fixture['productRoles'])}; no credits, memberships, or bookings")
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
        print("candidate: none found from now +30m through day +14")
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
        booker_actor_id = BOOKER
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure(
            ("andy-bogard", "terry-bogard", booker_actor_id)
        )
        if booker_actor_id not in identities:
            raise BookingIntentError("character intent has no verified player booker")
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
        booker = identities[booker_actor_id]
        booker_auth = FirebaseAuthenticator(
            actor_auth_cache(root, booker_actor_id)
        ).authenticate(booker.email, booker.password, credentials.firebase_api_key)
        candidate = report["candidateSession"]
        requested_credits = min(
            float(report["actors"][booker_actor_id]["virtualCredits"]), 25.0
        )
        response = PreviewBookingEvaluator(policy, booker_auth.id_token).evaluate(
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


def plan_preview_intent(
    root: Path, intent_id: Optional[str], announce: bool = True
) -> int:
    """Map a character agreement to a preview candidate without persisting it."""

    storage = Storage(root)
    try:
        ledger = load_intent_ledger(storage)
        selected_id, intent = select_intent(ledger, intent_id)
        if intent.get("status") not in {"requested", "agreed", "preview_ready", "preview_blocked"}:
            raise BookingIntentError(
                f"booking intent cannot be planned from status {intent.get('status')!r}"
            )
        constraints = intent.get("constraints")
        duration = constraints.get("durationMinutes") if isinstance(constraints, dict) else None
        if not isinstance(duration, int) or duration < 30 or duration % 30:
            raise BookingIntentError("preview planning requires a positive 30-minute increment")
        journey = intent.get("journey", "customer_booking")
        if journey not in {"customer_booking", "customer_training", "customer_team_signup", "owner_open_play"}:
            raise BookingIntentError("preview planning does not recognize the journey type")

        credentials = PreviewCredentials.load(root)
        policy = TargetPolicy.from_config(credentials.config)
        booker_actor_id = resolve_booking_owner(intent)
        if not isinstance(booker_actor_id, str):
            raise BookingIntentError("character intent has no verified player booker")
        if intent.get("bookerActorId") != booker_actor_id:
            # Migrate pre-field team agreements from their named initiating
            # captain.  This is durable local compatibility state only.
            intent["bookerActorId"] = booker_actor_id
            save_intent(storage, ledger, intent)
        planned_at = datetime.now(timezone.utc).isoformat()
        with storage.writer_lock():
            ledger = load_intent_ledger(storage)
            selected_id, intent = select_intent(ledger, selected_id)
            canonical_occurrence = ensure_planned_locked(storage, intent, planned_at)
            save_intent(storage, ledger, intent)
        identity_ids = tuple(dict.fromkeys(("andy-bogard", "terry-bogard", booker_actor_id, *intent.get("participants", []))))
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure(identity_ids)
        if booker_actor_id not in identities:
            raise BookingIntentError("character intent has no verified player booker")
        admin_auth = FirebaseAuthenticator(
            root / "secrets" / "preview-auth-cache.json"
        ).authenticate(
            credentials.admin_email,
            credentials.admin_password,
            credentials.firebase_api_key,
        )
        report = inspect_preview_readiness(
            root,
            policy,
            PreviewReadonlyClient(policy, admin_auth.id_token),
            identities,
            credentials.firebase_api_key,
        )
        if not report.get("readyForBookingPreview"):
            raise PreviewEvaluationError("readiness gate must pass before intent planning")
        booker = identities[booker_actor_id]
        booker_auth = FirebaseAuthenticator(
            actor_auth_cache(root, booker_actor_id)
        ).authenticate(booker.email, booker.password, credentials.firebase_api_key)
        booker_client = PreviewReadonlyClient(policy, booker_auth.id_token)
        observed = datetime.fromisoformat(report["observedAt"])
        search = dict(
            pod_id=report["location"]["podId"],
            timezone_name=report["location"]["timezone"] or "UTC",
            now=observed,
            first_day_offset=int(constraints["daysAhead"][0]),
            last_day_offset=int(constraints["daysAhead"][1]),
            allowed_local_windows=constraints["localWindows"],
            required_duration_minutes=duration,
            required_guest_count=(len(intent.get("participants", [])) - 1 if journey == "customer_team_signup" else None),
        )
        candidate = None
        if may_start_early(intent):
            candidate = early_agreed_start(
                find_candidate_session(booker_client, safety_lead_minutes=0, **search),
                observed,
                EARLY_START_WINDOW_MINUTES,
            )
        if candidate is None:
            candidate = find_candidate_session(booker_client, **search)
        if not isinstance(candidate, dict):
            intent["status"] = "preview_blocked"
            intent["previewPlan"] = {
                "pullRequest": policy.pull_request_number,
                "targetOrigin": policy.target_origin,
                "observedAt": report["observedAt"],
                "blocker": "no_slot_matches_character_availability",
            }
            intent["remoteWrites"] = 0
            save_intent(storage, ledger, intent)
            _sync_world_intent_status(storage, intent)
            _record_intent_blocker(storage, intent, policy.pull_request_number)
            raise BookingIntentError(
                "no legal preview slot matches the agreed local availability"
            )
        claim = _local_slot_claim(ledger, selected_id, candidate)
        if claim is not None:
            intent["status"] = "agreed"
            intent["slotContention"] = {
                "status": "waiting_for_local_claim",
                "claimedByIntentId": claim["id"],
                "tableId": candidate["tableId"],
                "startTime": candidate["startTime"],
                "observedAt": report["observedAt"],
            }
            intent["nextAction"] = "replan_after_other_captain_booking_resolves"
            save_intent(storage, ledger, intent)
            storage.append_jsonl(storage.channel_path("club"), {
                "id": f"{selected_id}:slot-contention:{candidate['tableId']}:{candidate['startTime']}",
                "channel": "club", "from": "lead", "to": intent.get("participants", []),
                "kind": "training_slot_waiting",
                "intentId": selected_id,
                "text": "Another agreed club training has the local claim on this W37 table and time. Your agreement remains active; the lead will replan from fresh availability after that booking resolves.",
                "observedAt": report["observedAt"], "remoteWrites": 0,
            })
            raise BookingIntentError("local W37 slot is already claimed by another agreed training")
        start = datetime.fromisoformat(candidate["startTime"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(candidate["endTime"].replace("Z", "+00:00"))
        if int((end - start).total_seconds() / 60) != constraints["durationMinutes"]:
            raise BookingIntentError("preview candidate does not satisfy intent duration")
        max_delay = constraints.get("maxDelayMinutes")
        if isinstance(max_delay, int) and (start - datetime.fromisoformat(report["observedAt"])).total_seconds() > max_delay * 60:
            intent["status"] = "preview_blocked"
            intent["previewPlan"] = {"pullRequest": policy.pull_request_number, "targetOrigin": policy.target_origin, "observedAt": report["observedAt"], "blocker": "no_slot_within_max_delay", "maxDelayMinutes": max_delay}
            intent["remoteWrites"] = 0
            save_intent(storage, ledger, intent)
            _sync_world_intent_status(storage, intent)
            _record_intent_blocker(storage, intent, policy.pull_request_number)
            raise BookingIntentError(f"no legal preview slot begins within {max_delay} minutes")
        requested_credits = min(float(report["actors"][booker_actor_id]["virtualCredits"]), 25.0)
        evaluation = (
            summarize_booking_preview(PreviewBookingEvaluator(policy, booker_auth.id_token).evaluate(
                candidate["sessionId"], candidate["tableId"], requested_credits
            ))
            if journey in {"customer_booking", "customer_training"}
            else {"status": "OWNER_EVENT_PLANNED", "total": 0.0, "currency": None,
                  "errorCodes": [], "readyForOrder": True}
        )
        if journey == "customer_team_signup":
            evaluation = summarize_booking_preview(PreviewBookingEvaluator(policy, booker_auth.id_token).evaluate_team(
                candidate["items"], requested_credits
            ))
        intent["status"] = (
            "preview_ready" if evaluation["readyForOrder"] else "preview_blocked"
        )
        intent["nextAction"] = (
            "apply_guarded_player_booking"
            if evaluation["readyForOrder"]
            else "address_preview_booking_blocker"
        )
        intent["previewPlan"] = {
            "pullRequest": policy.pull_request_number,
            "targetOrigin": policy.target_origin,
            "observedAt": report["observedAt"],
            "occurrenceKey": canonical_occurrence,
            "areaName": report["location"]["areaName"],
            "podName": report["location"]["podName"],
            "podId": report["location"]["podId"],
            "sessionId": candidate["sessionId"],
            "tableId": candidate["tableId"],
            "items": candidate["items"],
            "durationMinutes": candidate["durationMinutes"],
            "startTime": candidate["startTime"],
            "endTime": candidate["endTime"],
            "pulledForward": candidate.get("pulledForward") is True,
            "earlyWindowMinutes": candidate.get("earlyWindowMinutes"),
            "virtualCredits": requested_credits,
            "evaluation": evaluation,
        }
        intent["remoteWrites"] = 0
        save_intent(storage, ledger, intent)
        with storage.writer_lock():
            record_window_locked(
                storage, occurrence_key=canonical_occurrence, intent_id=selected_id,
                planned_window={
                    "startTime": candidate["startTime"], "endTime": candidate["endTime"],
                    "podRef": report["location"]["podId"], "durationMinutes": candidate["durationMinutes"],
                },
                occurred_at=report["observedAt"],
            )
        _sync_world_intent_status(storage, intent)
    except (
        BookingIntentError,
        CredentialsError,
        FirebaseAuthError,
        IdentityRegistryError,
        PreviewEvaluationError,
        PreviewReadError,
        TargetPolicyError,
        ValueError,
    ) as exc:
        print(f"ERROR preview intent plan: {exc}", file=sys.stderr)
        return 2

    if announce:
        print(
            f"PREVIEW BOOKING INTENT // {intent['status'].upper()} // "
            f"PR #{policy.pull_request_number}"
        )
        print(f"intent: {selected_id}")
        print(
            f"reason: {intent['reason']}; participants: "
            f"{', '.join(intent['participants'])}"
        )
        print(
            f"location: {report['location']['areaName']} / "
            f"{report['location']['podName']}"
        )
        print(f"journey: {journey}; duration: {candidate['durationMinutes']} minutes")
        print(f"slot: {candidate['startTime']} to {candidate['endTime']} ({len(candidate['items'])} grid slots)")
        print(
            f"calculation: {evaluation['status']}; total={evaluation['total']:.2f} "
            f"{evaluation['currency'] or ''}; "
            f"errors={','.join(evaluation['errorCodes']) or 'none'}"
        )
        print("remote writes: 0; durable intent updated")
    return 0 if evaluation["readyForOrder"] else 1


def create_preview_open_play(
    root: Path, apply: bool, confirmation: Optional[str], intent_id: Optional[str]
) -> int:
    """Create the owner event first; only a later actor tick may promote it."""
    storage = Storage(root)
    try:
        if plan_preview_intent(root, intent_id, announce=False) != 0:
            raise PreviewWriteError("owner Open Play intent is not ready")
        ledger = load_intent_ledger(storage)
        selected_id, intent = select_intent(ledger, intent_id)
        if intent.get("journey") != "owner_open_play" or intent.get("status") != "preview_ready":
            raise PreviewWriteError("selected intent is not a ready owner Open Play")
        plan = intent.get("previewPlan")
        if not isinstance(plan, dict) or not isinstance(plan.get("items"), list) or not plan["items"]:
            raise PreviewWriteError("owner Open Play plan is incomplete")
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        if plan.get("targetOrigin") != read_policy.target_origin:
            raise PreviewWriteError("owner Open Play was planned for another origin")
        name = f"Preview Club Open Play {selected_id.rsplit(':', 1)[-1]}"
        event = intent.get("event")
        writes = 0
        if apply and not isinstance(event, dict):
            admin_auth = FirebaseAuthenticator(root / "secrets" / "preview-auth-cache.json").authenticate(
                credentials.admin_email, credentials.admin_password, credentials.firebase_api_key
            )
            write_policy = TargetPolicy.from_config(replace(
                credentials.config, mode=RunMode.PREVIEW_WRITE,
                preview_write_confirmation=confirmation,
            ))
            result = PreviewOwnerEventWriter(write_policy, admin_auth.id_token).create_open_play(
                plan["items"], name, total_teams=len(intent.get("participants", []))
            )
            event_id = result["id"]
            client = PreviewReadonlyClient(read_policy, admin_auth.id_token)
            event = client.get(f"/apis/v2/events/{event_id}")
            if not isinstance(event, dict) or event.get("id") != event_id or event.get("subtype") != "OPEN_PLAY":
                raise PreviewWriteError("owner Open Play read-back did not match the planned event")
            writes = 1
            intent["event"] = {"eventId": event_id, "name": name, "status": event.get("status"),
                               "startTime": event.get("startTime"), "endTime": event.get("endTime")}
            intent["status"] = "event_created"
            intent["remoteWrites"] = 1
            save_intent(storage, ledger, intent)
            _sync_world_intent_status(storage, intent)
        print(f"PREVIEW CLUB OPEN PLAY // {'APPLIED' if apply else 'DRY RUN'} // PR #{read_policy.pull_request_number}")
        print(f"intent: {selected_id}; duration: {plan['durationMinutes']} minutes; grid slots: {len(plan['items'])}")
        print(f"location: {plan['areaName']} / {plan['podName']}")
        print(f"slot: {plan['startTime']} to {plan['endTime']}")
        print(f"promotion: blocked until this event is created; writes: {writes}")
        return 0
    except (BookingIntentError, CredentialsError, FirebaseAuthError, IdentityRegistryError,
            PreviewReadError, PreviewWriteError, TargetPolicyError, ValueError) as exc:
        print(f"ERROR preview Open Play: {exc}", file=sys.stderr)
        return 2


def signup_preview_open_play(
    root: Path, apply: bool, confirmation: Optional[str], intent_id: Optional[str]
) -> int:
    """Let named players self-sign-up only after the owner event is promoted."""
    storage = Storage(root)
    try:
        ledger = load_intent_ledger(storage)
        selected_id, intent = select_intent(ledger, intent_id)
        event = intent.get("event")
        if intent.get("journey") != "owner_open_play" or intent.get("status") not in {"event_created", "players_registered"}:
            raise PreviewWriteError("Open Play must be created before player signup")
        if not isinstance(event, dict) or not isinstance(event.get("eventId"), str):
            raise PreviewWriteError("Open Play event record is missing")
        promotion_key = intent.get("promotionOccurrenceKey") or f"actor-runtime:season-001:{selected_id.rsplit(':', 1)[-1]}"
        promotion_kind = intent.get("promotionKind") or "runtime_announcement"
        messages = storage.read_jsonl(storage.channel_path("club"))
        if not any(row.get("occurrenceKey") == promotion_key and row.get("kind") == promotion_kind for row in messages):
            raise PreviewWriteError("Open Play must be promoted before player signup")
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure(intent.get("participants", []))
        player_rows = []
        writes = 0
        for actor_id in intent.get("participants", []):
            identity = identities.get(actor_id)
            if identity is None or not identity.podplay_user_id:
                raise PreviewWriteError(f"player identity is incomplete: {actor_id}")
            auth = FirebaseAuthenticator(actor_auth_cache(root, actor_id)).authenticate(
                identity.email, identity.password, credentials.firebase_api_key
            )
            client = PreviewReadonlyClient(read_policy, auth.id_token)
            signup = find_event_signup(client, event["eventId"], identity.podplay_user_id)
            action = "already_registered" if signup else "would_register"
            if apply and not signup:
                write_policy = TargetPolicy.from_config(replace(
                    credentials.config, mode=RunMode.PREVIEW_WRITE,
                    preview_write_confirmation=confirmation,
                ))
                PreviewEventSignupWriter(write_policy, auth.id_token).signup(event["eventId"], identity.podplay_user_id)
                signup = find_event_signup(client, event["eventId"], identity.podplay_user_id)
                if not isinstance(signup, dict):
                    raise PreviewWriteError(f"signup read-back failed for {actor_id}")
                action, writes = "registered", writes + 1
            player_rows.append({"actorId": actor_id, "action": action, "signupId": signup.get("id") if isinstance(signup, dict) else None})
        if apply and all(row["action"] in {"registered", "already_registered"} for row in player_rows):
            intent["status"] = "players_registered"
            intent["signups"] = player_rows
            intent["remoteWrites"] = int(intent.get("remoteWrites", 0)) + writes
            save_intent(storage, ledger, intent)
            _sync_world_intent_status(storage, intent)
        print(f"PREVIEW CLUB OPEN PLAY SIGNUP // {'APPLIED' if apply else 'DRY RUN'} // PR #{read_policy.pull_request_number}")
        print(f"intent: {selected_id}; event: {event['eventId']}; writes: {writes}")
        for row in player_rows:
            print(f"- {row['actorId']}: {row['action']}")
        return 0
    except (BookingIntentError, CredentialsError, FirebaseAuthError, IdentityRegistryError,
            PreviewReadError, PreviewWriteError, TargetPolicyError, ValueError) as exc:
        print(f"ERROR preview Open Play signup: {exc}", file=sys.stderr)
        return 2


def _sync_world_intent_status(storage: Storage, intent: dict) -> None:
    world = storage.load_json(storage.world_path, default=None)
    if not isinstance(world, dict):
        return
    summaries = world.get("bookingIntents")
    if not isinstance(summaries, list):
        return
    for summary in summaries:
        if isinstance(summary, dict) and summary.get("id") == intent.get("id"):
            summary["status"] = intent.get("status")
            preview_plan = intent.get("previewPlan")
            if isinstance(preview_plan, dict):
                summary["previewPlan"] = {
                    key: preview_plan.get(key)
                    for key in ("occurrenceKey", "startTime", "endTime", "blocker")
                    if preview_plan.get(key) is not None
                }
            if intent.get("reason") == "owner_priority_announcement":
                campaign_id = str(intent["id"])[len("intent:") :]
                for campaign in world.get("campaigns", []):
                    if isinstance(campaign, dict) and campaign.get("id") == campaign_id:
                        campaign["status"] = intent.get("status")
                        booking = intent.get("booking")
                        if isinstance(booking, dict):
                            campaign["eventId"] = booking.get("eventId")
                            campaign["occurrenceKey"] = booking.get("occurrenceKey")
            storage.write_json(storage.world_path, world)
            return


def _record_intent_blocker(storage: Storage, intent: dict, pull_request: int) -> None:
    """Persist one deduplicated, secret-free issue for a planned journey."""
    intent_id = intent.get("id")
    preview_plan = intent.get("previewPlan")
    if not isinstance(intent_id, str) or not isinstance(preview_plan, dict):
        return
    issue_id = f"intent-blocked-{intent_id.replace(':', '-')}"
    storage.write_json(
        storage.state / "issues" / "open" / f"{issue_id}.json",
        {
            "id": issue_id,
            "code": "no_matching_preview_session",
            "status": "open",
            "detector": "preview_intent_planner",
            "pullRequest": pull_request,
            "intentId": intent_id,
            "reason": intent.get("reason"),
            "constraints": intent.get("constraints"),
            "blocker": preview_plan.get("blocker"),
            "observedAt": preview_plan.get("observedAt"),
            "remoteWrites": 0,
        },
    )


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


def _local_slot_claim(ledger: Dict[str, Any], selected_id: str, candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return an unbooked local claim for this exact physical slot, if any.

    This is coordination only: it never represents a remote reservation.  The
    booking writer still reads PodPlay immediately before its single order.
    """
    for intent_id, other in ledger.get("intents", {}).items():
        if intent_id == selected_id or not isinstance(other, dict) or other.get("status") != "preview_ready":
            continue
        plan = other.get("previewPlan")
        if not isinstance(plan, dict):
            continue
        if all(plan.get(key) == candidate.get(key) for key in ("tableId", "startTime", "endTime")):
            return {"id": intent_id}
    return None


def seed_payment_methods(
    root: Path,
    apply: bool,
    confirmation: Optional[str],
    stripe_env: Optional[Path],
    actor_ids: Optional[List[str]] = None,
) -> int:
    try:
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        selected_actors = tuple(actor_ids or ("andy-bogard", "terry-bogard"))
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure(selected_actors)
        actor_rows = {}
        actor_sessions = {}
        for actor_id in selected_actors:
            identity = identities[actor_id]
            auth = FirebaseAuthenticator(
                actor_auth_cache(root, actor_id)
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
                setup_intent_id = PreviewPaymentMethodWriter(
                    write_policy, auth.id_token, stripe_secret
                ).add_visa()
                writes += 1
                payment_result = wait_for_payment_method(client)
                row["verified"] = payment_result["present"]
                row["setupIntentId"] = setup_intent_id
                row["paymentElementId"] = payment_result["paymentElementId"]
                row["brand"] = payment_result["brand"]
                if not row["verified"]:
                    raise PreviewWriteError(
                        f"payment-method read-back timed out for {actor_id}"
                    )
            _record_preview_payment_ledger(storage=Storage(root), policy=read_policy, identities=identities, rows=actor_rows)
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


def _record_preview_payment_ledger(*, storage: Storage, policy: TargetPolicy, identities: dict, rows: dict) -> None:
    """Keep a private cleanup ledger for cards tied to disposable Preview Club actors."""
    path = storage.state / "preview-payment-methods.json"
    ledger = storage.load_json(path, default={"schemaVersion": 1, "entries": {}})
    entries = ledger.setdefault("entries", {})
    for actor_id, row in rows.items():
        if not row.get("verified"):
            continue
        identity = identities[actor_id]
        entries[actor_id] = {
            "actorId": actor_id, "email": identity.email, "firebaseUid": identity.firebase_uid,
            "podplayUserId": identity.podplay_user_id, "previewOrigin": policy.target_origin,
            "pullRequest": policy.pull_request_number, "paymentElementId": row.get("paymentElementId"),
            "brand": row.get("brand"), "setupIntentId": row.get("setupIntentId"),
            "status": "active", "cleanup": "reconcile on next preview readiness; detach only after explicit approval",
        }
    storage.write_json(path, ledger)


def reschedule_preview(root: Path, apply: bool, confirmation: Optional[str], request_id: Optional[str] = None) -> int:
    """Lead-only move of the one event Riley already proposed to reschedule."""
    storage = Storage(root)
    try:
        pending = pending_reschedule(root, request_id)
        intent_id = pending.get("intent_id")
        event_id = pending.get("event_id")
        if not isinstance(intent_id, str) or not isinstance(event_id, str):
            raise PreviewWriteError("Riley request is missing its event reference")
        ledger = load_intent_ledger(storage)
        _, intent = select_intent(ledger, intent_id)
        preview_plan = intent.get("previewPlan")
        if not isinstance(preview_plan, dict):
            raise PreviewWriteError("Riley request has no durable preview plan")
        pod_id = preview_plan.get("podId")
        duration = preview_plan.get("durationMinutes")
        old_start = preview_plan.get("startTime")
        if not isinstance(pod_id, str) or not isinstance(duration, int) or not isinstance(old_start, str):
            raise PreviewWriteError("Riley request preview plan is incomplete")
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        if preview_plan.get("targetOrigin") != read_policy.target_origin:
            raise PreviewWriteError("Riley request belongs to another preview origin")
        admin_auth = FirebaseAuthenticator(root / "secrets" / "preview-auth-cache.json").authenticate(
            credentials.admin_email, credentials.admin_password, credentials.firebase_api_key
        )
        client = PreviewReadonlyClient(read_policy, admin_auth.id_token)
        pod = client.get(f"/apis/v2/pods/{pod_id}")
        timezone_name = pod.get("timezone") if isinstance(pod, dict) else None
        if not isinstance(timezone_name, str):
            raise PreviewReadError("Riley request pod has no timezone")
        now = datetime.now(timezone.utc)
        candidate = early_agreed_start(
            find_candidate_session(client, pod_id, timezone_name, now, safety_lead_minutes=0, required_duration_minutes=duration),
            now,
            EARLY_START_WINDOW_MINUTES,
        )
        if not isinstance(candidate, dict):
            raise PreviewWriteError("no free court starts within the agreed 15-minute window")
        if candidate.get("startTime") == old_start:
            raise PreviewWriteError("nearest legal court is already the event's current start")
        plan = {"eventId": event_id, "podId": pod_id, "startTime": candidate["startTime"], "endTime": candidate["endTime"], "durationMinutes": duration}
        if apply:
            write_policy = TargetPolicy.from_config(replace(
                credentials.config, mode=RunMode.PREVIEW_WRITE, preview_write_confirmation=confirmation
            ))
            PreviewEventRescheduleWriter(write_policy, admin_auth.id_token).reschedule(
                event_id, plan["startTime"], plan["endTime"]
            )
            event = client.get(f"/apis/v2/events/{event_id}")
            if not isinstance(event, dict) or event.get("id") != event_id or event.get("isCanceled") is True or event.get("startTime") != plan["startTime"] or event.get("endTime") != plan["endTime"]:
                raise PreviewWriteError("same-event reschedule read-back did not match the plan")
            preview_plan.update({
                "startTime": plan["startTime"], "endTime": plan["endTime"],
                "sessionId": candidate["sessionId"], "tableId": candidate["tableId"],
                "items": candidate["items"], "observedAt": now.isoformat(),
            })
            if isinstance(intent.get("event"), dict):
                intent["event"].update({"startTime": plan["startTime"], "endTime": plan["endTime"]})
            intent["previewPlan"] = preview_plan
            save_intent(storage, ledger, intent)
            occurrence = intent.get("occurrenceKey") or preview_plan.get("occurrenceKey")
            if isinstance(occurrence, str):
                with storage.writer_lock():
                    record_window_locked(
                        storage,
                        occurrence_key=occurrence,
                        intent_id=intent_id,
                        planned_window={
                            "startTime": plan["startTime"],
                            "endTime": plan["endTime"],
                            "podRef": pod_id,
                            "durationMinutes": duration,
                        },
                        occurred_at=now.isoformat(),
                    )
            mark_rescheduled(root, pending["requestId"], plan, now)
            storage.append_jsonl(storage.channel_path("club"), {
                "id": f"{pending['requestId']}:riley-completed", "channel": "club", "from": "riley",
                "to": intent.get("participants", []), "kind": "staff_reschedule_completed",
                "inReplyTo": pending["requestId"], "text": "The lead moved the same event to the confirmed earlier court.",
                "observedAt": now.isoformat(),
            })
    except (ValueError, BookingIntentError, CredentialsError, FirebaseAuthError, PreviewReadError, PreviewWriteError, TargetPolicyError) as exc:
        print(f"ERROR preview reschedule: {exc}", file=sys.stderr)
        return 2
    print(f"PREVIEW CLUB RESCHEDULE // {'APPLIED' if apply else 'DRY RUN'} // PR #{read_policy.pull_request_number}")
    print(f"event: {plan['eventId']}; same-event move: {plan['startTime']} to {plan['endTime']}")
    print(f"writes: {1 if apply else 0}")
    return 0


def book_preview_match(
    root: Path,
    apply: bool,
    confirmation: Optional[str],
    intent_id: Optional[str] = None,
) -> int:
    storage = Storage(root)
    try:
        selected_intent_id = None
        selected_intent = None
        intent_ledger = None
        if intent_id:
            intent_ledger = load_intent_ledger(storage)
            selected_intent_id, selected_intent = select_intent(intent_ledger, intent_id)
            # A Rally bridge has already performed a live availability and
            # quote evaluation before moving the intent to preview_ready.
            # Re-running that plan here races the exact court we are about to
            # order and can turn a verified plan into an unrelated network
            # failure.  Unready intents still take the established planning
            # path, preserving the direct CLI workflow.
            if selected_intent.get("status") != "preview_ready":
                if plan_preview_intent(root, intent_id, announce=False) != 0:
                    raise PreviewWriteError("character intent is not ready for booking")
                intent_ledger = load_intent_ledger(storage)
                selected_intent_id, selected_intent = select_intent(intent_ledger, intent_id)
            if selected_intent.get("status") != "preview_ready":
                raise PreviewWriteError("character intent is not preview_ready")
            if selected_intent.get("journey", "customer_booking") not in {"customer_booking", "customer_training", "customer_team_signup"}:
                raise PreviewWriteError("owner Open Play intents must use preview create-open-play")
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        ledger = load_match_ledger(storage, read_policy.target_origin)
        booker_actor_id = resolve_booking_owner(selected_intent) if selected_intent is not None else BOOKER
        if not isinstance(booker_actor_id, str):
            raise PreviewWriteError("booking intent has no verified player booker")
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure((booker_actor_id,))
        if booker_actor_id not in identities:
            raise PreviewWriteError("booking intent has no verified player booker")
        booker = identities[booker_actor_id]
        booker_auth = FirebaseAuthenticator(
            actor_auth_cache(root, booker_actor_id)
        ).authenticate(booker.email, booker.password, credentials.firebase_api_key)
        booker_client = PreviewReadonlyClient(read_policy, booker_auth.id_token)
        if selected_intent is not None:
            preview_plan = selected_intent.get("previewPlan")
            if (
                not isinstance(preview_plan, dict)
                or preview_plan.get("targetOrigin") != read_policy.target_origin
            ):
                raise PreviewWriteError("character intent was planned for another origin")
            required = (
                "occurrenceKey",
                "podId",
                "sessionId",
                "tableId",
                "startTime",
                "endTime",
                "virtualCredits",
            )
            if any(not isinstance(preview_plan.get(key), (str, int, float)) for key in required):
                raise PreviewWriteError("character intent preview plan is incomplete")
            plan = {
                "occurrenceKey": preview_plan["occurrenceKey"],
                "podId": preview_plan["podId"],
                "ownerUserId": booker.podplay_user_id,
                "sessionId": preview_plan["sessionId"],
                "tableId": preview_plan["tableId"],
                "startTime": preview_plan["startTime"],
                "endTime": preview_plan["endTime"],
                "virtualCredits": float(preview_plan["virtualCredits"]),
                "items": preview_plan.get("items"),
            }
        else:
            admin_auth = FirebaseAuthenticator(
                root / "secrets" / "preview-auth-cache.json"
            ).authenticate(
                credentials.admin_email,
                credentials.admin_password,
                credentials.firebase_api_key,
            )
            report = inspect_preview_readiness(
                root,
                read_policy,
                PreviewReadonlyClient(read_policy, admin_auth.id_token),
                identities,
                credentials.firebase_api_key,
            )
            candidate = report.get("candidateSession")
            if not report.get("readyForBookingPreview") or not isinstance(candidate, dict):
                raise PreviewWriteError("booking readiness gate is blocked")
            credits = min(float(report["actors"]["andy-bogard"]["virtualCredits"]), 25.0)
            preview = summarize_booking_preview(
                PreviewBookingEvaluator(read_policy, booker_auth.id_token).evaluate(
                    candidate["sessionId"], candidate["tableId"], credits
                )
            )
            if not preview["readyForOrder"]:
                raise PreviewWriteError("booking preview is not ready for ORDER")
            plan = {
                "occurrenceKey": occurrence_key(
                    report["location"]["podId"], candidate["startTime"], "andy-bogard"
                ),
                "podId": report["location"]["podId"],
                "ownerUserId": booker.podplay_user_id,
                "sessionId": candidate["sessionId"],
                "tableId": candidate["tableId"],
                "startTime": candidate["startTime"],
                "endTime": candidate["endTime"],
                "virtualCredits": credits,
            }
        existing_state = ledger["matches"].get(plan["occurrenceKey"], {})
        state = dict(existing_state) if isinstance(existing_state, dict) else {}
        prepared_at = state.get("preparedAt") if isinstance(state, dict) else None
        matches = find_matching_events(
            booker_client,
            plan["podId"],
            plan["ownerUserId"],
            plan["startTime"],
            plan["endTime"],
        )
        if len(matches) > 1:
            raise PreviewWriteError("multiple matching events found; refusing to create or choose")
        writes = 0
        order_summary = state.get("order") if isinstance(state, dict) else None
        event_id = matches[0].get("id") if matches else state.get("eventId") if isinstance(state, dict) else None
        if apply and not event_id:
            prepared_at = datetime.now(timezone.utc).isoformat()
            write_config = replace(
                credentials.config,
                mode=RunMode.PREVIEW_WRITE,
                preview_write_confirmation=confirmation,
            )
            write_policy = TargetPolicy.from_config(write_config)
            save_match(
                storage,
                ledger,
                {
                    "schemaVersion": 2,
                    "targetOrigin": read_policy.target_origin,
                    "phase": "planned",
                    "preparedAt": prepared_at,
                    "plan": plan,
                },
            )
            try:
                items = plan.get("items")
                additional_items = items[1:] if isinstance(items, list) else None
                session_guests_id = plan.get("items", [{}])[0].get("sessionGuestsId") if isinstance(plan.get("items"), list) and plan["items"] else None
                order = PreviewBookingWriter(write_policy, booker_auth.id_token).order(
                    plan["sessionId"], plan["tableId"], plan["virtualCredits"], additional_items, session_guests_id
                )
                order_summary = summarize_order(order)
                event_id = order_summary["eventId"]
                writes = 1
            except PreviewWriteError:
                matches = find_matching_events(
                    booker_client,
                    plan["podId"],
                    plan["ownerUserId"],
                    plan["startTime"],
                    plan["endTime"],
                )
                if len(matches) != 1:
                    raise
                event_id = matches[0]["id"]
        if apply:
            if not isinstance(event_id, str):
                raise PreviewWriteError("booking event ID is missing after ORDER")
            event = read_back_event(booker_client, event_id, plan)
            next_state = dict(state) if isinstance(state, dict) else {}
            phase = (
                next_state.get("phase")
                if next_state.get("phase") in {"joined", "checked-in"}
                else "booked"
            )
            next_state.update(
                {
                    "schemaVersion": 2,
                    "targetOrigin": read_policy.target_origin,
                    "phase": phase,
                    "preparedAt": prepared_at
                    or datetime.now(timezone.utc).isoformat(),
                    "plan": plan,
                    "eventId": event_id,
                    "order": order_summary,
                    "event": event,
                    "intentId": selected_intent_id,
                }
            )
            save_match(storage, ledger, next_state)
            if selected_intent is not None and intent_ledger is not None:
                selected_intent.update(
                    {
                        "status": "booked",
                        # `writes` is one only when this invocation issued a
                        # new Preview ORDER.  Reconciliation of an existing
                        # event remains a zero-write operation.
                        "remoteWrites": int(selected_intent.get("remoteWrites", 0)) + writes,
                        "booking": {
                            "eventId": event_id,
                            "occurrenceKey": plan["occurrenceKey"],
                            "bookedAt": datetime.now(timezone.utc).isoformat(),
                        },
                    }
                )
                save_intent(storage, intent_ledger, selected_intent)
                _sync_world_intent_status(storage, selected_intent)
                with storage.writer_lock():
                    record_booked_locked(
                        storage, occurrence_key=plan["occurrenceKey"],
                        intent_id=selected_intent_id, event_ref=event_id,
                        booker_actor_id=booker_actor_id,
                        occurred_at=selected_intent["booking"]["bookedAt"],
                        evidence_ref=f"evidence:preview:event:{event_id}:booking-readback",
                    )
        else:
            event = None
    except (
        BookingIntentError,
        CredentialsError,
        FirebaseAuthError,
        IdentityRegistryError,
        PreviewEvaluationError,
        PreviewReadError,
        PreviewMatchLedgerError,
        PreviewWriteError,
        TargetPolicyError,
    ) as exc:
        print(f"ERROR preview booking: {exc}", file=sys.stderr)
        return 2

    print(
        f"PREVIEW CLUB BOOKING // {'APPLIED' if apply else 'DRY RUN'} // "
        f"PR #{read_policy.pull_request_number}"
    )
    print(f"occurrence: {plan['occurrenceKey']}")
    print(f"slot: {plan['startTime']} to {plan['endTime']}")
    if selected_intent_id:
        print(f"intent: {selected_intent_id}")
    if apply:
        print(f"event: {event_id} ({event['status']}, read back)")
        print(f"writes: {writes}; phase: booked")
    else:
        print(f"existing matches: {len(matches)}; writes planned: {0 if matches else 1}")
    return 0


def signup_preview_team_challenge(root: Path, apply: bool, confirmation: Optional[str], intent_id: str) -> int:
    """Create one agreed team booking, then add only its named teammates.

    The booking owner pays the advertised three guest places.  Each added
    member is read back under their own token; no admin/on-behalf participant
    write is used.  A partial run records completed members and is safe to
    resume without duplicating them.
    """
    storage = Storage(root)
    try:
        if not apply:
            if plan_preview_intent(root, intent_id, announce=False) != 0:
                raise PreviewWriteError("team challenge booking did not become ready")
        else:
            # A prior attempt may have completed the guarded booking and then
            # failed before adding a teammate.  Resume that exact event rather
            # than planning or ordering a duplicate court.
            existing_ledger = load_intent_ledger(storage)
            _, existing_intent = select_intent(existing_ledger, intent_id)
            if existing_intent.get("status") not in {"booked", "team_signup_in_progress", "team_members_registered"}:
                if book_preview_match(root, True, confirmation, intent_id) != 0:
                    raise PreviewWriteError("team challenge booking did not become ready")
        ledger = load_intent_ledger(storage)
        selected_id, intent = select_intent(ledger, intent_id)
        permitted_statuses = {"booked", "team_members_registered"} if apply else {"preview_ready"}
        if intent.get("journey") != "customer_team_signup" or intent.get("status") not in permitted_statuses:
            raise PreviewWriteError("team signup requires an agreed customer_team_signup intent")
        participants = [actor for actor in intent.get("participants", []) if isinstance(actor, str)]
        if len(participants) < 2 or len(set(participants)) != len(participants):
            raise PreviewWriteError("team signup requires two or more distinct named players")
        owner = resolve_booking_owner(intent)
        booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
        event_id = booking.get("eventId")
        if not isinstance(owner, str) or owner not in participants:
            raise PreviewWriteError("team signup booking owner is missing")
        if intent.get("bookerActorId") != owner:
            intent["bookerActorId"] = owner
            save_intent(storage, ledger, intent)
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure(participants)
        if any(not identities[actor].podplay_user_id for actor in participants):
            raise PreviewWriteError("all named team players need verified Preview identities")
        owner_identity = identities[owner]
        owner_auth = FirebaseAuthenticator(actor_auth_cache(root, owner)).authenticate(
            owner_identity.email, owner_identity.password, credentials.firebase_api_key
        )
        if not apply:
            plan = intent.get("previewPlan") if isinstance(intent.get("previewPlan"), dict) else {}
            if not isinstance(plan.get("items"), list) or any(not isinstance(item.get("sessionGuestsId"), str) for item in plan["items"] if isinstance(item, dict)):
                raise PreviewWriteError("team challenge needs a W37 guest-capacity plan")
            print(f"PREVIEW CLUB TEAM SIGNUP // DRY RUN // PR #{read_policy.pull_request_number}")
            print(f"intent: {selected_id}; members: {len(participants)}; booking write: 1; team-member writes: {len(participants) - 1}")
            print(f"slot: {plan.get('startTime')} to {plan.get('endTime')}")
            return 0
        if not isinstance(event_id, str):
            raise PreviewWriteError("team signup event is missing")
        owner_client = PreviewReadonlyClient(read_policy, owner_auth.id_token)
        event = read_back_event(owner_client, event_id, {
            "podId": intent["previewPlan"]["podId"], "startTime": intent["previewPlan"]["startTime"],
            "endTime": intent["previewPlan"]["endTime"], "items": intent["previewPlan"]["items"],
            # The product response must prove the socially designated captain
            # owns this existing booking before teammates are added.  The
            # durable preview plan deliberately never stores private user IDs.
            "ownerUserId": owner_identity.podplay_user_id,
        })
        if event.get("eventId") != event_id:
            raise PreviewWriteError("team challenge booking read-back did not match")
        if not isinstance(event.get("teamSize"), int) or event["teamSize"] <= 1:
            # A court booking with guest capacity is not a product team event.
            # Its legal path is a retained signup share link redeemed by each
            # socially named teammate, not the team-members endpoint.
            intent["status"] = "booked"
            intent["nextAction"] = "capture_shared_invitation_then_each_named_player_redeems"
            save_intent(storage, ledger, intent)
            print(f"PREVIEW CLUB TEAM SIGNUP // BOOKED SHARE ROUTE // PR #{read_policy.pull_request_number}")
            print(f"intent: {selected_id}; named recipients: {len(participants) - 1}; next: capture one player share invitation")
            return 0
        rows = {row.get("actorId"): row for row in intent.get("teamSignups", []) if isinstance(row, dict)}
        writes = 0
        for actor in participants:
            if actor == owner:
                rows[actor] = {"actorId": actor, "status": "BOOKING_OWNER"}
                continue
            identity = identities[actor]
            actor_auth = FirebaseAuthenticator(actor_auth_cache(root, actor)).authenticate(
                identity.email, identity.password, credentials.firebase_api_key
            )
            actor_client = PreviewReadonlyClient(read_policy, actor_auth.id_token)
            try:
                existing = find_actor_invitation(actor_client, event_id, identity.podplay_user_id)
            except PreviewReadError as exc:
                # A private event is correctly invisible to a teammate until
                # the owner adds them.  That is an absence of an invitation,
                # not a failed post-add verification.
                if "HTTP 403" not in str(exc):
                    raise
                existing = None
            if isinstance(existing, dict):
                prior = rows.get(actor) if isinstance(rows.get(actor), dict) else {}
                rows[actor] = {
                    "actorId": actor,
                    "status": "ACCEPTED",
                    "invitationId": existing.get("id"),
                    "observedAt": prior.get("observedAt") or datetime.now(timezone.utc).isoformat(),
                }
                continue
            if not apply:
                rows[actor] = {"actorId": actor, "status": "WOULD_ADD"}
                continue
            write_policy = TargetPolicy.from_config(replace(
                credentials.config, mode=RunMode.PREVIEW_WRITE, preview_write_confirmation=confirmation,
            ))
            result = PreviewTeamMemberWriter(write_policy, owner_auth.id_token).add_paid_by_leader(event_id, owner_identity.podplay_user_id, {
                "fullName": f"{identity.first_name} {identity.last_name}", "email": identity.email, "userId": identity.podplay_user_id,
            })
            existing = find_actor_invitation(actor_client, event_id, identity.podplay_user_id)
            if not isinstance(existing, dict):
                raise PreviewWriteError("team-member ORDER was not readable by its named player")
            rows[actor] = {
                "actorId": actor,
                "status": "ACCEPTED",
                "invitationId": result.get("invitation", {}).get("id"),
                "observedAt": datetime.now(timezone.utc).isoformat(),
            }
            writes += 1
            intent["teamSignups"] = [rows[key] for key in participants]
            intent["status"] = "team_signup_in_progress"
            save_intent(storage, ledger, intent)
        intent["teamSignups"] = [rows[key] for key in participants]
        if apply and all(row.get("status") in {"BOOKING_OWNER", "ACCEPTED"} for row in intent["teamSignups"]):
            intent["status"] = "team_members_registered"
            intent["nextAction"] = "check_in_window_then_named_player_check_ins"
            intent["remoteWrites"] = int(intent.get("remoteWrites", 0)) + writes
        save_intent(storage, ledger, intent)
        occurrence = intent.get("occurrenceKey") or booking.get("occurrenceKey")
        if apply and isinstance(occurrence, str):
            with storage.writer_lock():
                for row in intent["teamSignups"]:
                    actor = row.get("actorId")
                    observed_at = row.get("observedAt")
                    if (
                        actor != owner
                        and row.get("status") == "ACCEPTED"
                        and isinstance(actor, str)
                        and isinstance(observed_at, str)
                    ):
                        record_invitation_locked(
                            storage,
                            occurrence_key=occurrence,
                            intent_id=selected_id,
                            actor_id=actor,
                            status="accepted",
                            occurred_at=observed_at,
                            evidence_ref=(
                                f"evidence:preview:event:{event_id}:"
                                f"participant:{actor}:invitation-readback"
                            ),
                        )
        _sync_world_intent_status(storage, intent)
        print(f"PREVIEW CLUB TEAM SIGNUP // {'APPLIED' if apply else 'DRY RUN'} // PR #{read_policy.pull_request_number}")
        print(f"intent: {selected_id}; members: {len(participants)}; added: {writes}")
        for row in intent["teamSignups"]:
            print(f"- {row['actorId']}: {row['status']}")
        return 0
    except (BookingIntentError, CredentialsError, FirebaseAuthError, IdentityRegistryError, PreviewReadError,
            PreviewWriteError, PreviewMatchLedgerError, TargetPolicyError, ValueError, KeyError) as exc:
        print(f"ERROR preview team signup: {exc}", file=sys.stderr)
        return 2


def open_preview_share_invitation(root: Path, intent_id: str, confirmation: str) -> int:
    """Capture a booked player's share URL and announce availability without leaking it."""
    storage = Storage(root)
    try:
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        write_policy = TargetPolicy.from_config(replace(
            credentials.config, mode=RunMode.PREVIEW_WRITE, preview_write_confirmation=confirmation,
        ))
        ledger = load_intent_ledger(storage)
        _, intent = select_intent(ledger, intent_id)
        booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
        event_id, owner = booking.get("eventId"), intent.get("bookerActorId")
        if intent.get("journey") not in {"customer_training", "customer_team_signup"} or intent.get("status") not in {"booked", "team_signup_in_progress", "team_members_registered"}:
            raise PreviewWriteError("share invitation requires a booked player-owned intent")
        if not isinstance(event_id, str) or not isinstance(owner, str):
            raise PreviewWriteError("booked training intent is missing its event owner")
        participants = intent.get("participants")
        recipients = [actor_id for actor_id in participants if isinstance(actor_id, str) and actor_id != owner] if isinstance(participants, list) else []
        if not recipients:
            raise PreviewWriteError("booked intent is missing named invitation recipients")
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure((owner,))
        identity = identities[owner]
        auth = FirebaseAuthenticator(actor_auth_cache(root, owner)).authenticate(
            identity.email, identity.password, credentials.firebase_api_key
        )
        response = open_share_invitation(write_policy, auth.id_token, event_id)
        key = booking.get("occurrenceKey")
        if not isinstance(key, str):
            raise PreviewWriteError("booked training intent is missing occurrence key")
        record = record_share_invitation(storage, read_policy, key=key, owner_actor_id=owner, response=response)
        if isinstance(record.get("maxRedeems"), int) and record["maxRedeems"] < len(recipients):
            raise PreviewWriteError("product share invitation does not have enough redeem capacity for the named recipients")
        with storage.writer_lock():
            messages = storage.read_jsonl(storage.channel_path("club"))
            existing_ids = {row.get("id") for row in messages if isinstance(row, dict)}
            for recipient in recipients:
                message_id = f"{intent_id}:share-invitation-ready:{recipient}"
                if message_id in existing_ids:
                    continue
                storage.append_jsonl(storage.channel_path("club"), {
                    "id": message_id, "channel": "club", "from": owner, "to": [recipient],
                    "kind": "share_invitation_ready", "intentId": intent_id, "eventId": event_id,
                    "shareInvitationKey": key,
                    "text": f"{TEAM_ACTOR_NAMES.get(recipient, (recipient,))[0]}, our W37 booking is confirmed. I opened the player invitation; accept it through the player handoff when you are ready.",
                    "observedAt": datetime.now(timezone.utc).isoformat(), "remoteWrites": 1,
                })
        print(f"PREVIEW CLUB SHARE INVITATION // CAPTURED // intent: {intent_id}; invitation: {record['invitationId']}; writes: 1")
        return 0
    except (BookingIntentError, CredentialsError, FirebaseAuthError, IdentityRegistryError, PreviewWriteError, ShareInvitationError, TargetPolicyError) as exc:
        print(f"ERROR preview share invitation: {exc}", file=sys.stderr)
        return 2


def redeem_preview_training_invitation(root: Path, intent_id: str, confirmation: str, recipient_arg: Optional[str] = None) -> int:
    """Let the named teammate redeem and accept a retained training link.

    The link is an opaque local credential held only in the ignored, mode-0600
    share-invitation store.  This command neither prints it nor puts it in the
    club channel.  It records acceptance only after an owner-authenticated
    product read-back confirms the recipient's invitation.
    """
    storage = Storage(root)
    try:
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        write_policy = TargetPolicy.from_config(replace(
            credentials.config,
            mode=RunMode.PREVIEW_WRITE,
            preview_write_confirmation=confirmation,
        ))
        intent_ledger = load_intent_ledger(storage)
        _, intent = select_intent(intent_ledger, intent_id)
        booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
        participants = intent.get("participants")
        owner = intent.get("bookerActorId")
        event_id = booking.get("eventId")
        occurrence = booking.get("occurrenceKey")
        if (
            intent.get("journey") not in {"customer_training", "customer_team_signup"}
            or intent.get("status") not in {"booked", "team_signup_in_progress", "team_members_registered"}
            or not isinstance(owner, str)
            or not isinstance(participants, list)
            or not isinstance(event_id, str)
            or not isinstance(occurrence, str)
        ):
            raise PreviewWriteError("share redemption requires a booked player-owned intent")
        eligible = [actor_id for actor_id in participants if isinstance(actor_id, str) and actor_id != owner]
        recipient = recipient_arg or (eligible[0] if len(eligible) == 1 else None)
        if recipient_arg is not None and recipient_arg not in eligible:
            raise PreviewWriteError("share redemption recipient is not named on this booking")
        if not isinstance(recipient, str):
            raise PreviewWriteError("booking has multiple recipients; choose one named recipient")
        share_store = storage.load_json(
            storage.state / "share-invitations.json",
            default={"schemaVersion": 1, "invitations": {}},
        )
        invitations = share_store.get("invitations") if isinstance(share_store, dict) else None
        retained = invitations.get(occurrence) if isinstance(invitations, dict) else None
        redeem_url = retained.get("redeemUrl") if isinstance(retained, dict) else None
        if not isinstance(redeem_url, str) or not redeem_url:
            raise PreviewWriteError("no retained player share invitation is available for this booking")

        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure((owner, recipient))
        owner_identity, recipient_identity = identities[owner], identities[recipient]
        owner_auth = FirebaseAuthenticator(actor_auth_cache(root, owner)).authenticate(
            owner_identity.email, owner_identity.password, credentials.firebase_api_key
        )
        recipient_auth = FirebaseAuthenticator(actor_auth_cache(root, recipient)).authenticate(
            recipient_identity.email, recipient_identity.password, credentials.firebase_api_key
        )
        owner_client = PreviewReadonlyClient(read_policy, owner_auth.id_token)
        invitation = find_actor_invitation(owner_client, event_id, recipient_identity.podplay_user_id)
        writes = 0
        if invitation is None:
            redeem_share_invitation(write_policy, recipient_auth.id_token, redeem_url)
            writes += 1
            invitation = find_actor_invitation(owner_client, event_id, recipient_identity.podplay_user_id)
        if invitation is None:
            raise PreviewWriteError("share redemption was not visible in the booking owner's read-back")
        invitation_summary = summarize_invitation(invitation, recipient_identity.podplay_user_id)
        acceptance_preview = None
        if not invitation_summary["accepted"]:
            acceptance_preview = summarize_acceptance(
                PreviewAcceptanceEvaluator(read_policy, recipient_auth.id_token).evaluate(
                    event_id, invitation_summary["invitationId"]
                )
            )
            PreviewParticipationWriter(write_policy, recipient_auth.id_token).accept(
                event_id, invitation_summary["invitationId"]
            )
            writes += 1
            invitation = find_actor_invitation(owner_client, event_id, recipient_identity.podplay_user_id)
            if invitation is None:
                raise PreviewWriteError("accepted training invitation disappeared on read-back")
            invitation_summary = summarize_invitation(invitation, recipient_identity.podplay_user_id)
        if not invitation_summary["accepted"]:
            raise PreviewWriteError("training invitation is not accepted")

        match_ledger = load_match_ledger(storage, read_policy.target_origin)
        match = match_ledger["matches"].get(occurrence)
        if not isinstance(match, dict):
            raise PreviewWriteError("booked training match is missing from the local match ledger")
        match = dict(match)
        match["phase"] = "joined"
        participant_rows = {
            row.get("actorId"): row for row in match.get("participants", [])
            if isinstance(row, dict) and isinstance(row.get("actorId"), str)
        }
        participant_rows[owner] = {
            "actorId": owner, "role": "booking owner",
            "bookingStatus": "BOOKED", "invitationStatus": "OWNER",
        }
        participant_rows[recipient] = {
            "actorId": recipient, "role": "accepted player",
            "bookingStatus": "BOOKED", "invitationId": invitation_summary["invitationId"],
            "invitationStatus": invitation_summary["status"],
            "attendance": invitation_summary["checkInStatus"],
        }
        match["participants"] = [participant_rows[actor] for actor in [owner, *eligible] if actor in participant_rows]
        match["lifecycle"] = {
            "state": "ACCEPTED",
            "steps": {
                "booking": "BOOKED", "invitation": "SHARE_REDEEMED",
                "acceptance": "ACCEPTED", "checkIn": "NOT_OBSERVED", "play": "NOT_RECORDED",
            },
        }
        save_match(storage, match_ledger, match)
        with storage.writer_lock():
            intent_ledger = load_intent_ledger(storage)
            _, current_intent = select_intent(intent_ledger, intent_id)
            redemptions = {
                row.get("actorId"): row for row in current_intent.get("shareRedemptions", [])
                if isinstance(row, dict) and isinstance(row.get("actorId"), str)
            }
            redemptions[recipient] = {"actorId": recipient, "status": "ACCEPTED"}
            current_intent["shareRedemptions"] = [redemptions[actor] for actor in eligible if actor in redemptions]
            if current_intent.get("journey") == "customer_team_signup":
                if all(redemptions.get(actor, {}).get("status") == "ACCEPTED" for actor in eligible):
                    current_intent["status"] = "team_members_registered"
                    current_intent["nextAction"] = "check_in_window_then_named_player_check_ins"
                else:
                    current_intent["status"] = "team_signup_in_progress"
                    current_intent["nextAction"] = "await_remaining_named_player_redemptions"
            save_intent(storage, intent_ledger, current_intent)
            observed_at = datetime.now(timezone.utc).isoformat()
            record_invitation_locked(
                storage, occurrence_key=occurrence, intent_id=intent_id,
                actor_id=recipient, status=str(invitation_summary["status"]),
                occurred_at=observed_at,
                evidence_ref=f"evidence:preview:event:{event_id}:participation:{recipient}",
            )
        message_id = f"{intent_id}:share-invitation-accepted:{recipient}"
        with storage.writer_lock():
            messages = storage.read_jsonl(storage.channel_path("club"))
            if message_id not in {row.get("id") for row in messages if isinstance(row, dict)}:
                storage.append_jsonl(storage.channel_path("club"), {
                    "id": message_id, "channel": "club", "from": recipient, "to": [owner],
                    "kind": "share_invitation_accepted", "intentId": intent_id, "eventId": event_id,
                    "inReplyTo": f"{intent_id}:share-invitation-ready:{recipient}",
                    "text": f"{TEAM_ACTOR_NAMES.get(recipient, (recipient,))[0]} redeemed {TEAM_ACTOR_NAMES.get(owner, (owner,))[0]}'s invitation and accepted the W37 session.",
                    "observedAt": datetime.now(timezone.utc).isoformat(), "remoteWrites": writes,
                })
    except (BookingIntentError, CredentialsError, FirebaseAuthError, IdentityRegistryError,
            PreviewMatchLedgerError, PreviewReadError, PreviewWriteError,
            ShareInvitationError, TargetPolicyError) as exc:
        print(f"ERROR preview training invitation redemption: {exc}", file=sys.stderr)
        return 2

    print(f"PREVIEW CLUB SHARE INVITATION // REDEEMED + ACCEPTED // intent: {intent_id}; writes: {writes}")
    if acceptance_preview:
        print(f"acceptance preview: {acceptance_preview['total']:.2f}; no errors")
    return 0


def join_preview_match(
    root: Path,
    apply: bool,
    confirmation: Optional[str],
    occurrence: Optional[str] = None,
) -> int:
    storage = Storage(root)
    try:
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        ledger = load_match_ledger(storage, read_policy.target_origin)
        occurrence, state = select_match(ledger, occurrence)
        if not isinstance(state, dict) or not isinstance(state.get("eventId"), str):
            raise PreviewWriteError("create and reconcile the selected booking before joining")
        event_id = state["eventId"]
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure()
        booker = identities["andy-bogard"]
        teammate = identities["terry-bogard"]
        booker_auth = FirebaseAuthenticator(
            actor_auth_cache(root, "andy-bogard")
        ).authenticate(booker.email, booker.password, credentials.firebase_api_key)
        teammate_auth = FirebaseAuthenticator(
            actor_auth_cache(root, "terry-bogard")
        ).authenticate(teammate.email, teammate.password, credentials.firebase_api_key)
        booker_client = PreviewReadonlyClient(read_policy, booker_auth.id_token)
        invitation = find_actor_invitation(booker_client, event_id, teammate.podplay_user_id)
        writes = 0
        preview_summary = None

        if apply:
            write_config = replace(
                credentials.config,
                mode=RunMode.PREVIEW_WRITE,
                preview_write_confirmation=confirmation,
            )
            write_policy = TargetPolicy.from_config(write_config)
            if invitation is None:
                try:
                    PreviewParticipationWriter(write_policy, booker_auth.id_token).invite(
                        event_id, blue
                    )
                    writes += 1
                except PreviewWriteError:
                    invitation = find_actor_invitation(
                        booker_client, event_id, teammate.podplay_user_id
                    )
                    if invitation is None:
                        raise
                invitation = find_actor_invitation(
                    booker_client, event_id, teammate.podplay_user_id
                )
                if invitation is None:
                    raise PreviewWriteError("invitation write was not visible on read-back")

            invitation_summary = summarize_invitation(
                invitation, teammate.podplay_user_id
            )
            record_invite_sent(
                storage,
                read_policy,
                occurrence_key=occurrence,
                event_id=event_id,
                invitation=invitation,
                sender_actor_id="andy-bogard",
                recipient_actor_id="terry-bogard",
            )
            if invitation_summary["status"] not in ACCEPTED_STATUSES:
                preview_summary = summarize_acceptance(
                    PreviewAcceptanceEvaluator(
                        read_policy, teammate_auth.id_token
                    ).evaluate(event_id, invitation_summary["invitationId"])
                )
                try:
                    summarize_acceptance(
                        PreviewParticipationWriter(
                            write_policy, teammate_auth.id_token
                        ).accept(event_id, invitation_summary["invitationId"])
                    )
                    writes += 1
                except PreviewWriteError:
                    invitation = find_actor_invitation(
                        booker_client, event_id, teammate.podplay_user_id
                    )
                    if invitation is None or invitation.get("status") not in ACCEPTED_STATUSES:
                        raise
                invitation = find_actor_invitation(
                    booker_client, event_id, teammate.podplay_user_id
                )
                if invitation is None:
                    raise PreviewWriteError("accepted invitation disappeared on read-back")
            invitation_summary = summarize_invitation(
                invitation, teammate.podplay_user_id
            )
            if not invitation_summary["accepted"]:
                raise PreviewWriteError("Terry Bogard invitation is not accepted")
            state.update(
                {
                    "phase": "joined",
                    "blueInvitation": invitation_summary,
                    "participants": [
                        {"actorId": "andy-bogard", "role": "booking owner"},
                        {
                            "actorId": "terry-bogard",
                            "role": "invited player",
                            "invitationId": invitation_summary["invitationId"],
                            "invitationStatus": invitation_summary["status"],
                            "attendance": invitation_summary["checkInStatus"],
                        },
                    ],
                    "acceptancePreview": preview_summary,
                }
            )
            save_match(storage, ledger, state)
        else:
            invitation_summary = (
                summarize_invitation(invitation, teammate.podplay_user_id)
                if invitation is not None
                else None
            )
            if invitation_summary and invitation_summary["status"] not in ACCEPTED_STATUSES:
                preview_summary = summarize_acceptance(
                    PreviewAcceptanceEvaluator(
                        read_policy, teammate_auth.id_token
                    ).evaluate(event_id, invitation_summary["invitationId"])
                )
    except (
        CredentialsError,
        FirebaseAuthError,
        IdentityRegistryError,
        PreviewMatchLedgerError,
        PreviewReadError,
        PreviewWriteError,
        TargetPolicyError,
    ) as exc:
        print(f"ERROR preview participation: {exc}", file=sys.stderr)
        return 2

    print(
        f"PREVIEW CLUB PARTICIPATION // {'APPLIED' if apply else 'DRY RUN'} // "
        f"PR #{read_policy.pull_request_number}"
    )
    print(f"event: {event_id}")
    print(f"occurrence: {occurrence}")
    if invitation_summary:
        print(
            f"Teammate invitation: {invitation_summary['invitationId']} "
            f"({invitation_summary['status']})"
        )
    else:
        print("Teammate invitation: missing")
    if preview_summary:
        print(f"acceptance preview: ${preview_summary['total']:.2f}; no errors")
    if apply:
        print(f"writes: {writes}; phase: joined")
    else:
        if invitation_summary is None:
            planned = 2
        elif invitation_summary["accepted"]:
            planned = 0
        else:
            planned = 1
        print(f"writes planned: {planned}")
    return 0


def check_in_preview_match(
    root: Path,
    apply: bool,
    confirmation: Optional[str],
    occurrence: Optional[str] = None,
) -> int:
    storage = Storage(root)
    try:
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        ledger = load_match_ledger(storage, read_policy.target_origin)
        occurrence, state = select_match(ledger, occurrence)
        plan = state.get("plan") if isinstance(state, dict) else None
        if (
            not isinstance(state, dict)
            or not isinstance(plan, dict)
            or not isinstance(state.get("eventId"), str)
            or not isinstance(plan.get("startTime"), str)
        ):
            raise PreviewWriteError("join the manual match before checking in")
        event_id = state["eventId"]
        start_time = datetime.fromisoformat(plan["startTime"].replace("Z", "+00:00"))
        end_value = plan.get("endTime")
        if not isinstance(end_value, str):
            raise PreviewWriteError("match plan is missing its end time")
        end_time = datetime.fromisoformat(end_value.replace("Z", "+00:00"))
        eligible, window_detail = check_in_window(start_time, end_time, datetime.now(timezone.utc))
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure()
        booker = identities["andy-bogard"]
        teammate = identities["terry-bogard"]
        booker_auth = FirebaseAuthenticator(
            actor_auth_cache(root, "andy-bogard")
        ).authenticate(booker.email, booker.password, credentials.firebase_api_key)
        teammate_auth = FirebaseAuthenticator(
            actor_auth_cache(root, "terry-bogard")
        ).authenticate(teammate.email, teammate.password, credentials.firebase_api_key)
        booker_client = PreviewReadonlyClient(read_policy, booker_auth.id_token)
        owner_invitation = find_owner_invitation(booker_client, event_id)
        blue_invitation = find_actor_invitation(
            booker_client, event_id, teammate.podplay_user_id
        )
        if blue_invitation is None or blue_invitation.get("status") not in ACCEPTED_STATUSES:
            raise PreviewWriteError("Terry Bogard must accept before check-in")
        participants = [
            ("Andy Bogard", booker_auth.id_token, owner_invitation),
            ("Terry Bogard", teammate_auth.id_token, blue_invitation),
        ]
        statuses = {
            label: {
                "invitationId": invitation["id"],
                "status": check_in_status(invitation),
            }
            for label, _token, invitation in participants
        }
        writes = 0
        if apply:
            if not eligible:
                raise PreviewWriteError(
                    f"check-in window {window_detail}"
                )
            write_config = replace(
                credentials.config,
                mode=RunMode.PREVIEW_WRITE,
                preview_write_confirmation=confirmation,
            )
            write_policy = TargetPolicy.from_config(write_config)
            for label, token, invitation in participants:
                if statuses[label]["status"] == "CHECKED_IN":
                    continue
                try:
                    PreviewParticipationWriter(write_policy, token).check_in(
                        event_id, invitation["id"]
                    )
                    writes += 1
                except PreviewWriteError:
                    refreshed = (
                        find_owner_invitation(booker_client, event_id)
                        if invitation["id"] == "00000000-0000-0000-0000-000000000000"
                        else find_actor_invitation(
                            booker_client, event_id, teammate.podplay_user_id
                        )
                    )
                    if refreshed is None or check_in_status(refreshed) != "CHECKED_IN":
                        raise
                refreshed = (
                    find_owner_invitation(booker_client, event_id)
                    if invitation["id"] == "00000000-0000-0000-0000-000000000000"
                    else find_actor_invitation(booker_client, event_id, teammate.podplay_user_id)
                )
                if refreshed is None:
                    raise PreviewWriteError("invitation disappeared after check-in")
                statuses[label]["status"] = check_in_status(refreshed)
            if any(row["status"] != "CHECKED_IN" for row in statuses.values()):
                raise PreviewWriteError("player check-in read-back is incomplete")
            state.update({"phase": "checked-in", "checkIns": statuses})
            save_match(storage, ledger, state)
    except (ValueError, CredentialsError, FirebaseAuthError, IdentityRegistryError,
            PreviewMatchLedgerError, PreviewReadError, PreviewWriteError,
            TargetPolicyError) as exc:
        print(f"ERROR preview check-in: {exc}", file=sys.stderr)
        return 2

    print(
        f"PREVIEW CLUB CHECK-IN // {'APPLIED' if apply else 'DRY RUN'} // "
        f"PR #{read_policy.pull_request_number}"
    )
    print(f"event: {event_id}; window: one hour before {plan['startTime']} through {end_value}; eligible: {str(eligible).lower()}")
    print(f"occurrence: {occurrence}")
    for label, row in statuses.items():
        print(f"- {label}: {row['status']}")
    if apply:
        print(f"writes: {writes}; phase: checked-in")
    else:
        planned = sum(row["status"] != "CHECKED_IN" for row in statuses.values())
        print(f"writes planned when eligible: {planned}")
    return 0


def check_in_training_player(
    root: Path,
    apply: bool,
    confirmation: Optional[str],
    intent_id: str,
    actor_id: str,
) -> int:
    """Let one named, accepted training player record their own arrival.

    This is intentionally per-player: King arriving is not evidence that Mai
    arrived, and vice versa.  A dry run performs only authenticated reads.
    """
    storage = Storage(root)
    try:
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        ledger = load_intent_ledger(storage)
        _, intent = select_intent(ledger, intent_id)
        actor_id = canonical_actor_id(actor_id)
        participants = intent.get("participants")
        booking = intent.get("booking")
        plan = intent.get("previewPlan")
        if (
            intent.get("journey") != "customer_training"
            or intent.get("status") != "booked"
            or not isinstance(participants, list)
            or actor_id not in participants
            or not isinstance(booking, dict)
            or not isinstance(plan, dict)
            or not isinstance(booking.get("eventId"), str)
            or not isinstance(plan.get("startTime"), str)
            or not isinstance(plan.get("endTime"), str)
        ):
            raise PreviewWriteError("training check-in requires one booked training participant")
        start_time = datetime.fromisoformat(plan["startTime"].replace("Z", "+00:00"))
        end_time = datetime.fromisoformat(plan["endTime"].replace("Z", "+00:00"))
        eligible, window_detail = check_in_window(start_time, end_time, datetime.now(timezone.utc))
        owner_id = intent.get("bookerActorId")
        if not isinstance(owner_id, str) or owner_id not in participants:
            raise PreviewWriteError("booked training intent is missing its player owner")
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure((owner_id, actor_id))
        owner = identities[owner_id]
        actor = identities[actor_id]
        owner_auth = FirebaseAuthenticator(actor_auth_cache(root, owner_id)).authenticate(
            owner.email, owner.password, credentials.firebase_api_key
        )
        actor_auth = FirebaseAuthenticator(actor_auth_cache(root, actor_id)).authenticate(
            actor.email, actor.password, credentials.firebase_api_key
        )
        event_id = booking["eventId"]
        owner_client = PreviewReadonlyClient(read_policy, owner_auth.id_token)
        invitation = (
            find_owner_invitation(owner_client, event_id)
            if actor_id == owner_id
            else find_actor_invitation(owner_client, event_id, actor.podplay_user_id)
        )
        if invitation is None:
            raise PreviewWriteError("player invitation has not been read back")
        if actor_id != owner_id and invitation.get("status") not in ACCEPTED_STATUSES:
            raise PreviewWriteError("player must accept the training invitation before check-in")
        status = check_in_status(invitation)
        writes = 0
        if apply:
            # A later idempotent reconciliation may need to surface an
            # already-read-back arrival after the event has ended.  It must
            # never reopen the product check-in endpoint outside its window.
            if status != "CHECKED_IN" and not eligible:
                raise PreviewWriteError(f"training check-in window {window_detail}")
            write_policy = TargetPolicy.from_config(replace(
                credentials.config,
                mode=RunMode.PREVIEW_WRITE,
                preview_write_confirmation=confirmation,
            ))
            if status != "CHECKED_IN":
                PreviewParticipationWriter(write_policy, actor_auth.id_token).check_in(event_id, invitation["id"])
                writes = 1
                refreshed = (
                    find_owner_invitation(owner_client, event_id)
                    if actor_id == owner_id
                    else find_actor_invitation(owner_client, event_id, actor.podplay_user_id)
                )
                if refreshed is None or check_in_status(refreshed) != "CHECKED_IN":
                    raise PreviewWriteError("player check-in read-back is incomplete")
                status = "CHECKED_IN"
            match_ledger = load_match_ledger(storage, read_policy.target_origin)
            occurrence = booking.get("occurrenceKey")
            if isinstance(occurrence, str) and isinstance(match_ledger["matches"].get(occurrence), dict):
                match = dict(match_ledger["matches"][occurrence])
                rows = match.get("participants") if isinstance(match.get("participants"), list) else []
                rows = [row for row in rows if isinstance(row, dict) and row.get("actorId") != actor_id]
                rows.append({
                    "actorId": actor_id,
                    "role": "booking owner" if actor_id == owner_id else "accepted training player",
                    "bookingStatus": "BOOKED",
                    "invitationStatus": "OWNER" if actor_id == owner_id else "ACCEPTED",
                    "attendance": status,
                })
                match["participants"] = rows
                match["phase"] = "checked-in" if status == "CHECKED_IN" else match.get("phase", "booked")
                lifecycle = match.get("lifecycle") if isinstance(match.get("lifecycle"), dict) else {}
                steps = lifecycle.get("steps") if isinstance(lifecycle.get("steps"), dict) else {}
                steps["checkIn"] = "PARTIALLY_CHECKED_IN" if status == "CHECKED_IN" else "NOT_OBSERVED"
                lifecycle["steps"] = steps
                lifecycle["state"] = "CHECK_IN_RECORDED" if status == "CHECKED_IN" else lifecycle.get("state", "BOOKED")
                match["lifecycle"] = lifecycle
                save_match(storage, match_ledger, match)
                if status == "CHECKED_IN":
                    record_training_check_in(
                        storage,
                        occurrence_key=occurrence,
                        event_id=event_id,
                        actor_id=actor_id,
                        actor_label=_actor_display_name(actor_id),
                        recipient_actor_ids=participants,
                        intent_id=intent_id,
                    )
    except (ValueError, BookingIntentError, CredentialsError, FirebaseAuthError,
            IdentityRegistryError, PreviewMatchLedgerError, PreviewReadError,
            PreviewWriteError, TargetPolicyError) as exc:
        print(f"ERROR training check-in: {exc}", file=sys.stderr)
        return 2

    print(f"PREVIEW CLUB TRAINING CHECK-IN // {'APPLIED' if apply else 'DRY RUN'} // PR #{read_policy.pull_request_number}")
    print(f"intent: {intent_id}; player: {actor_id}; event: {event_id}")
    print(f"window: {start_time.isoformat()} minus 1 hour through {end_time.isoformat()}; {window_detail}")
    print(f"check-in: {status}; writes: {writes if apply else 0}")
    return 0


def _actor_display_name(actor_id: str) -> str:
    """Return a safe human label for either team or ordinary player identities."""
    name = TEAM_ACTOR_NAMES.get(actor_id) or ACTOR_NAMES.get(actor_id)
    if isinstance(name, tuple):
        return " ".join(part for part in name if isinstance(part, str) and part)
    return actor_id


def list_preview_matches(root: Path) -> int:
    storage = Storage(root)
    try:
        credentials = PreviewCredentials.load(root)
        policy = TargetPolicy.from_config(credentials.config)
        ledger = load_match_ledger(storage, policy.target_origin)
        index = sanitized_match_index(ledger)
    except (CredentialsError, PreviewMatchLedgerError, TargetPolicyError) as exc:
        print(f"ERROR preview matches: {exc}", file=sys.stderr)
        return 2

    print(f"PREVIEW CLUB MATCHES // PR #{policy.pull_request_number}")
    if not index["matches"]:
        print("no matches")
        return 0
    for row in index["matches"]:
        marker = "*" if row["active"] else " "
        print(
            f"{marker} {row['occurrenceKey']} // {row.get('phase') or 'unknown'} // "
            f"{row.get('startTime') or 'unknown'} // {row.get('eventId') or 'no event'}"
        )
    return 0


def select_preview_match(root: Path, occurrence: str) -> int:
    storage = Storage(root)
    try:
        credentials = PreviewCredentials.load(root)
        policy = TargetPolicy.from_config(credentials.config)
        ledger = load_match_ledger(storage, policy.target_origin)
        select_active(storage, ledger, occurrence)
    except (CredentialsError, PreviewMatchLedgerError, TargetPolicyError) as exc:
        print(f"ERROR preview select: {exc}", file=sys.stderr)
        return 2
    print(f"ACTIVE PREVIEW MATCH // {occurrence} // PR #{policy.pull_request_number}")
    return 0


def preview_match_status(root: Path, occurrence: Optional[str] = None) -> int:
    storage = Storage(root)
    try:
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        ledger = load_match_ledger(storage, read_policy.target_origin)
        occurrence, state = select_match(ledger, occurrence)
        plan = state.get("plan") if isinstance(state, dict) else None
        if (
            not isinstance(state, dict)
            or not isinstance(plan, dict)
            or not isinstance(state.get("eventId"), str)
            or not isinstance(plan.get("startTime"), str)
        ):
            raise PreviewReadError("selected match checkpoint is incomplete")
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure()
        booker = identities["andy-bogard"]
        teammate = identities["terry-bogard"]
        booker_auth = FirebaseAuthenticator(
            actor_auth_cache(root, "andy-bogard")
        ).authenticate(booker.email, booker.password, credentials.firebase_api_key)
        client = PreviewReadonlyClient(read_policy, booker_auth.id_token)
        event = read_back_event(client, state["eventId"], plan)
        owner_invitation = find_owner_invitation(client, state["eventId"])
        blue_invitation = find_actor_invitation(
            client, state["eventId"], teammate.podplay_user_id
        )
        if blue_invitation is None:
            raise PreviewReadError("Terry Bogard invitation is missing")
        start_time = datetime.fromisoformat(plan["startTime"].replace("Z", "+00:00"))
        observed_at = datetime.now(timezone.utc)
        result = classify_match_status(
            blue_invitation,
            owner_invitation,
            start_time,
            observed_at,
        )
        owner_check_in = check_in_status(owner_invitation)
        blue_check_in = check_in_status(blue_invitation)
        previous_snapshot = storage.load_json(
            storage.state / "preview-match-status.json", default={}
        )
        previous_venue = (
            previous_snapshot.get("venue")
            if isinstance(previous_snapshot, dict)
            and isinstance(previous_snapshot.get("venue"), dict)
            else {}
        )
        readiness = storage.load_json(
            storage.state / "preview-readiness.json", default={}
        )
        location = readiness.get("location") if isinstance(readiness, dict) else None
        candidate = (
            readiness.get("candidateSession") if isinstance(readiness, dict) else None
        )
        if not isinstance(location, dict) or location.get("podId") != plan.get("podId"):
            location = {}
        if (
            not isinstance(candidate, dict)
            or candidate.get("sessionId") != plan.get("sessionId")
        ):
            candidate = {}
        order = state.get("order") if isinstance(state.get("order"), dict) else {}
        previous_journey = (
            previous_snapshot.get("journey")
            if isinstance(previous_snapshot, dict)
            else None
        )
        prepared_at = state.get("preparedAt")
        if not isinstance(prepared_at, str) and isinstance(previous_journey, dict):
            prepared_at = previous_journey.get("preparedAt")
        if not isinstance(prepared_at, str):
            prepared_at = datetime.fromtimestamp(
                (storage.state / "preview-matches.json").stat().st_mtime,
                timezone.utc,
            ).isoformat()
        court_assignment = (
            "Auto-assigned court"
            if candidate.get("tableType") == "AUTO"
            else candidate.get("tableType")
            or previous_venue.get("courtAssignment")
            or (
                "Auto-assigned court"
                if "@auto@" in str(plan.get("tableId") or "")
                else None
            )
        )
        storage.write_json(
            storage.state / "preview-match-status.json",
            {
                "schemaVersion": 1,
                "observedAt": observed_at.isoformat(),
                "pullRequest": read_policy.pull_request_number,
                "activeOccurrenceKey": ledger.get("activeOccurrenceKey"),
                "matchIndex": sanitized_match_index(ledger)["matches"],
                "result": result,
                "event": event,
                "venue": {
                    "areaName": location.get("areaName"),
                    "podName": location.get("podName"),
                    "timezone": location.get("timezone"),
                    "courtAssignment": court_assignment,
                    "periodType": candidate.get("periodType")
                    or previous_venue.get("periodType"),
                    "tablesLeftAtSelection": candidate.get("tablesLeft")
                    if candidate
                    else previous_venue.get("tablesLeftAtSelection"),
                },
                "journey": {
                    "phase": state.get("phase"),
                    "preparedAt": prepared_at,
                    "occurrenceKey": plan.get("occurrenceKey"),
                    "orderTotal": order.get("total"),
                    "currency": order.get("currency"),
                    "virtualCreditsUsed": order.get("virtualCredits"),
                },
                "blueInvitation": {
                    "status": blue_invitation["status"],
                },
                "checkIns": {
                    "redCaptain": owner_check_in,
                    "blueCaptain": blue_check_in,
                },
            },
        )
    except (
        ValueError,
        CredentialsError,
        FirebaseAuthError,
        IdentityRegistryError,
        PreviewMatchLedgerError,
        PreviewReadError,
        TargetPolicyError,
    ) as exc:
        print(f"ERROR preview match status: {exc}", file=sys.stderr)
        return 2

    print(f"PREVIEW CLUB MATCH // {result} // PR #{read_policy.pull_request_number}")
    print(f"event: {event['eventId']} ({event['status']})")
    print(f"occurrence: {occurrence}")
    venue = " / ".join(
        value
        for value in (location.get("areaName"), location.get("podName"))
        if isinstance(value, str) and value
    )
    print(f"venue: {venue or plan['podId']}")
    if location.get("timezone"):
        print(f"venue timezone: {location['timezone']}")
    if court_assignment:
        print(f"court: {court_assignment.lower()}")
    print(f"slot: {event['startTime']} to {event['endTime']}")
    print(f"Teammate invitation: {blue_invitation['status']}")
    print(f"Booker check-in: {owner_check_in}; teammate check-in: {blue_check_in}")
    print("remote writes: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
