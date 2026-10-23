import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podplay_sim_club.ambient_health import check
from podplay_sim_club.credentials import PreviewCredentials


class AmbientHealthTests(unittest.TestCase):
    def _credentials(self):
        return PreviewCredentials(
            preview_origin="https://podify-pr-1-staging-main-service-test.a.run.app",
            allowed_preview_origins=("https://podify-pr-1-staging-main-service-test.a.run.app",),
            admin_email="admin@example.test", admin_password="secret", firebase_api_key="key",
        )

    def test_baseline_then_transition_is_safe_observation(self):
        with tempfile.TemporaryDirectory() as directory, patch("podplay_sim_club.ambient_health.PreviewCredentials.load", return_value=self._credentials()):
            root = Path(directory)
            first = check(root, fetch=lambda origin: {"httpStatus": 200, "bodyBytes": 100, "html": "<html><body>healthy public login shell with enough markup to be credible</body></html>"})
            second = check(root, fetch=lambda origin: {"networkError": True})
            self.assertEqual("reachable", first["status"])
            self.assertFalse(first["changed"])
            self.assertEqual("unreachable", second["status"])
            self.assertTrue(second["changed"])
            journal = (root / "state/channels/staff-planning.jsonl").read_text()
            self.assertIn("incident observation", journal)
            self.assertNotIn("regression conclusion", journal)
