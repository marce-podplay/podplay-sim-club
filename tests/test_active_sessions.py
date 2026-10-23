from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.active_sessions import sessions, tick  # noqa: E402
from podplay_sim_club.arcade import attach_ended_active_session_replay  # noqa: E402
from podplay_sim_club.local_evidence import capture_live_play_receipts  # noqa: E402
from podplay_sim_club.result_requests import request_finished_event_result, report_finished_event_result  # noqa: E402
from podplay_sim_club.storage import Storage  # noqa: E402


class ActiveSessionTests(unittest.TestCase):
    def test_all_visible_check_ins_start_local_play_and_window_end_finishes_it(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            storage = Storage(root)
            intent_id = "intent:training:club-lab:one"
            storage.write_json(storage.state / "booking-intents.json", {
                "schemaVersion": 1,
                "intents": {intent_id: {
                    "id": intent_id,
                    "status": "booked_browser_observed",
                    "journey": "customer_training",
                    "bookerActorId": "andy-bogard",
                    "participants": ["andy-bogard", "terry-bogard"],
                    "booking": {
                        "occurrenceKey": "browser-observed:training:one",
                        "startTime": "2026-10-23T18:00:00Z",
                        "endTime": "2026-10-23T18:30:00Z",
                        "venueLabel": "West 37th Manhattan / Open Pod",
                    },
                    "browserCheckIns": {
                        "andy-bogard": {"status": "CHECKED_IN", "source": "visible_staff_browser"},
                        "terry-bogard": {"status": "CHECKED_IN", "source": "visible_staff_browser"},
                    },
                }},
            })
            started = tick(root, now=datetime(2026, 10, 23, 18, 10, tzinfo=timezone.utc))
            captured = capture_live_play_receipts(root, observed_at=datetime(2026, 10, 23, 18, 10, tzinfo=timezone.utc))
            self.assertEqual("started", started["action"])
            self.assertEqual("PLAYING", sessions(root)[0]["status"])
            self.assertEqual("captured", captured["action"])
            self.assertTrue((storage.state / "evidence" / "images" / captured["items"][0]["image"]).is_file())

            ended = tick(root, now=datetime(2026, 10, 23, 18, 30, tzinfo=timezone.utc))
            self.assertEqual("ended", ended["action"])
            self.assertEqual("ENDED", sessions(root)[0]["status"])
            replay = attach_ended_active_session_replay(root, now=datetime(2026, 10, 23, 18, 31, tzinfo=timezone.utc))
            request = request_finished_event_result(root, now=datetime(2026, 10, 23, 18, 31, tzinfo=timezone.utc))
            self.assertEqual("local_active_session_arcade_replay_attached", replay["action"])
            self.assertEqual("event_result_requested", request["action"])
            self.assertTrue(request["eventId"].startswith("local-arcade-"))
            replay_winner = storage.load_json(storage.state / "booking-intents.json")["intents"][intent_id]["arcadeReplay"]["winner"][0]
            result = report_finished_event_result(
                root, intent_id, "andy-bogard", replay_winner,
                now=datetime(2026, 10, 23, 18, 32, tzinfo=timezone.utc),
            )
            social = storage.load_json(storage.state / "social-engine.json")
            self.assertEqual("reported", result["status"])
            self.assertEqual(4, social["motives"]["andy-bogard"]["autonomy"])
            self.assertEqual(11, social["motives"][replay_winner]["competence"])
            self.assertEqual(8, social["motives"]["andy-bogard"]["relatedness"])
            self.assertEqual(8, social["motives"]["terry-bogard"]["relatedness"])
            facts = (storage.state / "journal" / "facts.jsonl").read_text()
            self.assertIn("event.local_play_started", facts)
            self.assertIn("event.local_play_ended", facts)

    def test_one_check_in_never_starts_local_play(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            storage = Storage(root)
            storage.write_json(storage.state / "booking-intents.json", {
                "schemaVersion": 1,
                "intents": {"intent:training:club-lab:two": {
                    "participants": ["andy-bogard", "terry-bogard"],
                    "booking": {"startTime": "2026-10-23T18:00:00Z", "endTime": "2026-10-23T18:30:00Z"},
                    "browserCheckIns": {"andy-bogard": {"status": "CHECKED_IN", "source": "visible_staff_browser"}},
                }},
            })
            self.assertEqual("pass", tick(root, now=datetime(2026, 10, 23, 18, 10, tzinfo=timezone.utc))["action"])
            self.assertEqual([], sessions(root))
