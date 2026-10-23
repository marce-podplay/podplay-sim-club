from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.continuity import (  # noqa: E402
    ContinuityError,
    export_bundle,
    import_bundle,
    inspect_bundle,
)
from podplay_sim_club.facts import append_fact, make_fact  # noqa: E402
from podplay_sim_club.storage import Storage  # noqa: E402


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.source = self._root()
        self.destination = self._root()
        self.bundle = self.source / "community-portable.json"

    def tearDown(self):
        shutil.rmtree(self.source)
        shutil.rmtree(self.destination)

    def _root(self):
        root = Path(tempfile.mkdtemp(prefix="sim-club-continuity-"))
        (root / "characters").mkdir()
        (root / "characters" / "roster.json").write_text(json.dumps({
            "schemaVersion": 4,
            "characters": [
                {"actorId": "sofia", "role": "club_owner"},
                {"actorId": "andy-bogard", "role": "player"},
            ],
        }), encoding="utf-8")
        return root

    def test_exports_only_local_community_history_and_imports_idempotently(self):
        at = datetime(2026, 10, 8, tzinfo=timezone.utc)
        portable = make_fact(
            fact_type="communication.message", natural_key="hello", subject_ref="conversation:hello",
            correlation_id="conversation:hello", source_kind="club_message", actor_id="sofia",
            occurred_at=at, payload={"messageId": "message:hello", "recipientActorIds": ["andy-bogard"], "speechAct": "request"},
        )
        preview = make_fact(
            fact_type="event.booked", natural_key="preview", subject_ref="occurrence:one",
            correlation_id="journey:one", source_kind="preview_readback", actor_id="lead",
            occurred_at=at, payload={"productEventRef": "EVT-SAFE-CODE"},
        )
        append_fact(self.source, portable)
        append_fact(self.source, preview)
        Storage(self.source).write_json(self.source / "state" / "social-engine.json", {
            "schemaVersion": 1,
            "motives": {"andy-bogard": {"desireToPlay": 72, "curiosity": 20, "frustration": 5, "competition": 40}},
        })

        summary = export_bundle(self.source, self.bundle)
        self.assertEqual(1, summary["factCount"])
        self.assertFalse(summary["previewStateIncluded"])
        self.assertEqual(summary, inspect_bundle(self.bundle))

        imported = import_bundle(self.destination, self.bundle, replace_progression=True)
        self.assertEqual(1, imported["factsAppended"])
        self.assertTrue(imported["progressionApplied"])
        self.assertFalse(imported["previewStateRestored"])
        repeated = import_bundle(self.destination, self.bundle)
        self.assertEqual(0, repeated["factsAppended"])
        state = Storage(self.destination).load_json(self.destination / "state" / "social-engine.json")
        self.assertEqual(72, state["motives"]["andy-bogard"]["desireToPlay"])

    def test_rejects_a_bundle_for_a_different_roster(self):
        export_bundle(self.source, self.bundle)
        roster = json.loads((self.destination / "characters" / "roster.json").read_text())
        roster["characters"].append({"actorId": "riley", "role": "club_staff"})
        (self.destination / "characters" / "roster.json").write_text(json.dumps(roster), encoding="utf-8")
        with self.assertRaises(ContinuityError):
            import_bundle(self.destination, self.bundle)
