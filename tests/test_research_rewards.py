import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.research_rewards import (  # noqa: E402
    ResearchRewardError, approve, offer, respond, snapshot,
)
from podplay_sim_club.storage import Storage  # noqa: E402


class ResearchRewardTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sim-club-reward-"))
        secrets = self.root / "secrets"
        secrets.mkdir()
        credentials = secrets / "preview-credentials.json"
        credentials.write_text(json.dumps({
            "mode": "preview-readonly", "previewOrigin": "https://podify-pr-9-staging-main-service-test.a.run.app",
            "allowedPreviewOrigins": ["https://podify-pr-9-staging-main-service-test.a.run.app"],
            "admin": {"email": "admin@example.test", "password": "not-a-real-password"},
            "firebaseApiKey": "not-a-real-key",
        }), encoding="utf-8")
        credentials.chmod(0o600)
        self.storage = Storage(self.root)
        self.probe_id = "probe:feature:reward"
        self.storage.write_json(self.storage.state / "product-research.json", {
            "schemaVersion": 1, "probes": {self.probe_id: {
                "probeId": self.probe_id, "featureId": "team-signup", "status": "observed",
                "verifiers": [{"actorId": "andy-bogard", "decision": "accept"}],
                "configuration": {"result": "observed"}, "issueCandidate": None,
            }},
        })

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_participation_offer_requires_player_acceptance_and_sofia_approval(self):
        reward = offer(self.root, probe_id=self.probe_id, actor_id="andy-bogard", kind="participation")
        self.assertEqual(50, reward["amount"])
        self.assertEqual("offered", reward["status"])
        accepted = respond(self.root, reward_id=reward["rewardId"], actor_id="andy-bogard", decision="accept")
        self.assertEqual("accepted", accepted["status"])
        approved = approve(self.root, reward_id=reward["rewardId"], actor_id="sofia")
        self.assertEqual("approved", approved["status"])
        self.assertEqual(50, snapshot(self.root)["recognition"]["andy-bogard"]["researchXp"])
        with self.assertRaises(ResearchRewardError):
            offer(self.root, probe_id=self.probe_id, actor_id="andy-bogard", kind="participation")
        facts = self.storage.read_jsonl(self.storage.state / "journal" / "facts.jsonl")
        self.assertEqual(["offer", "accept"], [row["payload"]["speechAct"] for row in facts])

    def test_finding_requires_issue_candidate(self):
        with self.assertRaises(ResearchRewardError):
            offer(self.root, probe_id=self.probe_id, actor_id="andy-bogard", kind="finding")
