from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.browser_handoff import _HandoffState  # noqa: E402


class BrowserHandoffTests(unittest.TestCase):
    def test_each_supported_field_is_one_time_and_actor_bound(self):
        state = _HandoffState(
            actor_id="mai",
            email="private@example.test",
            password="private-password",
            allowed_origin="https://preview.example.test",
        )
        self.assertIsNone(state.claim("king-captain", "email"))
        self.assertEqual("private@example.test", state.claim("mai", "email"))
        self.assertIsNone(state.claim("mai", "email"))
        self.assertFalse(state.complete)
        self.assertEqual("private-password", state.claim("mai", "password"))
        self.assertTrue(state.complete)

    def test_unsupported_field_is_refused(self):
        state = _HandoffState(
            actor_id="mai",
            email="private@example.test",
            password="private-password",
            allowed_origin="https://preview.example.test",
        )
        self.assertIsNone(state.claim("mai", "firebase_uid"))
        self.assertFalse(state.complete)
