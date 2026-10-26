from pathlib import Path
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.staff_follow_up import _blocker_route, _review_required_intents
from podplay_sim_club.storage import Storage


class StaffFollowUpTests(unittest.TestCase):
    def test_reported_blocked_worker_requires_coordinator_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = Storage(root)
            storage.write_json(storage.state / "worker-contracts.json", {
                "schemaVersion": 1,
                "contracts": {
                    "worker:one": {
                        "intentId": "intent:one", "status": "reported", "outcome": "blocked",
                    },
                    "worker:two": {
                        "intentId": "intent:two", "status": "reported", "outcome": "visible_progress",
                    },
                },
            })

            self.assertEqual({"intent:one"}, _review_required_intents(storage))
            route = _blocker_route({}, "coordinator_review_observed_browser_journey")
            self.assertEqual("observed_browser_journey_requires_review", route["blocker"])
            self.assertEqual(["lead"], route["requestedFrom"])

    def test_missing_contract_state_has_no_review_requirement(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(set(), _review_required_intents(Storage(Path(directory))))
