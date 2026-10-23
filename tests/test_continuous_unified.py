import unittest

from podplay_sim_club.continuous_unified import ContinuousResearchError, _checkin_open, _confirm_path, _own_spot


class ContinuousUnifiedTests(unittest.TestCase):
    def test_normalizes_client_facing_confirm_link(self):
        self.assertEqual(
            _confirm_path({"_links": {"confirm": {"href": "/bookings/quotes/q-1/confirm"}}}),
            "/apis/v2/bookings/quotes/q-1/confirm",
        )

    def test_refuses_cross_origin_confirm_link(self):
        with self.assertRaises(ContinuousResearchError):
            _confirm_path({"_links": {"confirm": {"href": "https://example.test/bookings/quotes/q/confirm"}}})

    def test_finds_only_the_players_own_spot_and_respects_arrival_action(self):
        booking = {"group": {"spots": [
            {"id": "other", "player": {"id": "other-player"}, "actions": []},
            {"id": "mine", "player": {"id": "my-player"}, "actions": [{"key": "show_check_in_code", "enabled": True}]},
        ]}}
        spot = _own_spot(booking, "my-player")
        self.assertEqual("mine", spot["id"])
        self.assertTrue(_checkin_open(spot))
        self.assertIsNone(_own_spot(booking, "missing-player"))
