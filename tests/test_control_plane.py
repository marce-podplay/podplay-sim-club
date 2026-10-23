from pathlib import Path
import shutil
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.control_plane import (  # noqa: E402
    ControlPlaneError, acknowledge, acknowledge_awareness, acknowledge_handoff, acknowledge_release_notice, announce,
    authorize_rate_cards_planning, release_notice, research_handoff, season_notice, tick,
    request_rate_cards_pricing_basis,
    propose_rate_cards_pricing_basis,
    respond_to_rate_cards_pricing_basis,
    preauthorize_rate_cards_configuration_run,
    offer_rate_cards_future_pilot_interest, respond_to_rate_cards_future_pilot_interest,
)
from podplay_sim_club.storage import Storage  # noqa: E402


class ControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sim-club-control-"))

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_transition_is_gated_and_awareness_is_not_acceptance(self):
        notice = season_notice(self.root, "pr-7488")
        notice_id = notice["noticeId"]
        with self.assertRaises(ControlPlaneError):
            announce(self.root, notice_id, "riley")
        with self.assertRaises(ControlPlaneError):
            acknowledge(self.root, notice_id, "andy-bogard")
        acknowledge(self.root, notice_id, "riley")
        announced = announce(self.root, notice_id, "riley")
        self.assertEqual("players_informed", announced["status"])
        with self.assertRaises(ControlPlaneError):
            acknowledge_awareness(self.root, notice_id, "sofia")
        acknowledge_awareness(self.root, notice_id, "andy-bogard")
        complete = acknowledge_awareness(self.root, notice_id, "terry-bogard")
        self.assertEqual("players_acknowledged", complete["status"])
        storage = Storage(self.root)
        messages = storage.read_jsonl(storage.channel_path("club"))
        self.assertEqual(3, len(messages))
        self.assertTrue(all(row["remoteWrites"] == 0 for row in messages))
        facts = storage.read_jsonl(storage.state / "journal" / "facts.jsonl")
        self.assertEqual(["inform", "acknowledge", "acknowledge"], [row["payload"]["speechAct"] for row in facts])
        self.assertEqual({"notices", "handoffs", "releaseNotices", "authorizations", "runAuthorizations", "configurationRequests", "playerPilotInterest", "priceVerificationRequests", "remoteWrites", "autonomousMessages"}, set(tick(self.root)))

    def test_notice_rejects_non_safe_season_values(self):
        with self.assertRaises(ControlPlaneError):
            season_notice(self.root, "https://private.example/pr-7488")

    def test_release_notice_is_staff_awareness_not_configuration_or_authority(self):
        notice = release_notice(self.root, "rate-cards-unified-pricing", "PP-7827")
        self.assertEqual("addressed_to_staff", notice["status"])
        self.assertEqual("acknowledge_or_pass_authorized_read_only_probe", notice["nextDecision"])
        control = Storage(self.root).read_jsonl(Storage(self.root).channel_path("control-plane"))
        self.assertEqual("release_notice", control[0]["kind"])
        self.assertEqual(["sofia", "riley"], control[0]["to"])
        self.assertNotIn("url", control[0])

    def test_release_notice_receipt_needs_each_named_staff_member(self):
        notice = release_notice(self.root, "rate-cards-unified-pricing", "PP-7827")
        with self.assertRaises(ControlPlaneError):
            acknowledge_release_notice(self.root, notice["noticeId"], "andy-bogard")
        partial = acknowledge_release_notice(self.root, notice["noticeId"], "sofia")
        self.assertEqual("addressed_to_staff", partial["status"])
        complete = acknowledge_release_notice(self.root, notice["noticeId"], "riley")
        self.assertEqual("staff_acknowledged", complete["status"])
        self.assertEqual(["riley", "sofia"], complete["acknowledgedBy"])
        control = Storage(self.root).read_jsonl(Storage(self.root).channel_path("control-plane"))
        self.assertTrue(all(row["remoteWrites"] == 0 for row in control))

    def test_lead_authorization_allows_planning_not_preview_mutation(self):
        authorization = authorize_rate_cards_planning(self.root)
        self.assertEqual("addressed_to_staff", authorization["status"])
        self.assertIn("prepare a pod-scoped technical change packet", authorization["allowed"])
        self.assertIn("enable Unified Pricing", authorization["forbidden"])
        self.assertEqual("authorized_product_admin_supplies_pricing_basis_or_passes", authorization["nextDecision"])
        self.assertEqual(0, authorization["remoteWrites"])

    def test_pricing_basis_request_requires_the_technical_packet_and_never_writes(self):
        with self.assertRaises(ControlPlaneError):
            request_rate_cards_pricing_basis(self.root)
        storage = Storage(self.root)
        storage.write_json(storage.state / "configuration-experiments.json", {
            "schemaVersion": 1,
            "experiments": {"experiment:rate-cards-unified-pricing:pp-7827": {
                "technicalChangePacket": {"status": "awaiting_authorized_pricing_basis"},
            }},
        })
        request = request_rate_cards_pricing_basis(self.root)
        self.assertEqual("awaiting_external_product_response", request["status"])
        self.assertIn("supply_approved_pricing_basis", request["responseOptions"])
        self.assertIn("enable Unified Pricing", request["doesNotAuthorize"])
        self.assertEqual("POD only; the selected target is resolved by the guarded adapter and is never supplied in the request", request["pricingBasisContract"]["assignment"])
        self.assertIn("COURT with explicit splitting choice", request["pricingBasisContract"]["pricingConfig"]["billing"])
        self.assertEqual(0, request["remoteWrites"])

    def test_lead_pricing_proposal_is_reviewable_and_not_a_preview_write(self):
        storage = Storage(self.root)
        storage.write_json(storage.state / "configuration-experiments.json", {
            "schemaVersion": 1,
            "experiments": {"experiment:rate-cards-unified-pricing:pp-7827": {
                "technicalChangePacket": {"status": "awaiting_authorized_pricing_basis"},
            }},
        })
        request_rate_cards_pricing_basis(self.root)
        proposal = propose_rate_cards_pricing_basis(self.root)
        basis = proposal["proposedBasis"]
        self.assertEqual("proposal_ready_for_external_review", proposal["status"])
        self.assertEqual("USD", basis["currency"])
        self.assertEqual(30, basis["pricingConfig"]["priceDurationMinutes"])
        cents = [row["reservationPriceCents"] for package in basis["pricingConfig"]["pricePackages"] for row in package["pricesByPlayerCount"]]
        self.assertTrue(all(1000 <= value <= 5000 for value in cents))
        self.assertEqual("Sunday-first: 0 Sunday through 6 Saturday; times are local to the selected pod", basis["scheduleProposal"]["weekdayIndexing"])
        self.assertTrue(basis["notAProductPayload"])
        self.assertEqual(0, proposal["remoteWrites"])

    def test_external_pricing_acceptance_does_not_enable_preview(self):
        storage = Storage(self.root)
        storage.write_json(storage.state / "configuration-experiments.json", {
            "schemaVersion": 1,
            "experiments": {"experiment:rate-cards-unified-pricing:pp-7827": {
                "technicalChangePacket": {"status": "awaiting_authorized_pricing_basis"},
            }},
        })
        request_rate_cards_pricing_basis(self.root)
        propose_rate_cards_pricing_basis(self.root)
        with self.assertRaises(ControlPlaneError):
            respond_to_rate_cards_pricing_basis(self.root, actor_id="riley", decision="accept")
        accepted = respond_to_rate_cards_pricing_basis(
            self.root, actor_id="external-product-authority", decision="accept",
            note="Approved as a pilot basis; configuration still needs a separate proposal.",
        )
        self.assertEqual("pricing_basis_accepted_awaiting_configuration_proposal", accepted["status"])
        self.assertEqual("accept", accepted["externalResponse"]["decision"])
        self.assertIn("configuration_proposal", accepted["nextDecision"])
        self.assertEqual(0, accepted["remoteWrites"])

    def test_three_yes_preauthorization_is_basis_bound_and_still_needs_a_dry_run(self):
        storage = Storage(self.root)
        storage.write_json(storage.state / "configuration-experiments.json", {
            "schemaVersion": 1,
            "experiments": {"experiment:rate-cards-unified-pricing:pp-7827": {
                "technicalChangePacket": {"status": "awaiting_authorized_pricing_basis"},
            }},
        })
        request_rate_cards_pricing_basis(self.root)
        propose_rate_cards_pricing_basis(self.root)
        respond_to_rate_cards_pricing_basis(self.root, actor_id="external-product-authority", decision="accept")
        with self.assertRaises(ControlPlaneError):
            preauthorize_rate_cards_configuration_run(
                self.root, pricing_basis_confirmation="yes", configuration_proposal_confirmation="yes", preview_write_confirmation="y",
            )
        authorization = preauthorize_rate_cards_configuration_run(
            self.root, pricing_basis_confirmation="yes", configuration_proposal_confirmation="yes", preview_write_confirmation="yes",
        )
        self.assertEqual("preauthorized_for_manifest_bound_execution", authorization["status"])
        self.assertIn("render a dry-run change manifest", authorization["requiredBeforeAnyWrite"])
        self.assertIn("a different target", authorization["doesNotAuthorize"])
        self.assertEqual(0, authorization["remoteWrites"])

    def test_players_can_opt_into_future_contact_without_authorizing_configuration(self):
        storage = Storage(self.root)
        storage.write_json(storage.state / "configuration-experiments.json", {
            "schemaVersion": 1, "experiments": {"experiment:rate-cards-unified-pricing:pp-7827": {
                "dryRunManifest": {"status": "ready_for_exact_origin_confirmation"},
            }},
        })
        offer = offer_rate_cards_future_pilot_interest(self.root, actor_id="riley")
        self.assertEqual("awaiting_player_responses", offer["status"])
        with self.assertRaises(ControlPlaneError):
            respond_to_rate_cards_future_pilot_interest(self.root, offer_id=offer["offerId"], actor_id="riley", decision="accept")
        responded = respond_to_rate_cards_future_pilot_interest(self.root, offer_id=offer["offerId"], actor_id="andy-bogard", decision="accept")
        self.assertEqual("accept", responded["responses"]["andy-bogard"]["decision"])
        self.assertEqual("future_research_contact_only", responded["meaning"])
        self.assertEqual(0, responded["remoteWrites"])
        complete = respond_to_rate_cards_future_pilot_interest(self.root, offer_id=offer["offerId"], actor_id="terry-bogard", decision="pass")
        self.assertEqual("player_interest_responses_complete", complete["status"])
        self.assertEqual("pass", complete["responses"]["terry-bogard"]["decision"])

    def test_observed_research_can_be_handed_to_staff_without_authorizing_a_journey(self):
        probe_id = "probe:feature:sample"
        self.storage = Storage(self.root)
        self.storage.write_json(self.storage.state / "product-research.json", {
            "schemaVersion": 1, "probes": {probe_id: {
                "probeId": probe_id, "featureId": "team-signup", "status": "observed",
                "configuration": {"result": "observed", "summary": {
                    "configuredAreaPresent": True, "configuredPodPresent": True,
                    "bookingQuotaShapePresent": True,
                }, "release": {"status": "unknown"}},
            }},
        })
        handoff = research_handoff(self.root, probe_id)
        self.assertEqual("addressed_to_staff", handoff["status"])
        self.assertEqual("request_or_pass_player_owned_ui_journey", handoff["nextDecision"])
        acknowledge_handoff(self.root, handoff["handoffId"], "sofia")
        complete = acknowledge_handoff(self.root, handoff["handoffId"], "riley")
        self.assertEqual("staff_acknowledged", complete["status"])
        control = self.storage.read_jsonl(self.storage.channel_path("control-plane"))
        self.assertTrue(all(row["remoteWrites"] == 0 for row in control))
