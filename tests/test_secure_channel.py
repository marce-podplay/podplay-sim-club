from datetime import datetime, timezone
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.secure_channel import (  # noqa: E402
    SecureChannelError,
    deliver_invitation,
    retain_invitation_link,
)


class SecureChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        state = self.root / "state"
        state.mkdir()
        (state / "booking-intents.json").write_text(json.dumps({
            "schemaVersion": 1,
            "activeIntentId": "intent:training:1",
            "intents": {
                "intent:training:1": {
                    "id": "intent:training:1", "bookerActorId": "blanka",
                    "participants": ["blanka", "zangief", "dhalsim"],
                    "journey": "customer_training", "status": "booked_browser_observed",
                    "booking": {"occurrenceKey": "browser-observed:intent:training:1"},
                    "previewPlan": {"endTime": "2026-10-23T17:00:00Z"},
                }
            },
        }), encoding="utf-8")
        self.when = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def test_retained_link_is_private_and_delivery_is_immediate_but_url_free_on_club_feed(self):
        retained = retain_invitation_link(
            self.root, intent_id="intent:training:1", owner_actor_id="blanka",
            invitation_url="https://preview.example.test/invites/local-test-token",
            expected_origin="https://preview.example.test", observed_at=self.when,
        )
        self.assertNotIn("Url", json.dumps(retained))
        private = self.root / "state" / "secure-channel" / "owner-invitations.json"
        self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(private.parent.stat().st_mode), 0o700)

        delivered = deliver_invitation(
            self.root, intent_id="intent:training:1", owner_actor_id="blanka",
            recipient_actor_ids=["zangief", "dhalsim"], observed_at=self.when,
        )
        self.assertEqual(delivered["recipients"], ["zangief", "dhalsim"])
        intent = json.loads((self.root / "state" / "booking-intents.json").read_text())["intents"]["intent:training:1"]
        self.assertEqual("await_named_player_share_decision", intent["nextAction"])
        mailboxes = json.loads((self.root / "state" / "secure-channel" / "recipient-mailboxes.json").read_text())
        self.assertEqual(mailboxes["zangief:intent:training:1"]["status"], "delivered")
        feed = (self.root / "state" / "channels" / "club.jsonl").read_text()
        self.assertNotIn("/invites/", feed)
        self.assertIn("private local invite channel", feed)

    def test_delivery_rejects_unnamed_recipient_and_wrong_origin(self):
        with self.assertRaises(SecureChannelError):
            retain_invitation_link(
                self.root, intent_id="intent:training:1", owner_actor_id="blanka",
                invitation_url="https://elsewhere.example.test/invites/local-test-token",
                expected_origin="https://preview.example.test", observed_at=self.when,
            )
        retain_invitation_link(
            self.root, intent_id="intent:training:1", owner_actor_id="blanka",
            invitation_url="https://preview.example.test/invites/local-test-token",
            expected_origin="https://preview.example.test", observed_at=self.when,
        )
        with self.assertRaises(SecureChannelError):
            deliver_invitation(
                self.root, intent_id="intent:training:1", owner_actor_id="blanka",
                recipient_actor_ids=["ryu"], observed_at=self.when,
            )

    def test_private_delivery_drives_the_normal_recipient_offer_and_task(self):
        """New browser-captured links must not depend on the legacy URL store."""
        from podplay_sim_club.share_invitations import (
            next_share_invitation_task,
            record_share_attention,
            record_share_choice,
        )
        from podplay_sim_club.social_engine import _share_redemption_offers
        from podplay_sim_club.storage import Storage

        retain_invitation_link(
            self.root, intent_id="intent:training:1", owner_actor_id="blanka",
            invitation_url="https://preview.example.test/invites/local-test-token",
            expected_origin="https://preview.example.test", observed_at=self.when,
        )
        deliver_invitation(
            self.root, intent_id="intent:training:1", owner_actor_id="blanka",
            recipient_actor_ids=["zangief", "dhalsim"], observed_at=self.when,
        )

        offers = _share_redemption_offers(
            Storage(self.root), {"blanka": {}, "zangief": {}, "dhalsim": {}}, self.when,
        )
        self.assertEqual(
            ["acknowledge_share_invitation", "acknowledge_share_invitation"],
            [offer["kind"] for offer in offers],
        )
        self.assertEqual(["zangief", "dhalsim"], [offer["actorId"] for offer in offers])

        record_share_attention(self.root, intent_id="intent:training:1", actor_id="zangief", now=self.when)
        record_share_choice(self.root, intent_id="intent:training:1", actor_id="zangief", action="redeem", now=self.when)
        task = next_share_invitation_task(self.root, now=self.when)
        self.assertEqual("redeem_share_invitation", task["action"])
        self.assertEqual("zangief", task["recipientActorId"])
