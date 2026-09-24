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
from podplay_sim_club.cli import main
from podplay_sim_club.config import ClubConfig, ConfigurationError, RunMode
from podplay_sim_club.fake_preview import FakePreview
from podplay_sim_club.orchestrator import Orchestrator, SimulatedCrash
from podplay_sim_club.preview import PreviewAdapter
from podplay_sim_club.server import ObservatoryServer
from podplay_sim_club.target_policy import TargetPolicy, TargetPolicyError
from podplay_sim_club.terminal import render


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
        self.assertIn("PREVIEW CLUB // WORLD SIGNAL", page)


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


if __name__ == "__main__":
    unittest.main()
