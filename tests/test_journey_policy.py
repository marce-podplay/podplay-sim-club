from pathlib import Path
import json
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.journey_policy import (  # noqa: E402
    browser_journey_required, claim, dispatch, record_observation,
)


class JourneyPolicyTests(unittest.TestCase):
    def test_dispatch_queues_only_a_named_sim_and_redacts_product_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = {"action": "check_in_training", "intentId": "intent:training:one", "actorId": "andy-bogard"}
            first = dispatch(root, task=task, role="player", observed_at="2026-10-22T12:00:00Z")
            second = dispatch(root, task=task, role="player", observed_at="2026-10-22T12:01:00Z")
            self.assertEqual("queued", first["status"])
            self.assertEqual(first["jobKey"], second["jobKey"])
            state = json.loads((root / "state" / "browser-journeys.json").read_text())
            job = state["jobs"][first["jobKey"]]
            self.assertEqual("andy-bogard", job["actorId"])
            self.assertEqual("isolated_visible_browser", job["workflow"])
            self.assertEqual("playbooks/sim-player-check-in.md", job["playbook"])
            self.assertNotIn("origin", job)
            self.assertNotIn("password", job)

    def test_unnamed_task_never_queues_a_browser_session(self):
        job = browser_journey_required(task={"action": "plan_training_booking"}, role="player")
        self.assertIsNone(job["actorId"])
        with tempfile.TemporaryDirectory() as directory:
            result = dispatch(Path(directory), task={"action": "plan_training_booking"}, role="player")
            self.assertNotIn("jobKey", result)
            self.assertFalse((Path(directory) / "state" / "browser-journeys.json").exists())

    def test_named_sim_can_claim_and_record_a_bounded_browser_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = dispatch(
                root,
                task={"action": "plan_training_booking", "intentId": "intent:training:one", "bookerActorId": "kyo-captain"},
                role="player",
                observed_at="2026-10-22T12:00:00Z",
            )
            claimed = claim(root, job_key=job["jobKey"], actor_id="kyo-captain", observed_at="2026-10-22T12:01:00Z")
            observed = record_observation(
                root,
                job_key=job["jobKey"],
                actor_id="kyo-captain",
                kind="visible_action_completed",
                observed_at="2026-10-22T12:02:00Z",
            )
            self.assertEqual("active", claimed["status"])
            self.assertEqual("observed", observed["status"])
            state = json.loads((root / "state" / "browser-journeys.json").read_text())
            saved = state["jobs"][job["jobKey"]]
            self.assertEqual("visible_action_completed", saved["lastObservation"])
            self.assertEqual("playbooks/sim-player-booking.md", saved["playbook"])
            self.assertNotIn("origin", saved)
            self.assertNotIn("evidencePath", saved)

            dispatch(
                root,
                task={"action": "plan_training_booking", "intentId": "intent:training:one", "bookerActorId": "kyo-captain"},
                role="player",
                observed_at="2026-10-22T12:03:00Z",
            )
            refreshed = json.loads((root / "state" / "browser-journeys.json").read_text())["jobs"][job["jobKey"]]
            self.assertEqual("observed", refreshed["status"])
            self.assertEqual("visible_action_completed", refreshed["lastObservation"])

    def test_invitation_and_arrival_work_items_select_their_own_playbooks(self):
        invitation = browser_journey_required(
            task={"action": "redeem_share_invitation", "intentId": "intent:training:one", "recipientActorId": "mai"},
            role="player",
        )
        arrival = browser_journey_required(
            task={"action": "check_in_training", "intentId": "intent:training:one", "actorId": "mai"},
            role="player",
        )
        self.assertEqual("mai", invitation["actorId"])
        self.assertEqual("playbooks/sim-player-invitation.md", invitation["playbook"])
        self.assertEqual("playbooks/sim-player-check-in.md", arrival["playbook"])
