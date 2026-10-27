"""Repository command-line interface."""

import argparse
import json
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
from .event_observation import observe_finished_events, observe_finished_training_sessions
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
    UnifiedBookingQuoteEvaluator,
    summarize_booking_preview,
    summarize_unified_booking_quote,
)
from .preview_booking import (
    PreviewBookingWriter,
    UnifiedBookingWriter,
    find_matching_events,
    occurrence_key,
    payment_method_id,
    read_back_event,
    summarize_order,
    summarize_unified_order,
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
from .preview_event_signup import (
    PreviewEventSignupWriter,
    find_event_signup,
    read_captain_attendance_invitation,
)
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
from .continuous_unified import (
    ContinuousResearchError,
    check_in_arrivals as check_in_continuous_unified_arrivals,
    run_campaigns as run_continuous_unified_campaigns,
)
from .ambient_health import AmbientHealthError, check as check_ambient_health
from .server import serve
from .storage import Storage
from .target_policy import TargetPolicy, TargetPolicyError
from .team_challenges import (
    TeamChallengeError,
    accept_challenge,
    plan_challenge,
    propose_captain_challenge,
    resolve_booking_owner,
)
from .staff_team_event import StaffTeamEventError, acknowledge as acknowledge_staff_team_event, offer as offer_staff_team_event
from .result_requests import report_finished_event_result
from .training_sessions import (
    TrainingSessionError,
    accept as accept_training,
    invite_third_player,
    propose as propose_training,
    withdraw_third_player_invitation,
)
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
from .team_activation import record_verified_team_activation
from .facts import append_fact, load_facts
from .protocol import append_protocol_facts_locked
from .event_lifecycle import (
    ensure_planned_locked,
    record_booked_locked,
    record_invitation_locked,
    record_window_locked,
)
from .projections import rebuild_projections
from .self_assets import SelfAssetValidationError
from .product_research import (
    ProductResearchError,
    acknowledge_verification,
    create_verification_request,
    discover_team_signup_context_with_client,
    feature_contract,
    issue_candidate,
    research_snapshot,
    run_response_tick as run_research_response_tick,
)
from .continuity import ContinuityError, export_bundle, import_bundle, inspect_bundle
from .season_recovery import (
    SeasonRecoveryError,
    approve as approve_season_recovery,
    apply as apply_season_recovery,
    plan as plan_season_recovery,
    request as request_season_recovery,
    snapshot as season_recovery_snapshot,
)
from .control_plane import (
    ControlPlaneError,
    acknowledge as acknowledge_control_notice,
    acknowledge_awareness as acknowledge_control_awareness,
    announce as announce_control_notice,
    acknowledge_handoff as acknowledge_research_handoff,
    acknowledge_release_notice as acknowledge_release,
    authorize_rate_cards_planning,
    request_rate_cards_pricing_basis,
    propose_rate_cards_pricing_basis as create_rate_cards_pricing_basis_proposal,
    respond_to_rate_cards_pricing_basis,
    preauthorize_rate_cards_configuration_run,
    offer_rate_cards_future_pilot_interest,
    respond_to_rate_cards_future_pilot_interest,
    request_rate_cards_price_verification,
    respond_to_rate_cards_price_verification,
    record_rate_cards_price_verification_observation,
    research_handoff as create_research_handoff,
    release_notice as create_release_notice,
    season_notice as create_season_notice,
    tick as tick_control_plane,
)
from .research_rewards import (
    ResearchRewardError,
    apply as apply_research_reward,
    approve as approve_research_reward,
    offer as offer_research_reward,
    respond as respond_research_reward,
    snapshot as research_rewards_snapshot,
)
from .configuration_experiments import (
    ConfigurationExperimentError,
    add_diary_note as add_configuration_diary_note,
    decide_configuration_exploration,
    discover_live_configuration,
    discover_live_scope_path,
    experiment_snapshot,
    issue_technical_handoff,
    record_configuration_observation,
    propose_configuration_change,
    prepare_rate_cards_change_packet,
    prepare_rate_cards_configuration_proposal,
    render_rate_cards_dry_run_manifest,
    enact_rate_cards_pod_pilot,
    record_rate_cards_enactment_scope_readback,
    prepare_rate_cards_price_prediction,
    adopt_rate_cards_checkout_tax_tolerance,
    refresh_uncertainty as refresh_configuration_uncertainty,
    request_configuration_exploration,
    workflow_contract as configuration_workflow_contract,
)
from .research_portfolio import (
    ResearchPortfolioError,
    portfolio_snapshot,
    sync_rate_cards_experiment,
)
from .staff_consultation import (
    StaffConsultationError,
    attach_rate_cards_scope_readback,
    propose_rate_cards_plan,
    revise_rate_cards_plan,
    respond as respond_staff_consultation,
    snapshot as staff_consultation_snapshot,
)
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

    browser_journey = subparsers.add_parser(
        "browser-journey",
        help="claim or record one named Sim's local visible-browser work item",
    )
    browser_journey_subcommands = browser_journey.add_subparsers(dest="browser_journey_command", required=True)
    browser_journey_claim = browser_journey_subcommands.add_parser("claim", help="let the named Sim take the next visible-browser step")
    browser_journey_claim.add_argument("--job", required=True, dest="job_key")
    browser_journey_claim.add_argument("--actor", required=True, dest="actor_id")
    browser_journey_claim.add_argument("--at", help="observation time; defaults to now")
    browser_journey_observe = browser_journey_subcommands.add_parser("observe", help="record a bounded visible-browser observation")
    browser_journey_observe.add_argument("--job", required=True, dest="job_key")
    browser_journey_observe.add_argument("--actor", required=True, dest="actor_id")
    browser_journey_observe.add_argument(
        "--kind", required=True,
        choices=["visible_observation", "visible_action_completed", "unexpected_ui", "browser_pass"],
    )
    browser_journey_observe.add_argument("--at", help="observation time; defaults to now")
    browser_journey_reconcile = browser_journey_subcommands.add_parser(
        "reconcile-booking", help="reconcile a visibly confirmed player booking into the social ledger"
    )
    browser_journey_reconcile.add_argument("--intent", required=True, dest="intent_id")
    browser_journey_reconcile.add_argument("--owner", required=True, dest="owner_actor_id")
    browser_journey_reconcile.add_argument("--venue", required=True, dest="venue_label")
    browser_journey_reconcile.add_argument("--start", required=True, dest="start_time")
    browser_journey_reconcile.add_argument("--end", required=True, dest="end_time")
    browser_journey_reconcile.add_argument("--group-size", required=True, type=int, dest="group_size")
    browser_journey_reconcile.add_argument("--at", help="observation time; defaults to now")
    browser_journey_recover = browser_journey_subcommands.add_parser(
        "recover-pair-booking", help="record a new browser-confirmed pair booking after a prior social agreement"
    )
    browser_journey_recover.add_argument("--source-intent", required=True, dest="source_intent_id")
    browser_journey_recover.add_argument("--owner", required=True, dest="owner_actor_id")
    browser_journey_recover.add_argument("--venue", required=True, dest="venue_label")
    browser_journey_recover.add_argument("--start", required=True, dest="start_time")
    browser_journey_recover.add_argument("--end", required=True, dest="end_time")
    browser_journey_recover.add_argument("--group-size", required=True, type=int, dest="group_size")
    browser_journey_recover.add_argument("--at", help="observation time; defaults to now")
    browser_journey_accept = browser_journey_subcommands.add_parser(
        "reconcile-invitation", help="reconcile a visibly confirmed player invitation acceptance into the local ledger"
    )
    browser_journey_accept.add_argument("--intent", required=True, dest="intent_id")
    browser_journey_accept.add_argument("--recipient", required=True, dest="recipient_actor_id")
    browser_journey_accept.add_argument("--at", help="observation time; defaults to now")
    browser_journey_check_in = browser_journey_subcommands.add_parser(
        "reconcile-staff-check-in", help="reconcile one visibly confirmed staff-browser check-in into the local ledger"
    )
    browser_journey_check_in.add_argument("--intent", required=True, dest="intent_id")
    browser_journey_check_in.add_argument("--participant", required=True, dest="participant_actor_id")
    browser_journey_check_in.add_argument("--at", help="observation time; defaults to now")

    worker_dispatch = subparsers.add_parser(
        "worker-dispatch",
        help="claim and report one bounded economical Sim worker contract",
    )
    worker_dispatch_subcommands = worker_dispatch.add_subparsers(dest="worker_dispatch_command", required=True)
    worker_dispatch_next = worker_dispatch_subcommands.add_parser(
        "next", help="claim the oldest runnable contract and print its secret-free launch packet"
    )
    worker_dispatch_next.add_argument("--worker", required=True, dest="worker_id")
    worker_dispatch_active = worker_dispatch_subcommands.add_parser(
        "active", help="read the one already-claimed secret-free launch packet for a worker"
    )
    worker_dispatch_active.add_argument("--worker", required=True, dest="worker_id")
    worker_dispatch_runbook = worker_dispatch_subcommands.add_parser(
        "runbook", help="read the bounded evidence-first routine for an already-active worker"
    )
    worker_dispatch_runbook.add_argument("--worker", required=True, dest="worker_id")
    worker_dispatch_launch = worker_dispatch_subcommands.add_parser(
        "launch", help="optionally start one bounded economical Codex worker for the next queued contract"
    )
    worker_dispatch_launch.add_argument("--worker", required=True, dest="worker_id")
    worker_dispatch_launch.add_argument(
        "--execute", action="store_true",
        help="atomically claim and start the worker; without it this is a non-mutating queue preview",
    )
    worker_dispatch_cycle = worker_dispatch_subcommands.add_parser(
        "cycle", help="run one single-flight outer-dispatcher cycle"
    )
    worker_dispatch_cycle.add_argument("--worker", required=True, dest="worker_id")
    worker_dispatch_cycle.add_argument(
        "--execute", action="store_true",
        help="reap an exited worker and start at most one queued worker; without it this is a non-mutating preview",
    )
    worker_dispatch_reap = worker_dispatch_subcommands.add_parser(
        "reap", help="quarantine exited economical workers that did not return a receipt"
    )
    worker_dispatch_recover = worker_dispatch_subcommands.add_parser(
        "recover-unobserved", help="release an older dispatcher-side failure that has no browser observation"
    )
    worker_dispatch_recover.add_argument("--contract", required=True, dest="contract_key")
    worker_dispatch_quarantine = worker_dispatch_subcommands.add_parser(
        "quarantine-legacy-release", help="quarantine a pre-safety-fix blocked receipt that was requeued"
    )
    worker_dispatch_quarantine.add_argument("--contract", required=True, dest="contract_key")
    worker_dispatch_report = worker_dispatch_subcommands.add_parser(
        "report", help="record one bounded economical worker receipt"
    )
    worker_dispatch_report.add_argument("--contract", required=True, dest="contract_key")
    worker_dispatch_report.add_argument("--worker", required=True, dest="worker_id")
    worker_dispatch_report.add_argument(
        "--outcome", required=True,
        choices=["visible_progress", "unexpected_ui", "blocked", "not_started", "pass"],
    )
    worker_dispatch_report.add_argument("--diary", required=True, dest="diary_line")
    worker_dispatch_report.add_argument("--receipt", dest="receipt_ref", help="local worker-bootstrap receipt path for visible outcomes")

    staff_follow_up = subparsers.add_parser("staff-follow-up", help="route one Riley initiative follow-up after a staff-worker decision")
    staff_follow_up_subcommands = staff_follow_up.add_subparsers(dest="staff_follow_up_command", required=True)
    staff_follow_up_route = staff_follow_up_subcommands.add_parser("route", help="write the normal non-coercive follow-up for one named initiative")
    staff_follow_up_route.add_argument("--intent", required=True, dest="intent_id")
    staff_follow_up_route.add_argument("--at", help="observation time; defaults to now")

    staff_dispatch = subparsers.add_parser("staff-dispatch", help="claim and report one economical staff-worker contract")
    staff_dispatch_subcommands = staff_dispatch.add_subparsers(dest="staff_dispatch_command", required=True)
    staff_dispatch_next = staff_dispatch_subcommands.add_parser("next", help="claim one queued staff contract")
    staff_dispatch_next.add_argument("--worker", required=True, dest="worker_id")
    staff_dispatch_active = staff_dispatch_subcommands.add_parser("active", help="read one active secret-free staff contract")
    staff_dispatch_active.add_argument("--worker", required=True, dest="worker_id")
    staff_dispatch_launch = staff_dispatch_subcommands.add_parser("launch", help="optionally start one economical Riley worker")
    staff_dispatch_launch.add_argument("--worker", required=True, dest="worker_id")
    staff_dispatch_launch.add_argument("--execute", action="store_true", help="claim and start one staff worker; without it this is a non-mutating preview")
    staff_dispatch_reap = staff_dispatch_subcommands.add_parser("reap", help="report exited staff workers that did not return a receipt")
    staff_dispatch_report = staff_dispatch_subcommands.add_parser("report", help="record a bounded staff-worker outcome")
    staff_dispatch_report.add_argument("--contract", required=True, dest="contract_key")
    staff_dispatch_report.add_argument("--worker", required=True, dest="worker_id")
    staff_dispatch_report.add_argument("--outcome", required=True, choices=["routed", "pass", "blocked"])
    staff_dispatch_report.add_argument("--diary", required=True, dest="diary_line")

    secure_channel = subparsers.add_parser("secure-channel", help="retain and deliver browser-created invitations privately")
    secure_channel_subcommands = secure_channel.add_subparsers(dest="secure_channel_command", required=True)
    secure_retain = secure_channel_subcommands.add_parser("retain-browser-invite", help="read one visible invite URL from stdin into the owner's private mailbox")
    secure_retain.add_argument("--intent", required=True, dest="intent_id")
    secure_retain.add_argument("--owner", required=True, dest="owner_actor_id")
    secure_retain.add_argument("--origin", required=True, dest="expected_origin")
    secure_deliver = secure_channel_subcommands.add_parser("deliver", help="immediately deliver a retained invitation to named private mailboxes")
    secure_deliver.add_argument("--intent", required=True, dest="intent_id")
    secure_deliver.add_argument("--owner", required=True, dest="owner_actor_id")
    secure_deliver.add_argument("--recipient", required=True, action="append", dest="recipient_actor_ids")

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

    research = subparsers.add_parser(
        "research", help="manage explicit, secret-free feature verification requests"
    )
    research_subcommands = research.add_subparsers(dest="research_command", required=True)
    contract = research_subcommands.add_parser("contract", help="show the bounded feature contract")
    contract.add_argument("--feature", required=True, choices=["team-signup"])
    request = research_subcommands.add_parser("request", help="ask named players or staff to verify a feature")
    request.add_argument("--feature", required=True, choices=["team-signup"])
    request.add_argument("--requester", required=True, choices=["sofia", "riley"])
    request.add_argument("--verifier", action="append", required=True, help="named player or staff actor; repeat")
    acknowledge = research_subcommands.add_parser("acknowledge", help="record one named verifier's accept or pass")
    acknowledge.add_argument("--probe", required=True)
    acknowledge.add_argument("--actor", required=True)
    acknowledge.add_argument("--decision", required=True, choices=["accept", "pass"])
    discover = research_subcommands.add_parser("discover", help="run the accepted team-signup GET-only configuration read-back")
    discover.add_argument("--probe", required=True)
    candidate = research_subcommands.add_parser("issue-candidate", help="record a non-regression issue candidate after read-back")
    candidate.add_argument("--probe", required=True)
    candidate.add_argument("--reason", required=True)
    research_tick = research_subcommands.add_parser("tick", help="run bounded named-verifier response turns for one local research request")
    research_tick.add_argument("--probe", required=True)
    research_tick.add_argument("--turns", type=int, default=1)
    research_subcommands.add_parser("status", help="show safe feature contracts and probe state")
    research_continuous = research_subcommands.add_parser(
        "continuous-unified",
        help="run the bounded normal-use off-peak and peak Unified Booking campaigns",
    )
    research_continuous.add_argument("--apply", action="store_true", required=True)
    research_continuous.add_argument("--confirm-origin", required=True, help="exact approved Preview origin")
    research_arrivals = research_subcommands.add_parser(
        "continuous-unified-arrivals",
        help="let named campaign players accept and perform their own check-in only when the product arrival window opens",
    )
    research_arrivals.add_argument("--apply", action="store_true", required=True)
    research_arrivals.add_argument("--confirm-origin", required=True, help="exact approved Preview origin")

    health = subparsers.add_parser("health", help="run isolated, safe public-app health observations")
    health_subcommands = health.add_subparsers(dest="health_command", required=True)
    health_subcommands.add_parser("check", help="check the public login shell and record only a status transition")
    reward = research_subcommands.add_parser("reward", help="offer, accept, approve, and guarded-apply research recognition rewards")
    reward_subcommands = reward.add_subparsers(dest="research_reward_command", required=True)
    reward_offer = reward_subcommands.add_parser("offer", help="offer an earned virtual-credit reward without granting it")
    reward_offer.add_argument("--probe", required=True)
    reward_offer.add_argument("--actor", required=True)
    reward_offer.add_argument("--kind", required=True, choices=["participation", "finding"])
    reward_respond = reward_subcommands.add_parser("respond", help="let the offered player accept or pass")
    reward_respond.add_argument("--reward", required=True)
    reward_respond.add_argument("--actor", required=True)
    reward_respond.add_argument("--decision", required=True, choices=["accept", "pass"])
    reward_approve = reward_subcommands.add_parser("approve", help="let Sofia approve an accepted reward locally")
    reward_approve.add_argument("--reward", required=True)
    reward_approve.add_argument("--actor", required=True, choices=["sofia"])
    reward_apply = reward_subcommands.add_parser("apply", help="apply an approved reward with exact-origin confirmation and read-back")
    reward_apply.add_argument("--reward", required=True)
    reward_apply.add_argument("--confirm-origin", required=True)
    reward_subcommands.add_parser("status", help="show safe research-reward and recognition state")

    experiment = subparsers.add_parser("experiment", help="run the evidence-first Club Lab configuration workflow")
    experiment_subcommands = experiment.add_subparsers(dest="experiment_command", required=True)
    experiment_subcommands.add_parser("workflow", help="show roles, boundaries, and state-machine phases")
    experiment_request = experiment_subcommands.add_parser("request-configuration", help="Riley or Sofia requests a read-only configuration exploration")
    experiment_request.add_argument("--requester", required=True, choices=["sofia", "riley"])
    experiment_handoff = experiment_subcommands.add_parser("admin-handoff", help="Riley or Sofia issues the code-derived read-only checklist to the admin")
    experiment_handoff.add_argument("--actor", required=True, choices=["sofia", "riley"])
    experiment_decision = experiment_subcommands.add_parser("admin-decision", help="authorized PodPlay admin accepts or passes the exploration")
    experiment_decision.add_argument("--actor", required=True, choices=["podplay-admin"])
    experiment_decision.add_argument("--decision", required=True, choices=["accept", "pass"])
    experiment_subcommands.add_parser("discover-configuration", help="run the accepted admin GET-only configuration read-back")
    experiment_subcommands.add_parser("discover-scope-path", help="read Tenant → Area → Pod Booking Engine summaries; never writes Preview")
    experiment_record = experiment_subcommands.add_parser("record-configuration", help="record sanitized admin configuration read-back; never writes Preview")
    experiment_record.add_argument("--actor", required=True, choices=["podplay-admin"])
    experiment_record.add_argument("--booking-engine", required=True, choices=["LEGACY", "UNIFIED", "UNKNOWN"])
    experiment_record.add_argument("--effective-scope", required=True, choices=["region", "area", "pod", "unknown"])
    experiment_record.add_argument("--schedule", required=True, choices=["active", "future_only", "draft_only", "missing", "unknown"])
    experiment_record.add_argument("--rate-card", required=True, choices=["effective", "missing", "unknown"])
    experiment_record.add_argument("--readiness", required=True, choices=["ready", "generate", "missing_rate_card", "blocked_schedule", "conversion_failed", "unknown"])
    experiment_diary = experiment_subcommands.add_parser("diary", help="write a concise, secret-free operational diary note")
    experiment_diary.add_argument("--actor", required=True, choices=["sofia", "riley", "morgan-vale", "podplay-admin"])
    experiment_diary.add_argument("--note", required=True)
    experiment_subcommands.add_parser("refresh-uncertainty", help="re-project uncertainty from the current safe evidence")
    experiment_proposal = experiment_subcommands.add_parser("propose-change", help="ask the admin whether to plan a configuration change; never writes Preview")
    experiment_proposal.add_argument("--actor", required=True, choices=["sofia", "riley"])
    experiment_packet = experiment_subcommands.add_parser("prepare-change-packet", help="prepare a pod-scoped Rate Cards packet; never writes Preview")
    experiment_packet.add_argument("--actor", required=True, choices=["sofia", "riley"])
    experiment_configuration_proposal = experiment_subcommands.add_parser("prepare-configuration-proposal", help="prepare the manifest-bound Rate Cards proposal; never writes Preview")
    experiment_configuration_proposal.add_argument("--actor", required=True, choices=["sofia", "riley"])
    experiment_manifest = experiment_subcommands.add_parser("render-dry-run-manifest", help="render the Rate Cards no-write manifest from current evidence")
    experiment_manifest.add_argument("--actor", required=True, choices=["sofia", "riley"])
    experiment_subcommands.add_parser("enact-rate-cards-pod-pilot", help="apply the accepted manifest to the selected Preview pod and retain sanitized read-back evidence")
    experiment_subcommands.add_parser("record-enactment-scope-readback", help="classify a post-enactment safe scope read-back without asserting a regression")
    experiment_prediction = experiment_subcommands.add_parser("prepare-price-prediction", help="prepare one exact no-booking price prediction after verified enactment")
    experiment_prediction.add_argument("--actor", required=True, choices=["sofia", "riley"])
    experiment_tax_policy = experiment_subcommands.add_parser("adopt-checkout-tax-tolerance", help="keep configured-price predictions exact while allowing bounded checkout tax")
    experiment_tax_policy.add_argument("--actor", required=True, choices=["sofia", "riley"])
    experiment_subcommands.add_parser("status", help="show safe experiment state and evidence references")

    portfolio = subparsers.add_parser("portfolio", help="inspect the compact, release-scoped Club Lab research portfolio")
    portfolio_subcommands = portfolio.add_subparsers(dest="portfolio_command", required=True)
    portfolio_subcommands.add_parser("status", help="show compact experiment cards and template catalog")
    portfolio_subcommands.add_parser("sync-rate-cards", help="link the active PP-7827 experiment into the portfolio")

    consultation = subparsers.add_parser("consultation", help="send a non-coercive written plan to a named staff or partner character")
    consultation_subcommands = consultation.add_subparsers(dest="consultation_command", required=True)
    consultation_propose = consultation_subcommands.add_parser("propose-rate-cards", help="address the reversible pod-scoped Rate Cards plan to a named person")
    consultation_propose.add_argument("--proposer", required=True, choices=["sofia", "riley"])
    consultation_propose.add_argument("--recipient", required=True)
    consultation_response = consultation_subcommands.add_parser("respond", help="record the named recipient's plan response; never performs a product action")
    consultation_response.add_argument("--plan", required=True)
    consultation_response.add_argument("--actor", required=True)
    consultation_response.add_argument("--decision", required=True, choices=["accept", "pass", "request_changes"])
    consultation_response.add_argument("--note", default="")
    consultation_revise = consultation_subcommands.add_parser("revise-rate-cards", help="reply to a named recipient's change request without performing product work")
    consultation_revise.add_argument("--proposer", required=True, choices=["sofia", "riley"])
    consultation_revise.add_argument("--plan", required=True)
    consultation_revise.add_argument("--note", required=True)
    consultation_evidence = consultation_subcommands.add_parser("attach-rate-cards-scope", help="attach the current safe live scope read-back to addressed Rate Cards plans")
    consultation_evidence.add_argument("--actor", required=True, choices=["sofia", "riley"])
    consultation_subcommands.add_parser("status", help="show the local staff-planning channel")

    continuity = subparsers.add_parser(
        "continuity", help="export or import a secret-free fictional community bundle"
    )
    continuity_subcommands = continuity.add_subparsers(dest="continuity_command", required=True)
    continuity_export = continuity_subcommands.add_parser(
        "export", help="write a new bundle; Preview accounts and state are excluded"
    )
    continuity_export.add_argument("--output", required=True, type=Path)
    continuity_inspect = continuity_subcommands.add_parser(
        "inspect", help="validate and summarize a bundle without changing local state"
    )
    continuity_inspect.add_argument("--input", required=True, type=Path)
    continuity_import = continuity_subcommands.add_parser(
        "import", help="merge compatible safe facts and rebuild projections"
    )
    continuity_import.add_argument("--input", required=True, type=Path)
    continuity_import.add_argument(
        "--replace-progression", action="store_true",
        help="also replace local fictional motives; omitted by default",
    )

    recovery = subparsers.add_parser(
        "season-recovery", help="Riley-inspectable, lead-gated Preview season reconciliation"
    )
    recovery_subcommands = recovery.add_subparsers(dest="season_recovery_command", required=True)
    recovery_subcommands.add_parser("status", help="show the latest safe recovery plan and request")
    recovery_subcommands.add_parser("plan", help="perform the administrator's GET-only recovery inspection")
    recovery_request = recovery_subcommands.add_parser("request", help="let Riley or Sofia request the planned reconciliation")
    recovery_request.add_argument("--actor", required=True, choices=["riley", "sofia"])
    recovery_approve = recovery_subcommands.add_parser("approve", help="record external-lead approval without a remote write")
    recovery_approve.add_argument("--actor", required=True, choices=["lead"])
    recovery_apply = recovery_subcommands.add_parser("apply", help="execute the approved reconciliation through the existing seed adapter")
    recovery_apply.add_argument("--confirm-origin", required=True, help="exact approved Preview origin")

    control = subparsers.add_parser(
        "control", help="operate the local-only operator-to-club season-transition protocol"
    )
    control_subcommands = control.add_subparsers(dest="control_command", required=True)
    control_notice = control_subcommands.add_parser("season-notice", help="address a safe season-transition notice to Riley")
    control_notice.add_argument("--season", required=True, help="safe PR label, for example pr-7488")
    control_ack = control_subcommands.add_parser("acknowledge", help="have Riley acknowledge a private control notice")
    control_ack.add_argument("--notice", required=True)
    control_ack.add_argument("--actor", required=True, choices=["riley"])
    control_announce = control_subcommands.add_parser("announce-season", help="let Riley inform named players through the normal club channel")
    control_announce.add_argument("--notice", required=True)
    control_announce.add_argument("--actor", required=True, choices=["riley"])
    control_player_ack = control_subcommands.add_parser("acknowledge-awareness", help="record one player's awareness-only response")
    control_player_ack.add_argument("--notice", required=True)
    control_player_ack.add_argument("--actor", required=True, choices=["andy-bogard", "terry-bogard"])
    control_handoff = control_subcommands.add_parser("research-handoff", help="address an observed research result to Sofia and Riley")
    control_handoff.add_argument("--probe", required=True)
    control_handoff_ack = control_subcommands.add_parser("acknowledge-handoff", help="record Sofia or Riley's receipt of a research handoff")
    control_handoff_ack.add_argument("--handoff", required=True)
    control_handoff_ack.add_argument("--actor", required=True, choices=["sofia", "riley"])
    control_release = control_subcommands.add_parser("release-notice", help="address a safe release notice to Sofia and Riley")
    control_release.add_argument("--feature", required=True, help="safe feature identifier, for example rate-cards-unified-pricing")
    control_release.add_argument("--release", required=True, help="safe release reference, for example PP-7827")
    control_release_ack = control_subcommands.add_parser("acknowledge-release", help="record Sofia or Riley's receipt of a release notice")
    control_release_ack.add_argument("--notice", required=True)
    control_release_ack.add_argument("--actor", required=True, choices=["sofia", "riley"])
    control_subcommands.add_parser("authorize-rate-cards-planning", help="record a bounded lead authorization for planning only; never changes Preview")
    control_subcommands.add_parser("request-rate-cards-pricing-basis", help="open the explicit external pricing-basis request; never changes Preview")
    control_subcommands.add_parser("propose-rate-cards-pricing-basis", help="send a reviewable 30-minute USD proposal to external product authority; never changes Preview")
    control_pricing_response = control_subcommands.add_parser("respond-rate-cards-pricing-basis", help="record external product authority's explicit response; never changes Preview")
    control_pricing_response.add_argument("--actor", required=True, choices=["external-product-authority"])
    control_pricing_response.add_argument("--decision", required=True, choices=["accept", "request_changes", "pass"])
    control_pricing_response.add_argument("--note", default="")
    control_run = control_subcommands.add_parser("preauthorize-rate-cards-run", help="record three explicit confirmations for a future manifest-bound run; never changes Preview")
    control_run.add_argument("--pricing-basis", required=True, help="exactly yes")
    control_run.add_argument("--configuration-proposal", required=True, help="exactly yes")
    control_run.add_argument("--preview-write-after-dry-run", required=True, help="exactly yes")
    control_offer = control_subcommands.add_parser("offer-rate-cards-pilot-interest", help="let Riley ask named players about future pilot contact only")
    control_offer.add_argument("--actor", required=True, choices=["riley"])
    control_interest_response = control_subcommands.add_parser("respond-rate-cards-pilot-interest", help="record one player's future-pilot accept or pass")
    control_interest_response.add_argument("--offer", required=True)
    control_interest_response.add_argument("--actor", required=True, choices=["andy-bogard", "terry-bogard"])
    control_interest_response.add_argument("--decision", required=True, choices=["accept", "pass"])
    control_price_request = control_subcommands.add_parser("request-rate-cards-price-verification", help="let Riley ask opted-in Andy for one no-booking displayed-price observation")
    control_price_request.add_argument("--actor", required=True, choices=["riley"])
    control_price_response = control_subcommands.add_parser("respond-rate-cards-price-verification", help="record Andy's accept or pass for the no-booking display observation")
    control_price_response.add_argument("--request", required=True)
    control_price_response.add_argument("--actor", required=True, choices=["andy-bogard"])
    control_price_response.add_argument("--decision", required=True, choices=["accept", "pass"])
    control_price_observation = control_subcommands.add_parser("record-rate-cards-price-observation", help="record the normalized no-booking quote observation")
    control_price_observation.add_argument("--request", required=True)
    control_subcommands.add_parser("tick", help="project the explicit control queue; performs no autonomous actions")

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
        help="serve one existing actor or the approved administrator's login fields locally for the exact Preview browser origin",
    )
    preview_handoff_identity = preview_browser_handoff.add_mutually_exclusive_group(required=True)
    preview_handoff_identity.add_argument("--actor", help="existing actor id")
    preview_handoff_identity.add_argument("--admin", action="store_true", help="approved Preview administrator only")
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
    preview_subcommands.add_parser(
        "observe-finished",
        help="record one finished event's read-back without making Preview writes",
    )
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
    inspect = subparsers.add_parser("inspect", help="read secret-free social, belief, and booking-plan projections")
    inspect_subcommands = inspect.add_subparsers(dest="inspect_command", required=True)
    inspect_player = inspect_subcommands.add_parser("player", help="show one Sim's motives and self-awareness")
    inspect_player.add_argument("--actor", required=True)
    inspect_plan = inspect_subcommands.add_parser("booking-plan", help="show one product-neutral plan and quote explanation")
    inspect_plan.add_argument("--id", required=True, dest="booking_plan_id")
    inspect_subcommands.add_parser("booking-plans", help="list projected booking-plan IDs and stages")
    assess = subparsers.add_parser("assess", help="derive a local expectation comparison without a product write")
    assess_subcommands = assess.add_subparsers(dest="assess_command", required=True)
    assess_plan = assess_subcommands.add_parser("booking-plan", help="compare a named payer's beliefs to an observed quote")
    assess_plan.add_argument("--id", required=True, dest="booking_plan_id")
    assess_plan.add_argument("--at", help="assessment timestamp; defaults to now")
    approval = subparsers.add_parser("payer-approval", help="request or accept payer-owned approval for an expectation-matched plan")
    approval_subcommands = approval.add_subparsers(dest="approval_command", required=True)
    approval_request = approval_subcommands.add_parser("request", help="ask the named payer to approve one fresh quote")
    approval_request.add_argument("--id", required=True, dest="booking_plan_id")
    approval_request.add_argument("--at", help="request timestamp; defaults to now")
    approval_accept = approval_subcommands.add_parser("accept", help="record the named payer's approval")
    approval_accept.add_argument("--id", required=True, dest="booking_plan_id")
    approval_accept.add_argument("--payer", required=True, dest="payer_actor_id")
    approval_accept.add_argument("--at", help="acceptance timestamp; defaults to now")
    expect = subparsers.add_parser("expect", help="record a Sim's local expectation; never a product entitlement")
    expect_subcommands = expect.add_subparsers(dest="expect_command", required=True)
    expect_price = expect_subcommands.add_parser("booking-price", help="record one actor's acceptable price ceiling")
    expect_price.add_argument("--actor", required=True)
    expect_price.add_argument("--max-amount", type=float, required=True)
    expect_price.add_argument("--currency", required=True)
    expect_price.add_argument("--at", help="expectation timestamp; defaults to now")
    challenge = subparsers.add_parser(
        "challenge", help="record a durable no-write team challenge from a roster scenario"
    )
    challenge.add_argument(
        "--scenario", default="andy-vs-kyo-doubles.json", help="scenario file under scenarios/"
    )
    challenge.add_argument(
        "--propose", action="store_true",
        help="send one named captain-to-captain proposal locally instead of using a pre-agreed scenario",
    )
    challenge.add_argument("--challenger", help="rostered captain making a --propose challenge")
    challenge.add_argument("--opponent", help="rostered opposing captain for a --propose challenge")
    challenge.add_argument("--accept", action="store_true", help="record the addressed captain's acceptance of --intent")
    challenge.add_argument("--intent", help="team challenge intent to accept")
    challenge.add_argument("--actor", help="addressed captain accepting --intent")
    staff_team_event = subparsers.add_parser("staff-team-event", help="offer and acknowledge one staff-published free team research event")
    staff_team_event_subcommands = staff_team_event.add_subparsers(dest="staff_team_event_command", required=True)
    staff_team_event_offer = staff_team_event_subcommands.add_parser("offer")
    staff_team_event_offer.add_argument("--challenge", required=True)
    staff_team_event_ack = staff_team_event_subcommands.add_parser("acknowledge")
    staff_team_event_ack.add_argument("--intent", required=True)
    staff_team_event_ack.add_argument("--actor", required=True)
    staff_team_event_ack.add_argument("--decision", required=True, choices=["accept", "pass"])
    training = subparsers.add_parser(
        "training", help="record a captain-led two-player training proposal or acceptance locally"
    )
    training.add_argument(
        "--scenario", default="women-fighters-training.json", help="training scenario file under scenarios/"
    )
    training.add_argument(
        "--accept", action="store_true", help="record the named teammate's acceptance after the captain proposal")
    training.add_argument("--intent", help="existing training agreement for a third-player social invitation")
    training.add_argument("--invite-third", dest="third_player", help="address one rostered third player; local social request only")
    training.add_argument("--withdraw-third", action="store_true", help="withdraw an unaccepted optional third-player request")
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
    parser = build_parser()
    args = parser.parse_args(argv)
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
            print(f"CLUB LAB FACT JOURNAL // {len(facts)} VALID FACTS")
            if facts:
                print(f"last: {facts[-1]['factId']} // {facts[-1]['type']}")
            return 0
        if args.state_command == "rebuild":
            result = rebuild_projections(root)
            print(
                "CLUB LAB PROJECTIONS // REBUILT // "
                f"{result['metadata']['factCount']} FACTS // "
                f"{len(result['conversations']['conversations'])} CONVERSATIONS // "
                f"{len(result['commitments']['commitments'])} COMMITMENTS // "
                f"{len(result['events']['events'])} EVENTS // "
                f"{len(result['participation']['participation'])} PARTICIPATIONS // "
                f"{len(result['beliefs']['beliefs'])} BELIEFS"
            )
            return 0
        raise AssertionError(f"unhandled state command {args.state_command}")

    if args.command == "inspect":
        from .inspectors import InspectorNotFoundError, booking_plan_inspector, inspection_index, player_inspector
        try:
            if args.inspect_command == "player":
                value = player_inspector(root, args.actor)
            elif args.inspect_command == "booking-plan":
                value = booking_plan_inspector(root, args.booking_plan_id)
            else:
                value = {"bookingPlans": inspection_index(root)["bookingPlans"]}
        except InspectorNotFoundError as exc:
            print(f"ERROR inspect: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0

    if args.command == "assess":
        from .booking_plans import make_comparison_fact
        from .inspectors import InspectorNotFoundError, assess_booking_plan
        try:
            when = args.at or datetime.now(timezone.utc).isoformat()
            comparison = assess_booking_plan(root, args.booking_plan_id, when)
            fact = make_comparison_fact(
                comparison=comparison, occurred_at=when,
                correlation_id=f"journey:{args.booking_plan_id}",
            )
            append_fact(root, fact)
            rebuild_projections(root)
        except (InspectorNotFoundError, ValueError) as exc:
            print(f"ERROR assess: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(comparison, indent=2, sort_keys=True))
        return 0

    if args.command == "payer-approval":
        from .payer_approval import PayerApprovalError, accept as accept_payer_approval, request as request_payer_approval
        try:
            when = args.at or datetime.now(timezone.utc).isoformat()
            result = (
                request_payer_approval(root, args.booking_plan_id, when)
                if args.approval_command == "request"
                else accept_payer_approval(root, args.booking_plan_id, args.payer_actor_id, when)
            )
        except PayerApprovalError as exc:
            print(f"ERROR payer approval: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "browser-journey":
        from .browser_booking_observation import (
            BrowserBookingObservationError,
            reconcile as reconcile_browser_booking,
            recover_pair_booking,
            reconcile_invitation_acceptance,
            reconcile_staff_check_in,
        )
        from .journey_policy import claim as claim_browser_journey, record_observation
        try:
            when = args.at or datetime.now(timezone.utc).isoformat()
            if args.browser_journey_command == "claim":
                result = claim_browser_journey(root, job_key=args.job_key, actor_id=args.actor_id, observed_at=when)
            elif args.browser_journey_command == "observe":
                result = record_observation(
                    root, job_key=args.job_key, actor_id=args.actor_id,
                    kind=args.kind, observed_at=when,
                )
            elif args.browser_journey_command == "reconcile-booking":
                result = reconcile_browser_booking(
                    root, intent_id=args.intent_id, owner_actor_id=args.owner_actor_id,
                    venue_label=args.venue_label, start_time=args.start_time, end_time=args.end_time,
                    group_size=args.group_size, observed_at=parse_instant(when),
                )
                rebuild_projections(root)
            elif args.browser_journey_command == "recover-pair-booking":
                result = recover_pair_booking(
                    root, source_intent_id=args.source_intent_id, owner_actor_id=args.owner_actor_id,
                    venue_label=args.venue_label, start_time=args.start_time, end_time=args.end_time,
                    group_size=args.group_size, observed_at=parse_instant(when),
                )
                rebuild_projections(root)
            elif args.browser_journey_command == "reconcile-invitation":
                result = reconcile_invitation_acceptance(
                    root, intent_id=args.intent_id, recipient_actor_id=args.recipient_actor_id,
                    observed_at=parse_instant(when),
                )
                rebuild_projections(root)
            else:
                result = reconcile_staff_check_in(
                    root, intent_id=args.intent_id, participant_actor_id=args.participant_actor_id,
                    observed_at=parse_instant(when),
                )
                rebuild_projections(root)
        except (BrowserBookingObservationError, ValueError) as exc:
            print(f"ERROR browser journey: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "worker-dispatch":
        from .worker_dispatch import (
            active_packet as active_worker_packet,
            claim_next as claim_next_worker_contract,
            quarantine_legacy_release,
            recover_unobserved_release,
            report as report_worker_contract,
            runbook as worker_runbook,
        )
        from .codex_worker_launcher import dispatch_once as dispatch_codex_worker, launch as launch_codex_worker, reap as reap_codex_workers
        try:
            if args.worker_dispatch_command == "next":
                result = claim_next_worker_contract(root, worker_id=args.worker_id)
            elif args.worker_dispatch_command == "active":
                result = active_worker_packet(root, worker_id=args.worker_id)
            elif args.worker_dispatch_command == "runbook":
                result = worker_runbook(root, worker_id=args.worker_id)
            elif args.worker_dispatch_command == "launch":
                result = launch_codex_worker(root, worker_id=args.worker_id, execute=args.execute)
            elif args.worker_dispatch_command == "cycle":
                result = dispatch_codex_worker(root, worker_id=args.worker_id, execute=args.execute)
            elif args.worker_dispatch_command == "reap":
                result = reap_codex_workers(root)
            elif args.worker_dispatch_command == "recover-unobserved":
                result = recover_unobserved_release(root, contract_key=args.contract_key)
            elif args.worker_dispatch_command == "quarantine-legacy-release":
                result = quarantine_legacy_release(root, contract_key=args.contract_key)
            else:
                result = report_worker_contract(
                    root, contract_key=args.contract_key, worker_id=args.worker_id,
                    outcome=args.outcome, diary_line=args.diary_line, receipt_ref=args.receipt_ref,
                )
        except ValueError as exc:
            print(f"ERROR worker dispatch: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "staff-follow-up":
        from .staff_follow_up import tick as route_staff_follow_up
        try:
            result = route_staff_follow_up(root, now=parse_instant(args.at), intent_id=args.intent_id)
        except ValueError as exc:
            print(f"ERROR staff follow-up: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "staff-dispatch":
        from .staff_worker_dispatch import active_packet as active_staff_packet, claim_next as claim_next_staff_contract, report as report_staff_contract
        from .codex_staff_worker_launcher import launch as launch_staff_worker, reap as reap_staff_workers
        try:
            if args.staff_dispatch_command == "next":
                result = claim_next_staff_contract(root, worker_id=args.worker_id)
            elif args.staff_dispatch_command == "active":
                result = active_staff_packet(root, worker_id=args.worker_id)
            elif args.staff_dispatch_command == "launch":
                result = launch_staff_worker(root, worker_id=args.worker_id, execute=args.execute)
            elif args.staff_dispatch_command == "reap":
                result = reap_staff_workers(root)
            else:
                result = report_staff_contract(
                    root, contract_key=args.contract_key, worker_id=args.worker_id,
                    outcome=args.outcome, diary_line=args.diary_line,
                )
        except ValueError as exc:
            print(f"ERROR staff dispatch: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "secure-channel":
        from .secure_channel import SecureChannelError, deliver_invitation, retain_invitation_link
        try:
            if args.secure_channel_command == "retain-browser-invite":
                result = retain_invitation_link(
                    root, intent_id=args.intent_id, owner_actor_id=args.owner_actor_id,
                    invitation_url=sys.stdin.read().strip(), expected_origin=args.expected_origin,
                )
            else:
                result = deliver_invitation(
                    root, intent_id=args.intent_id, owner_actor_id=args.owner_actor_id,
                    recipient_actor_ids=args.recipient_actor_ids,
                )
        except SecureChannelError as exc:
            print(f"ERROR secure channel: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "expect":
        from .self_assets import SelfAssetValidationError, make_belief_fact
        try:
            if args.max_amount < 0 or len(args.currency) != 3 or not args.currency.isalpha() or args.currency.upper() != args.currency:
                raise SelfAssetValidationError("price expectation needs a non-negative amount and uppercase 3-letter currency")
            when = args.at or datetime.now(timezone.utc).isoformat()
            fact = make_belief_fact(
                actor_id=args.actor, subject="acceptable_booking_price", status="expected",
                expected_value={"maxAmount": args.max_amount, "currency": args.currency},
                occurred_at=when, correlation_id=f"expectation:{args.actor}:booking-price",
                source_kind="actor_expectation",
            )
            append_fact(root, fact)
            rebuild_projections(root)
        except (SelfAssetValidationError, ValueError) as exc:
            print(f"ERROR expectation: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"actorId": args.actor, "subject": "acceptable_booking_price", "status": "expected", "value": {"maxAmount": args.max_amount, "currency": args.currency}, "remoteWrites": 0}, indent=2, sort_keys=True))
        return 0

    if args.command == "research":
        try:
            if args.research_command == "contract":
                result = feature_contract(args.feature)
            elif args.research_command == "request":
                result = create_verification_request(
                    root, feature_id=args.feature, requester=args.requester, verifier_ids=args.verifier
                )
            elif args.research_command == "acknowledge":
                result = acknowledge_verification(
                    root, probe_id=args.probe, actor_id=args.actor, decision=args.decision
                )
            elif args.research_command == "issue-candidate":
                result = issue_candidate(root, probe_id=args.probe, reason=args.reason)
            elif args.research_command == "discover":
                result = discover_feature_context(root, args.probe)
            elif args.research_command == "tick":
                result = run_research_response_tick(root, probe_id=args.probe, turns=args.turns)
            elif args.research_command == "continuous-unified":
                result = run_continuous_unified_campaigns(root, args.confirm_origin)
            elif args.research_command == "continuous-unified-arrivals":
                result = check_in_continuous_unified_arrivals(root, args.confirm_origin)
            elif args.research_command == "reward":
                if args.research_reward_command == "offer":
                    result = offer_research_reward(root, probe_id=args.probe, actor_id=args.actor, kind=args.kind)
                elif args.research_reward_command == "respond":
                    result = respond_research_reward(root, reward_id=args.reward, actor_id=args.actor, decision=args.decision)
                elif args.research_reward_command == "approve":
                    result = approve_research_reward(root, reward_id=args.reward, actor_id=args.actor)
                elif args.research_reward_command == "apply":
                    result = apply_research_reward(root, reward_id=args.reward, confirmation=args.confirm_origin)
                else:
                    result = research_rewards_snapshot(root)
            else:
                result = research_snapshot(root)
        except (ProductResearchError, ResearchRewardError, ContinuousResearchError, CredentialsError, FirebaseAuthError, IdentityRegistryError, PreviewReadError, PreviewWriteError, TargetPolicyError) as exc:
            print(f"ERROR research: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "health":
        try:
            result = check_ambient_health(root)
        except (AmbientHealthError, CredentialsError, TargetPolicyError) as exc:
            print(f"ERROR health: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "experiment":
        try:
            if args.experiment_command == "workflow":
                result = configuration_workflow_contract()
            elif args.experiment_command == "request-configuration":
                result = request_configuration_exploration(root, requester=args.requester)
            elif args.experiment_command == "admin-handoff":
                result = issue_technical_handoff(root, actor_id=args.actor)
            elif args.experiment_command == "admin-decision":
                result = decide_configuration_exploration(root, actor_id=args.actor, decision=args.decision)
            elif args.experiment_command == "discover-configuration":
                result = discover_live_configuration(root)
            elif args.experiment_command == "discover-scope-path":
                result = discover_live_scope_path(root)
            elif args.experiment_command == "record-configuration":
                result = record_configuration_observation(
                    root, actor_id=args.actor, booking_engine=args.booking_engine,
                    effective_scope=args.effective_scope, schedule=args.schedule,
                    rate_card=args.rate_card, readiness=args.readiness,
                )
            elif args.experiment_command == "diary":
                result = add_configuration_diary_note(root, actor_id=args.actor, note=args.note)
            elif args.experiment_command == "refresh-uncertainty":
                result = refresh_configuration_uncertainty(root)
            elif args.experiment_command == "propose-change":
                result = propose_configuration_change(root, actor_id=args.actor)
            elif args.experiment_command == "prepare-change-packet":
                result = prepare_rate_cards_change_packet(root, actor_id=args.actor)
            elif args.experiment_command == "prepare-configuration-proposal":
                result = prepare_rate_cards_configuration_proposal(root, actor_id=args.actor)
            elif args.experiment_command == "render-dry-run-manifest":
                result = render_rate_cards_dry_run_manifest(root, actor_id=args.actor)
            elif args.experiment_command == "enact-rate-cards-pod-pilot":
                result = enact_rate_cards_pod_pilot(root)
            elif args.experiment_command == "record-enactment-scope-readback":
                result = record_rate_cards_enactment_scope_readback(root)
            elif args.experiment_command == "prepare-price-prediction":
                result = prepare_rate_cards_price_prediction(root, actor_id=args.actor)
            elif args.experiment_command == "adopt-checkout-tax-tolerance":
                result = adopt_rate_cards_checkout_tax_tolerance(root, actor_id=args.actor)
            else:
                result = experiment_snapshot(root)
        except ConfigurationExperimentError as exc:
            print(f"ERROR experiment: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "portfolio":
        try:
            result = sync_rate_cards_experiment(root) if args.portfolio_command == "sync-rate-cards" else portfolio_snapshot(root)
        except ResearchPortfolioError as exc:
            print(f"ERROR portfolio: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "consultation":
        try:
            if args.consultation_command == "propose-rate-cards":
                result = propose_rate_cards_plan(root, proposer=args.proposer, recipient=args.recipient)
            elif args.consultation_command == "respond":
                result = respond_staff_consultation(root, plan_id=args.plan, actor_id=args.actor, decision=args.decision, note=args.note)
            elif args.consultation_command == "revise-rate-cards":
                result = revise_rate_cards_plan(root, proposer=args.proposer, plan_id=args.plan, note=args.note)
            elif args.consultation_command == "attach-rate-cards-scope":
                result = attach_rate_cards_scope_readback(root, actor_id=args.actor)
            else:
                result = staff_consultation_snapshot(root)
        except StaffConsultationError as exc:
            print(f"ERROR consultation: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "continuity":
        try:
            if args.continuity_command == "export":
                result = export_bundle(root, args.output)
            elif args.continuity_command == "inspect":
                result = inspect_bundle(args.input)
            else:
                result = import_bundle(
                    root, args.input, replace_progression=args.replace_progression
                )
        except ContinuityError as exc:
            print(f"ERROR continuity: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "season-recovery":
        try:
            if args.season_recovery_command == "status":
                result = season_recovery_snapshot(root)
            elif args.season_recovery_command == "plan":
                result = plan_season_recovery(root)
            elif args.season_recovery_command == "request":
                result = request_season_recovery(root, args.actor)
            elif args.season_recovery_command == "approve":
                result = approve_season_recovery(root, args.actor)
            else:
                result = apply_season_recovery(root, args.confirm_origin)
        except (SeasonRecoveryError, CredentialsError, FirebaseAuthError, IdentityRegistryError, PreviewReadError, PreviewWriteError, TargetPolicyError) as exc:
            print(f"ERROR season recovery: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "control":
        try:
            if args.control_command == "season-notice":
                result = create_season_notice(root, args.season)
            elif args.control_command == "acknowledge":
                result = acknowledge_control_notice(root, args.notice, args.actor)
            elif args.control_command == "announce-season":
                result = announce_control_notice(root, args.notice, args.actor)
            elif args.control_command == "acknowledge-awareness":
                result = acknowledge_control_awareness(root, args.notice, args.actor)
            elif args.control_command == "research-handoff":
                result = create_research_handoff(root, args.probe)
            elif args.control_command == "release-notice":
                result = create_release_notice(root, args.feature, args.release)
            elif args.control_command == "acknowledge-release":
                result = acknowledge_release(root, args.notice, args.actor)
            elif args.control_command == "authorize-rate-cards-planning":
                result = authorize_rate_cards_planning(root)
            elif args.control_command == "request-rate-cards-pricing-basis":
                result = request_rate_cards_pricing_basis(root)
            elif args.control_command == "propose-rate-cards-pricing-basis":
                result = create_rate_cards_pricing_basis_proposal(root)
            elif args.control_command == "respond-rate-cards-pricing-basis":
                result = respond_to_rate_cards_pricing_basis(root, actor_id=args.actor, decision=args.decision, note=args.note)
            elif args.control_command == "preauthorize-rate-cards-run":
                result = preauthorize_rate_cards_configuration_run(
                    root, pricing_basis_confirmation=args.pricing_basis,
                    configuration_proposal_confirmation=args.configuration_proposal,
                    preview_write_confirmation=args.preview_write_after_dry_run,
                )
            elif args.control_command == "offer-rate-cards-pilot-interest":
                result = offer_rate_cards_future_pilot_interest(root, actor_id=args.actor)
            elif args.control_command == "respond-rate-cards-pilot-interest":
                result = respond_to_rate_cards_future_pilot_interest(root, offer_id=args.offer, actor_id=args.actor, decision=args.decision)
            elif args.control_command == "request-rate-cards-price-verification":
                result = request_rate_cards_price_verification(root, actor_id=args.actor)
            elif args.control_command == "respond-rate-cards-price-verification":
                result = respond_to_rate_cards_price_verification(root, request_id=args.request, actor_id=args.actor, decision=args.decision)
            elif args.control_command == "record-rate-cards-price-observation":
                result = record_rate_cards_price_verification_observation(root, request_id=args.request)
            elif args.control_command == "acknowledge-handoff":
                result = acknowledge_research_handoff(root, args.handoff, args.actor)
            else:
                result = tick_control_plane(root)
        except ControlPlaneError as exc:
            print(f"ERROR control: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "challenge":
        try:
            now = datetime.now(timezone.utc)
            if args.propose:
                if not isinstance(args.challenger, str) or not isinstance(args.opponent, str):
                    raise TeamChallengeError("--propose requires --challenger and --opponent")
                intent = propose_captain_challenge(root, args.challenger, args.opponent, now)
                action = "PROPOSED"
            elif args.accept:
                if not isinstance(args.intent, str) or not isinstance(args.actor, str):
                    raise TeamChallengeError("--accept requires --intent and --actor")
                intent = accept_challenge(root, args.intent, args.actor, now)
                action = "AGREED"
            else:
                intent = plan_challenge(root, args.scenario, now)
                action = "AGREED"
        except (TeamChallengeError, ValueError) as exc:
            print(f"ERROR team challenge: {exc}", file=sys.stderr)
            return 2
        print(f"CLUB LAB TEAM CHALLENGE // {action} // LOCAL ONLY")
        print(f"intent: {intent['id']}")
        print(f"match: {intent['challenger']['teamName']} vs {intent['opponent']['teamName']}")
        players = intent.get("participantCharacters") or intent.get("participants", [])
        print(f"players: {', '.join(players)}")
        print(f"duration: {intent['constraints']['durationMinutes']} minutes")
        print(f"next: {intent['nextAction']}")
        print("writes: 0")
        return 0

    if args.command == "staff-team-event":
        try:
            if args.staff_team_event_command == "offer":
                intent = offer_staff_team_event(root, args.challenge, datetime.now(timezone.utc))
            else:
                intent = acknowledge_staff_team_event(root, args.intent, args.actor, args.decision, datetime.now(timezone.utc))
        except StaffTeamEventError as exc:
            print(f"ERROR staff team event: {exc}", file=sys.stderr)
            return 2
        print(f"CLUB LAB STAFF TEAM EVENT // {intent['status'].upper()} // LOCAL ONLY")
        print(f"intent: {intent['id']}; acknowledgements: {len(intent.get('acknowledgements', {}))}/{len(intent['participants'])}")
        print(f"next: {intent['nextAction']}; writes: 0")
        return 0

    if args.command == "training":
        try:
            now = datetime.now(timezone.utc)
            if args.withdraw_third:
                if not args.intent:
                    raise TrainingSessionError("--withdraw-third requires --intent")
                ledger = load_intent_ledger(Storage(root))
                original = ledger["intents"].get(args.intent)
                if not isinstance(original, dict) or not isinstance(original.get("bookerActorId"), str):
                    raise TrainingSessionError("training agreement is unavailable")
                intent = withdraw_third_player_invitation(root, args.intent, original["bookerActorId"], now)
                action = "third_player_withdrawn"
            elif args.third_player:
                if not args.intent:
                    raise TrainingSessionError("--invite-third requires --intent")
                ledger = load_intent_ledger(Storage(root))
                original = ledger["intents"].get(args.intent)
                if not isinstance(original, dict) or not isinstance(original.get("bookerActorId"), str):
                    raise TrainingSessionError("training agreement is unavailable")
                intent = invite_third_player(
                    root, args.intent, original["bookerActorId"], args.third_player, now
                )
                action = "third_player_invited"
            else:
                intent = propose_training(root, args.scenario, now)
                action = "captain_invited"
                if args.accept:
                    intent = accept_training(root, args.scenario, now)
                    action = "agreed"
        except TrainingSessionError as exc:
            print(f"ERROR training: {exc}", file=sys.stderr)
            return 2
        print(f"CLUB LAB TRAINING // {action.upper()} // LOCAL ONLY")
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
        print("CLUB LAB ARCADE // SIMULATED // LOCAL ONLY")
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
        print("CLUB LAB ARCADE REPLAY // ATTACHED // LOCAL FICTION ONLY")
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
                    root, args.actor, use_admin=args.admin, port=args.port, lifetime_seconds=args.lifetime
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
        if args.preview_command == "observe-finished":
            return observe_finished_preview(root)
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
                f"CLUB LAB ACTOR RUNTIME // tick {result['tick']} // "
                f"{len(result['turns'])} turns // remote writes 0"
            )
            for turn in result["turns"]:
                print(f"- {turn['actorId']}: {turn['action']} — {turn['detail']}")
            return 0
        if args.runtime_command == "tournament-tick":
            result = run_tournament_tick(root, now=parse_instant(args.now) if args.now else None)
            print(f"CLUB LAB TOURNAMENT // tick {result['tick']} // remote writes 0")
            print(f"- {result['action']['actorId']}: {result['action']['action']} — {result['action']['detail']}")
            return 0
        if args.runtime_command == "report-winner":
            state = report_winner(root, args.winner, args.reporter, now=parse_instant(args.now) if args.now else None)
            print(f"CLUB LAB TOURNAMENT RESULT // {state['rounds']['semi-final']['winner']} // no score")
            return 0
        if args.runtime_command == "report-event-result":
            result = report_finished_event_result(
                root, args.intent, args.reporter, args.winner,
                now=parse_instant(args.now) if args.now else None,
            )
            print(f"CLUB LAB EVENT RESULT // {result['winner']} // captain attestation; no score")
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


def discover_feature_context(root: Path, probe_id: str) -> Dict[str, Any]:
    """Use the existing administrator only for the contract's GET-only read-back.

    This function never seeds, reconciles, or provisions identities.  The
    authenticated response is reduced by ``product_research`` before state is
    written.
    """
    credentials = PreviewCredentials.load(root)
    policy = TargetPolicy.from_config(credentials.config)
    session = FirebaseAuthenticator(root / "secrets" / "preview-admin-auth-cache.json").authenticate(
        credentials.admin_email, credentials.admin_password, credentials.firebase_api_key
    )
    client = PreviewReadonlyClient(policy, session.id_token)
    return discover_team_signup_context_with_client(
        root,
        probe_id=probe_id,
        client=client,
        target_fingerprint=f"pr-{policy.pull_request_number}:team-signup-contract-v1",
        observed_at=datetime.now(timezone.utc),
    )


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
            if team:
                record_verified_team_activation(root, team, TEAM_ACTORS[team])
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
        f"CLUB LAB SEED // {'APPLIED' if apply else 'DRY RUN'} // "
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

    print(f"CLUB LAB READINESS // READ-ONLY // Preview PR #{report['pullRequest']}")
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
        if intent.get("status") not in {"requested", "agreed", "preview_ready", "preview_blocked", "ready_to_publish"}:
            raise BookingIntentError(
                f"booking intent cannot be planned from status {intent.get('status')!r}"
            )
        constraints = intent.get("constraints")
        duration = constraints.get("durationMinutes") if isinstance(constraints, dict) else None
        if not isinstance(duration, int) or duration < 30 or duration % 30:
            raise BookingIntentError("preview planning requires a positive 30-minute increment")
        journey = intent.get("journey", "customer_booking")
        if journey not in {"customer_booking", "customer_training", "customer_team_signup", "owner_open_play", "owner_team_signup"}:
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
        booking_engine = report["location"].get("bookingEngine")
        if journey in {"customer_booking", "customer_training"}:
            def unified_quote() -> Dict[str, Any]:
                return summarize_unified_booking_quote(
                    UnifiedBookingQuoteEvaluator(policy, booker_auth.id_token).evaluate(
                        report["location"]["podId"], candidate["startTime"],
                        candidate["endTime"], len(intent.get("participants", [])),
                    )
                )

            if booking_engine == "UNIFIED":
                evaluation = unified_quote()
            else:
                try:
                    evaluation = summarize_booking_preview(
                        PreviewBookingEvaluator(policy, booker_auth.id_token).evaluate(
                            candidate["sessionId"], candidate["tableId"], requested_credits
                        )
                    )
                except PreviewEvaluationError as exc:
                    # Older Preview read models may not disclose the effective
                    # pod override. The legacy endpoint's stable code is a
                    # bounded capability signal, not a product failure.
                    if "UNIFIED_BOOKING_VENUE" not in str(exc):
                        raise
                    booking_engine = "UNIFIED"
                    evaluation = unified_quote()
        elif journey == "customer_team_signup":
            evaluation = summarize_booking_preview(PreviewBookingEvaluator(policy, booker_auth.id_token).evaluate_team(
                candidate["items"], requested_credits
            ))
        else:
            evaluation = {"status": "OWNER_EVENT_PLANNED", "total": 0.0, "currency": None,
                          "errorCodes": [], "readyForOrder": True}
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
            "bookingEngine": booking_engine or "LEGACY",
            "evaluation": evaluation,
        }
        # Every player-owned booking gets the canonical, product-neutral
        # boundary record once a real availability/quote plan exists. It is
        # still only a read-only plan; payer approval and guarded apply remain
        # separate. Owner-created Open Play deliberately has a different
        # authority model and does not cross this customer-payment boundary.
        booking_plan_facts = []
        if journey in {"customer_training", "customer_team_signup"}:
            from .booking_plans import make_booking_plan, make_plan_fact, make_quote_evidence, make_quote_fact
            total = evaluation.get("total")
            currency = evaluation.get("currency")
            errors = evaluation.get("errorCodes") if isinstance(evaluation.get("errorCodes"), list) else []
            quote_fields = {
                "total": {"status": "observed" if isinstance(total, (int, float)) else "unknown", "value": float(total) if isinstance(total, (int, float)) else None},
                "currency": {"status": "observed" if isinstance(currency, str) and len(currency) == 3 and currency.isupper() else "unknown", "value": currency if isinstance(currency, str) and len(currency) == 3 and currency.isupper() else None},
                "membershipTreatment": {"status": "unknown", "value": None},
                "virtualCreditTreatment": {"status": "observed" if requested_credits > 0 else "not_applicable", "value": "owner_credit_requested" if requested_credits > 0 else None},
                "passTreatment": {"status": "not_applicable", "value": None},
                "capacityTreatment": {
                    "status": "observed",
                    "value": "team_guest_capacity_requested" if journey == "customer_team_signup" else "named_training_capacity_requested",
                },
                "cancellationTerms": {"status": "unknown", "value": None},
                "blockingCodes": {"status": "observed", "value": errors},
            }
            plan = make_booking_plan(
                occurrence_key=canonical_occurrence, owner_actor_id=booker_actor_id,
                participant_actor_ids=intent["participants"], payer_actor_id=booker_actor_id,
                venue_ref=f"venue:preview:{policy.pull_request_number}:{report['location']['podId']}",
                starts_at=candidate["startTime"], ends_at=candidate["endTime"],
                benefit_strategy="owner_only" if requested_credits > 0 else "use_none",
                stage="quoted" if evaluation["readyForOrder"] else "blocked",
            )
            quote = make_quote_evidence(
                booking_plan=plan, observed_at=report["observedAt"],
                result="observed", fields=quote_fields,
            )
            intent["previewPlan"]["bookingPlanId"] = plan["bookingPlanId"]
            booking_plan_facts = [
                make_plan_fact(plan=plan, occurred_at=report["observedAt"], correlation_id=f"journey:{selected_id}"),
                make_quote_fact(evidence=quote, occurred_at=report["observedAt"], correlation_id=f"journey:{selected_id}"),
            ]
        intent["remoteWrites"] = 0
        save_intent(storage, ledger, intent)
        with storage.writer_lock():
            if booking_plan_facts:
                append_protocol_facts_locked(storage, booking_plan_facts)
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
        if intent.get("journey") not in {"owner_open_play", "owner_team_signup"} or intent.get("status") not in {"preview_ready", "ready_to_publish"}:
            raise PreviewWriteError("selected intent is not ready for staff event publication")
        plan = intent.get("previewPlan")
        if not isinstance(plan, dict) or not isinstance(plan.get("items"), list) or not plan["items"]:
            raise PreviewWriteError("owner Open Play plan is incomplete")
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        if plan.get("targetOrigin") != read_policy.target_origin:
            raise PreviewWriteError("owner Open Play was planned for another origin")
        team_size = int(intent.get("teamSize", 1)) if intent.get("journey") == "owner_team_signup" else 1
        total_teams = int(intent.get("totalTeams", len(intent.get("participants", [])))) if team_size > 1 else len(intent.get("participants", []))
        if team_size > 1 and (team_size != 2 or total_teams != 2):
            raise PreviewWriteError("staff team research events are bounded to two teams of two")
        name = f"Club Lab {'Team Signup' if team_size > 1 else 'Open Play'} {selected_id.rsplit(':', 1)[-1]}"
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
                plan["items"], name, total_teams=total_teams, team_size=team_size
            )
            event_id = result["id"]
            client = PreviewReadonlyClient(read_policy, admin_auth.id_token)
            event = client.get(f"/apis/v2/events/{event_id}")
            if not isinstance(event, dict) or event.get("id") != event_id or event.get("subtype") != "OPEN_PLAY":
                raise PreviewWriteError("owner Open Play read-back did not match the planned event")
            writes = 1
            if event.get("teamSize") != team_size or event.get("totalTeams") != total_teams:
                raise PreviewWriteError("owner event capacity read-back did not match the bounded plan")
            intent["event"] = {"eventId": event_id, "name": name, "status": event.get("status"),
                               "startTime": event.get("startTime"), "endTime": event.get("endTime"),
                               "teamSize": team_size, "totalTeams": total_teams}
            intent["status"] = "event_created"
            intent["remoteWrites"] = 1
            save_intent(storage, ledger, intent)
            _sync_world_intent_status(storage, intent)
        print(f"CLUB LAB OPEN PLAY // {'APPLIED' if apply else 'DRY RUN'} // Preview PR #{read_policy.pull_request_number}")
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
        if intent.get("journey") not in {"owner_open_play", "owner_team_signup"} or intent.get("status") not in {"event_created", "players_registered"}:
            raise PreviewWriteError("staff event must be created before player signup")
        if not isinstance(event, dict) or not isinstance(event.get("eventId"), str):
            raise PreviewWriteError("Open Play event record is missing")
        if intent.get("journey") == "owner_open_play":
            promotion_key = intent.get("promotionOccurrenceKey") or f"actor-runtime:season-001:{selected_id.rsplit(':', 1)[-1]}"
            promotion_kind = intent.get("promotionKind") or "runtime_announcement"
            messages = storage.read_jsonl(storage.channel_path("club"))
            if not any(row.get("occurrenceKey") == promotion_key and row.get("kind") == promotion_kind for row in messages):
                raise PreviewWriteError("Open Play must be promoted before player signup")
        credentials = PreviewCredentials.load(root)
        read_policy = TargetPolicy.from_config(credentials.config)
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure(intent.get("participants", []))
        player_rows = []
        teams = intent.get("teams") if isinstance(intent.get("teams"), list) else []
        captain_ids = [row.get("captainActorId") for row in teams if isinstance(row, dict)]
        actor_ids = captain_ids if intent.get("journey") == "owner_team_signup" else intent.get("participants", [])
        writes = 0
        for actor_id in actor_ids:
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
        member_rows = []
        if intent.get("journey") == "owner_team_signup" and apply:
            admin_auth = FirebaseAuthenticator(root / "secrets" / "preview-auth-cache.json").authenticate(
                credentials.admin_email, credentials.admin_password, credentials.firebase_api_key
            )
            write_policy = TargetPolicy.from_config(replace(credentials.config, mode=RunMode.PREVIEW_WRITE, preview_write_confirmation=confirmation))
            for team in teams:
                captain, member = team.get("captainActorId"), team.get("memberActorId")
                if not isinstance(captain, str) or not isinstance(member, str):
                    raise PreviewWriteError("team event has incomplete named teams")
                leader, teammate = identities[captain], identities[member]
                teammate_auth = FirebaseAuthenticator(actor_auth_cache(root, member)).authenticate(teammate.email, teammate.password, credentials.firebase_api_key)
                teammate_client = PreviewReadonlyClient(read_policy, teammate_auth.id_token)
                existing = find_actor_invitation(teammate_client, event["eventId"], teammate.podplay_user_id)
                if not isinstance(existing, dict):
                    PreviewTeamMemberWriter(write_policy, admin_auth.id_token).add_paid_by_leader(event["eventId"], leader.podplay_user_id, {"fullName": f"{teammate.first_name} {teammate.last_name}", "email": teammate.email, "userId": teammate.podplay_user_id})
                    existing = find_actor_invitation(teammate_client, event["eventId"], teammate.podplay_user_id)
                    writes += 1
                if not isinstance(existing, dict):
                    raise PreviewWriteError(f"team-member read-back failed for {member}")
                member_rows.append({"actorId": member, "action": "registered", "invitationId": existing.get("id")})
        if apply and all(row["action"] in {"registered", "already_registered"} for row in player_rows):
            intent["status"] = "players_registered"
            intent["signups"] = player_rows
            if member_rows:
                intent["teamSignups"] = member_rows
            intent["remoteWrites"] = int(intent.get("remoteWrites", 0)) + writes
            save_intent(storage, ledger, intent)
            _sync_world_intent_status(storage, intent)
        print(f"CLUB LAB OPEN PLAY SIGNUP // {'APPLIED' if apply else 'DRY RUN'} // Preview PR #{read_policy.pull_request_number}")
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
        f"CLUB LAB CREDITS // {'APPLIED' if apply else 'DRY RUN'} // "
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
        f"CLUB LAB PAYMENT METHODS // {'APPLIED' if apply else 'DRY RUN'} // "
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
    print(f"CLUB LAB RESCHEDULE // {'APPLIED' if apply else 'DRY RUN'} // Preview PR #{read_policy.pull_request_number}")
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
            if selected_intent is not None and selected_intent.get("journey") in {"customer_training", "customer_team_signup"}:
                from .payer_approval import PayerApprovalError, require_accepted
                preview_plan = selected_intent.get("previewPlan") if isinstance(selected_intent.get("previewPlan"), dict) else {}
                booking_plan_id = preview_plan.get("bookingPlanId")
                if not isinstance(booking_plan_id, str):
                    raise PreviewWriteError("player-owned booking apply requires a canonical booking plan")
                try:
                    require_accepted(root, booking_plan_id, datetime.now(timezone.utc).isoformat())
                except PayerApprovalError as exc:
                    raise PreviewWriteError(str(exc)) from exc
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
                if preview_plan.get("bookingEngine") == "UNIFIED":
                    method = payment_method_id(
                        booker_client.get("/apis/v2/users/current/payment-method")
                    )
                    order = UnifiedBookingWriter(write_policy, booker_auth.id_token).order(
                        plan["podId"], plan["startTime"], plan["endTime"],
                        len(selected_intent.get("participants", [])), method,
                    )
                    order_summary = summarize_unified_order(order)
                else:
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
        f"CLUB LAB BOOKING // {'APPLIED' if apply else 'DRY RUN'} // "
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
                from .payer_approval import PayerApprovalError, require_accepted
                preview_plan = existing_intent.get("previewPlan") if isinstance(existing_intent.get("previewPlan"), dict) else {}
                booking_plan_id = preview_plan.get("bookingPlanId")
                if not isinstance(booking_plan_id, str):
                    raise PreviewWriteError("team signup apply requires a canonical booking plan")
                try:
                    require_accepted(root, booking_plan_id, datetime.now(timezone.utc).isoformat())
                except PayerApprovalError as exc:
                    raise PreviewWriteError(str(exc)) from exc
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
            print(f"CLUB LAB TEAM SIGNUP // DRY RUN // Preview PR #{read_policy.pull_request_number}")
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
            print(f"CLUB LAB TEAM SIGNUP // BOOKED SHARE ROUTE // Preview PR #{read_policy.pull_request_number}")
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
        print(f"CLUB LAB TEAM SIGNUP // {'APPLIED' if apply else 'DRY RUN'} // Preview PR #{read_policy.pull_request_number}")
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
            intent_ledger = load_intent_ledger(storage)
            _, current_intent = select_intent(intent_ledger, intent_id)
            current_intent["nextAction"] = "await_named_player_invitation_attention"
            save_intent(storage, intent_ledger, current_intent, make_active=False)
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
        print(f"CLUB LAB SHARE INVITATION // CAPTURED // intent: {intent_id}; invitation: {record['invitationId']}; writes: 1")
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

    print(f"CLUB LAB SHARE INVITATION // REDEEMED + ACCEPTED // intent: {intent_id}; writes: {writes}")
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
        f"CLUB LAB PARTICIPATION // {'APPLIED' if apply else 'DRY RUN'} // "
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
        f"CLUB LAB CHECK-IN // {'APPLIED' if apply else 'DRY RUN'} // "
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


def observe_finished_preview(root: Path) -> int:
    """Run only the read-back reducers for finished events.

    This command intentionally does not advance actors, publish an offer, or
    write to Preview. It gives operators one auditable way to close elapsed
    research sessions without confusing elapsed time with attendance.
    """
    try:
        owner_event = observe_finished_events(root)
        team_or_training = observe_finished_training_sessions(root)
    except (CredentialsError, FirebaseAuthError, IdentityRegistryError,
            PreviewReadError, TargetPolicyError, ValueError) as exc:
        print(f"ERROR preview finished-event observation: {exc}", file=sys.stderr)
        return 2
    print("CLUB LAB FINISHED-EVENT OBSERVATION // READ-BACK ONLY")
    print(f"owner event: {owner_event['action']}")
    print(f"player/team attendance: {team_or_training['action']}")
    print("remote writes: 0")
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
        owner_event = intent.get("event")
        plan = intent.get("previewPlan")
        event_record = booking if isinstance(booking, dict) else owner_event
        staff_team_event = intent.get("journey") == "owner_team_signup"
        if (
            intent.get("journey") not in {"customer_training", "customer_team_signup", "owner_team_signup"}
            or intent.get("status") not in ({"players_registered"} if staff_team_event else {"booked", "team_members_registered"})
            or not isinstance(participants, list)
            or actor_id not in participants
            or not isinstance(event_record, dict)
            or not isinstance(plan, dict)
            or not isinstance(event_record.get("eventId"), str)
            or not isinstance(plan.get("startTime"), str)
            or not isinstance(plan.get("endTime"), str)
        ):
            raise PreviewWriteError("check-in requires one registered named participant")
        start_time = datetime.fromisoformat(plan["startTime"].replace("Z", "+00:00"))
        end_time = datetime.fromisoformat(plan["endTime"].replace("Z", "+00:00"))
        eligible, window_detail = check_in_window(start_time, end_time, datetime.now(timezone.utc))
        owner_id = intent.get("bookerActorId")
        if staff_team_event:
            teams = intent.get("teams") if isinstance(intent.get("teams"), list) else []
            captain_ids = {row.get("captainActorId") for row in teams if isinstance(row, dict)}
            owner_id = None
        else:
            captain_ids = set()
        if not staff_team_event and (not isinstance(owner_id, str) or owner_id not in participants):
            raise PreviewWriteError("booked training intent is missing its player owner")
        identity_ids = (actor_id,) if staff_team_event else (owner_id, actor_id)
        identities = IdentityRegistry(root / "secrets" / "preview-actors.json").ensure(identity_ids)
        owner = identities.get(owner_id) if isinstance(owner_id, str) else None
        actor = identities[actor_id]
        owner_auth = FirebaseAuthenticator(actor_auth_cache(root, owner_id)).authenticate(
            owner.email, owner.password, credentials.firebase_api_key
        ) if owner is not None else None
        actor_auth = FirebaseAuthenticator(actor_auth_cache(root, actor_id)).authenticate(
            actor.email, actor.password, credentials.firebase_api_key
        )
        event_id = event_record["eventId"]
        owner_client = PreviewReadonlyClient(read_policy, owner_auth.id_token) if owner_auth is not None else None
        actor_client = PreviewReadonlyClient(read_policy, actor_auth.id_token)
        if staff_team_event and actor_id in captain_ids:
            signup_rows = intent.get("signups") if isinstance(intent.get("signups"), list) else []
            signup_id = next((row.get("signupId") for row in signup_rows
                              if isinstance(row, dict) and row.get("actorId") == actor_id), None)
            invitation = read_captain_attendance_invitation(actor_client, event_id, signup_id)
        else:
            invitation = (
                find_owner_invitation(owner_client, event_id)
                if actor_id == owner_id
                else find_actor_invitation(actor_client if staff_team_event else owner_client, event_id, actor.podplay_user_id)
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
                if staff_team_event and actor_id in captain_ids:
                    refreshed = read_captain_attendance_invitation(actor_client, event_id, signup_id)
                else:
                    refreshed = (
                        find_owner_invitation(owner_client, event_id)
                        if actor_id == owner_id
                        else find_actor_invitation(actor_client if staff_team_event else owner_client, event_id, actor.podplay_user_id)
                    )
                if refreshed is None or check_in_status(refreshed) != "CHECKED_IN":
                    raise PreviewWriteError("player check-in read-back is incomplete")
                status = "CHECKED_IN"
            # Staff-published team research has its own intent-level attendance
            # evidence. It must not be blocked by an unrelated legacy match
            # ledger from a previous Preview origin.
            match_ledger = None if staff_team_event else load_match_ledger(storage, read_policy.target_origin)
            occurrence = booking.get("occurrenceKey") if isinstance(booking, dict) else intent.get("occurrenceKey")
            if staff_team_event:
                check_ins = dict(intent.get("checkIns") or {})
                check_ins[actor_id] = {"status": status, "observedAt": datetime.now(timezone.utc).isoformat()}
                intent["checkIns"] = check_ins
                save_intent(storage, ledger, intent, make_active=False)
            if (match_ledger is not None and isinstance(occurrence, str)
                    and isinstance(match_ledger["matches"].get(occurrence), dict)):
                match = dict(match_ledger["matches"][occurrence])
                rows = match.get("participants") if isinstance(match.get("participants"), list) else []
                rows = [row for row in rows if isinstance(row, dict) and row.get("actorId") != actor_id]
                rows.append({
                    "actorId": actor_id,
                    "role": "team captain" if staff_team_event and actor_id in captain_ids else "booking owner" if actor_id == owner_id else "accepted training player",
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

    print(f"CLUB LAB TRAINING CHECK-IN // {'APPLIED' if apply else 'DRY RUN'} // Preview PR #{read_policy.pull_request_number}")
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

    print(f"CLUB LAB MATCHES // Preview PR #{policy.pull_request_number}")
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

    print(f"CLUB LAB MATCH // {result} // Preview PR #{read_policy.pull_request_number}")
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
