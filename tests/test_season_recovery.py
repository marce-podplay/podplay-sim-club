from pathlib import Path
import shutil
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.season_recovery import (  # noqa: E402
    SeasonRecoveryError,
    _summary,
    approve,
    request,
    snapshot,
)
from podplay_sim_club.storage import Storage  # noqa: E402


class SeasonRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sim-club-recovery-"))
        self.storage = Storage(self.root)
        self.storage.write_json(self.storage.state / "season-recovery.json", {
            "schemaVersion": 2,
            "latestPlan": {
                "targetFingerprint": "pr-7488:season-recovery-v1",
                "profileFingerprint": "sha256:prior",
                "reconciliationRequired": True,
                "actors": [
                    {"actorId": "andy-bogard", "status": "missing"},
                    {"actorId": "riley", "status": "exists"},
                ],
            },
            "request": None,
        })

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_riley_can_request_but_only_lead_can_approve(self):
        recovery = request(self.root, "riley")
        self.assertEqual("requested", recovery["status"])
        self.assertEqual(["andy-bogard"], recovery["actorIds"])
        with self.assertRaises(SeasonRecoveryError):
            approve(self.root, "riley")
        approved = approve(self.root, "lead")
        self.assertEqual("approved", approved["status"])
        self.assertEqual("lead", approved["approvedBy"])
        self.assertEqual("approved", snapshot(self.root)["request"]["status"])

    def test_riley_cannot_request_when_plan_needs_no_reconciliation(self):
        self.storage.write_json(self.storage.state / "season-recovery.json", {
            "schemaVersion": 2,
            "latestPlan": {"targetFingerprint": "pr-7488:season-recovery-v1", "profileFingerprint": "sha256:prior", "reconciliationRequired": False, "actors": []},
            "request": None,
        })
        with self.assertRaises(SeasonRecoveryError):
            request(self.root, "riley")

    def test_all_previously_present_profiles_missing_marks_a_new_season_suspected(self):
        raw = {
            "tenantId": "private-tenant", "areaId": "private-area", "podId": "private-pod",
            "prerequisitesOk": True, "staffFixture": {"productRoles": []},
            "actors": {
                "andy-bogard": {"status": "missing", "podplayUserId": ""},
                "riley": {"status": "missing", "podplayUserId": ""},
            },
        }
        prior = {
            "profileFingerprint": "sha256:old",
            "actors": [
                {"actorId": "andy-bogard", "status": "exists"},
                {"actorId": "riley", "status": "exists"},
            ],
        }
        summary = _summary(raw, 7488, prior)
        self.assertEqual("new_preview_season_suspected", summary["seasonStatus"]["kind"])
        self.assertTrue(summary["reconciliationRequired"])
        self.assertFalse(summary["bootstrap"]["createsFirebaseIdentity"])
