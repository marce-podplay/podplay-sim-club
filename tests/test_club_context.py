import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.club_context import ClubContextError, load_club_context
from podplay_sim_club.identity_registry import IdentityRegistry
from podplay_sim_club.players import actor_auth_cache


class ClubContextTests(unittest.TestCase):
    def _write_context(self, root: Path, **overrides):
        payload = {
            "schemaVersion": 1,
            "key": "club-lab",
            "displayName": "The Club Lab",
            "staffActorIds": ["riley"],
            "playerActorIds": ["sofia", "andy-bogard"],
        }
        payload.update(overrides)
        (root / "seed").mkdir()
        (root / "seed" / "club-context.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_context_keeps_staff_and_players_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_context(root)
            context = load_club_context(root)
            self.assertEqual("club-lab", context.key)
            self.assertEqual(("riley",), context.staff_actor_ids)
            self.assertIn("sofia", context.player_actor_ids)

    def test_context_rejects_a_staff_player_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_context(root, staffActorIds=["riley", "sofia"])
            with self.assertRaises(ClubContextError):
                load_club_context(root)

    def test_product_ids_are_not_borrowed_between_club_contexts(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = IdentityRegistry(Path(directory) / "preview-actors.json")
            registry.ensure(("sofia",))
            registry.record_verified(
                "sofia", "firebase-sofia", "legacy-user", "https://preview.invalid",
                "tenant-legacy", "club-lab-legacy",
            )
            registry.record_verified(
                "sofia", "firebase-sofia", "unified-user", "https://preview.invalid",
                "tenant-unified", "club-lab-unified",
            )
            self.assertEqual("legacy-user", registry.identities_for_context("club-lab-legacy")["sofia"].podplay_user_id)
            self.assertEqual("unified-user", registry.identities_for_context("club-lab-unified")["sofia"].podplay_user_id)
            self.assertEqual("", registry.identities_for_context("club-lab-next")["sofia"].podplay_user_id)

    def test_future_context_has_a_private_token_cache(self):
        root = Path("/tmp/club-context-cache")
        self.assertEqual(root / "secrets" / "actor-auth" / "club-lab-unified" / "sofia.json", actor_auth_cache(root, "sofia", "club-lab-unified"))


if __name__ == "__main__":
    unittest.main()
