from pathlib import Path
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.configuration_experiments import (  # noqa: E402
    decide_configuration_exploration, record_configuration_observation,
    request_configuration_exploration,
)
from podplay_sim_club.storage import Storage  # noqa: E402
from podplay_sim_club.staff_consultation import (  # noqa: E402
    StaffConsultationError, propose_rate_cards_plan, respond, snapshot,
    attach_rate_cards_scope_readback, revise_rate_cards_plan,
)


class StaffConsultationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sim-club-consultation-"))

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_named_recipient_can_accept_planning_without_authorizing_action(self):
        plan = propose_rate_cards_plan(self.root, proposer="riley", recipient="morgan")
        self.assertEqual("addressed", plan["status"])
        self.assertEqual(0, plan["remoteWrites"])
        accepted = respond(self.root, plan_id=plan["planId"], actor_id="morgan", decision="accept")
        self.assertEqual("accepted_for_planning_only", accepted["status"])
        self.assertIn("not approval to configure", accepted["acceptMeans"])
        self.assertEqual([plan["planId"]], [item["planId"] for item in snapshot(self.root)["plans"]])

    def test_role_label_cannot_receive_or_respond_to_a_consultation(self):
        with self.assertRaises(StaffConsultationError):
            propose_rate_cards_plan(self.root, proposer="riley", recipient="podplay-admin")
        plan = propose_rate_cards_plan(self.root, proposer="sofia", recipient="morgan")
        with self.assertRaises(StaffConsultationError):
            respond(self.root, plan_id=plan["planId"], actor_id="riley", decision="accept")

    def test_live_scope_evidence_is_attached_without_deciding_the_plan(self):
        plan = propose_rate_cards_plan(self.root, proposer="riley", recipient="morgan")
        request_configuration_exploration(self.root, requester="riley")
        decide_configuration_exploration(self.root, actor_id="podplay-admin", decision="accept")
        observed = record_configuration_observation(
            self.root, actor_id="podplay-admin", booking_engine="LEGACY",
            effective_scope="unknown", schedule="missing", rate_card="missing",
            readiness="missing_rate_card",
        )
        state = Storage(self.root).load_json(Storage(self.root).state / "configuration-experiments.json")
        state["experiments"][observed["experimentId"]]["scopeDiscovery"] = {
            "tenantBaseline": "absent", "areaOverride": "absent", "areaEffective": "LEGACY",
            "podOverride": "absent", "podEffective": "LEGACY",
        }
        Storage(self.root).write_json(Storage(self.root).state / "configuration-experiments.json", state)
        supplemented = attach_rate_cards_scope_readback(self.root, actor_id="riley")
        self.assertEqual("addressed", supplemented["status"])
        self.assertEqual("LEGACY", supplemented["liveScopeReadback"]["podEffective"])
        self.assertEqual("pod", supplemented["codeDerivedFacts"]["narrowestScope"])

    def test_change_request_is_preserved_before_a_revised_plan_can_be_accepted(self):
        plan = propose_rate_cards_plan(self.root, proposer="riley", recipient="morgan")
        with self.assertRaises(StaffConsultationError):
            respond(self.root, plan_id=plan["planId"], actor_id="morgan", decision="request_changes")
        requested = respond(
            self.root, plan_id=plan["planId"], actor_id="morgan", decision="request_changes",
            note="Please name the approved source for Rate Card pricing values.",
        )
        self.assertEqual("changes_requested", requested["status"])
        revised = revise_rate_cards_plan(
            self.root, proposer="riley", plan_id=plan["planId"],
            note="Pricing values will be supplied by an authorized product administrator; no values will be invented here.",
        )
        self.assertEqual("addressed_revised", revised["status"])
        self.assertEqual("request_changes", revised["history"][0]["value"])
        accepted = respond(self.root, plan_id=plan["planId"], actor_id="morgan", decision="accept")
        self.assertEqual("accepted_for_planning_only", accepted["status"])


if __name__ == "__main__":
    unittest.main()
