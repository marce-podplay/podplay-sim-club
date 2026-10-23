from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.browser_booking_observation import recover_pair_booking, reconcile, reconcile_staff_check_in  # noqa: E402
from podplay_sim_club.facts import load_facts  # noqa: E402


class BrowserBookingObservationTests(unittest.TestCase):
    def test_recovers_new_visible_pair_booking_without_rewriting_old_agreement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            state.mkdir()
            (state / "booking-intents.json").write_text(json.dumps({
                "schemaVersion": 1,
                "intents": {"intent:training:old": {
                    "id": "intent:training:old", "journey": "customer_training",
                    "bookerActorId": "andy", "participants": ["andy", "terry"], "status": "missed_session",
                }},
            }), encoding="utf-8")
            result = recover_pair_booking(
                root, source_intent_id="intent:training:old", owner_actor_id="andy",
                venue_label="West 37th Manhattan / Open Pod", start_time="2026-10-24T02:00:00Z",
                end_time="2026-10-24T02:30:00Z", group_size=2,
                observed_at=datetime(2026, 10, 23, 21, tzinfo=timezone.utc),
            )
            ledger = json.loads((state / "booking-intents.json").read_text())
            recovered = ledger["intents"][result["intentId"]]
            self.assertEqual("missed_session", ledger["intents"]["intent:training:old"]["status"])
            self.assertEqual("booked_browser_observed", recovered["status"])
            self.assertEqual(["andy", "terry"], recovered["participants"])
            self.assertNotIn("shareRedemptions", recovered)
            self.assertNotIn("eventId", json.dumps(recovered))

    def test_reconciles_visible_booking_without_product_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            state.mkdir()
            (state / "booking-intents.json").write_text(json.dumps({
                "schemaVersion": 1, "activeIntentId": "intent:training:1",
                "intents": {"intent:training:1": {
                    "id": "intent:training:1", "journey": "customer_training",
                    "bookerActorId": "blanka", "participants": ["blanka", "zangief", "dhalsim"],
                    "status": "agreed",
                }},
            }), encoding="utf-8")
            result = reconcile(
                root, intent_id="intent:training:1", owner_actor_id="blanka",
                venue_label="West 37th Manhattan",
                start_time="2026-10-23T14:00:00Z", end_time="2026-10-23T14:30:00Z",
                group_size=3, observed_at=datetime(2026, 10, 23, 13, 30, tzinfo=timezone.utc),
            )
            self.assertEqual(result["status"], "booked_browser_observed")
            ledger = json.loads((state / "booking-intents.json").read_text())
            intent = ledger["intents"]["intent:training:1"]
            self.assertEqual(intent["status"], "booked_browser_observed")
            self.assertNotIn("eventId", intent["booking"])
            fact = load_facts(root)[0]
            self.assertEqual(fact["type"], "event.booked")
            self.assertNotIn("eventId", json.dumps(fact))
            diary = (state / "actors" / "blanka" / "journal.jsonl").read_text()
            self.assertIn("visible_booking_confirmed", diary)

    def test_reconciles_only_named_staff_confirmed_check_in(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            state.mkdir()
            (state / "booking-intents.json").write_text(json.dumps({
                "schemaVersion": 1, "activeIntentId": "intent:training:2",
                "intents": {"intent:training:2": {
                    "id": "intent:training:2", "journey": "customer_training",
                    "bookerActorId": "andy", "participants": ["andy", "terry"],
                    "status": "booked_browser_observed",
                    "booking": {"occurrenceKey": "browser-observed:intent:training:2"},
                }},
            }), encoding="utf-8")
            result = reconcile_staff_check_in(
                root, intent_id="intent:training:2", participant_actor_id="andy",
                observed_at=datetime(2026, 10, 23, 19, 20, tzinfo=timezone.utc),
            )
            self.assertEqual(result["status"], "checked_in_browser_observed")
            ledger = json.loads((state / "booking-intents.json").read_text())
            intent = ledger["intents"]["intent:training:2"]
            self.assertEqual(intent["browserCheckIns"]["andy"]["status"], "CHECKED_IN")
            self.assertEqual(intent["nextAction"], "await_remaining_named_player_check_in")
            fact = load_facts(root)[0]
            self.assertEqual(fact["type"], "participation.check_in_observed")
            self.assertNotIn("play", json.dumps(fact))
