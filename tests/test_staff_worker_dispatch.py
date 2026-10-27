from pathlib import Path
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.staff_worker_dispatch import active_packet, claim_next, queue, report  # noqa: E402
from podplay_sim_club.codex_staff_worker_launcher import launch, worker_prompt  # noqa: E402


class StaffWorkerDispatchTests(unittest.TestCase):
    def test_contract_is_durable_and_staff_outcome_is_journaled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queued = queue(root, task={
                "action": "route_staff_initiative", "actorId": "riley", "intentId": "intent:training:one",
                "signature": "v3:agreed:plan_live", "staffAction": "request_bounded_availability_plan",
                "requestedFrom": ["lead"], "blocker": "live_availability_not_read",
            })

            claimed = claim_next(root, worker_id="economical:staff-1")
            reported = report(
                root, contract_key=queued["contractKey"], worker_id="economical:staff-1", outcome="routed",
                diary_line="Riley routed the visible availability-planning request to the lead.",
            )

            self.assertEqual("staff_worker_contract_claimed", claimed["action"])
            self.assertEqual("staff_worker_reported", reported["action"])
            journal = root / "state" / "actors" / "riley" / "journal.jsonl"
            self.assertIn("staff_worker", journal.read_text())

    def test_only_economical_identity_can_claim_staff_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue(root, task={
                "action": "route_staff_initiative", "actorId": "riley", "intentId": "intent:training:one",
                "signature": "v3:agreed:plan_live", "staffAction": "request_bounded_availability_plan",
                "requestedFrom": ["lead"], "blocker": "live_availability_not_read",
            })
            with self.assertRaisesRegex(ValueError, "economical"):
                claim_next(root, worker_id="coordinator:riley")

    def test_staff_launcher_starts_a_detached_economical_riley_worker(self):
        from unittest.mock import Mock, patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue(root, task={
                "action": "route_staff_initiative", "actorId": "riley", "intentId": "intent:training:one",
                "signature": "v3:agreed:plan_live", "staffAction": "request_bounded_availability_plan",
                "requestedFrom": ["lead"], "blocker": "live_availability_not_read",
            })
            with patch("podplay_sim_club.codex_staff_worker_launcher.shutil.which", return_value="/opt/codex"), patch(
                "podplay_sim_club.codex_staff_worker_launcher.subprocess.Popen"
            ) as popen:
                popen.return_value = Mock(pid=1234)
                started = launch(root, worker_id="economical:riley-1", execute=True)

            self.assertEqual("economical_staff_worker_started", started["action"])
            active = active_packet(root, worker_id="economical:riley-1")
            self.assertEqual("staff_worker_active", active["action"])
            self.assertNotIn("password", str(active).lower())

    def test_staff_prompt_limits_riley_to_one_normal_follow_up(self):
        prompt = worker_prompt(root=REPO_ROOT, worker_id="economical:riley-1", packet={
            "contractKey": "staff:opaque", "intentId": "intent:opaque",
            "staffAction": "request_bounded_availability_plan", "blocker": "live_availability_not_read",
        })
        self.assertIn("staff-follow-up route", prompt)
        self.assertIn("Do not open a browser", prompt)
        self.assertNotIn("password", prompt.lower())
