from pathlib import Path
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.configuration_experiments import (  # noqa: E402
    ConfigurationExperimentError, add_diary_note, decide_configuration_exploration,
    _activation_summary, experiment_snapshot, issue_technical_handoff, record_configuration_observation, request_configuration_exploration,
    refresh_uncertainty,
    propose_configuration_change,
    prepare_rate_cards_change_packet,
    prepare_rate_cards_configuration_proposal,
    render_rate_cards_dry_run_manifest,
    _scope_path_summary,
)
from podplay_sim_club.storage import Storage  # noqa: E402
from podplay_sim_club.control_plane import authorize_rate_cards_planning  # noqa: E402
from podplay_sim_club.staff_consultation import propose_rate_cards_plan, respond  # noqa: E402


class ConfigurationExperimentTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sim-club-config-experiment-"))

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_configuration_is_gated_and_evidence_is_safe(self):
        requested = request_configuration_exploration(self.root, requester="riley")
        self.assertEqual("configuration_exploration_requested", requested["phase"])
        unknown = {claim["subject"]: claim for claim in requested["uncertainty"]["claims"]}
        self.assertEqual("UNKNOWN", unknown["feature.bookingEngine"]["value"])
        self.assertEqual("do_not_prepare_player_pricing_journey", unknown["feature.bookingEngine"]["stopRule"])
        self.assertEqual(7, requested["uncertainty"]["policy"]["maxUnknownAgeDays"])
        handoff = issue_technical_handoff(self.root, actor_id="riley")
        self.assertTrue(handoff["technicalHandoff"]["readOnly"])
        with self.assertRaises(ConfigurationExperimentError):
            record_configuration_observation(self.root, actor_id="podplay-admin", booking_engine="LEGACY", effective_scope="pod", schedule="missing", rate_card="missing", readiness="missing_rate_card")
        decide_configuration_exploration(self.root, actor_id="podplay-admin", decision="accept")
        observed = record_configuration_observation(self.root, actor_id="podplay-admin", booking_engine="UNIFIED", effective_scope="pod", schedule="active", rate_card="effective", readiness="ready")
        self.assertEqual("configuration_observed", observed["phase"])
        self.assertEqual("unified_enabled_and_ready", observed["configuration"]["outcome"])
        self.assertEqual("unknown", next(claim for claim in observed["uncertainty"]["claims"] if claim["subject"] == "declaredPricingPrediction")["value"])
        add_diary_note(self.root, actor_id="riley", note="Admin read-back is recorded; do not invite players until a separate prediction phase is approved.")
        rendered = (self.root / "state" / "experiments" / "experiment:rate-cards-unified-pricing:pp-7827" / "evidence.md").read_text()
        self.assertIn("unified_enabled_and_ready", rendered)
        self.assertIn("Uncertainty ledger", rendered)

        refreshed = refresh_uncertainty(self.root)
        self.assertEqual("new_season_reobservation", next(claim for claim in refreshed["uncertainty"]["claims"] if claim["subject"] == "feature.bookingEngine")["nextDiscriminatingObservation"])
        self.assertNotIn("http", rendered)
        messages = Storage(self.root).read_jsonl(Storage(self.root).channel_path("control-plane"))
        self.assertTrue(all(row["remoteWrites"] == 0 for row in messages))

    def test_unready_configuration_needs_a_separate_change_proposal(self):
        request_configuration_exploration(self.root, requester="riley")
        decide_configuration_exploration(self.root, actor_id="podplay-admin", decision="accept")
        record_configuration_observation(
            self.root, actor_id="podplay-admin", booking_engine="LEGACY",
            effective_scope="unknown", schedule="missing", rate_card="missing",
            readiness="missing_rate_card",
        )
        proposal = propose_configuration_change(self.root, actor_id="riley")
        self.assertEqual("change_proposal_needed", proposal["phase"])
        self.assertEqual("pod", proposal["changeProposal"]["defaultScopeCandidate"])
        self.assertEqual("unverified", proposal["changeProposal"]["scopeStatus"])
        self.assertEqual(0, proposal["remoteWrites"])

    def test_change_packet_requires_named_acceptance_and_bounded_authorization(self):
        request_configuration_exploration(self.root, requester="riley")
        decide_configuration_exploration(self.root, actor_id="podplay-admin", decision="accept")
        record_configuration_observation(
            self.root, actor_id="podplay-admin", booking_engine="LEGACY",
            effective_scope="unknown", schedule="missing", rate_card="missing",
            readiness="missing_rate_card",
        )
        proposal = propose_configuration_change(self.root, actor_id="riley")
        proposal["scopeDiscovery"] = {
            "tenantBaseline": "absent", "areaOverride": "absent", "areaEffective": "LEGACY",
            "podOverride": "absent", "podEffective": "LEGACY",
        }
        state_path = self.root / "state" / "configuration-experiments.json"
        Storage(self.root).write_json(state_path, {"schemaVersion": 1, "experiments": {proposal["experimentId"]: proposal}})
        with self.assertRaises(ConfigurationExperimentError):
            prepare_rate_cards_change_packet(self.root, actor_id="riley")
        plan = propose_rate_cards_plan(self.root, proposer="riley", recipient="morgan-vale")
        respond(self.root, plan_id=plan["planId"], actor_id="morgan-vale", decision="accept")
        authorize_rate_cards_planning(self.root)
        prepared = prepare_rate_cards_change_packet(self.root, actor_id="riley")
        packet = prepared["technicalChangePacket"]
        self.assertEqual("awaiting_authorized_pricing_basis", packet["status"])
        self.assertEqual("one selected pod", packet["target"])
        self.assertEqual(0, packet["remoteWrites"])
        self.assertNotIn("http", str(packet))

    def test_configuration_proposal_requires_the_manifest_bound_run_authorization(self):
        request_configuration_exploration(self.root, requester="riley")
        decide_configuration_exploration(self.root, actor_id="podplay-admin", decision="accept")
        record_configuration_observation(self.root, actor_id="podplay-admin", booking_engine="LEGACY", effective_scope="unknown", schedule="missing", rate_card="missing", readiness="missing_rate_card")
        proposal = propose_configuration_change(self.root, actor_id="riley")
        with self.assertRaises(ConfigurationExperimentError):
            prepare_rate_cards_configuration_proposal(self.root, actor_id="riley")
        storage = Storage(self.root)
        storage.write_json(storage.state / "control-plane.json", {
            "schemaVersion": 1, "notices": {}, "handoffs": {}, "releaseNotices": {}, "authorizations": {}, "configurationRequests": {},
            "runAuthorizations": {"control:rate-cards-configuration-run:pp-7827": {"status": "preauthorized_for_manifest_bound_execution"}},
        })
        prepared = prepare_rate_cards_configuration_proposal(self.root, actor_id="riley")
        self.assertEqual("prepared_pending_fresh_readback_and_dry_run", prepared["configurationProposal"]["status"])
        self.assertEqual(0, prepared["configurationProposal"]["remoteWrites"])
        manifest = render_rate_cards_dry_run_manifest(self.root, actor_id="riley")
        self.assertEqual("ready_for_exact_origin_confirmation", manifest["dryRunManifest"]["status"])
        self.assertTrue(all(not row["write"] for row in manifest["dryRunManifest"]["plannedOperations"] if "write" in row))

    def test_pass_and_unknown_are_not_silent_activation(self):
        request_configuration_exploration(self.root, requester="sofia")
        passed = decide_configuration_exploration(self.root, actor_id="podplay-admin", decision="pass")
        self.assertEqual("configuration_exploration_passed", passed["phase"])
        self.assertEqual("rate-cards-unified-pricing", experiment_snapshot(self.root)["workflow"]["featureId"])

    def test_diary_rejects_private_reference(self):
        request_configuration_exploration(self.root, requester="riley")
        with self.assertRaises(Exception):
            add_diary_note(self.root, actor_id="riley", note="See https://private.example/for details")

    def test_activation_readback_reduces_only_to_safe_enums(self):
        self.assertEqual(("active", "effective", "ready"), _activation_summary({"status": "EXISTING", "rateCard": {"id": "raw"}}))
        self.assertEqual(("missing", "missing", "missing_rate_card"), _activation_summary({"status": "MISSING_RATE_CARD"}))
        self.assertEqual(("future_only", "effective", "blocked_schedule"), _activation_summary({"status": "BLOCKED_SCHEDULE", "versions": [{"status": "FUTURE"}]}))

    def test_scope_path_reduces_tenant_area_and_pod_to_safe_booking_engine_enums(self):
        summary = _scope_path_summary(
            [{"id": "feature.bookingEngine", "value": "LEGACY", "secret": "never retained"}],
            {"venueSettingsOverrides": {"items": []}, "venueSettingsInherited": {"items": [{"id": "feature.bookingEngine", "value": "LEGACY"}]}},
            {"venueSettingsOverrides": {"items": [{"id": "feature.bookingEngine", "value": "UNIFIED"}]}, "venueSettingsInherited": {"items": [{"id": "feature.bookingEngine", "value": "UNIFIED"}]}},
        )
        self.assertEqual({"tenantBaseline": "LEGACY", "areaOverride": "absent", "areaEffective": "LEGACY", "podOverride": "UNIFIED", "podEffective": "UNIFIED"}, summary)
