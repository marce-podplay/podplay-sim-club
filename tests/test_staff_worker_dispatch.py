from pathlib import Path
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.staff_worker_dispatch import claim_next, queue, report  # noqa: E402


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
