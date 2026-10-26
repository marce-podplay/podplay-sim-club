from pathlib import Path
import json
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.journey_policy import dispatch as dispatch_browser_journey
from podplay_sim_club.worker_dispatch import active_packet, claim, claim_next, queue, report


class WorkerDispatchTests(unittest.TestCase):
    def test_operations_worker_claims_oldest_runnable_contract_with_its_browser_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:first", "bookerActorId": "andy-bogard",
            }, role="rally")
            second_job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:second", "bookerActorId": "terry-bogard",
            }, role="rally")
            queue(root, browser_job=second_job)
            queue(root, browser_job=first_job)

            launched = claim_next(root, worker_id="economical:runner-1")

            self.assertEqual("worker_contract_claimed", launched["action"])
            self.assertEqual("andy-bogard", launched["launchPacket"]["actorId"])
            self.assertEqual("playbooks/sim-player-booking.md", launched["launchPacket"]["playbook"])
            self.assertEqual({"research": False, "headless": True, "modelTier": "economical"}, launched["launchPacket"]["mode"])
            journeys = json.loads((root / "state" / "browser-journeys.json").read_text())
            self.assertEqual("active", journeys["jobs"][first_job["jobKey"]]["status"])
            packet = active_packet(root, worker_id="economical:runner-1")
            self.assertEqual(launched["launchPacket"], packet["launchPacket"])

    def test_operations_worker_skips_a_stale_contract_for_a_runnable_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale = queue(root, browser_job={
                "jobKey": "missing-job", "actorId": "andy-bogard", "intentId": "intent:stale",
                "journeyAction": "plan_training_booking", "playbook": "playbooks/sim-player-booking.md",
            })
            fresh_job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:fresh", "bookerActorId": "terry-bogard",
            }, role="rally")
            queue(root, browser_job=fresh_job)

            launched = claim_next(root, worker_id="economical:runner-1")

            self.assertEqual("worker_contract_claimed", launched["action"])
            self.assertEqual("terry-bogard", launched["launchPacket"]["actorId"])
            contracts = json.loads((root / "state" / "worker-contracts.json").read_text())["contracts"]
            self.assertEqual("queued", contracts[stale["contractKey"]]["status"])

    def test_not_started_worker_releases_unobserved_job_for_a_later_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:retry", "bookerActorId": "andy-bogard",
            }, role="rally")
            queued = queue(root, browser_job=job)
            claim_next(root, worker_id="economical:runner-1")

            reported = report(
                root, contract_key=queued["contractKey"], worker_id="economical:runner-1",
                outcome="not_started", diary_line="No rendered browser was available to start this journey.",
            )

            self.assertEqual("reported", reported["status"])
            journeys = json.loads((root / "state" / "browser-journeys.json").read_text())
            self.assertEqual("queued", journeys["jobs"][job["jobKey"]]["status"])
            retried = queue(root, browser_job=job)
            self.assertEqual("queued", retried["status"])
            contracts = json.loads((root / "state" / "worker-contracts.json").read_text())["contracts"]
            self.assertEqual("queued", contracts[queued["contractKey"]]["status"])

    def test_blocked_worker_quarantines_the_browser_job_without_a_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:uncertain", "bookerActorId": "andy-bogard",
            }, role="rally")
            queued = queue(root, browser_job=job)
            claim_next(root, worker_id="economical:runner-1")

            report(
                root, contract_key=queued["contractKey"], worker_id="economical:runner-1",
                outcome="blocked", diary_line="Worker stopped before it could return a rendered-browser receipt.",
            )

            journeys = json.loads((root / "state" / "browser-journeys.json").read_text())
            self.assertEqual("blocked", journeys["jobs"][job["jobKey"]]["status"])
            self.assertFalse(queued["contractKey"] in [
                item.get("contractKey") for item in json.loads((root / "state" / "worker-contracts.json").read_text())["contracts"].values()
                if item.get("status") == "queued"
            ])

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

