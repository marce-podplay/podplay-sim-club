from pathlib import Path
import json
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.worker_dispatch import claim, queue, report


class WorkerDispatchTests(unittest.TestCase):
    def test_contract_is_sequential_and_writes_a_bounded_diary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queued = queue(root, browser_job={
                "jobKey": "player:plan:intent:one:andy-bogard", "actorId": "andy-bogard",
                "intentId": "intent:one", "journeyAction": "plan_training_booking",
                "playbook": "playbooks/sim-player-booking.md",
            })
            self.assertEqual("queued", queued["status"])
            active = claim(root, contract_key=queued["contractKey"], worker_id="economical:andy-bogard")
            self.assertEqual("active", active["status"])
            done = report(root, contract_key=queued["contractKey"], worker_id="economical:andy-bogard", outcome="blocked", diary_line="Rendered checkout had no enabled continue control.")
            self.assertEqual("reported", done["status"])
            row = json.loads((root / "state" / "actors" / "andy-bogard" / "journal.jsonl").read_text())
            self.assertEqual("blocked", row["outcome"])
            self.assertEqual(0, row["remoteWrites"])

