from contextlib import redirect_stderr
from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.actions import ActionValidator, IllegalAction
from podplay_sim_club.booking_intents import load_intent_ledger
from podplay_sim_club.cli import main
from podplay_sim_club.config import ClubConfig, ConfigurationError, RunMode
from podplay_sim_club.credentials import CredentialsError, PreviewCredentials
from podplay_sim_club.firebase_auth import FirebaseAuthenticator
from podplay_sim_club.identity_registry import ActorIdentity, IdentityRegistry
from podplay_sim_club.fake_preview import FakePreview
from podplay_sim_club.orchestrator import Orchestrator, SimulatedCrash
from podplay_sim_club.preview import PreviewAdapter
from podplay_sim_club.preview_booking import PreviewBookingWriter, occurrence_key
from podplay_sim_club.preview_match_ledger import (
    load_match_ledger,
    sanitized_match_index,
    save_match,
    select_active,
    select_match,
)
from podplay_sim_club.preview_participation import (
    PreviewAcceptanceEvaluator,
    PreviewParticipationWriter,
    classify_match_status,
)
from podplay_sim_club.preview_evaluation import (
    PreviewBookingEvaluator,
    PreviewEvaluationError,
    summarize_booking_preview,
)
from podplay_sim_club.preview_readonly import PreviewReadonlyClient
from podplay_sim_club.preview_readiness import select_candidate_session, summarize_payment
from podplay_sim_club.preview_payment import load_test_stripe_secret
from podplay_sim_club.preview_write import (
    PreviewCreditWriter,
    PreviewIdentityWriter,
    PreviewWriteError,
)
from podplay_sim_club.server import ObservatoryServer
from podplay_sim_club.storage import Storage
from podplay_sim_club.target_policy import TargetPolicy, TargetPolicyError
from podplay_sim_club.terminal import render, render_needs


FIXED_NOW = datetime(2026, 9, 23, 15, 5, tzinfo=timezone.utc)
PREVIEW_ORIGIN = (
    "https://podify-pr-5207-staging-main-service-bay66ucqza-uk.a.run.app"
)


class SimClubTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        shutil.copytree(REPO_ROOT / "seed", self.root / "seed")
        shutil.copytree(REPO_ROOT / "scenarios", self.root / "scenarios")

    def tearDown(self):
        self.temporary.cleanup()

    def orchestrator(self):
        return Orchestrator(self.root)

    def test_hourly_match_completes_once_and_same_hour_is_noop(self):
        orchestrator = self.orchestrator()

        first = orchestrator.run_beat(turns=10, now=FIXED_NOW)
        second = orchestrator.run_beat(turns=10, now=FIXED_NOW)

        self.assertEqual("complete", first["status"])
        self.assertEqual(8, first["turnsExecuted"])
        self.assertEqual(0, second["turnsExecuted"])
        preview = json.loads((self.root / "state" / "fake-preview.json").read_text())
        self.assertEqual(1, len(preview["bookings"]))
        self.assertEqual(
            ["blue-captain", "red-captain"],
            sorted(preview["bookings"][0]["checkedIn"]),
        )

    def test_need_drives_conversation_and_durable_booking_intent(self):
        orchestrator = self.orchestrator()

        first = orchestrator.run_needs_beat(turns=2, now=FIXED_NOW)
        resumed = orchestrator.run_needs_beat(turns=2, now=FIXED_NOW)
        replay = orchestrator.run_needs_beat(turns=4, now=FIXED_NOW)

        self.assertEqual("running", first["status"])
        self.assertEqual("complete", resumed["status"])
        self.assertEqual(0, replay["turnsExecuted"])
        messages = orchestrator.storage.read_jsonl(
            orchestrator.storage.channel_path("club")
        )
        self.assertEqual(
            ["need_driven_proposal", "availability_agreement"],
            [message["kind"] for message in messages],
        )
        ledger = load_intent_ledger(orchestrator.storage)
        self.assertEqual(1, len(ledger["intents"]))
        intent = next(iter(ledger["intents"].values()))
        self.assertEqual("agreed", intent["status"])
        self.assertEqual(0, intent["remoteWrites"])
        self.assertEqual(
            ["red-captain", "blue-captain"], intent["participants"]
        )
        preview = json.loads((self.root / "state" / "fake-preview.json").read_text())
        self.assertEqual([], preview["bookings"])
        frame = render_needs(resumed["world"])
        self.assertIn("desire-to-play 85/80  READY", frame)
        self.assertIn("BOOKING INTENTS", frame)

    def test_orchestrator_uses_the_preview_adapter_boundary(self):
        created = []

        def factory(storage, seed):
            adapter = FakePreview(storage, seed)
            created.append(adapter)
            return adapter

        result = Orchestrator(self.root, preview_factory=factory).run_beat(
            turns=10, now=FIXED_NOW
        )

        self.assertEqual("complete", result["status"])
        self.assertTrue(created)
        self.assertTrue(all(isinstance(item, PreviewAdapter) for item in created))

    def test_non_fake_runtime_cannot_fall_back_to_fake(self):
        stderr = StringIO()
        with patch.dict(
            os.environ, {"PODPLAY_SIM_MODE": "preview-readonly"}, clear=True
        ):
            with redirect_stderr(stderr):
                result = main(["--home", str(self.root), "status"])

        self.assertEqual(2, result)
        self.assertIn("preview execution is not implemented", stderr.getvalue())
        self.assertFalse((self.root / "state").exists())

    def test_commit_before_checkpoint_reconciles_without_duplicate_booking(self):
        orchestrator = self.orchestrator()

        with self.assertRaises(SimulatedCrash):
            orchestrator.run_beat(
                turns=10, now=FIXED_NOW, crash_after_remote="booking_created"
            )

        preview_after_crash = json.loads(
            (self.root / "state" / "fake-preview.json").read_text()
        )
        self.assertEqual(1, len(preview_after_crash["bookings"]))

        resumed = orchestrator.run_beat(turns=10, now=FIXED_NOW)
        preview_after_resume = json.loads(
            (self.root / "state" / "fake-preview.json").read_text()
        )
        self.assertEqual("complete", resumed["status"])
        self.assertEqual(1, len(preview_after_resume["bookings"]))

    def test_fresh_preview_starts_new_season_and_preserves_actor_journal(self):
        orchestrator = self.orchestrator()
        orchestrator.run_beat(turns=10, now=FIXED_NOW)
        journal = self.root / "state" / "actors" / "red-captain" / "journal.jsonl"
        original_journal = journal.read_text()

        orchestrator.reset_fake_preview()
        view = orchestrator.world_view(FIXED_NOW)

        self.assertEqual("season-002", view["season"])
        self.assertEqual(original_journal, journal.read_text())
        preview = json.loads((self.root / "state" / "fake-preview.json").read_text())
        self.assertEqual([], preview["bookings"])
        self.assertEqual("preview-club-v1", preview["seedFingerprint"])
        archive = self.root / "state" / "seasons" / "season-001" / "world-final.json"
        self.assertTrue(archive.is_file())

    def test_actor_cannot_choose_another_pod(self):
        validator = ActionValidator("pod-1", ["book_match"])
        with self.assertRaises(IllegalAction):
            validator.validate(
                {"type": "book_match", "arguments": {"podId": "pod-other"}}
            )

    def test_terminal_contains_world_hints(self):
        orchestrator = self.orchestrator()
        orchestrator.run_beat(turns=10, now=FIXED_NOW)
        frame = render(orchestrator.world_view(FIXED_NOW))
        self.assertIn("PREVIEW CLUB // WORLD SIGNAL", frame)
        self.assertIn("POD-1 [R] ⇄ [B] occupied", frame)
        self.assertIn("BOOKING TRACE", frame)

    def test_observatory_serves_world_and_health(self):
        orchestrator = self.orchestrator()
        orchestrator.run_beat(turns=2, now=FIXED_NOW)
        orchestrator.storage.write_json(
            orchestrator.storage.state / "preview-match-status.json",
            {
                "schemaVersion": 1,
                "observedAt": "2026-09-24T17:00:00+00:00",
                "pullRequest": 7200,
                "result": "SCHEDULED",
                "event": {
                    "eventId": "event-live",
                    "status": "CONFIRMED",
                    "type": "REGULAR",
                    "subtype": "PRIVATE",
                    "startTime": "2026-09-26T12:00:00.000Z",
                    "endTime": "2026-09-26T12:30:00.000Z",
                },
                "venue": {
                    "areaName": "East 95th",
                    "podName": "Test",
                    "timezone": "America/New_York",
                    "courtAssignment": "Auto-assigned court",
                },
                "journey": {
                    "phase": "joined",
                    "preparedAt": "2026-09-24T17:00:00+00:00",
                    "occurrenceKey": "pp7444-test",
                    "orderTotal": 0,
                    "currency": "USD",
                    "virtualCreditsUsed": 8.8,
                },
                "blueInvitation": {"status": "ACCEPTED"},
                "checkIns": {
                    "redCaptain": "NOT_CHECKED_IN",
                    "blueCaptain": "NOT_CHECKED_IN",
                },
            },
        )
        orchestrator.storage.write_json(
            orchestrator.storage.state / "preview-matches.json",
            {
                "schemaVersion": 2,
                "targetOrigin": PREVIEW_ORIGIN,
                "activeOccurrenceKey": "pp7444-test",
                "matches": {
                    "pp7444-test": {
                        "phase": "joined",
                        "eventId": "event-live",
                        "plan": {
                            "occurrenceKey": "pp7444-test",
                            "podId": "pod-test",
                            "startTime": "2026-09-26T12:00:00.000Z",
                            "endTime": "2026-09-26T12:30:00.000Z",
                        },
                        "event": {"status": "CONFIRMED"},
                    }
                },
            },
        )
        server = ObservatoryServer(orchestrator, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.address
        try:
            with urlopen(f"http://{host}:{port}/health", timeout=2) as response:
                health = json.load(response)
            with urlopen(f"http://{host}:{port}/api/world", timeout=2) as response:
                world = json.load(response)
            with urlopen(f"http://{host}:{port}/", timeout=2) as response:
                page = response.read().decode("utf-8")
        finally:
            server.shutdown()
            thread.join(timeout=2)

        self.assertEqual({"mode": "fake", "ok": True}, health)
        self.assertEqual("season-001", world["season"])
        self.assertEqual("SCHEDULED", world["previewMatch"]["result"])
        self.assertEqual("pp7444-test", world["previewMatches"]["activeOccurrenceKey"])
        self.assertEqual(1, len(world["previewMatches"]["matches"]))
        self.assertIn("PODPLAY SIM CLUB // OBSERVATORY", page)
        self.assertIn("LOCAL FAKE WORLD", page)
        self.assertIn("REMOTE PRODUCT STATE", page)
        self.assertIn('data-view="preview" aria-selected="true"', page)
        self.assertIn('id="fake-view" hidden', page)
        self.assertIn('id="copy-next"', page)
        self.assertIn("navigator.clipboard.writeText", page)
        self.assertIn("if (element.innerHTML !== html)", page)
        self.assertIn("CHARACTER NEEDS", page)
        self.assertIn("BOOKING INTENTS", page)
        self.assertIn("bookingIntentLedger", world)


class PreviewMatchLedgerTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.storage = Storage(self.root)
        self.origin = PREVIEW_ORIGIN

    def tearDown(self):
        self.temporary.cleanup()

    def match(self, key, start, event_id):
        return {
            "schemaVersion": 2,
            "targetOrigin": self.origin,
            "phase": "booked",
            "eventId": event_id,
            "plan": {
                "occurrenceKey": key,
                "podId": "pod-test",
                "startTime": start,
                "endTime": start,
            },
            "event": {"status": "CONFIRMED"},
        }

    def test_migrates_single_match_checkpoint_without_losing_it(self):
        legacy = self.match("first", "2026-09-26T12:00:00Z", "event-1")
        legacy["schemaVersion"] = 1
        self.storage.write_json(
            self.storage.state / "preview-manual-match.json", legacy
        )

        ledger = load_match_ledger(self.storage, self.origin)

        self.assertEqual(2, ledger["schemaVersion"])
        self.assertEqual("first", ledger["activeOccurrenceKey"])
        self.assertEqual("event-1", ledger["matches"]["first"]["eventId"])
        self.assertTrue((self.storage.state / "preview-matches.json").is_file())

    def test_upsert_is_occurrence_idempotent_and_active_is_explicit(self):
        ledger = load_match_ledger(self.storage, self.origin)
        save_match(
            self.storage,
            ledger,
            self.match("first", "2026-09-26T12:00:00Z", "event-1"),
        )
        save_match(
            self.storage,
            ledger,
            self.match("second", "2026-09-26T12:30:00Z", "event-2"),
        )
        save_match(
            self.storage,
            ledger,
            self.match("second", "2026-09-26T12:30:00Z", "event-2"),
        )

        self.assertEqual(2, len(ledger["matches"]))
        self.assertEqual("second", select_match(ledger)[0])
        select_active(self.storage, ledger, "first")
        self.assertEqual("first", select_match(ledger)[0])
        index = sanitized_match_index(ledger)
        self.assertEqual(["first", "second"], [row["occurrenceKey"] for row in index["matches"]])
        self.assertTrue(index["matches"][0]["active"])


class TargetPolicyTestCase(unittest.TestCase):
    def config(self, mode=RunMode.PREVIEW_READONLY, **changes):
        values = {
            "mode": mode,
            "preview_origin": PREVIEW_ORIGIN,
            "allowed_preview_origins": (PREVIEW_ORIGIN,),
            "preview_write_confirmation": None,
        }
        values.update(changes)
        return ClubConfig(**values)

    def test_exact_pr_preview_origin_is_approved(self):
        policy = TargetPolicy.from_config(self.config())

        self.assertEqual(5207, policy.pull_request_number)
        self.assertEqual(PREVIEW_ORIGIN, policy.target_origin)
        policy.assert_url(f"{PREVIEW_ORIGIN}/apis/v2/users?limit=1")

    def test_shared_staging_is_rejected_even_when_allowlisted(self):
        origin = "https://pingpod-staging.podplay.app"
        with self.assertRaises(TargetPolicyError):
            TargetPolicy.from_config(
                self.config(
                    preview_origin=origin,
                    allowed_preview_origins=(origin,),
                )
            )

    def test_production_is_rejected_even_when_allowlisted(self):
        origin = "https://pingpod.podplay.app"
        with self.assertRaises(TargetPolicyError):
            TargetPolicy.from_config(
                self.config(
                    preview_origin=origin,
                    allowed_preview_origins=(origin,),
                )
            )

    def test_preview_must_be_in_exact_allowlist(self):
        with self.assertRaises(TargetPolicyError):
            TargetPolicy.from_config(self.config(allowed_preview_origins=()))

    def test_preview_origin_cannot_contain_a_path(self):
        with self.assertRaises(TargetPolicyError):
            TargetPolicy.from_config(
                self.config(preview_origin=f"{PREVIEW_ORIGIN}/admin")
            )

    def test_preview_write_requires_exact_origin_confirmation(self):
        with self.assertRaises(TargetPolicyError):
            TargetPolicy.from_config(self.config(mode=RunMode.PREVIEW_WRITE))

        policy = TargetPolicy.from_config(
            self.config(
                mode=RunMode.PREVIEW_WRITE,
                preview_write_confirmation=PREVIEW_ORIGIN,
            )
        )
        self.assertEqual(PREVIEW_ORIGIN, policy.target_origin)
        self.assertTrue(policy.writes_allowed)

    def test_cross_origin_request_or_redirect_is_rejected(self):
        policy = TargetPolicy.from_config(self.config())
        with self.assertRaises(TargetPolicyError):
            policy.assert_url(
                "https://podify-pr-5208-staging-main-service-bay66ucqza-uk.a.run.app/"
            )

    def test_environment_configuration_is_explicit(self):
        config = ClubConfig.from_environment(
            {
                "PODPLAY_SIM_MODE": "preview-readonly",
                "PODPLAY_SIM_PREVIEW_ORIGIN": PREVIEW_ORIGIN,
                "PODPLAY_SIM_ALLOWED_PREVIEW_ORIGINS": PREVIEW_ORIGIN,
                "PODPLAY_SIM_ADMIN_EMAIL": "admin@example.test",
            }
        )

        self.assertIs(config.mode, RunMode.PREVIEW_READONLY)
        self.assertEqual((PREVIEW_ORIGIN,), config.allowed_preview_origins)
        self.assertEqual("admin@example.test", config.admin_email)

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ConfigurationError):
            ClubConfig.from_environment({"PODPLAY_SIM_MODE": "production"})


class PreviewConnectionTestCase(unittest.TestCase):
    def test_credentials_require_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secrets = root / "secrets"
            secrets.mkdir()
            path = secrets / "preview-credentials.json"
            path.write_text(json.dumps({}), encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaises(CredentialsError):
                PreviewCredentials.load(root)

    def test_firebase_auth_uses_private_cache_then_reuses_it(self):
        calls = []
        now = 1_700_000_000
        payload = base64_url({"exp": now + 3600})
        token = f"header.{payload}.signature"

        def request_json(url, body):
            calls.append((url, body))
            return {
                "idToken": token,
                "refreshToken": "refresh-value",
                "email": "admin@example.test",
            }

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "auth.json"
            authenticator = FirebaseAuthenticator(cache, request_json, now=lambda: now)
            first = authenticator.authenticate("admin@example.test", "password", "api-key")
            second = authenticator.authenticate("admin@example.test", "password", "api-key")

            self.assertEqual("password", first.source)
            self.assertEqual("cache", second.source)
            self.assertEqual(1, len(calls))
            self.assertEqual(0o600, cache.stat().st_mode & 0o777)
            self.assertNotIn("password", cache.read_text(encoding="utf-8"))

    def test_preview_client_allows_get_but_rejects_other_origin(self):
        policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_READONLY,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
            )
        )

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def geturl(self):
                return PREVIEW_ORIGIN + "/apis/v2/areas"

            def read(self, _limit):
                return b'{"items":[]}'

        requests = []

        def transport(request, timeout):
            requests.append((request, timeout))
            return Response()

        client = PreviewReadonlyClient(policy, "secret-token", transport=transport)
        result = client.get("/apis/v2/areas")
        self.assertEqual([], result["items"])
        self.assertEqual("GET", requests[0][0].method)
        self.assertEqual("Bearer secret-token", requests[0][0].get_header("Authorization"))
        with self.assertRaises(TargetPolicyError):
            client.get("https://example.com/apis/v2/areas")

    def test_identity_registry_generates_private_stable_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preview-actors.json"
            registry = IdentityRegistry(path)
            first = registry.ensure()
            second = registry.ensure()

            self.assertEqual(first["sofia"].email, second["sofia"].email)
            self.assertEqual(first["sofia"].password, second["sofia"].password)
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertNotIn(first["sofia"].password, repr(first["sofia"]))

    def test_identity_writer_requires_write_policy(self):
        policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_READONLY,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
            )
        )
        with self.assertRaises(PreviewWriteError):
            PreviewIdentityWriter(policy)

        with self.assertRaises(PreviewWriteError):
            PreviewCreditWriter(policy, "admin-token")

    def test_payment_reference_is_not_a_saved_payment_method(self):
        result = summarize_payment(
            {
                "id": "actor-id",
                "stripe": {"userId": "cus_test"},
                "preferredCard": None,
                "preferredBankAccount": None,
                "paymentElement": None,
                "virtualCredits": 0,
            }
        )

        self.assertFalse(result["hasPaymentMethod"])
        self.assertEqual(0.0, result["virtualCredits"])

    def test_payment_element_proves_saved_payment_method(self):
        result = summarize_payment(
            {"paymentElement": {"id": "pm_test"}, "virtualCredits": 12.5}
        )

        self.assertTrue(result["hasPaymentMethod"])
        self.assertEqual(12.5, result["virtualCredits"])

    def test_candidate_session_uses_available_default_table_rate(self):
        result = select_candidate_session(
            [
                {
                    "id": "session-1",
                    "status": "AVAILABLE",
                    "tablesLeft": 1,
                    "startTime": "2026-09-26T12:00:00.000Z",
                    "endTime": "2026-09-26T12:30:00.000Z",
                    "operatingDate": "2026-09-26",
                    "periodType": "OFF_PEAK",
                    "defaultTable": {"id": "fixed"},
                    "availableTables": {
                        "items": [
                            {"id": "auto", "type": "AUTO", "rate": 10},
                            {"id": "fixed", "type": "FIXED_TABLE", "rate": 15},
                        ]
                    },
                }
            ]
        )

        self.assertEqual("session-1", result["sessionId"])
        self.assertEqual("fixed", result["tableId"])
        self.assertEqual(15.0, result["rate"])

    def test_candidate_session_chooses_nearest_after_safety_lead(self):
        sessions = [
            {
                "id": "later",
                "status": "AVAILABLE",
                "tablesLeft": 1,
                "startTime": "2026-09-24T18:00:00Z",
                "endTime": "2026-09-24T18:30:00Z",
                "availableTables": {"items": [{"id": "later-table", "rate": 0}]},
            },
            {
                "id": "too-soon",
                "status": "AVAILABLE",
                "tablesLeft": 1,
                "startTime": "2026-09-24T17:10:00Z",
                "endTime": "2026-09-24T17:40:00Z",
                "availableTables": {"items": [{"id": "soon-table", "rate": 0}]},
            },
            {
                "id": "nearest",
                "status": "AVAILABLE",
                "tablesLeft": 1,
                "startTime": "2026-09-24T17:40:00Z",
                "endTime": "2026-09-24T18:10:00Z",
                "availableTables": {"items": [{"id": "near-table", "rate": 0}]},
            },
        ]

        result = select_candidate_session(
            sessions,
            not_before=datetime(2026, 9, 24, 17, 30, tzinfo=timezone.utc),
        )

        self.assertEqual("nearest", result["sessionId"])

    def test_candidate_session_honors_agreed_venue_local_window(self):
        sessions = [
            {
                "id": "earlier-evening",
                "status": "AVAILABLE",
                "tablesLeft": 1,
                "startTime": "2026-09-28T21:00:00Z",
                "endTime": "2026-09-28T21:30:00Z",
                "availableTables": {"items": [{"id": "evening-table", "rate": 0}]},
            },
            {
                "id": "next-morning",
                "status": "AVAILABLE",
                "tablesLeft": 1,
                "startTime": "2026-09-29T13:00:00Z",
                "endTime": "2026-09-29T13:30:00Z",
                "availableTables": {"items": [{"id": "morning-table", "rate": 0}]},
            },
        ]

        result = select_candidate_session(
            sessions,
            timezone_name="America/New_York",
            allowed_local_windows=["08:00-12:00"],
        )

        self.assertEqual("next-morning", result["sessionId"])

    def test_booking_evaluator_sends_only_fixed_preview_payload(self):
        policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_READONLY,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
            )
        )

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def geturl(self):
                return PREVIEW_ORIGIN + "/apis/v2/bookings"

            def read(self, _limit):
                return b'{"type":"PREVIEW","status":"DRAFT","summary":{"total":0}}'

        requests = []

        def transport(request, timeout):
            requests.append((request, timeout))
            return Response()

        result = PreviewBookingEvaluator(policy, "secret", transport).evaluate(
            "session-1", "table-1"
        )
        body = json.loads(requests[0][0].data.decode("utf-8"))

        self.assertEqual("PREVIEW", result["type"])
        self.assertEqual("POST", requests[0][0].method)
        self.assertEqual("PREVIEW", body["type"])
        self.assertEqual("USE_NONE", body["passesStrategy"])
        self.assertEqual(0, body["virtualCredits"])
        self.assertNotIn("owner", body)

    def test_booking_evaluator_rejects_order_payload(self):
        with self.assertRaises(PreviewEvaluationError):
            PreviewBookingEvaluator._validate_payload(
                {
                    "type": "ORDER",
                    "items": [],
                    "chargeStrategy": "ONLY_OWNER",
                    "passesStrategy": "USE_NONE",
                    "virtualCredits": 0,
                    "termsAgreed": True,
                    "bookingMode": "USER_BOOKED",
                }
            )

    def test_booking_preview_summary_is_sanitized(self):
        result = summarize_booking_preview(
            {
                "type": "PREVIEW",
                "status": "DRAFT",
                "summary": {
                    "total": 0,
                    "currency": "USD",
                    "virtualCredits": 0,
                    "maxChargableAmount": 0,
                    "errors": [],
                },
                "items": [{"errors": []}],
            }
        )

        self.assertTrue(result["readyForOrder"])
        self.assertEqual([], result["errorCodes"])

    def test_payment_seed_refuses_live_stripe_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stripe.env"
            path.write_text("STRIPE_SECRET_KEY=sk_live_forbidden\n", encoding="utf-8")

            with self.assertRaises(PreviewWriteError):
                load_test_stripe_secret(path)

    def test_payment_seed_loads_only_test_stripe_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stripe.env"
            path.write_text(
                "UNRELATED=value\nSTRIPE_SECRET_KEY='sk_test_example'\n",
                encoding="utf-8",
            )

            self.assertEqual("sk_test_example", load_test_stripe_secret(path))

    def test_booking_writer_has_one_write_budget_and_fixed_order_shape(self):
        policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_WRITE,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
                preview_write_confirmation=PREVIEW_ORIGIN,
            )
        )

        class Response:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def geturl(self):
                return PREVIEW_ORIGIN + "/apis/v2/bookings"

            def read(self, _limit):
                return b'{"id":"event-1","type":"ORDER","status":"CONFIRMED"}'

        requests = []

        def transport(request, timeout):
            requests.append((request, timeout))
            return Response()

        writer = PreviewBookingWriter(policy, "actor-token", transport)
        result = writer.order("session-1", "table-1", 25)
        body = json.loads(requests[0][0].data.decode("utf-8"))

        self.assertEqual("event-1", result["id"])
        self.assertEqual("ORDER", body["type"])
        self.assertEqual("ONLY_OWNER", body["chargeStrategy"])
        self.assertNotIn("owner", body)
        with self.assertRaises(PreviewWriteError):
            writer.order("session-1", "table-1", 25)

    def test_booking_occurrence_key_is_stable_and_slot_specific(self):
        first = occurrence_key("pod-1", "2026-09-26T12:00:00Z", "red-captain")
        second = occurrence_key("pod-1", "2026-09-26T12:00:00Z", "red-captain")
        other = occurrence_key("pod-1", "2026-09-26T12:30:00Z", "red-captain")

        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_participation_writer_uses_pending_leader_paid_invitation(self):
        policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_WRITE,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
                preview_write_confirmation=PREVIEW_ORIGIN,
            )
        )

        class Response:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def geturl(self):
                return PREVIEW_ORIGIN + "/apis/v2/events/event-1/invitations"

            def read(self, _limit):
                return b'{"id":"invite-1","status":"INVITATION_EXTENDED"}'

        requests = []
        writer = PreviewParticipationWriter(
            policy, "red-token", lambda request, timeout: requests.append(request) or Response()
        )
        actor = ActorIdentity(
            actor_id="blue-captain",
            email="blue@example.test",
            first_name="Blue",
            last_name="Captain",
            password="secret",
            podplay_user_id="blue-user-1",
        )
        writer.invite("event-1", actor)
        body = json.loads(requests[0].data.decode("utf-8"))

        self.assertEqual("GUEST", body["type"])
        self.assertEqual("PAID_BY_INVITER", body["chargeType"])
        self.assertEqual(actor.podplay_user_id, body["invitee"]["userId"])
        with self.assertRaises(PreviewWriteError):
            writer.invite("event-1", actor)

    def test_acceptance_evaluator_cannot_persist(self):
        policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_READONLY,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
            )
        )

        class Response:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def geturl(self):
                return PREVIEW_ORIGIN + "/apis/v2/events/event-1/invitations/invite-1/acceptance"

            def read(self, _limit):
                return b'{"summary":{"total":0,"errors":[]},"invitation":{"id":"invite-1"}}'

        requests = []
        PreviewAcceptanceEvaluator(
            policy, "blue-token", lambda request, timeout: requests.append(request) or Response()
        ).evaluate("event-1", "invite-1")
        body = json.loads(requests[0].data.decode("utf-8"))

        self.assertEqual("PREVIEW", body["type"])
        self.assertEqual("USE_NONE", body["passesStrategy"])

    def test_participation_writer_check_in_has_empty_body(self):
        policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_WRITE,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
                preview_write_confirmation=PREVIEW_ORIGIN,
            )
        )

        class Response:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def geturl(self):
                return PREVIEW_ORIGIN + "/apis/v2/events/event-1/invitations/invite-1/check-in"

            def read(self, _limit):
                return b'{"id":"invite-1","status":"CHECKED_IN"}'

        requests = []
        PreviewParticipationWriter(
            policy, "blue-token", lambda request, timeout: requests.append(request) or Response()
        ).check_in("event-1", "invite-1")

        self.assertEqual({}, json.loads(requests[0].data.decode("utf-8")))

    def test_match_status_waits_then_passes(self):
        owner = {"checkIn": {"status": "NOT_CHECKED_IN"}}
        blue = {
            "status": "ACCEPTED",
            "checkIn": {"status": "NOT_CHECKED_IN"},
        }
        start = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)

        self.assertEqual(
            "SCHEDULED",
            classify_match_status(blue, owner, start, FIXED_NOW),
        )
        owner["checkIn"]["status"] = "CHECKED_IN"
        blue["checkIn"]["status"] = "CHECKED_IN"
        self.assertEqual("PASS", classify_match_status(blue, owner, start, start))


def base64_url(value):
    import base64

    encoded = base64.urlsafe_b64encode(json.dumps(value).encode("utf-8"))
    return encoded.decode("ascii").rstrip("=")


if __name__ == "__main__":
    unittest.main()
