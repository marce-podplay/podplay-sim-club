from pathlib import Path
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.configuration_experiments import (  # noqa: E402
    decide_configuration_exploration, propose_configuration_change,
    record_configuration_observation, request_configuration_exploration,
)
from podplay_sim_club.research_portfolio import (  # noqa: E402
    ResearchPortfolioError, portfolio_snapshot, sync_rate_cards_experiment,
)


class ResearchPortfolioTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sim-club-portfolio-"))

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_release_maps_to_one_compact_experiment_with_dependency_context(self):
        with self.assertRaises(ResearchPortfolioError):
            sync_rate_cards_experiment(self.root)
        request_configuration_exploration(self.root, requester="riley")
        entry = sync_rate_cards_experiment(self.root)
        self.assertEqual("PP-7827", entry["releaseReference"])
        self.assertEqual("configuration_intensive", entry["template"])
        self.assertEqual(["unified-pricing"], entry["dependencyContexts"])
        self.assertEqual("active", entry["portfolioStatus"])
        self.assertEqual(3, entry["uncertainty"]["unresolvedClaimCount"])
        snapshot = portfolio_snapshot(self.root)
        self.assertFalse(snapshot["compactionPolicy"]["availableNow"])

    def test_change_proposal_is_waiting_for_an_admin_decision(self):
        request_configuration_exploration(self.root, requester="riley")
        decide_configuration_exploration(self.root, actor_id="podplay-admin", decision="accept")
        record_configuration_observation(
            self.root, actor_id="podplay-admin", booking_engine="LEGACY",
            effective_scope="unknown", schedule="missing", rate_card="missing",
            readiness="missing_rate_card",
        )
        propose_configuration_change(self.root, actor_id="riley")
        self.assertEqual("awaiting_admin_configuration_decision", sync_rate_cards_experiment(self.root)["portfolioStatus"])
