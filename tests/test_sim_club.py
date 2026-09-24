from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.actions import ActionValidator, IllegalAction
from podplay_sim_club.orchestrator import Orchestrator, SimulatedCrash
from podplay_sim_club.server import ObservatoryServer
from podplay_sim_club.terminal import render


FIXED_NOW = datetime(2026, 9, 23, 15, 5, tzinfo=timezone.utc)


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


if __name__ == "__main__":
    unittest.main()
