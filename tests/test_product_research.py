from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.product_research import (  # noqa: E402
    ProductResearchError,
    acknowledge_verification,
    create_verification_request,
    discover_team_signup_context_with_client,
    issue_candidate,
    run_response_tick,
)
from podplay_sim_club.storage import Storage  # noqa: E402


class FakeClient:
    def __init__(self):
        self.paths = []

    def get(self, path):
        self.paths.append(path)
        if path == "/apis/v2/tenants/current/settings":
            return {"items": [
                {"id": "feature.areaLiabilityWaiver", "value": False},
                {"id": "feature.liabilityWaiver", "value": False},
                {"id": "feature.requireAreaOnSignup", "value": False},
                {"id": "feature.requirePhoneNumberOnSignup", "value": True},
                {"id": "stripe.testModeEnabled", "value": True},
                {"id": "private.setting", "value": "must-not-persist"},
            ]}
        if path == "/apis/v2/areas":
            return {"items": [{"id": "raw-area-id"}]}
        if path == "/apis/v2/areas/raw-area-id/pods":
            return {"items": [{"id": "raw-pod-id"}]}
        if path == "/apis/v2/users/current/settings/booking/raw-area-id/pods":
            return {"items": [{"pod": {"id": "raw-pod-id"}, "quotas": {"maxDurationHours": 2}}]}
        raise AssertionError(path)


class ProductResearchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sim-club-research-"))
        (self.root / "characters").mkdir()
        (self.root / "seed").mkdir()
        (self.root / "characters" / "roster.json").write_text(json.dumps({"characters": [
            {"actorId": "sofia", "role": "club_owner"},
            {"actorId": "riley", "role": "club_staff"},
            {"actorId": "andy-bogard", "role": "player"},
            {"actorId": "terry-bogard", "role": "player"},
        ]}), encoding="utf-8")
        (self.root / "seed" / "preview-club.json").write_text(json.dumps({
            "schemaVersion": 1, "location": {"areaId": "raw-area-id", "podId": "raw-pod-id"},
        }), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_named_acceptance_gates_a_sanitized_get_only_readback(self):
        request = create_verification_request(
            self.root, feature_id="team-signup", requester="sofia",
            verifier_ids=["andy-bogard", "terry-bogard"],
            requested_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
        )
        with self.assertRaises(ProductResearchError):
            discover_team_signup_context_with_client(
                self.root, probe_id=request["probeId"], client=FakeClient(),
                target_fingerprint="pr-1:team-signup-contract-v1", observed_at=datetime.now(timezone.utc),
            )
        acknowledge_verification(self.root, probe_id=request["probeId"], actor_id="andy-bogard", decision="accept")
        ready = acknowledge_verification(self.root, probe_id=request["probeId"], actor_id="terry-bogard", decision="accept")
        self.assertEqual("ready_for_readback", ready["status"])
        observed = discover_team_signup_context_with_client(
            self.root, probe_id=request["probeId"], client=FakeClient(),
            target_fingerprint="pr-1:team-signup-contract-v1", observed_at=datetime(2026, 10, 8, 1, tzinfo=timezone.utc),
        )
        self.assertEqual("observed", observed["status"])
        self.assertTrue(observed["configuration"]["summary"]["phoneRequired"])
        self.assertEqual("unknown", observed["configuration"]["release"]["status"])
        serialized = json.dumps(observed)
        self.assertNotIn("raw-area-id", serialized)
        self.assertNotIn("raw-pod-id", serialized)
        self.assertNotIn("private.setting", serialized)

    def test_pass_ends_the_request_and_issue_is_not_a_regression(self):
        request = create_verification_request(
            self.root, feature_id="team-signup", requester="riley", verifier_ids=["andy-bogard"]
        )
        passed = acknowledge_verification(self.root, probe_id=request["probeId"], actor_id="andy-bogard", decision="pass")
        self.assertEqual("passed", passed["status"])
        with self.assertRaises(ProductResearchError):
            issue_candidate(self.root, probe_id=request["probeId"], reason="one failed journey")

    def test_named_response_tick_delivers_then_accepts_only_the_read_only_probe(self):
        request = create_verification_request(
            self.root, feature_id="team-signup", requester="sofia",
            verifier_ids=["andy-bogard", "terry-bogard"],
        )
        outcome = run_response_tick(self.root, probe_id=request["probeId"], turns=3)
        self.assertEqual("ready_for_readback", outcome["probe"]["status"])
        self.assertEqual(["delivered_request", "verifier_response", "verifier_response"], [turn["action"] for turn in outcome["turns"]])
        storage = Storage(self.root)
        messages = storage.read_jsonl(storage.channel_path("club"))
        self.assertTrue(all(row["remoteWrites"] == 0 for row in messages))
        facts = storage.read_jsonl(storage.state / "journal" / "facts.jsonl")
        self.assertEqual(["request", "acknowledge", "acknowledge"], [row["payload"]["speechAct"] for row in facts])
