from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.actions import ActionValidator, IllegalAction
from podplay_sim_club.actor_runtime import run_tick
from podplay_sim_club.booking_intents import load_intent_ledger, save_intent
from podplay_sim_club.cli import _local_slot_claim, main
from podplay_sim_club.config import ClubConfig, ConfigurationError, RunMode
from podplay_sim_club.credentials import CredentialsError, PreviewCredentials
from podplay_sim_club.communications import record_invite_sent
from podplay_sim_club.firebase_auth import AuthSession, FirebaseAuthenticator
from podplay_sim_club.player_login import session_check_record, verify_player_session
from podplay_sim_club.identity_registry import ActorIdentity, IdentityRegistry, TEAM_ACTORS
from podplay_sim_club.preview_seed import load_preview_seed, seed_actor_ids
from podplay_sim_club.fake_preview import FakePreview
from podplay_sim_club.orchestrator import Orchestrator, SimulatedCrash
from podplay_sim_club.preview import PreviewAdapter
from podplay_sim_club.preview_booking import PreviewBookingWriter, occurrence_key
from podplay_sim_club.team_signup import PreviewTeamMemberWriter
from podplay_sim_club.preview_owner_event import PreviewOwnerEventWriter
from podplay_sim_club.preview_event_signup import (
    PreviewEventSignupWriter,
    read_captain_attendance_invitation,
)
from podplay_sim_club.preview_match_ledger import (
    load_match_ledger,
    sanitized_match_index,
    save_match,
    select_active,
    select_match,
)
from podplay_sim_club.preview_participation import (
    PreviewAcceptanceEvaluator,
    PreviewParticipationWriter,
    check_in_window,
    classify_match_status,
)
from podplay_sim_club.preview_evaluation import (
    PreviewBookingEvaluator,
    PreviewEvaluationError,
    UnifiedBookingQuoteEvaluator,
    summarize_booking_preview,
    summarize_unified_booking_quote,
)
from podplay_sim_club.preview_booking import UnifiedBookingWriter, summarize_unified_order
from podplay_sim_club.preview_readonly import PreviewReadError, PreviewReadonlyClient
from podplay_sim_club.preview_readiness import (
    early_agreed_start,
    may_start_early,
    select_candidate_session,
    summarize_payment,
)
from podplay_sim_club.preview_payment import load_test_stripe_secret
from podplay_sim_club.preview_write import (
    PreviewCreditWriter,
    PreviewIdentityWriter,
    PreviewRoleWriter,
    PreviewWriteError,
)
from podplay_sim_club.server import ObservatoryServer
from podplay_sim_club.storage import Storage
from podplay_sim_club.target_policy import TargetPolicy, TargetPolicyError
from podplay_sim_club.terminal import render, render_needs
from podplay_sim_club.tournament import acknowledge_result_request, report_winner, run_tick as run_tournament_tick


FIXED_NOW = datetime(2026, 9, 23, 15, 5, tzinfo=timezone.utc)
PREVIEW_ORIGIN = (
    "https://podify-pr-5207-staging-main-service-bay66ucqza-uk.a.run.app"
)


class SimClubTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        shutil.copytree(REPO_ROOT / "seed", self.root / "seed")
        shutil.copytree(REPO_ROOT / "scenarios", self.root / "scenarios")
        shutil.copytree(REPO_ROOT / "characters", self.root / "characters")

    def tearDown(self):
        self.temporary.cleanup()

    def test_named_roster_keeps_team_captains_and_pickleball_guest_rules_consistent(self):
        roster = json.loads((REPO_ROOT / "characters" / "roster.json").read_text())
        characters = {character["id"]: character for character in roster["characters"]}

        self.assertEqual(4, roster["schemaVersion"])
        from podplay_sim_club.affinity_groups import groups_for_team, team_relationships, validate_roster_affinity_groups
        validate_roster_affinity_groups(roster)
        self.assertEqual(
            ["game-series:king-of-fighters", "region:japan"],
            [group["id"] for group in groups_for_team(roster, "japan-team")],
        )
        relationship = next(row for row in team_relationships(roster) if row["teamIds"] == ["bogard-higashi", "japan-team"])
        self.assertEqual(["game-series:king-of-fighters"], relationship["sharedAffinityGroupIds"])
        self.assertEqual("high", relationship["explicitRivalry"]["intensity"])
        street_fighter = next(team for team in roster["teams"] if team["id"] == "world-warriors")
        self.assertEqual(["ryu", "ken-masters", "chun-li"], street_fighter["members"])
        sf_relationship = next(row for row in team_relationships(roster) if row["teamIds"] == ["street-fighter-contenders", "world-warriors"])
        self.assertEqual(["game-series:street-fighter"], sf_relationship["sharedAffinityGroupIds"])
        self.assertIsNone(sf_relationship["explicitRivalry"])
        self.assertEqual(("ryu", "ken-masters", "chun-li"), TEAM_ACTORS["world-warriors"])
        self.assertEqual(("blanka", "zangief", "dhalsim"), TEAM_ACTORS["street-fighter-contenders"])
        active_actor_ids = {row.get("actorId") for row in characters.values()}
        self.assertTrue(set(TEAM_ACTORS["world-warriors"]).issubset(active_actor_ids))
        self.assertTrue(set(TEAM_ACTORS["street-fighter-contenders"]).issubset(active_actor_ids))
        for team in roster["teams"]:
            self.assertIn(team["captainId"], team["members"])
            self.assertTrue(all(member in characters for member in team["members"]))
            pickleball = team["sports"]["pickleball"]
            self.assertEqual(2, pickleball["preferredTeamSize"])
            self.assertTrue(pickleball["captainInvitesOneTeammate"])
            self.assertTrue(pickleball["thirdMemberMayAttendAsGuest"])
        self.assertEqual("kyo-kusanagi", next(team for team in roster["teams"] if team["id"] == "japan-team")["captainId"])
        riley = characters["riley-chen"]
        self.assertEqual("club_staff", riley["role"])
        self.assertEqual(["ADMIN", "FRONT_DESK"], riley["productRoles"])
        self.assertEqual("riley", roster["staff"][0]["actorId"])
        self.assertEqual(["ADMIN", "FRONT_DESK"], roster["staff"][0]["productRoles"])
        team_ids = {team["id"] for team in roster["teams"]}
        for rivalry in roster["rivalries"]:
            self.assertEqual(2, len(rivalry["teams"]))
            self.assertTrue(set(rivalry["teams"]).issubset(team_ids))
            self.assertIn(rivalry["preferredJourney"], {"doubles", "club_open_play"})

    def test_front_desk_hears_a_reschedule_request_before_other_offers(self):
        from podplay_sim_club.social_engine import tick

        storage = Storage(self.root)
        storage.append_jsonl(
            storage.channel_path("club"),
            {
                "id": "msg-move-earlier",
                "channel": "club",
                "from": "andy-bogard",
                "to": ["riley"],
                "kind": "reschedule_request",
                "text": "Both captains agree. Please move the doubles match earlier.",
                "observedAt": "2026-09-29T14:00:00Z",
            },
        )

        first = tick(self.root, now=FIXED_NOW)
        self.assertEqual("riley", first["offers"][0]["actorId"])
        self.assertEqual("reschedule_match", first["offers"][0]["kind"])
        self.assertEqual("msg-move-earlier", first["offers"][0]["messageId"])

        storage.append_jsonl(
            storage.channel_path("club"),
            {
                "id": "msg-move-earlier:reply",
                "channel": "club",
                "from": "riley",
                "to": ["andy-bogard"],
                "kind": "staff_reply",
                "inReplyTo": "msg-move-earlier",
                "text": "I asked the lead to move that same match.",
                "observedAt": "2026-09-29T14:05:00Z",
            },
        )
        second = tick(self.root, now=datetime(2026, 9, 23, 15, 25, tzinfo=timezone.utc))
        self.assertFalse(any(offer.get("messageId") == "msg-move-earlier" for offer in second["offers"]))

    def test_riley_proposes_one_same_event_reschedule_only_after_both_doubles_captains_ask(self):
        from podplay_sim_club.front_desk import tick

        storage = Storage(self.root)
        intent_id = "intent:doubles-001"
        ledger = load_intent_ledger(storage)
        save_intent(storage, ledger, {
            "schemaVersion": 1, "id": intent_id, "status": "event_created",
            "journey": "customer_team_signup",
            "participants": ["andy-bogard", "terry-bogard", "kyo-captain", "benimaru"],
            "challenger": {"captainActorId": "andy-bogard"},
            "opponent": {"captainActorId": "kyo-captain"},
            "event": {"eventId": "event-001"}, "remoteWrites": 0,
        })
        for sender in ("andy-bogard", "kyo-captain"):
            storage.append_jsonl(storage.channel_path("club"), {
                "id": f"move-{sender}", "channel": "club", "from": sender,
                "to": ["riley"], "kind": "reschedule_request", "intentId": intent_id,
                "text": "Please move our agreed doubles match earlier.", "observedAt": "2026-09-29T14:00:00Z",
            })
        storage.write_json(storage.state / "social-engine.json", {
            "schemaVersion": 1,
            "offers": [{"actorId": "riley", "kind": "reschedule_match", "messageId": "move-andy-bogard"}],
        })

        result = tick(self.root, now=FIXED_NOW)
        self.assertEqual("propose_reschedule", result["action"])
        self.assertEqual("event-001", result["event_id"])
        reply = storage.read_jsonl(storage.channel_path("club"))[-1]
        self.assertEqual("move-andy-bogard", reply["inReplyTo"])
        self.assertEqual("riley", reply["from"])

    def test_riley_turns_a_new_session_message_into_a_requested_intent_without_publishing(self):
        from podplay_sim_club.front_desk import tick

        storage = Storage(self.root)
        storage.append_jsonl(storage.channel_path("club"), {
            "id": "ask-again", "channel": "club", "from": "andy-bogard", "to": ["riley"],
            "kind": "new_event_request", "text": "Please schedule another club hour.",
            "observedAt": "2026-09-29T16:00:00Z",
        })
        storage.write_json(storage.state / "social-engine.json", {
            "schemaVersion": 1,
            "offers": [{"actorId": "riley", "kind": "answer_player", "messageId": "ask-again"}],
        })

        result = tick(self.root, now=FIXED_NOW)
        self.assertEqual("new_event_requested", result["action"])
        intent = load_intent_ledger(storage)["intents"][result["intent_id"]]
        self.assertEqual("requested", intent["status"])
        self.assertEqual(0, intent["remoteWrites"])
        facts = storage.read_jsonl(storage.state / "journal" / "facts.jsonl")
        self.assertEqual("communication.message", facts[-1]["type"])
        self.assertEqual("acknowledge", facts[-1]["payload"]["speechAct"])

    def test_sofia_can_stimulate_a_quiet_club_without_creating_an_event(self):
        from podplay_sim_club.owner_engagement import tick as owner_tick
        from podplay_sim_club.social_engine import tick as social_tick

        storage = Storage(self.root)
        social = social_tick(self.root, now=FIXED_NOW)
        self.assertEqual("sofia", social["offers"][-1]["actorId"])
        self.assertEqual("stimulate_quiet_club", social["offers"][-1]["kind"])

        result = owner_tick(self.root, now=FIXED_NOW)
        self.assertEqual("advertise_interest_check", result["action"])
        self.assertEqual(0, result["remoteWrites"])
        message = storage.read_jsonl(storage.channel_path("club"))[-1]
        self.assertEqual("sofia", message["from"])
        self.assertEqual("owner_interest_check", message["kind"])
        self.assertEqual({}, load_intent_ledger(storage)["intents"])

        later = social_tick(self.root, now=datetime(2026, 9, 23, 15, 30, tzinfo=timezone.utc))
        self.assertFalse(any(offer.get("kind") == "stimulate_quiet_club" for offer in later["offers"]))

    def test_verified_team_activation_is_a_local_training_welcome_not_a_booking(self):
        from podplay_sim_club.owner_engagement import tick as owner_tick
        from podplay_sim_club.team_activation import record_verified_team_activation

        record_verified_team_activation(self.root, "world-warriors", ["ryu", "ken-masters", "chun-li"], now=FIXED_NOW)
        result = owner_tick(self.root, now=FIXED_NOW)
        self.assertEqual("welcome_verified_team", result["action"])
        self.assertEqual("world-warriors", result["teamId"])
        messages = Storage(self.root).read_jsonl(Storage(self.root).channel_path("club"))
        welcome = next(row for row in messages if row.get("kind") == "owner_team_training_nudge")
        self.assertEqual(["ryu", "ken-masters", "chun-li"], welcome["to"])
        self.assertEqual(45, welcome["motiveBoost"])
        self.assertEqual(0, welcome["remoteWrites"])

    def test_captain_challenge_cooldown_does_not_block_a_distinct_teammate_offer(self):
        from podplay_sim_club.social_engine import tick as social_tick

        storage = Storage(self.root)
        storage.write_json(storage.state / "social-engine.json", {
            "schemaVersion": 1,
            "lastTickAt": "2026-09-23T15:00:00Z",
            "motives": {
                "andy-bogard": {"desireToPlay": 100, "curiosity": 0, "frustration": 0},
                "terry-bogard": {"desireToPlay": 40, "curiosity": 0, "frustration": 0},
            },
            # Old actor-wide records are retained only as history; they cannot
            # suppress a different legal interaction after migration.
            "cooldowns": {"andy-bogard": "2026-09-23T15:00:00Z"},
            "interactionCooldowns": {
                "andy-bogard:challenge_rival_captain:kyo-captain": "2026-09-23T15:00:00Z"
            },
            "interactionCooldownSemantics": "action-only",
        })

        result = social_tick(self.root, now=datetime(2026, 9, 23, 15, 5, tzinfo=timezone.utc))
        offers = [offer for offer in result["offers"] if offer.get("actorId") == "andy-bogard"]
        self.assertIn("invite_teammate", [offer["kind"] for offer in offers])
        self.assertNotIn("challenge_rival_captain", [offer["kind"] for offer in offers])
        state = storage.load_json(storage.state / "social-engine.json")
        self.assertIn("legacyActorCooldowns", state)
        self.assertNotIn("cooldowns", state)

    def test_rally_tick_can_ground_a_post_training_captain_challenge_without_product_writes(self):
        from podplay_sim_club.rally_engine import tick
        from podplay_sim_club.social_engine import record_completed_event

        record_completed_event(
            self.root, ["andy-bogard", "terry-bogard"], now=FIXED_NOW,
            team_id="bogard-higashi", occurrence_key="training:rally-challenge:001",
        )

        result = tick(self.root, now=FIXED_NOW)
        self.assertLessEqual(len(result["rounds"]), 2)
        self.assertTrue(all(1 <= len(round_["actorTurns"]) <= 10 for round_ in result["rounds"]))
        self.assertTrue(result["rounds"][0]["messageSent"])
        self.assertTrue(any(turn["action"] == "challenge_rival_captain" for round_ in result["rounds"] for turn in round_["actorTurns"]))
        intents = load_intent_ledger(Storage(self.root))["intents"]
        invited = [intent for intent in intents.values() if intent.get("reason") == "recent_team_training_competition"]
        self.assertEqual(1, len(invited))
        self.assertEqual("agreed", invited[0]["status"])
        messages = Storage(self.root).read_jsonl(Storage(self.root).channel_path("club"))
        self.assertIn("captain_challenge_acceptance", [row.get("kind") for row in messages])
        self.assertEqual(0, result["remoteWrites"])

    def test_rally_tick_queues_an_agreed_training_for_the_named_bookers_browser(self):
        """A social agreement queues UI work; it never invokes a booking adapter."""
        from podplay_sim_club.rally_engine import tick

        storage = Storage(self.root)
        intent_id = "intent:training:browser-dispatch"
        ledger = load_intent_ledger(storage)
        save_intent(storage, ledger, {
            "schemaVersion": 1,
            "id": intent_id,
            "journey": "customer_training",
            "status": "agreed",
            "createdAt": "2026-09-23T15:00:00Z",
            "agreedAt": "2026-09-23T15:01:00Z",
            "bookerActorId": "andy-bogard",
            "participants": ["andy-bogard", "terry-bogard"],
            "constraints": {"durationMinutes": 30},
        })

        result = tick(self.root, now=FIXED_NOW)

        job = result["browserJourney"]
        self.assertEqual("queued", job["status"])
        self.assertEqual("andy-bogard", job["actorId"])
        self.assertEqual("plan_training_booking", job["journeyAction"])
        self.assertEqual(intent_id, job["intentId"])
        self.assertEqual("playbooks/sim-player-booking.md", job["playbook"])
        self.assertEqual(0, result["remoteWrites"])
        self.assertEqual("pass", result["bookingBridge"]["action"])

        jobs = storage.load_json(storage.state / "browser-journeys.json")["jobs"]
        self.assertIn(job["jobKey"], jobs)

    def test_rally_queue_resumes_after_each_ten_people_and_wraps_only_next_tick(self):
        from podplay_sim_club.rally_engine import tick

        scenario_path = self.root / "scenarios" / "rally-engine.json"
        scenario = json.loads(scenario_path.read_text())
        scenario["actors"] = [f"person-{number}" for number in range(25)]
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")

        first = tick(self.root, now=FIXED_NOW)
        second = tick(self.root, now=datetime(2026, 9, 23, 15, 25, tzinfo=timezone.utc))
        third = tick(self.root, now=datetime(2026, 9, 23, 15, 45, tzinfo=timezone.utc))

        self.assertEqual([f"person-{number}" for number in range(10)], first["rounds"][0]["actorIds"])
        self.assertEqual([f"person-{number}" for number in range(10, 20)], second["rounds"][0]["actorIds"])
        self.assertEqual([f"person-{number}" for number in range(20, 25)], third["rounds"][0]["actorIds"])
        self.assertEqual(0, third["state"]["schedulerCursor"])

    def test_andy_vs_kyo_doubles_scenario_uses_the_roster_pairs_and_guests(self):
        roster = json.loads((REPO_ROOT / "characters" / "roster.json").read_text())
        scenario = json.loads((REPO_ROOT / "scenarios" / "andy-vs-kyo-doubles.json").read_text())
        teams = {team["id"]: team for team in roster["teams"]}

        self.assertEqual("doubles_challenge", scenario["journey"])
        self.assertEqual(60, scenario["durationMinutes"])
        for side in (scenario["challenger"], scenario["opponent"]):
            team = teams[side["teamId"]]
            self.assertEqual(team["captainId"], side["captainId"])
            self.assertIn(side["teammateId"], team["members"])
            self.assertNotEqual(side["captainId"], side["teammateId"])
            self.assertTrue(set(side["guestEligible"]).issubset(set(team["members"]) - {side["captainId"], side["teammateId"]}))

    def test_women_fighters_training_requires_king_proposal_then_mai_acceptance(self):
        from podplay_sim_club.training_sessions import accept, propose

        invited = propose(self.root, "women-fighters-training.json", FIXED_NOW)
        self.assertEqual("captain_invited", invited["status"])
        self.assertEqual(["king-captain", "mai"], invited["participants"])
        self.assertEqual(30, invited["constraints"]["durationMinutes"])
        self.assertEqual(0, invited["remoteWrites"])

        agreed = accept(
            self.root, "women-fighters-training.json",
            datetime(2026, 9, 23, 15, 10, tzinfo=timezone.utc),
        )
        self.assertEqual("agreed", agreed["status"])
        self.assertEqual("king-captain", agreed["bookerActorId"])
        self.assertEqual("plan_live_w37_availability_then_player_booking", agreed["nextAction"])
        messages = Storage(self.root).read_jsonl(Storage(self.root).channel_path("club"))
        self.assertEqual(["training_invitation", "training_acceptance"], [row["kind"] for row in messages])
        from podplay_sim_club.training_sessions import next_booking_task
        task = next_booking_task(self.root)
        self.assertEqual("plan_training_booking", task["action"])
        self.assertEqual(agreed["id"], task["intentId"])
        self.assertEqual(
            ["capture_share_invitation_link", "invite_player", "accept_invitation"],
            task["playerActionsAfterBooking"],
        )

    def test_lead_booking_bridge_requires_an_explicit_plan_then_confirmed_apply(self):
        from podplay_sim_club.lead_booking_bridge import advance
        from podplay_sim_club.training_sessions import next_booking_task

        storage = Storage(self.root)
        intent = {
            "schemaVersion": 1, "id": "intent:training:bridge", "journey": "customer_training",
            "status": "agreed", "bookerActorId": "king-captain",
            "participants": ["king-captain", "mai"], "remoteWrites": 0,
            "constraints": {"durationMinutes": 30},
        }
        ledger = load_intent_ledger(storage)
        save_intent(storage, ledger, intent)
        task = next_booking_task(self.root)
        calls = []

        def planner(root, intent_id, announce):
            calls.append(("plan", intent_id, announce))
            current_ledger = load_intent_ledger(Storage(root))
            current = current_ledger["intents"][intent_id]
            current.update({"status": "preview_ready", "previewPlan": {"startTime": "2026-10-06T18:00:00Z"}})
            save_intent(Storage(root), current_ledger, current)
            return 0

        def booker(root, apply, confirmation, intent_id):
            calls.append(("book", apply, confirmation, intent_id))
            current_ledger = load_intent_ledger(Storage(root))
            current = current_ledger["intents"][intent_id]
            current.update({"status": "booked", "remoteWrites": 1, "booking": {"eventId": "event-bridge"}})
            save_intent(Storage(root), current_ledger, current)
            return 0

        waiting = advance(self.root, task, now=FIXED_NOW, planner=planner, training_booker=booker, team_booker=booker)
        self.assertEqual("awaiting_live_plan", waiting["action"])
        self.assertEqual([], calls)

        planned = advance(self.root, task, now=FIXED_NOW, plan_preview=True, planner=planner, training_booker=booker, team_booker=booker)
        self.assertEqual("preview_plan_ready", planned["action"])
        self.assertEqual([("plan", intent["id"], False)], calls)
        apply_task = next_booking_task(self.root)
        self.assertEqual("apply_training_booking", apply_task["action"])

        awaiting_apply = advance(self.root, apply_task, now=FIXED_NOW, planner=planner, training_booker=booker, team_booker=booker)
        self.assertEqual("awaiting_preview_apply", awaiting_apply["action"])
        applied = advance(self.root, apply_task, now=FIXED_NOW, apply_preview=True, confirmation=PREVIEW_ORIGIN, planner=planner, training_booker=booker, team_booker=booker)
        self.assertEqual("booking_read_back", applied["action"])
        self.assertEqual(1, applied["remoteWrites"])
        self.assertEqual(("book", True, PREVIEW_ORIGIN, intent["id"]), calls[-1])
        bridge = storage.load_json(storage.state / "lead-booking-bridge.json")
        self.assertEqual("booking_read_back", bridge["lastAttempt"]["action"])

    def test_historical_training_does_not_block_a_fresh_captain_invitation(self):
        from podplay_sim_club.training_sessions import propose_pair

        storage = Storage(self.root)
        ledger = load_intent_ledger(storage)
        save_intent(storage, ledger, {
            "schemaVersion": 1, "id": "intent:training:bogard-higashi:old",
            "journey": "customer_training", "trainingTeamId": "bogard-higashi",
            "status": "booked", "createdAt": "2026-09-01T12:00:00Z",
            "participants": ["andy-bogard", "terry-bogard"],
            "previewPlan": {"endTime": "2026-09-01T13:00:00Z"},
        })

        fresh = propose_pair(self.root, "andy-bogard", "terry-bogard", FIXED_NOW)
        self.assertEqual("captain_invited", fresh["status"])
        self.assertNotEqual("intent:training:bogard-higashi:old", fresh["id"])

    def test_booked_named_training_advertises_owner_share_without_product_write(self):
        from podplay_sim_club.server import _with_social_lifecycle_offers
        from podplay_sim_club.social_engine import tick as social_tick

        storage = Storage(self.root)
        intent_id = "intent:training:share-offer"
        occurrence = "occurrence:intent:share-offer"
        ledger = load_intent_ledger(storage)
        save_intent(storage, ledger, {
            "schemaVersion": 1, "id": intent_id, "journey": "customer_training",
            "status": "booked", "bookerActorId": "king-captain",
            "participants": ["king-captain", "mai"], "remoteWrites": 1,
            "booking": {"eventId": "event-share-offer", "occurrenceKey": occurrence},
            "previewPlan": {"endTime": "2026-09-23T17:00:00Z"},
        })

        result = social_tick(self.root, now=FIXED_NOW)
        offers = [row for row in result["offers"] if row.get("intentId") == intent_id]
        self.assertEqual(["capture_share_invitation"], [row["kind"] for row in offers])
        self.assertEqual("king-captain", offers[0]["actorId"])
        self.assertEqual(["mai"], offers[0]["recipientActorIds"])
        self.assertEqual([], storage.read_jsonl(storage.channel_path("club")))
        self.assertFalse((storage.state / "share-invitations.json").exists())

        card = _with_social_lifecycle_offers(Orchestrator(self.root), {
            "matches": [{
                "socialIntentRef": intent_id,
                "lifecycle": {"state": "BOOKED", "steps": {"invitation": "NOT_OBSERVED"}},
            }],
        })["matches"][0]
        self.assertEqual("AWAITING_OWNER_SHARE", card["lifecycle"]["state"])
        self.assertEqual("OWNER_SHARE_OFFERED", card["lifecycle"]["steps"]["invitation"])
        self.assertEqual("capture_share_invitation", card["socialNextAction"]["kind"])

        storage.write_json(storage.state / "share-invitations.json", {
            "schemaVersion": 1, "invitations": {occurrence: {"redeemUrl": "private"}},
        })
        from podplay_sim_club.server import _invitation_conversations
        conversations = _invitation_conversations(Orchestrator(self.root))
        self.assertEqual("handoff_ready", conversations[0]["stage"])
        self.assertEqual("recipient_acknowledge_handoff", conversations[0]["expectedNextStep"])
        after_capture = [
            row for row in social_tick(self.root, now=FIXED_NOW)["offers"]
            if row.get("intentId") == intent_id
        ]
        self.assertNotIn("capture_share_invitation", [row["kind"] for row in after_capture])
        self.assertEqual(["acknowledge_share_invitation"], [row["kind"] for row in after_capture])

        from podplay_sim_club.share_invitations import record_share_attention
        record_share_attention(self.root, intent_id=intent_id, actor_id="mai", now=FIXED_NOW)
        conversations = _invitation_conversations(Orchestrator(self.root))
        self.assertEqual("acknowledged", conversations[0]["stage"])
        self.assertEqual("recipient_accept_ask_or_pass", conversations[0]["expectedNextStep"])
        after_attention = [
            row for row in social_tick(self.root, now=FIXED_NOW)["offers"]
            if row.get("intentId") == intent_id
        ]
        self.assertEqual(["redeem_share_invitation"], [row["kind"] for row in after_attention])

    def test_share_choices_bridge_only_after_apply_and_recipient_is_named(self):
        from podplay_sim_club.share_invitations import (
            next_share_invitation_task, record_share_attention, record_share_choice,
        )
        from podplay_sim_club.share_invitation_bridge import advance

        storage = Storage(self.root)
        intent_id = "intent:training:share-bridge"
        occurrence = "occurrence:intent:share-bridge"
        ledger = load_intent_ledger(storage)
        save_intent(storage, ledger, {
            "schemaVersion": 1, "id": intent_id, "journey": "customer_training",
            "status": "team_members_registered", "bookerActorId": "king-captain",
            "participants": ["king-captain", "mai"],
            "booking": {"eventId": "event-share-bridge", "occurrenceKey": occurrence},
            "previewPlan": {"endTime": "2026-09-23T17:00:00Z"},
        })
        with self.assertRaises(Exception):
            record_share_choice(self.root, intent_id=intent_id, actor_id="mai", action="capture", now=FIXED_NOW)
        record_share_choice(self.root, intent_id=intent_id, actor_id="king-captain", action="capture", now=FIXED_NOW)
        task = next_share_invitation_task(self.root, now=FIXED_NOW)
        self.assertEqual("capture_share_invitation", task["action"])
        calls = []
        waiting = advance(
            self.root, task, now=FIXED_NOW,
            capture=lambda *args: calls.append(args) or 0,
            redeem=lambda *args: calls.append(args) or 0,
        )
        self.assertEqual("awaiting_preview_apply", waiting["action"])
        self.assertEqual([], calls)
        done = advance(
            self.root, task, now=FIXED_NOW, apply_preview=True, confirmation=PREVIEW_ORIGIN,
            capture=lambda *args: calls.append(args) or 0,
            redeem=lambda *args: calls.append(args) or 0,
        )
        self.assertEqual("read_back_complete", done["action"])
        self.assertEqual((self.root, intent_id, PREVIEW_ORIGIN), calls[-1])

        storage.write_json(storage.state / "share-invitations.json", {
            "schemaVersion": 1, "invitations": {occurrence: {"redeemUrl": "private"}},
        })
        with self.assertRaises(Exception):
            record_share_choice(self.root, intent_id=intent_id, actor_id="mai", action="redeem", now=FIXED_NOW)
        attention = record_share_attention(self.root, intent_id=intent_id, actor_id="mai", now=FIXED_NOW)
        self.assertEqual("acknowledged", attention["status"])
        message = storage.read_jsonl(storage.channel_path("club"))[-1]
        self.assertEqual("share_invitation_acknowledged", message["kind"])
        self.assertEqual("acknowledge", message["speechAct"])
        record_share_choice(self.root, intent_id=intent_id, actor_id="mai", action="redeem", now=FIXED_NOW)
        redemption = next_share_invitation_task(self.root, now=FIXED_NOW)
        self.assertEqual("redeem_share_invitation", redemption["action"])
        self.assertEqual("mai", redemption["recipientActorId"])

    def test_training_check_in_is_per_player_and_apply_gated(self):
        from podplay_sim_club.check_in_bridge import advance, next_task, offers, record_choice

        storage = Storage(self.root)
        intent_id = "intent:training:arrival"
        ledger = load_intent_ledger(storage)
        save_intent(storage, ledger, {
            "schemaVersion": 1, "id": intent_id, "journey": "customer_training",
            "status": "booked", "bookerActorId": "king-captain",
            "participants": ["king-captain", "mai"],
            "booking": {"eventId": "event-arrival", "occurrenceKey": "occurrence:intent:arrival"},
            "previewPlan": {"startTime": "2026-09-23T15:30:00Z", "endTime": "2026-09-23T16:00:00Z"},
            "shareRedemptions": [{"actorId": "mai", "status": "ACCEPTED"}],
        })
        people = {"king-captain": {}, "mai": {}}
        available = offers(self.root, now=FIXED_NOW, people=people)
        self.assertEqual({"king-captain", "mai"}, {row["actorId"] for row in available})
        record_choice(self.root, intent_id=intent_id, actor_id="mai", now=FIXED_NOW)
        task = next_task(self.root, now=FIXED_NOW)
        self.assertEqual(("check_in_training", "mai"), (task["action"], task["actorId"]))
        calls = []
        self.assertEqual("awaiting_preview_apply", advance(
            self.root, task, now=FIXED_NOW,
            check_in=lambda *args: calls.append(args) or 0,
        )["action"])
        self.assertEqual([], calls)
        self.assertEqual("read_back_complete", advance(
            self.root, task, now=FIXED_NOW, apply_preview=True, confirmation=PREVIEW_ORIGIN,
            check_in=lambda *args: calls.append(args) or 0,
        )["action"])
        self.assertEqual((self.root, True, PREVIEW_ORIGIN, intent_id, "mai"), calls[-1])

        # Team signup uses the same named-player invitation and arrival
        # contract.  The bridge must not care whether the social purpose was
        # a practice or a captain-led challenge.
        team_intent_id = "intent:challenge:arrival"
        save_intent(storage, load_intent_ledger(storage), {
            "schemaVersion": 1, "id": team_intent_id, "journey": "customer_team_signup",
            "status": "team_members_registered", "bookerActorId": "king-captain",
            "participants": ["king-captain", "mai"],
            "booking": {"eventId": "event-challenge-arrival", "occurrenceKey": "occurrence:intent:challenge-arrival"},
            "previewPlan": {"startTime": "2026-09-23T15:30:00Z", "endTime": "2026-09-23T16:00:00Z"},
            "shareRedemptions": [{"actorId": "mai", "status": "ACCEPTED"}],
        })
        record_choice(self.root, intent_id=team_intent_id, actor_id="mai", now=FIXED_NOW)
        team_intent = load_intent_ledger(storage)["intents"][team_intent_id]
        self.assertEqual("accepted", team_intent["checkInChoices"]["mai"]["status"])

    def test_live_lead_task_expires_stale_agreement_into_history(self):
        from podplay_sim_club.training_sessions import next_booking_task

        storage = Storage(self.root)
        ledger = load_intent_ledger(storage)
        stale = {
            "schemaVersion": 1, "id": "intent:training:stale", "journey": "customer_training",
            "status": "agreed", "createdAt": "2026-09-20T12:00:00Z",
            "agreedAt": "2026-09-20T12:01:00Z", "bookerActorId": "andy-bogard",
            "participants": ["andy-bogard", "terry-bogard"],
            "constraints": {"durationMinutes": 30},
        }
        save_intent(storage, ledger, stale)

        self.assertEqual("pass", next_booking_task(self.root, now=FIXED_NOW)["action"])
        stored = load_intent_ledger(storage)["intents"][stale["id"]]
        self.assertEqual("expired", stored["status"])
        self.assertEqual("history_only_fresh_player_agreement_required", stored["nextAction"])

    def test_newer_team_acceptance_supersedes_prior_unbooked_booking_authority(self):
        from podplay_sim_club.training_sessions import accept_intent

        storage = Storage(self.root)
        ledger = load_intent_ledger(storage)
        prior = {
            "schemaVersion": 1, "id": "intent:training:bogard-higashi:prior",
            "journey": "customer_training", "trainingTeamId": "bogard-higashi",
            "status": "agreed", "createdAt": "2026-09-23T14:00:00Z",
            "agreedAt": "2026-09-23T14:10:00Z", "bookerActorId": "andy-bogard",
            "participants": ["andy-bogard", "terry-bogard"],
            "constraints": {"durationMinutes": 30},
        }
        current = {
            "schemaVersion": 1, "id": "intent:training:bogard-higashi:current",
            "journey": "customer_training", "trainingTeamId": "bogard-higashi",
            "status": "captain_invited", "createdAt": "2026-09-23T15:00:00Z",
            "bookerActorId": "andy-bogard", "participants": ["andy-bogard", "terry-bogard"],
            "constraints": {"durationMinutes": 30}, "nextAction": "await_teammate_acceptance",
        }
        save_intent(storage, ledger, prior)
        save_intent(storage, ledger, current)

        accepted = accept_intent(self.root, current["id"], "terry-bogard", FIXED_NOW)
        stored = load_intent_ledger(storage)["intents"]
        self.assertEqual("agreed", accepted["status"])
        self.assertEqual("superseded", stored[prior["id"]]["status"])
        self.assertEqual(current["id"], stored[prior["id"]]["supersededBy"])

    def test_live_team_signup_task_expires_stale_captain_agreement(self):
        from podplay_sim_club.team_challenges import next_team_signup_task

        storage = Storage(self.root)
        stale = {
            "schemaVersion": 1, "id": "intent:team-challenge:stale",
            "journey": "customer_team_signup", "status": "agreed",
            "createdAt": "2026-09-20T12:00:00Z", "agreedAt": "2026-09-20T12:01:00Z",
            "bookerActorId": "andy-bogard",
            "participants": ["andy-bogard", "terry-bogard", "kyo-captain", "benimaru"],
            "challenger": {"captainActorId": "andy-bogard"},
            "opponent": {"captainActorId": "kyo-captain"},
            "constraints": {"durationMinutes": 60},
        }
        save_intent(storage, load_intent_ledger(storage), stale)

        self.assertEqual("pass", next_team_signup_task(self.root, now=FIXED_NOW)["action"])
        stored = load_intent_ledger(storage)["intents"][stale["id"]]
        self.assertEqual("expired", stored["status"])
        self.assertEqual("history_only_fresh_captain_agreement_required", stored["nextAction"])

    def test_player_share_invitation_link_is_private_and_same_preview_only(self):
        from podplay_sim_club.share_invitations import (
            ShareInvitationError,
            capture_share_invitation_link,
            record_share_invitation,
            redeem_share_invitation,
        )

        policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_READONLY,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
            )
        )
        response = {
            "id": "open-invitation-1",
            "redeemUrl": PREVIEW_ORIGIN + "/invitation?key=private-key",
            "maxRedeems": 3,
            "redeemsRemaining": 2,
        }
        self.assertEqual(response["redeemUrl"], capture_share_invitation_link(response, policy))
        record = record_share_invitation(
            Storage(self.root), policy, key="observed:king", owner_actor_id="king-captain", response=response,
        )
        path = Storage(self.root).state / "share-invitations.json"
        self.assertEqual(0o600, path.stat().st_mode & 0o777)
        self.assertEqual(2, record["redeemsRemaining"])
        with self.assertRaises(ShareInvitationError):
            capture_share_invitation_link({"redeemUrl": "https://example.invalid/invitation?key=x"}, policy)

        write_policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_WRITE,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
                preview_write_confirmation=PREVIEW_ORIGIN,
            )
        )

        class Response:
            status = 201
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def geturl(self): return PREVIEW_ORIGIN + "/apis/v2/events/invitations"
            def read(self): return b'{"invitation":{"id":"invite-1"}}'

        sent = []
        redeem_share_invitation(
            write_policy, "recipient-token", response["redeemUrl"],
            transport=lambda request, _timeout: sent.append(request) or Response(),
        )
        self.assertEqual(
            {"sharingKey": "private-key"},
            json.loads(sent[0].data.decode("utf-8")),
        )
        self.assertNotIn("invitation?key=", sent[0].full_url)

    def test_expired_unsent_share_invitation_notifies_mai_then_riley_opens_rebooking_request(self):
        from podplay_sim_club.share_invitations import expire_unsent_booking_invitation
        from podplay_sim_club.social_engine import tick as social_tick
        from podplay_sim_club.front_desk import tick as front_desk_tick

        storage = Storage(self.root)
        key = "observed:king-training"
        storage.write_json(storage.state / "share-invitations.json", {
            "schemaVersion": 1,
            "invitations": {key: {
                "key": key, "ownerActorId": "king-captain", "invitationId": "open-1",
                "redeemUrl": PREVIEW_ORIGIN + "/invitation?key=private", "maxRedeems": 1,
                "redeemsRemaining": 1, "capturedAt": "2026-09-29T20:00:00Z",
            }},
        })
        storage.write_json(storage.state / "observed-bookings.json", {
            "schemaVersion": 1,
            "bookings": [{
                "occurrenceKey": key, "endTime": "2026-09-29T22:30:00Z",
                "participants": [{"actorId": "king-captain"}, {"actorId": "mai", "invitationStatus": "LINK_CAPTURED_NOT_SENT"}],
                "lifecycle": {"state": "BOOKED", "steps": {"invitation": "LINK_CAPTURED", "acceptance": "NOT_SENT"}},
            }],
        })

        expired = expire_unsent_booking_invitation(
            self.root, owner_actor_id="king-captain", now=datetime(2026, 9, 30, tzinfo=timezone.utc),
        )
        self.assertEqual("report_expired_share_invitation", expired["action"])
        messages = storage.read_jsonl(storage.channel_path("club"))
        self.assertEqual("share_invitation_expired", messages[-1]["kind"])
        self.assertEqual(["mai", "riley"], messages[-1]["to"])
        self.assertNotIn("invitation?key=", messages[-1]["text"])

        social_tick(self.root, now=datetime(2026, 9, 30, tzinfo=timezone.utc))
        response = front_desk_tick(self.root, now=datetime(2026, 9, 30, tzinfo=timezone.utc))
        self.assertEqual("rebooking_requested", response["action"], response)
        self.assertEqual(30, load_intent_ledger(storage)["intents"][response["intentId"]]["constraints"]["durationMinutes"])
        self.assertEqual("EXPIRED_UNSENT", storage.load_json(storage.state / "observed-bookings.json")["bookings"][0]["lifecycle"]["state"])

    def test_only_designated_captain_can_attest_tournament_winner(self):
        storage = Storage(self.root)
        storage.write_json(
            storage.state / "tournament.json",
            {"schemaVersion": 1, "id": "bogard-japan-women-fighters-001", "rounds": {"semi-final": {"status": "awaiting_result"}}},
        )

        with self.assertRaisesRegex(ValueError, "designated result reporter"):
            report_winner(self.root, "Japan Team", "kyo-captain", now=FIXED_NOW)

        state = report_winner(self.root, "Japan Team", "andy-bogard", now=FIXED_NOW)
        self.assertEqual("reported", state["rounds"]["semi-final"]["status"])
        self.assertEqual("Japan Team", state["rounds"]["semi-final"]["winner"])

    def test_finished_training_result_requires_requested_captain_and_named_winner(self):
        from podplay_sim_club.result_requests import report_finished_event_result

        storage = Storage(self.root)
        intent_id = "intent:training:japan-team:001"
        storage.write_json(storage.state / "booking-intents.json", {
            "schemaVersion": 1,
            "activeIntentId": intent_id,
            "intents": {intent_id: {
                "id": intent_id, "journey": "customer_training", "status": "attendance_observed",
                "trainingTeamId": "japan-team", "bookerActorId": "kyo-captain",
                "participants": ["kyo-captain", "benimaru"],
                "eventObservation": {"eventId": "event-japan", "played": False},
                "resultRequest": {
                    "eventId": "event-japan", "reporterActorId": "kyo-captain",
                    "status": "requested", "evidence": "verified_check_in_only",
                },
            }},
        })
        with self.assertRaises(ValueError):
            report_finished_event_result(self.root, intent_id, "benimaru", "Japan Team", FIXED_NOW)
        result = report_finished_event_result(self.root, intent_id, "kyo-captain", "Japan Team", FIXED_NOW)

        self.assertEqual("reported", result["status"])
        self.assertEqual("Japan Team", result["winner"])
        persisted = Storage(self.root).load_json(Storage(self.root).state / "booking-intents.json")
        self.assertTrue(persisted["intents"][intent_id]["eventObservation"]["played"])
        message = Storage(self.root).read_jsonl(Storage(self.root).channel_path("club"))[-1]
        self.assertEqual("event_result_reported", message["kind"])

    def test_designated_captain_can_confirm_only_a_grounded_arcade_replay(self):
        from podplay_sim_club.club_rounds import tick as player_round
        from podplay_sim_club.social_engine import tick as social_tick

        storage = Storage(self.root)
        intent_id = "intent:training:bogard-higashi:replay-confirmation"
        storage.write_json(storage.state / "booking-intents.json", {
            "schemaVersion": 1, "activeIntentId": intent_id,
            "intents": {intent_id: {
                "id": intent_id, "journey": "customer_training", "status": "attendance_observed",
                "trainingTeamId": "bogard-higashi", "bookerActorId": "andy-bogard",
                "participants": ["andy-bogard", "terry-bogard"],
                "booking": {"eventId": "event-replay"},
                "eventObservation": {"eventId": "event-replay", "outcome": "attendance_observed", "played": False},
                "arcadeReplay": {"status": "ready", "winner": ["terry-bogard"]},
                "resultRequest": {"eventId": "event-replay", "reporterActorId": "andy-bogard", "status": "requested", "evidence": "arcade_replay"},
            }},
        })
        offers = social_tick(self.root, now=FIXED_NOW)["offers"]
        self.assertIn(
            "confirm_arcade_replay_result",
            [offer.get("kind") for offer in offers if offer.get("actorId") == "andy-bogard"],
        )
        result = player_round(self.root, actor_id="andy-bogard", now=FIXED_NOW)
        self.assertEqual("confirm_arcade_replay_result", result["action"])
        self.assertEqual("Terry Bogard", result["winner"])
        persisted = storage.load_json(storage.state / "booking-intents.json")["intents"][intent_id]
        self.assertEqual("reported", persisted["result"]["status"])
        self.assertEqual("arcade_replay_captain_confirmation", persisted["result"]["evidence"])

    def test_completed_training_suppresses_practice_and_enables_generic_captain_challenge(self):
        from podplay_sim_club.club_rounds import tick as player_round
        from podplay_sim_club.social_engine import record_completed_event, tick as social_tick

        record_completed_event(
            self.root, ["andy-bogard", "terry-bogard"], now=FIXED_NOW,
            team_id="bogard-higashi", occurrence_key="training:completion:001",
        )
        offers = social_tick(self.root, now=FIXED_NOW)["offers"]
        andy_offers = [offer for offer in offers if offer.get("actorId") == "andy-bogard"]
        self.assertNotIn("invite_teammate", [offer.get("kind") for offer in andy_offers])
        challenge = next(offer for offer in andy_offers if offer.get("kind") == "challenge_rival_captain")
        self.assertEqual("kyo-captain", challenge["rivalCaptainId"])
        action = player_round(self.root, actor_id="andy-bogard", now=FIXED_NOW)
        self.assertEqual("challenge_rival_captain", action["action"])
        intent = Storage(self.root).load_json(Storage(self.root).state / "booking-intents.json")["intents"][action["intentId"]]
        self.assertEqual("recent_team_training_competition", intent["reason"])
        self.assertEqual(["andy-bogard", "terry-bogard", "kyo-captain", "benimaru"], intent["participants"])
        self.assertEqual(0, intent["remoteWrites"])

    def test_arcade_table_tennis_is_seeded_replayable_and_local_only(self):
        from podplay_sim_club.arcade import simulate_and_store, simulate_table_tennis

        ratings = {
            "kyo-captain": {"serve": 70, "attack": 76, "defense": 66, "composure": 72, "teamwork": 60},
            "benimaru": {"serve": 64, "attack": 68, "defense": 70, "composure": 66, "teamwork": 75},
            "andy-bogard": {"serve": 69, "attack": 73, "defense": 71, "composure": 70, "teamwork": 74},
            "terry-bogard": {"serve": 66, "attack": 72, "defense": 73, "composure": 71, "teamwork": 76},
        }
        config = {"left": ["kyo-captain", "benimaru"], "right": ["andy-bogard", "terry-bogard"]}
        first = simulate_table_tennis(config, ratings, "a" * 64)
        second = simulate_table_tennis(config, ratings, "a" * 64)

        self.assertEqual(first, second)
        self.assertGreaterEqual(max(first["score"].values()), 11)
        self.assertGreaterEqual(abs(first["score"]["left"] - first["score"]["right"]), 2)
        stored = simulate_and_store(self.root, occurrence_key="arcade:demo-001", left=config["left"], right=config["right"])
        again = simulate_and_store(self.root, occurrence_key="arcade:demo-001", left=config["left"], right=config["right"])
        self.assertEqual(stored, again)
        self.assertEqual("arcade_simulation", stored["kind"])
        self.assertTrue((Storage(self.root).state / "arcade" / "matches.json").is_file())

    def test_live_play_receipt_is_local_idempotent_and_not_attendance_or_result(self):
        from podplay_sim_club.local_evidence import capture_live_play_receipts, evidence_index

        storage = Storage(self.root)
        occurrence = "training:club-lab:001"
        storage.write_json(storage.state / "preview-matches.json", {
            "schemaVersion": 1,
            "matches": {
                occurrence: {
                    "occurrenceKey": occurrence,
                    "label": "Evening training",
                    "venue": {"areaName": "West 37th", "podName": "Open Pod"},
                    "participants": [
                        {"actorId": "andy-bogard", "label": "Andy Bogard"},
                        {"actorId": "terry-bogard", "label": "Terry Bogard"},
                    ],
                    "lifecycle": {"steps": {"play": "PLAYING"}},
                }
            },
        })

        captured = capture_live_play_receipts(self.root, observed_at=FIXED_NOW)
        again = capture_live_play_receipts(self.root, observed_at=FIXED_NOW)
        item = captured["items"][0]

        self.assertEqual("captured", captured["action"])
        self.assertEqual("pass", again["action"])
        self.assertEqual(0, captured["remoteWrites"])
        self.assertTrue((storage.state / "evidence" / "images" / item["image"]).is_file())
        self.assertEqual([item], evidence_index(self.root)["items"])
        self.assertNotIn("attendance", item["boundary"].split("not ")[0])
        self.assertNotIn("result", item["boundary"].split("not ")[0])
        andy_entries = storage.read_jsonl(storage.actor_journal_path("andy-bogard"))
        self.assertEqual("local_live_play_scene_captured", andy_entries[-1]["step"])

    def test_live_play_receipt_does_not_capture_check_in_or_expose_actor_ids_as_names(self):
        from podplay_sim_club.local_evidence import capture_live_play_receipts

        storage = Storage(self.root)
        storage.write_json(storage.state / "observed-bookings.json", {
            "bookings": [{
                "occurrenceKey": "training:club-lab:check-in",
                "participants": [{"actorId": "andy-bogard"}, {"actorId": "terry-bogard"}],
                "lifecycle": {"steps": {"play": "NOT_RECORDED", "checkIn": "CONFIRMED"}},
            }],
        })

        self.assertEqual("pass", capture_live_play_receipts(self.root, observed_at=FIXED_NOW)["action"])
        storage.write_json(storage.state / "observed-bookings.json", {
            "bookings": [{
                "occurrenceKey": "training:club-lab:check-in",
                "participants": [{"actorId": "andy-bogard"}, {"actorId": "terry-bogard"}],
                "lifecycle": {"steps": {"play": "PLAYING"}},
            }],
        })
        captured = capture_live_play_receipts(self.root, observed_at=FIXED_NOW)
        self.assertEqual(["Andy Bogard", "Terry Bogard"], captured["items"][0]["participants"])

    def test_arcade_replay_is_confirmed_by_the_named_reporter_without_claiming_check_in(self):
        from podplay_sim_club.arcade import attach_training_replay
        from podplay_sim_club.result_requests import request_finished_event_result, report_finished_event_result

        storage = Storage(self.root)
        intent_id = "intent:training:women-fighters:arcade"
        storage.write_json(storage.state / "booking-intents.json", {
            "schemaVersion": 1, "activeIntentId": intent_id,
            "intents": {intent_id: {
                "id": intent_id, "journey": "customer_training", "status": "booked",
                "trainingTeamId": "women-fighters", "bookerActorId": "king-captain",
                "participants": ["king-captain", "mai"],
                "booking": {"eventId": "event-arcade", "occurrenceKey": "women-arcade@2026-09-20T10:00:00Z", "endTime": "2026-09-20T10:30:00Z"},
                "eventObservation": {"eventId": "event-arcade", "played": False},
            }},
        })
        replay = attach_training_replay(self.root, intent_id=intent_id)
        self.assertEqual("arcade_replay", replay["replay"]["evidence"])
        requested = request_finished_event_result(self.root, now=FIXED_NOW)
        self.assertEqual(intent_id, requested["intentId"])
        winner = replay["replay"]["winner"][0]
        confirmed = report_finished_event_result(self.root, intent_id, "king-captain", winner, FIXED_NOW)
        self.assertEqual("arcade_replay_captain_confirmation", confirmed["evidence"])
        persisted = storage.load_json(storage.state / "booking-intents.json")["intents"][intent_id]
        self.assertFalse(persisted["eventObservation"]["played"])
        self.assertTrue(persisted["eventObservation"]["fictionalPlayed"])
        protocol = storage.read_jsonl(storage.state / "journal" / "facts.jsonl")
        self.assertEqual(
            ["request", "report"],
            [row["payload"]["speechAct"] for row in protocol if row["type"] == "communication.message"],
        )

    def test_arcade_replay_bridge_requires_finished_observed_training(self):
        from podplay_sim_club.arcade import attach_completed_training_replay

        storage = Storage(self.root)
        intent_id = "intent:training:bogard-higashi:arcade-bridge"
        storage.write_json(storage.state / "booking-intents.json", {
            "schemaVersion": 1, "activeIntentId": intent_id,
            "intents": {intent_id: {
                "id": intent_id, "journey": "customer_training", "status": "attendance_observed",
                "bookerActorId": "andy-bogard", "participants": ["andy-bogard", "terry-bogard"],
                "booking": {"eventId": "event-bridge", "occurrenceKey": "bridge@2026-09-20T10:00:00Z", "endTime": "2026-09-20T10:30:00Z"},
                "eventObservation": {"eventId": "event-bridge", "outcome": "attendance_observed", "endTime": "2026-09-20T10:30:00Z", "played": False},
            }},
        })
        before_end = attach_completed_training_replay(self.root, now=datetime(2026, 9, 20, 10, 29, tzinfo=timezone.utc))
        self.assertEqual("pass", before_end["action"])
        attached = attach_completed_training_replay(self.root, now=datetime(2026, 9, 20, 10, 31, tzinfo=timezone.utc))
        self.assertEqual("arcade_replay_attached", attached["action"])
        replay = storage.load_json(storage.state / "booking-intents.json")["intents"][intent_id]["arcadeReplay"]
        self.assertEqual("ready", replay["status"])
        again = attach_completed_training_replay(self.root, now=FIXED_NOW)
        self.assertEqual("pass", again["action"])

    def test_arcade_replay_bridge_derives_doubles_sides_from_named_captains(self):
        from podplay_sim_club.arcade import attach_completed_training_replay

        storage = Storage(self.root)
        intent_id = "intent:challenge:arcade-bridge"
        storage.write_json(storage.state / "booking-intents.json", {
            "schemaVersion": 1, "activeIntentId": intent_id,
            "intents": {intent_id: {
                "id": intent_id, "journey": "customer_team_signup", "status": "attendance_observed",
                "bookerActorId": "andy-bogard",
                "participants": ["andy-bogard", "terry-bogard", "kyo-captain", "benimaru"],
                "challenger": {"captainActorId": "andy-bogard", "teammateActorId": "terry-bogard", "teamName": "Bogard-Higashi"},
                "opponent": {"captainActorId": "kyo-captain", "teammateActorId": "benimaru", "teamName": "Japan Team"},
                "booking": {"eventId": "event-doubles", "occurrenceKey": "doubles@2026-09-20T10:00:00Z", "endTime": "2026-09-20T10:30:00Z"},
                "eventObservation": {"eventId": "event-doubles", "outcome": "attendance_observed", "endTime": "2026-09-20T10:30:00Z", "played": False},
            }},
        })
        attached = attach_completed_training_replay(self.root, now=datetime(2026, 9, 20, 10, 31, tzinfo=timezone.utc))
        self.assertEqual("arcade_replay_attached", attached["action"])
        replay = storage.load_json(storage.state / "arcade" / "matches.json")["matches"]["doubles@2026-09-20T10:00:00Z"]
        self.assertEqual(["andy-bogard", "terry-bogard"], replay["left"])
        self.assertEqual(["kyo-captain", "benimaru"], replay["right"])

    def test_team_member_can_acknowledge_but_not_attest_tournament_result(self):
        storage = Storage(self.root)
        storage.write_json(
            storage.state / "tournament.json",
            {"schemaVersion": 1, "id": "bogard-japan-women-fighters-001", "rounds": {"semi-final": {"status": "awaiting_result"}}},
        )
        reaction = acknowledge_result_request(self.root, "kyo-captain", now=FIXED_NOW)
        self.assertEqual({"status": "acknowledged", "actorId": "kyo-captain"}, reaction)
        with self.assertRaisesRegex(ValueError, "designated reporter"):
            acknowledge_result_request(self.root, "andy-bogard", now=FIXED_NOW)

    def test_next_tournament_tick_requests_owner_creation_of_reported_final(self):
        storage = Storage(self.root)
        storage.write_json(
            storage.state / "tournament.json",
            {"schemaVersion": 1, "id": "bogard-japan-women-fighters-001", "rounds": {"semi-final": {"status": "reported", "winner": "Japan Team"}}},
        )
        first = run_tournament_tick(self.root, now=FIXED_NOW)
        second = run_tournament_tick(self.root, now=FIXED_NOW)

        self.assertEqual("awaiting_event", first["state"]["rounds"]["final"]["status"])
        self.assertEqual("Japan Team", first["state"]["rounds"]["final"]["challenger"])
        self.assertEqual("coordinator", second["action"]["actorId"])
        self.assertEqual("create_open_play", second["action"]["action"])

    def test_team_challenge_records_four_named_players_without_remote_writes(self):
        from podplay_sim_club.team_challenges import next_team_signup_task, plan_challenge

        intent = plan_challenge(
            self.root,
            "andy-vs-kyo-doubles.json",
            FIXED_NOW,
        )

        self.assertEqual("customer_team_signup", intent["journey"])
        self.assertEqual(
            ["andy-bogard", "terry-bogard", "kyo-captain", "benimaru"],
            intent["participants"],
        )
        self.assertEqual("owner_open_play", intent["fallbackJourney"])
        self.assertEqual("andy-bogard", intent["bookerActorId"])
        self.assertEqual(0, intent["remoteWrites"])
        task = next_team_signup_task(self.root)
        self.assertEqual("plan_team_signup_booking", task["action"])
        self.assertEqual(intent["id"], task["intentId"])
        self.assertEqual(intent["participants"], task["participants"])
        replay = plan_challenge(self.root, "andy-vs-kyo-doubles.json", FIXED_NOW)
        self.assertEqual(intent, replay)

    def test_sofia_promotes_any_captain_agreed_challenge_without_booking_it(self):
        from podplay_sim_club.owner_engagement import tick as owner_tick
        from podplay_sim_club.team_challenges import plan_challenge

        intent = plan_challenge(self.root, "andy-vs-kyo-doubles.json", FIXED_NOW)
        result = owner_tick(self.root, now=FIXED_NOW)

        self.assertEqual("promote_captain_agreed_challenge", result["action"])
        self.assertEqual(intent["id"], result["intentId"])
        message = Storage(self.root).read_jsonl(Storage(self.root).channel_path("club"))[-1]
        self.assertEqual("owner_challenge_promotion", message["kind"])
        self.assertEqual(intent["participants"], message["to"])
        stored = load_intent_ledger(Storage(self.root))["intents"][intent["id"]]
        self.assertIn("ownerChallengePromotionAt", stored)
        self.assertEqual(0, stored["remoteWrites"])

    def test_team_signup_task_uses_named_captain_not_participant_order_for_legacy_intent(self):
        from podplay_sim_club.team_challenges import next_team_signup_task, resolve_booking_owner

        storage = Storage(self.root)
        ledger = load_intent_ledger(storage)
        intent = {
            "schemaVersion": 1, "id": "intent:legacy-team", "status": "agreed",
            "journey": "customer_team_signup", "createdAt": "2026-09-30T12:00:00+00:00",
            # Deliberately not captain-first: legacy recovery must use the
            # social challenger role, not this incidental list ordering.
            "participants": ["teammate", "captain", "opponent-captain"],
            "challenger": {"captainActorId": "captain"},
            "constraints": {"durationMinutes": 30},
        }
        save_intent(storage, ledger, intent)

        self.assertEqual("captain", resolve_booking_owner(intent))
        task = next_team_signup_task(self.root)
        self.assertEqual("captain", task["bookerActorId"])
        self.assertEqual(intent["participants"], task["participants"])

    def test_direct_captain_challenge_is_an_invitation_not_a_booking_or_acceptance(self):
        from podplay_sim_club.team_challenges import propose_challenge

        intent = propose_challenge(self.root, "andy-vs-kyo-doubles.json", FIXED_NOW)

        self.assertEqual("captain_invited", intent["status"])
        self.assertEqual("await_opponent_captain_acceptance", intent["nextAction"])
        self.assertEqual(0, intent["remoteWrites"])
        invitation = Storage(self.root).read_jsonl(Storage(self.root).channel_path("club"))[-1]
        self.assertEqual("captain_challenge_invitation", invitation["kind"])
        self.assertEqual(["kyo-captain"], invitation["to"])

    def test_only_addressed_captain_accepts_challenge_and_protocol_projects_it(self):
        from podplay_sim_club.facts import load_facts
        from podplay_sim_club.team_challenges import TeamChallengeError, accept_challenge, propose_challenge

        invited = propose_challenge(self.root, "andy-vs-kyo-doubles.json", FIXED_NOW)
        with self.assertRaises(TeamChallengeError):
            accept_challenge(self.root, invited["id"], "benimaru", FIXED_NOW)
        agreed = accept_challenge(self.root, invited["id"], "kyo-captain", FIXED_NOW)

        self.assertEqual("agreed", agreed["status"])
        facts = load_facts(self.root)
        self.assertEqual(
            ["communication.message", "commitment.proposed", "communication.message", "commitment.accepted"],
            [row["type"] for row in facts],
        )
        projection = Storage(self.root).load_json(Storage(self.root).state / "projections" / "commitments.json")
        commitment = projection["commitments"][invited["commitmentId"]]
        self.assertEqual("accepted", commitment["status"])
        self.assertEqual(["proposed", "accepted"], [row["status"] for row in commitment["transitions"]])

    def test_captain_defers_an_overlapping_challenge_and_riley_can_acknowledge_it(self):
        from podplay_sim_club.front_desk import tick as front_desk_tick
        from podplay_sim_club.social_engine import tick as social_tick
        from podplay_sim_club.team_challenges import (
            TeamChallengeError, accept_challenge, defer_challenge_for_capacity,
            propose_captain_challenge, propose_challenge,
        )

        # Ryu can make the invitation while Andy is still free.  It remains a
        # request, not an agreement or calendar claim.
        pending = propose_captain_challenge(self.root, "ryu", "andy-bogard", FIXED_NOW)
        established = propose_challenge(self.root, "andy-vs-kyo-doubles.json", FIXED_NOW)
        accept_challenge(self.root, established["id"], "kyo-captain", FIXED_NOW)

        # Andy may not turn the second invitation into a concurrent commitment.
        with self.assertRaises(TeamChallengeError):
            accept_challenge(self.root, pending["id"], "andy-bogard", FIXED_NOW)
        deferred = defer_challenge_for_capacity(self.root, pending["id"], "andy-bogard", FIXED_NOW)
        self.assertEqual("captain_deferred", deferred["status"])
        self.assertEqual([established["id"]], deferred["deferredForIntentIds"])

        offers = social_tick(self.root, now=FIXED_NOW)["offers"]
        self.assertTrue(any(row.get("actorId") == "riley" for row in offers))
        reply = front_desk_tick(self.root, now=FIXED_NOW)
        self.assertEqual("challenge_capacity_recorded", reply["action"])
        messages = Storage(self.root).read_jsonl(Storage(self.root).channel_path("club"))
        self.assertEqual("challenge_capacity_request", messages[-2]["kind"])
        self.assertEqual("staff_reply", messages[-1]["kind"])

    def test_commitment_transitions_enforce_actor_authority_and_evidence(self):
        from podplay_sim_club.protocol import (
            ProtocolError, append_protocol_facts_locked, make_commitment_proposal,
            make_commitment_transition, make_message,
        )

        storage = Storage(self.root)
        with storage.writer_lock():
            message = make_message(
                message_id="message:demo:proposal", conversation_id="conversation:demo",
                correlation_id="journey:demo", sender_actor_id="captain-a",
                recipient_actor_ids=["captain-b"], speech_act="propose",
                content_code="demo.challenge", occurred_at="2026-09-23T15:00:00+00:00",
                commitment_id="commitment:demo",
            )
            proposal = make_commitment_proposal(
                commitment_id="commitment:demo", conversation_id="conversation:demo",
                correlation_id="journey:demo", proposer_actor_id="captain-a",
                debtor_actor_id="captain-b", beneficiary_actor_ids=["captain-a", "captain-b"],
                kind="demo", content={"capabilityId": "demo.accept"},
                occurred_at="2026-09-23T15:00:00+00:00", causation_id=message["factId"],
            )
            append_protocol_facts_locked(storage, [message, proposal])
            with self.assertRaises(ProtocolError):
                make_commitment_transition(
                    storage, commitment_id="commitment:demo", transition="accepted",
                    actor_id="captain-a", occurred_at="2026-09-23T15:01:00+00:00",
                    causation_id=message["factId"],
                )
            accepted = make_commitment_transition(
                storage, commitment_id="commitment:demo", transition="accepted",
                actor_id="captain-b", occurred_at="2026-09-23T15:01:00+00:00",
                causation_id=message["factId"],
            )
            append_protocol_facts_locked(storage, [accepted])
            with self.assertRaises(ProtocolError):
                make_commitment_transition(
                    storage, commitment_id="commitment:demo", transition="satisfied",
                    actor_id="lead", occurred_at="2026-09-23T16:00:00+00:00",
                    causation_id=accepted["factId"],
                )
            satisfied = make_commitment_transition(
                storage, commitment_id="commitment:demo", transition="satisfied",
                actor_id="lead", occurred_at="2026-09-23T16:00:00+00:00",
                causation_id=accepted["factId"], evidence_ref="evidence:demo:readback",
            )
            append_protocol_facts_locked(storage, [satisfied])
        projection = storage.load_json(storage.state / "projections" / "commitments.json")
        self.assertEqual("satisfied", projection["commitments"]["commitment:demo"]["status"])

    def test_missed_training_requires_fresh_proposal_and_teammate_acceptance(self):
        from podplay_sim_club.club_rounds import tick as club_round
        from podplay_sim_club.facts import load_facts
        from podplay_sim_club.projections import rebuild_projections
        from podplay_sim_club.social_engine import tick as social_tick

        storage = Storage(self.root)
        ledger = load_intent_ledger(storage)
        source = {
            "schemaVersion": 1,
            "id": "intent:training:women-fighters:missed",
            "status": "missed_session",
            "journey": "customer_training",
            "reason": "captain_teammate_training",
            "trainingTeamId": "women-fighters",
            "bookerActorId": "king-captain",
            "participants": ["king-captain", "mai"],
            "constraints": {"durationMinutes": 30, "daysAhead": [0, 14], "localWindows": ["00:00-24:00"], "slotPolicy": "nearest_valid_low_contention"},
            "eventObservation": {"eventId": "event-missed-training", "outcome": "missed_session"},
            "remoteWrites": 0,
        }
        save_intent(storage, ledger, source)
        storage.append_jsonl(storage.channel_path("club"), {
            "id": "event:event-missed-training:training-missed", "channel": "club", "from": "riley",
            "to": source["participants"], "kind": "training_missed_session", "intentId": source["id"],
            "eventId": "event-missed-training", "observedAt": "2026-09-23T15:00:00Z",
        })
        storage.write_json(storage.state / "social-engine.json", {
            "schemaVersion": 1,
            "lastTickAt": "2026-09-23T15:00:00Z",
            "interactionCooldownSemantics": "action-only",
            "motives": {
                "king-captain": {"desireToPlay": 100, "curiosity": 0, "frustration": 0},
                "mai": {"desireToPlay": 100, "curiosity": 0, "frustration": 0},
            },
        })

        social_tick(self.root, now=FIXED_NOW)
        proposed = club_round(self.root, actor_id="king-captain", now=FIXED_NOW)
        self.assertEqual("propose_session_retry", proposed["action"])
        retry = load_intent_ledger(storage)["intents"][proposed["intentId"]]
        self.assertEqual("captain_invited", retry["status"])
        self.assertNotIn("booking", retry)

        social_tick(self.root, now=FIXED_NOW)
        accepted = club_round(self.root, actor_id="mai", now=FIXED_NOW)
        self.assertEqual("accept_session_retry", accepted["action"])
        retry = load_intent_ledger(storage)["intents"][proposed["intentId"]]
        self.assertEqual("agreed", retry["status"])
        messages = storage.read_jsonl(storage.channel_path("club"))[-2:]
        self.assertEqual(["propose", "accept"], [row["speechAct"] for row in messages])
        self.assertEqual(messages[0]["commitmentId"], messages[1]["commitmentId"])
        facts = load_facts(self.root)
        self.assertEqual(
            ["communication.message", "commitment.proposed", "communication.message", "commitment.accepted"],
            [fact["type"] for fact in facts],
        )
        projection = rebuild_projections(self.root)
        commitment = projection["commitments"]["commitments"][messages[0]["commitmentId"]]
        self.assertEqual("accepted", commitment["status"])
        first = (storage.state / "projections" / "commitments.json").read_bytes()
        rebuild_projections(self.root)
        self.assertEqual(first, (storage.state / "projections" / "commitments.json").read_bytes())

    def test_missed_team_session_can_be_reproposed_by_either_captain(self):
        from podplay_sim_club.session_retries import accept_retry, propose_retry

        storage = Storage(self.root)
        ledger = load_intent_ledger(storage)
        source = {
            "schemaVersion": 1,
            "id": "intent:team-challenge:missed",
            "status": "missed_session",
            "journey": "customer_team_signup",
            "reason": "team_rivalry_doubles",
            "bookerActorId": "andy-bogard",
            "participants": ["andy-bogard", "terry-bogard", "kyo-captain", "benimaru"],
            "challenger": {"teamId": "bogard-higashi", "teamName": "Bogard-Higashi", "captainActorId": "andy-bogard", "teammateActorId": "terry-bogard"},
            "opponent": {"teamId": "japan-team", "teamName": "Japan Team", "captainActorId": "kyo-captain", "teammateActorId": "benimaru"},
            "constraints": {"durationMinutes": 60, "daysAhead": [0, 14], "localWindows": ["00:00-24:00"], "slotPolicy": "nearest_valid_low_contention"},
            "eventObservation": {"eventId": "event-missed-doubles", "outcome": "missed_session"},
            "remoteWrites": 0,
        }
        save_intent(storage, ledger, source)

        invited = propose_retry(self.root, source["id"], "kyo-captain", FIXED_NOW)
        self.assertEqual("kyo-captain", invited["bookerActorId"])
        self.assertEqual("japan-team", invited["challenger"]["teamId"])
        self.assertEqual("captain_invited", invited["status"])
        agreed = accept_retry(self.root, invited["id"], "andy-bogard", FIXED_NOW)
        self.assertEqual("agreed", agreed["status"])
        self.assertEqual("plan_team_signup_booking", agreed["nextAction"])
        self.assertNotIn("booking", agreed)

    def test_fact_journal_is_idempotent_and_rejects_secret_shaped_fields(self):
        from podplay_sim_club.facts import FactValidationError, append_fact, load_facts, make_fact

        fact = make_fact(
            fact_type="belief.observed", natural_key="king:membership:1",
            subject_ref="belief:king:membership", correlation_id="journey:self-discovery:king",
            source_kind="preview_readback", payload={"subject": "membership", "status": "unknown"},
            occurred_at=FIXED_NOW, actor_id="king-captain", privacy="operator",
        )
        self.assertTrue(append_fact(self.root, fact))
        self.assertFalse(append_fact(self.root, fact))
        self.assertEqual([fact], load_facts(self.root))
        with self.assertRaises(FactValidationError):
            make_fact(
                fact_type="belief.observed", natural_key="unsafe",
                subject_ref="belief:unsafe", correlation_id="journey:unsafe",
                source_kind="preview_readback", payload={"password": "never-store-this"},
                occurred_at=FIXED_NOW,
            )
        with self.assertRaises(FactValidationError):
            make_fact(
                fact_type="belief.observed", natural_key="unsafe-url",
                subject_ref="belief:unsafe", correlation_id="journey:unsafe",
                source_kind="preview_readback", payload={"value": "https://preview.invalid/invitation?token=private"},
                occurred_at=FIXED_NOW,
            )

    def test_event_and_participation_projections_keep_people_independent(self):
        from podplay_sim_club.event_lifecycle import (
            ensure_planned_locked, record_booked_locked, record_check_in_locked,
            record_invitation_locked, record_observation_locked, record_result_locked,
            record_result_requested_locked, record_window_locked,
        )
        from podplay_sim_club.projections import rebuild_projections
        from podplay_sim_club.server import (
            _attach_share_invitation_links, _prefer_projected_events,
        )

        storage = Storage(self.root)
        intent = {
            "id": "intent:training:projection-demo", "journey": "customer_training",
            "bookerActorId": "king-captain", "participants": ["king-captain", "mai"],
            "commitmentId": "commitment:training:projection-demo",
        }
        with storage.writer_lock():
            occurrence = ensure_planned_locked(storage, intent, "2026-09-23T15:00:00+00:00", {
                "startTime": "2026-09-23T16:00:00+00:00",
                "endTime": "2026-09-23T16:30:00+00:00", "podRef": "pod-w37",
            })
            record_window_locked(
                storage, occurrence_key=occurrence, intent_id=intent["id"],
                planned_window={
                    "startTime": "2026-09-23T16:15:00+00:00",
                    "endTime": "2026-09-23T16:45:00+00:00", "podRef": "pod-w37",
                },
                occurred_at="2026-09-23T15:02:00+00:00",
            )
            record_booked_locked(
                storage, occurrence_key=occurrence, intent_id=intent["id"],
                event_ref="event-demo", booker_actor_id="king-captain",
                occurred_at="2026-09-23T15:05:00+00:00",
                evidence_ref="evidence:event-demo:booking",
            )
            record_invitation_locked(
                storage, occurrence_key=occurrence, intent_id=intent["id"], actor_id="mai",
                status="ACCEPTED", occurred_at="2026-09-23T15:10:00+00:00",
                evidence_ref="evidence:event-demo:mai-invitation",
            )
            record_check_in_locked(
                storage, occurrence_key=occurrence, intent_id=intent["id"], actor_id="king-captain",
                status="CHECKED_IN", occurred_at="2026-09-23T15:55:00+00:00",
                evidence_ref="evidence:event-demo:king-check-in",
            )
            record_observation_locked(
                storage, occurrence_key=occurrence, intent_id=intent["id"],
                outcome="attendance_inconclusive", occurred_at="2026-09-23T16:31:00+00:00",
                evidence_ref="evidence:event-demo:finished",
                participant_observations={
                    "king-captain": {"accepted": True, "checkIn": "CHECKED_IN"},
                    "mai": {"accepted": True, "checkIn": "NOT_CHECKED_IN"},
                },
            )
            record_result_requested_locked(
                storage, occurrence_key=occurrence, intent_id=intent["id"],
                reporter_actor_id="king-captain", occurred_at="2026-09-23T16:32:00+00:00",
            )
            record_result_locked(
                storage, occurrence_key=occurrence, intent_id=intent["id"],
                reporter_actor_id="king-captain", winner="King",
                evidence_kind="captain_attestation", occurred_at="2026-09-23T16:33:00+00:00",
                evidence_ref="evidence:event-demo:result",
            )

        event = storage.load_json(storage.state / "projections" / "events.json")["events"][occurrence]
        people = storage.load_json(storage.state / "projections" / "participation.json")["participation"]
        king = people[f"participation:{occurrence}:king-captain"]
        mai = people[f"participation:{occurrence}:mai"]
        self.assertEqual("completed", event["status"])
        self.assertEqual("King", event["result"]["winner"])
        self.assertEqual("checked_in", king["attendance"])
        self.assertEqual("inconclusive", mai["attendance"])
        self.assertEqual("accepted", mai["invitation"])
        self.assertTrue(king["resultReporter"])
        self.assertEqual(["participant", "booking_owner"], king["roles"])
        self.assertEqual("2026-09-23T16:15:00+00:00", event["plannedWindow"]["startTime"])

        event_bytes = (storage.state / "projections" / "events.json").read_bytes()
        participation_bytes = (storage.state / "projections" / "participation.json").read_bytes()
        rebuild_projections(self.root)
        self.assertEqual(event_bytes, (storage.state / "projections" / "events.json").read_bytes())
        self.assertEqual(
            participation_bytes,
            (storage.state / "projections" / "participation.json").read_bytes(),
        )

        legacy = {
            "activeOccurrenceKey": occurrence,
            "matches": [{
                "occurrenceKey": occurrence,
                "participants": [{"actorId": "legacy-wrong-person"}],
                "legacyOnly": True,
            }],
        }
        card = _prefer_projected_events(Orchestrator(self.root), legacy)["matches"][0]
        self.assertEqual(["king-captain", "mai"], [row["actorId"] for row in card["participants"]])
        self.assertNotIn("legacyOnly", card)
        self.assertEqual("ACCEPTED", card["lifecycle"]["steps"]["acceptance"])
        self.assertEqual("PARTIAL", card["lifecycle"]["steps"]["checkIn"])
        self.assertEqual("RESULT_REPORTED", card["lifecycle"]["steps"]["play"])
        storage.write_json(storage.state / "share-invitations.json", {
            "invitations": {occurrence: {
                "redeemUrl": "https://preview.invalid/redeem/operator-approved-link",
                "maxRedeems": 3, "redeemsRemaining": 2,
                "capturedAt": "2026-09-23T15:06:00+00:00",
            }},
        })
        attached = _attach_share_invitation_links(
            Orchestrator(self.root), {"activeOccurrenceKey": occurrence, "matches": [card]}
        )
        self.assertEqual(2, attached["matches"][0]["shareInvitation"]["redeemsRemaining"])

    def test_self_asset_evidence_and_beliefs_are_safe_explicit_and_replayable(self):
        from podplay_sim_club.projections import rebuild_projections
        from podplay_sim_club.protocol import append_protocol_facts_locked
        from podplay_sim_club.facts import FactValidationError
        from podplay_sim_club.self_assets import (
            SelfAssetValidationError, make_belief_fact, make_evidence,
            persist_evidence_locked, validate_evidence,
        )

        storage = Storage(self.root)
        zero = make_evidence(
            probe_id="probe:self-assets:credits", target_fingerprint="pr-7200:build-demo",
            capability_id="actor.inspect_self_assets", actor_id="king-captain",
            subject="virtual_credit_balance", observed_at="2026-09-23T15:00:00Z",
            result="observed", observation=0,
        )
        self.assertEqual({"amount": 0.0, "unit": "virtual_credit"}, zero["safeObservation"])
        self.assertEqual(zero, make_evidence(
            probe_id="probe:self-assets:credits", target_fingerprint="pr-7200:build-demo",
            capability_id="actor.inspect_self_assets", actor_id="king-captain",
            subject="virtual_credit_balance", observed_at="2026-09-23T15:00:00Z",
            result="observed", observation=0,
        ))
        tampered = dict(zero)
        tampered["evidenceId"] = "evidence:self-assets:00000000000000000000"
        with self.assertRaises(SelfAssetValidationError):
            validate_evidence(tampered)
        unknown = make_evidence(
            probe_id="probe:self-assets:credits", target_fingerprint="pr-7200:build-demo",
            capability_id="actor.inspect_self_assets", actor_id="mai",
            subject="virtual_credit_balance", observed_at="2026-09-23T15:00:00Z",
            result="unknown", error_code="missing_field",
        )
        self.assertIsNone(unknown["safeObservation"])

        expected = make_belief_fact(
            actor_id="king-captain", subject="virtual_credit_balance", status="expected",
            expected_value={"amount": 20.0, "unit": "virtual_credit"},
            occurred_at="2026-09-23T14:55:00Z", correlation_id="journey:self-assets:king",
            source_kind="actor_fixture",
        )
        observed = make_belief_fact(
            actor_id="king-captain", subject="virtual_credit_balance", status="observed",
            observed_value=zero["safeObservation"], evidence_ref=zero["evidenceId"],
            expires_at="2026-09-23T15:15:00Z", occurred_at="2026-09-23T15:00:00Z",
            correlation_id="journey:self-assets:king", source_kind="preview_readback",
        )
        contradicted = make_belief_fact(
            actor_id="king-captain", subject="virtual_credit_balance", status="contradicted",
            expected_value={"amount": 20.0, "unit": "virtual_credit"},
            observed_value=zero["safeObservation"], evidence_ref=zero["evidenceId"],
            expires_at="2026-09-23T15:15:00Z", occurred_at="2026-09-23T15:00:01Z",
            correlation_id="journey:self-assets:king", source_kind="comparison",
        )
        stale = make_belief_fact(
            actor_id="king-captain", subject="virtual_credit_balance", status="stale",
            occurred_at="2026-09-23T15:15:00Z", correlation_id="journey:self-assets:king",
            source_kind="expiry_policy",
        )
        mai_unknown = make_belief_fact(
            actor_id="mai", subject="virtual_credit_balance", status="unknown",
            evidence_ref=unknown["evidenceId"], occurred_at="2026-09-23T15:00:00Z",
            correlation_id="journey:self-assets:mai", source_kind="preview_readback",
        )
        with storage.writer_lock():
            persist_evidence_locked(storage, zero)
            persist_evidence_locked(storage, unknown)
            append_protocol_facts_locked(
                storage, [expected, observed, contradicted, stale, mai_unknown]
            )

        beliefs = storage.load_json(storage.state / "projections" / "beliefs.json")["beliefs"]
        king = beliefs["belief:king-captain:virtual_credit_balance"]
        mai = beliefs["belief:mai:virtual_credit_balance"]
        self.assertEqual("stale", king["status"])
        self.assertEqual({"amount": 0.0, "unit": "virtual_credit"}, king["observedValue"])
        self.assertEqual({"amount": 20.0, "unit": "virtual_credit"}, king["expectedValue"])
        self.assertEqual(["expected", "observed", "contradicted", "stale"], [row["status"] for row in king["transitions"]])
        self.assertEqual("unknown", mai["status"])
        self.assertIsNone(mai["observedValue"])
        self.assertNotEqual(king["sourceEvidenceRef"], mai["sourceEvidenceRef"])

        first = (storage.state / "projections" / "beliefs.json").read_bytes()
        rebuild_projections(self.root)
        self.assertEqual(first, (storage.state / "projections" / "beliefs.json").read_bytes())

        with self.assertRaises(SelfAssetValidationError):
            make_evidence(
                probe_id="probe:self-assets:membership", target_fingerprint="pr-7200:build-demo",
                capability_id="actor.inspect_self_assets", actor_id="mai",
                subject="membership_assignment", observed_at="2026-09-23T15:00:00Z",
                result="observed", observation={"assigned": True, "email": "private@example.com"},
            )
        with self.assertRaises(FactValidationError):
            make_evidence(
                probe_id="probe:self-assets:membership",
                target_fingerprint="https://preview.invalid/private",
                capability_id="actor.inspect_self_assets", actor_id="mai",
                subject="membership_assignment", observed_at="2026-09-23T15:00:00Z",
                result="observed", observation={"assigned": False},
            )

    def test_self_asset_sanitizers_keep_entitlement_types_separate(self):
        from podplay_sim_club.self_assets import (
            SelfAssetValidationError, make_evidence,
        )

        common = {
            "probe_id": "probe:self-assets:account",
            "target_fingerprint": "pr-7200:build-demo",
            "capability_id": "actor.inspect_self_assets",
            "actor_id": "andy-bogard",
            "observed_at": "2026-09-23T15:00:00Z",
            "result": "observed",
        }
        packages = make_evidence(
            **common,
            subject="owned_package_entitlements",
            observation=[
                {"kind": "play_pass", "label": "Monthly play pass", "remainingQuantity": 2,
                 "unit": "play", "expiresAt": "2026-10-01T00:00:00Z", "status": "active"},
                {"kind": "package_credit", "label": "Training credit", "remainingQuantity": 40,
                 "unit": "credit", "expiresAt": None, "status": "active"},
            ],
        )
        self.assertEqual(
            ["package_credit", "play_pass"],
            [row["kind"] for row in packages["safeObservation"]],
        )
        self.assertEqual([40.0, 2.0], [row["remainingQuantity"] for row in packages["safeObservation"]])

        membership = make_evidence(
            **common, subject="membership_assignment",
            observation={
                "assigned": True, "type": "AREA_MEMBER", "displayName": "Preview Member",
                "isOwner": True, "startDate": "2026-09-01T00:00:00Z",
            },
        )
        self.assertTrue(membership["safeObservation"]["assigned"])
        self.assertNotIn("discount", membership["safeObservation"])

        benefits = make_evidence(
            **common, subject="membership_benefits_understood",
            observation=["Two customer-visible play passes"],
        )
        readiness = make_evidence(
            **common, subject="payment_readiness", observation=True,
        )
        self.assertEqual(["Two customer-visible play passes"], benefits["safeObservation"])
        self.assertEqual({"ready": True}, readiness["safeObservation"])

        with self.assertRaises(SelfAssetValidationError):
            make_evidence(
                **common, subject="owned_package_entitlements",
                observation=[{
                    "id": "raw-product-id", "kind": "play_pass", "label": "Pass",
                    "remainingQuantity": 1, "unit": "play", "expiresAt": None,
                    "status": "active",
                }],
            )

    def test_booking_plan_quote_contract_keeps_unknown_benefits_unknown(self):
        from podplay_sim_club.booking_plans import (
            BookingPlanValidationError, make_booking_plan, make_plan_fact,
            compare_quote_to_payer_beliefs, make_comparison_fact,
            make_quote_evidence, make_quote_fact,
        )
        from podplay_sim_club.facts import append_facts_locked
        from podplay_sim_club.projections import rebuild_projections_locked

        plan = make_booking_plan(
            occurrence_key="occurrence:training:bogard-higashi:007",
            owner_actor_id="andy-bogard",
            participant_actor_ids=["andy-bogard", "terry-bogard"],
            payer_actor_id="andy-bogard",
            venue_ref="venue:w37:table-1",
            starts_at="2026-10-07T19:00:00Z",
            ends_at="2026-10-07T19:30:00Z",
        )
        self.assertEqual("owner_pays", plan["requestedFunding"]["chargeStrategy"])
        self.assertEqual("andy-bogard", plan["payerActorId"])

        fields = {
            "total": {"status": "observed", "value": 18.0},
            "currency": {"status": "observed", "value": "USD"},
            "membershipTreatment": {"status": "unknown", "value": None},
            "virtualCreditTreatment": {"status": "not_applicable", "value": None},
            "passTreatment": {"status": "unknown", "value": None},
            "capacityTreatment": {"status": "observed", "value": "two_players_allowed"},
            "cancellationTerms": {"status": "unknown", "value": None},
            "blockingCodes": {"status": "observed", "value": []},
        }
        evidence = make_quote_evidence(
            booking_plan=plan, observed_at="2026-10-07T18:00:00Z",
            result="observed", fields=fields,
        )
        storage = Storage(self.root)
        with storage.writer_lock():
            append_facts_locked(storage, [
                make_plan_fact(plan=plan, occurred_at="2026-10-07T17:59:00Z", correlation_id="journey:007"),
                make_quote_fact(evidence=evidence, occurred_at="2026-10-07T18:00:00Z", correlation_id="journey:007"),
            ])
            rebuild_projections_locked(storage)
        projection = storage.load_json(storage.state / "projections" / "booking-plans.json")
        row = projection["bookingPlans"][plan["bookingPlanId"]]
        self.assertEqual("awaiting_payer_approval", row["stage"])
        self.assertEqual("unknown", row["quote"]["fields"]["passTreatment"]["status"])
        self.assertIsNone(row["quote"]["fields"]["membershipTreatment"]["value"])

        # A verified product write advances the operational stage, without
        # erasing the separate quote and payer-approval record.
        from podplay_sim_club.facts import make_fact
        with storage.writer_lock():
            append_facts_locked(storage, [
                make_fact(
                    fact_type="event.booked", natural_key="training-007-booked",
                    subject_ref=plan["occurrenceKey"], correlation_id="journey:007",
                    source_kind="preview_readback", evidence_ref="evidence:preview:event:training-007",
                    payload={"productEventRef": "event:training-007"},
                    occurred_at="2026-10-07T18:04:00Z", actor_id="lead", privacy="community",
                ),
            ])
            rebuild_projections_locked(storage)
        row = storage.load_json(storage.state / "projections" / "booking-plans.json")["bookingPlans"][plan["bookingPlanId"]]
        self.assertEqual("executed", row["stage"])
        self.assertEqual("event:training-007", row["execution"]["productEventRef"])

        from podplay_sim_club.inspectors import booking_plan_inspector, inspection_index, player_inspector
        inspected_plan = booking_plan_inspector(self.root, plan["bookingPlanId"])
        self.assertEqual("andy-bogard", inspected_plan["payerActorId"])
        self.assertEqual("unknown", inspected_plan["quote"]["fields"]["membershipTreatment"]["status"])
        andy = player_inspector(self.root, "andy-bogard")
        self.assertEqual("Andy Bogard", andy["label"])
        self.assertIn(plan["bookingPlanId"], [row["bookingPlanId"] for row in inspection_index(self.root)["bookingPlans"]])

        comparison = compare_quote_to_payer_beliefs(
            booking_plan=plan, quote_evidence=evidence, assessed_at="2026-10-07T18:02:00Z",
            beliefs=[
                {"actorId": "andy-bogard", "subject": "acceptable_booking_price", "status": "expected", "expectedValue": {"maxAmount": 20.0, "currency": "USD"}},
                {"actorId": "andy-bogard", "subject": "payment_readiness", "status": "observed", "observedValue": {"ready": True}},
            ],
        )
        self.assertEqual("within_expectation", comparison["status"])
        with storage.writer_lock():
            append_facts_locked(storage, [
                make_comparison_fact(comparison=comparison, occurred_at="2026-10-07T18:02:00Z", correlation_id="journey:007"),
            ])
            rebuild_projections_locked(storage)
        self.assertEqual(
            "within_expectation",
            storage.load_json(storage.state / "projections" / "booking-plans.json")["bookingPlans"][plan["bookingPlanId"]]["comparison"]["status"],
        )
        from podplay_sim_club.payer_approval import PayerApprovalError, accept as accept_payer_approval, require_accepted, request as request_payer_approval
        requested = request_payer_approval(self.root, plan["bookingPlanId"], "2026-10-07T18:03:00Z")
        self.assertEqual("andy-bogard", requested["payerActorId"])
        with self.assertRaises(PayerApprovalError):
            accept_payer_approval(self.root, plan["bookingPlanId"], "terry-bogard", "2026-10-07T18:03:30Z")
        from podplay_sim_club.club_rounds import tick as player_round
        from podplay_sim_club.social_engine import tick as social_tick
        approval_time = datetime(2026, 10, 7, 18, 3, 30, tzinfo=timezone.utc)
        offers = social_tick(self.root, now=approval_time)["offers"]
        self.assertTrue(any(
            row.get("actorId") == "andy-bogard"
            and row.get("kind") == "approve_booking_plan"
            and row.get("bookingPlanId") == plan["bookingPlanId"]
            for row in offers
        ))
        accepted = player_round(self.root, actor_id="andy-bogard", now=approval_time)
        self.assertEqual("approve_booking_plan", accepted["action"])
        self.assertEqual("accepted", accepted["approval"])
        self.assertEqual(
            "andy-bogard",
            require_accepted(self.root, plan["bookingPlanId"], "2026-10-07T18:04:00Z")["payerActorId"],
        )
        with self.assertRaises(PayerApprovalError):
            require_accepted(self.root, plan["bookingPlanId"], "2026-10-07T18:18:01Z")
        self.assertEqual(
            "accepted", booking_plan_inspector(self.root, plan["bookingPlanId"])["payerApproval"]["status"]
        )

        mismatch = compare_quote_to_payer_beliefs(
            booking_plan=plan, quote_evidence=evidence, assessed_at="2026-10-07T18:03:00Z",
            beliefs=[
                {"actorId": "andy-bogard", "subject": "acceptable_booking_price", "status": "expected", "expectedValue": {"maxAmount": 10.0, "currency": "USD"}},
                {"actorId": "andy-bogard", "subject": "payment_readiness", "status": "observed", "observedValue": {"ready": True}},
            ],
        )
        self.assertEqual("mismatch", mismatch["status"])
        self.assertEqual(["clarify", "counter", "pass", "ask_riley"], mismatch["recommendedActions"])

        with self.assertRaises(BookingPlanValidationError):
            make_quote_evidence(
                booking_plan=plan, observed_at="2026-10-07T18:01:00Z", result="observed",
                fields={**fields, "passTreatment": {"status": "unknown", "value": "free"}},
            )
        with self.assertRaises(BookingPlanValidationError):
            make_booking_plan(
                occurrence_key="occurrence:invalid", owner_actor_id="andy-bogard",
                participant_actor_ids=["andy-bogard", "terry-bogard"], payer_actor_id="mai",
                venue_ref="venue:w37:table-1", starts_at="2026-10-07T19:00:00Z",
                ends_at="2026-10-07T19:30:00Z", charge_strategy="owner_pays",
                benefit_strategy="use_none", stage="approved",
            )

    def test_cli_player_inspector_renders_json(self):
        output = StringIO()
        with redirect_stdout(output):
            result = main(["--home", str(self.root), "inspect", "player", "--actor", "andy-bogard"])
        self.assertEqual(0, result)
        self.assertEqual("andy-bogard", json.loads(output.getvalue())["actorId"])

    def test_cli_booking_plan_list_renders_safe_empty_index(self):
        output = StringIO()
        with redirect_stdout(output):
            result = main(["--home", str(self.root), "inspect", "booking-plans"])
        self.assertEqual(0, result)
        self.assertEqual({"bookingPlans": []}, json.loads(output.getvalue()))

    def test_cli_price_expectation_is_a_local_belief_not_a_product_write(self):
        output = StringIO()
        with redirect_stdout(output):
            result = main([
                "--home", str(self.root), "expect", "booking-price", "--actor", "andy-bogard",
                "--max-amount", "24", "--currency", "USD", "--at", "2026-10-07T18:00:00Z",
            ])
        self.assertEqual(0, result)
        self.assertEqual(0, json.loads(output.getvalue())["remoteWrites"])
        beliefs = Storage(self.root).load_json(Path(self.root) / "state" / "projections" / "beliefs.json")["beliefs"]
        self.assertEqual({"maxAmount": 24.0, "currency": "USD"}, beliefs["belief:andy-bogard:acceptable_booking_price"]["expectedValue"])

    def test_capability_catalog_is_role_general_and_contains_no_character_ids(self):
        catalog = json.loads((REPO_ROOT / "capabilities" / "catalog.json").read_text())
        roster = json.loads((REPO_ROOT / "characters" / "roster.json").read_text())
        serialized = json.dumps(catalog)
        self.assertEqual(1, catalog["schemaVersion"])
        self.assertEqual(len(catalog["capabilities"]), len({row["id"] for row in catalog["capabilities"]}))
        for character in roster["characters"]:
            actor_id = character.get("actorId")
            if isinstance(actor_id, str):
                self.assertNotIn(f'"{actor_id}"', serialized)

    def orchestrator(self):
        return Orchestrator(self.root)

    def test_hourly_match_completes_once_and_same_hour_is_noop(self):
        orchestrator = self.orchestrator()

        first = orchestrator.run_beat(turns=10, now=FIXED_NOW)
        second = orchestrator.run_beat(turns=10, now=FIXED_NOW)

        self.assertEqual("complete", first["status"])
        self.assertEqual(8, first["turnsExecuted"])
        self.assertEqual(0, second["turnsExecuted"])
        preview = json.loads((self.root / "state" / "fake-preview.json").read_text())
        self.assertEqual(1, len(preview["bookings"]))
        self.assertEqual(
            ["andy-bogard", "terry-bogard"],
            sorted(preview["bookings"][0]["checkedIn"]),
        )

    def test_need_drives_conversation_and_durable_booking_intent(self):
        orchestrator = self.orchestrator()

        first = orchestrator.run_needs_beat(turns=2, now=FIXED_NOW)
        resumed = orchestrator.run_needs_beat(turns=2, now=FIXED_NOW)
        replay = orchestrator.run_needs_beat(turns=4, now=FIXED_NOW)

        self.assertEqual("running", first["status"])
        self.assertEqual("complete", resumed["status"])
        self.assertEqual(0, replay["turnsExecuted"])
        messages = orchestrator.storage.read_jsonl(
            orchestrator.storage.channel_path("club")
        )
        self.assertEqual(
            ["need_driven_proposal", "availability_agreement"],
            [message["kind"] for message in messages],
        )
        ledger = load_intent_ledger(orchestrator.storage)
        self.assertEqual(1, len(ledger["intents"]))
        intent = next(iter(ledger["intents"].values()))
        self.assertEqual("agreed", intent["status"])
        self.assertEqual(0, intent["remoteWrites"])
        self.assertEqual(
            ["andy-bogard", "terry-bogard"], intent["participants"]
        )
        preview = json.loads((self.root / "state" / "fake-preview.json").read_text())
        self.assertEqual([], preview["bookings"])
        frame = render_needs(resumed["world"])
        self.assertIn("desire-to-play 85/80  READY", frame)
        self.assertIn("BOOKING INTENTS", frame)

    def test_owner_promotion_creates_a_flexible_near_term_intent(self):
        orchestrator = self.orchestrator()

        first = orchestrator.run_promotion_beat(turns=3, now=FIXED_NOW)
        resumed = orchestrator.run_promotion_beat(turns=3, now=FIXED_NOW)
        replay = orchestrator.run_promotion_beat(turns=4, now=FIXED_NOW)

        self.assertEqual("running", first["status"])
        self.assertEqual("complete", resumed["status"])
        self.assertEqual(0, replay["turnsExecuted"])
        messages = orchestrator.storage.read_jsonl(
            orchestrator.storage.channel_path("club")
        )
        self.assertEqual(
            ["owner_announcement", "promotion_response", "promotion_response"],
            [message["kind"] for message in messages],
        )
        intent = next(iter(load_intent_ledger(orchestrator.storage)["intents"].values()))
        self.assertEqual("owner_priority_announcement", intent["reason"])
        self.assertEqual(["00:00-24:00"], intent["constraints"]["localWindows"])
        self.assertEqual(1, len(resumed["world"]["campaigns"]))
        intent["status"] = "booked"
        save_intent(orchestrator.storage, load_intent_ledger(orchestrator.storage), intent)
        self.assertEqual(
            "booked", orchestrator.world_view(FIXED_NOW)["campaigns"][0]["status"]
        )
        self.assertIn("OWNER CAMPAIGNS", render_needs(resumed["world"]))

    def test_actor_runtime_records_a_free_hour_intent_and_then_passes(self):
        first = run_tick(self.root, max_turns=6, now=FIXED_NOW)
        replay = run_tick(self.root, max_turns=6, now=FIXED_NOW)

        self.assertEqual(0, first["remoteWrites"])
        self.assertEqual(
            ["sofia", "andy-bogard", "terry-bogard", "alex", "riley", "lead"],
            [turn["actorId"] for turn in first["turns"]],
        )
        self.assertEqual("intent", first["turns"][-1]["action"])
        self.assertTrue(all(turn["action"] == "pass" for turn in replay["turns"]))
        ledger = load_intent_ledger(Storage(self.root))
        intent = ledger["intents"]["intent:actor-runtime:season-001:free-hour-001"]
        self.assertEqual(60, intent["constraints"]["durationMinutes"])
        self.assertEqual("owner_open_play", intent["journey"])
        self.assertTrue(intent["constraints"]["freeToParticipants"])
        self.assertTrue((self.root / "state" / "actors" / "sofia" / "memory.md").is_file())
        intent["status"] = "event_created"
        save_intent(Storage(self.root), ledger, intent)
        promoted = run_tick(self.root, max_turns=3, now=FIXED_NOW)
        self.assertEqual(["message", "message", "message"], [turn["action"] for turn in promoted["turns"]])
        messages = Storage(self.root).read_jsonl(Storage(self.root).channel_path("club"))
        self.assertTrue({"runtime_announcement", "runtime_andy_signup", "runtime_terry_signup"}.issubset({message["kind"] for message in messages}))

    def test_blocked_intent_plan_records_a_durable_issue(self):
        from podplay_sim_club.cli import _record_intent_blocker

        storage = Storage(self.root)
        intent = {
            "id": "intent:actor-runtime:season-001:free-hour-001",
            "reason": "owner_free_hour_announcement",
            "constraints": {"durationMinutes": 60},
            "previewPlan": {"blocker": "no_slot_matches_character_availability", "observedAt": "2026-09-25T12:00:00Z"},
        }

        _record_intent_blocker(storage, intent, 7200)

        issue = storage.load_json(storage.state / "issues" / "open" / "intent-blocked-intent-actor-runtime-season-001-free-hour-001.json")
        self.assertEqual("no_matching_preview_session", issue["code"])
        self.assertEqual(0, issue["remoteWrites"])

    def test_orchestrator_uses_the_preview_adapter_boundary(self):
        created = []

        def factory(storage, seed):
            adapter = FakePreview(storage, seed)
            created.append(adapter)
            return adapter

        result = Orchestrator(self.root, preview_factory=factory).run_beat(
            turns=10, now=FIXED_NOW
        )

        self.assertEqual("complete", result["status"])
        self.assertTrue(created)
        self.assertTrue(all(isinstance(item, PreviewAdapter) for item in created))

    def test_non_fake_runtime_cannot_fall_back_to_fake(self):
        stderr = StringIO()
        with patch.dict(
            os.environ, {"PODPLAY_SIM_MODE": "preview-readonly"}, clear=True
        ):
            with redirect_stderr(stderr):
                result = main(["--home", str(self.root), "status"])

        self.assertEqual(2, result)
        self.assertIn("preview execution is not implemented", stderr.getvalue())
        self.assertFalse((self.root / "state").exists())

    def test_commit_before_checkpoint_reconciles_without_duplicate_booking(self):
        orchestrator = self.orchestrator()

        with self.assertRaises(SimulatedCrash):
            orchestrator.run_beat(
                turns=10, now=FIXED_NOW, crash_after_remote="booking_created"
            )

        preview_after_crash = json.loads(
            (self.root / "state" / "fake-preview.json").read_text()
        )
        self.assertEqual(1, len(preview_after_crash["bookings"]))

        resumed = orchestrator.run_beat(turns=10, now=FIXED_NOW)
        preview_after_resume = json.loads(
            (self.root / "state" / "fake-preview.json").read_text()
        )
        self.assertEqual("complete", resumed["status"])
        self.assertEqual(1, len(preview_after_resume["bookings"]))

    def test_fresh_preview_starts_new_season_and_preserves_actor_journal(self):
        orchestrator = self.orchestrator()
        orchestrator.run_beat(turns=10, now=FIXED_NOW)
        journal = self.root / "state" / "actors" / "andy-bogard" / "journal.jsonl"
        original_journal = journal.read_text()

        orchestrator.reset_fake_preview()
        view = orchestrator.world_view(FIXED_NOW)

        self.assertEqual("season-002", view["season"])
        self.assertEqual(original_journal, journal.read_text())
        preview = json.loads((self.root / "state" / "fake-preview.json").read_text())
        self.assertEqual([], preview["bookings"])
        self.assertEqual("preview-club-v1", preview["seedFingerprint"])
        archive = self.root / "state" / "seasons" / "season-001" / "world-final.json"
        self.assertTrue(archive.is_file())

    def test_actor_cannot_choose_another_pod(self):
        validator = ActionValidator("pod-1", ["book_match"])
        with self.assertRaises(IllegalAction):
            validator.validate(
                {"type": "book_match", "arguments": {"podId": "pod-other"}}
            )

    def test_terminal_contains_world_hints(self):
        orchestrator = self.orchestrator()
        orchestrator.run_beat(turns=10, now=FIXED_NOW)
        frame = render(orchestrator.world_view(FIXED_NOW))
        self.assertIn("CLUB LAB // WORLD SIGNAL", frame)
        self.assertIn("POD-1 [A] ⇄ [T] occupied", frame)
        self.assertIn("BOOKING TRACE", frame)

    def test_observatory_serves_preview_snapshot_and_health(self):
        orchestrator = self.orchestrator()
        orchestrator.run_beat(turns=2, now=FIXED_NOW)
        orchestrator.storage.write_json(
            orchestrator.storage.state / "preview-match-status.json",
            {
                "schemaVersion": 1,
                "observedAt": "2026-09-24T17:00:00+00:00",
                "pullRequest": 7200,
                "result": "SCHEDULED",
                "event": {
                    "eventId": "event-live",
                    "status": "CONFIRMED",
                    "type": "REGULAR",
                    "subtype": "PRIVATE",
                    "startTime": "2026-09-26T12:00:00.000Z",
                    "endTime": "2026-09-26T12:30:00.000Z",
                },
                "venue": {
                    "areaName": "East 95th",
                    "podName": "Test",
                    "timezone": "America/New_York",
                    "courtAssignment": "Auto-assigned court",
                },
                "journey": {
                    "phase": "joined",
                    "preparedAt": "2026-09-24T17:00:00+00:00",
                    "occurrenceKey": "pp7444-test",
                    "orderTotal": 0,
                    "currency": "USD",
                    "virtualCreditsUsed": 8.8,
                },
                "blueInvitation": {"status": "ACCEPTED"},
                "checkIns": {
                    "redCaptain": "NOT_CHECKED_IN",
                    "blueCaptain": "NOT_CHECKED_IN",
                },
            },
        )
        orchestrator.storage.append_jsonl(
            orchestrator.storage.channel_path("club"),
            {"id": "signal-1", "kind": "invite_sent", "from": "andy-bogard", "to": ["terry-bogard"], "observedAt": "2026-09-25T12:00:00Z"},
        )
        orchestrator.storage.write_json(
            orchestrator.storage.state / "booking-intents.json",
            {
                "schemaVersion": 1,
                "activeIntentId": "intent:team-challenge:andy-vs-kyo",
                "intents": {
                    "intent:team-challenge:andy-vs-kyo": {
                        "id": "intent:team-challenge:andy-vs-kyo",
                        "status": "agreed",
                        "reason": "team_rivalry_doubles",
                        "journey": "customer_team_signup",
                        "participants": ["andy-bogard", "terry-bogard", "kyo-captain", "benimaru"],
                        "constraints": {"durationMinutes": 60},
                        "nextAction": "seed_japan_team_then_plan_team_signup",
                    }
                },
            },
        )
        orchestrator.storage.write_json(
            orchestrator.storage.state / "preview-matches.json",
            {
                "schemaVersion": 2,
                "targetOrigin": PREVIEW_ORIGIN,
                "activeOccurrenceKey": "pp7444-test",
                "matches": {
                    "pp7444-test": {
                        "phase": "joined",
                        "eventId": "event-live",
                        "plan": {
                            "occurrenceKey": "pp7444-test",
                            "podId": "pod-test",
                            "startTime": "2026-09-26T12:00:00.000Z",
                            "endTime": "2026-09-26T12:30:00.000Z",
                        },
                        "event": {"status": "CONFIRMED"},
                    }
                },
            },
        )
        orchestrator.storage.write_json(
            orchestrator.storage.state / "preview-readiness.json",
            {
                "location": {
                    "podId": "pod-test",
                    "areaName": "East 95th",
                    "podName": "Test",
                    "timezone": "America/New_York",
                }
            },
        )
        orchestrator.storage.write_json(
            orchestrator.storage.state / "tournament.json",
            {"schemaVersion": 1, "rounds": {"semi-final": {"status": "awaiting_result"}}},
        )
        orchestrator.storage.write_json(
            orchestrator.storage.root / "secrets" / "unified-research-campaigns.json",
            {
                "campaigns": {
                    "peak": {
                        "owner": "kyo-captain",
                        "teammate": "benimaru",
                        "band": "peak",
                        "startsAt": "2026-09-26T12:00:00.000Z",
                        "endsAt": "2026-09-26T12:30:00.000Z",
                        "lastObservedStatus": "CONFIRMED",
                        "checkIns": {"kyo-captain": "confirmed"},
                        "bookingId": "private-booking-id",
                        "inviteToken": "private-invite-token",
                    }
                }
            },
        )
        server = ObservatoryServer(orchestrator, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.address
        try:
            with urlopen(f"http://{host}:{port}/health", timeout=2) as response:
                health = json.load(response)
            with urlopen(f"http://{host}:{port}/api/preview", timeout=2) as response:
                preview = json.load(response)
            request = Request(
                f"http://{host}:{port}/api/tournament/acknowledge",
                data=json.dumps({"actorId": "kyo-captain"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=2) as response:
                acknowledgement = json.load(response)
            request = Request(
                f"http://{host}:{port}/api/tournament/report",
                data=json.dumps({"reporter": "andy-bogard", "winner": "Japan Team"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=2) as response:
                captain_report = json.load(response)
            with urlopen(f"http://{host}:{port}/", timeout=2) as response:
                page = response.read().decode("utf-8")
        finally:
            server.shutdown()
            thread.join(timeout=2)

        self.assertEqual({"mode": "preview-observatory", "ok": True}, health)
        self.assertEqual("SCHEDULED", preview["previewMatch"]["result"])
        self.assertEqual("pp7444-test", preview["previewMatches"]["activeOccurrenceKey"])
        self.assertEqual(1, len(preview["previewMatches"]["matches"]))
        self.assertEqual("EVT-EVENTLIV", preview["previewMatches"]["matches"][0]["eventCode"])
        self.assertNotIn("previewOrigin", preview)
        self.assertNotIn("eventUrl", preview["previewMatches"]["matches"][0])
        self.assertEqual("Andy Bogard", preview["previewMatches"]["matches"][0]["participants"][0]["label"])
        self.assertNotIn("captainAction", preview)
        self.assertEqual({"status": "acknowledged", "actorId": "kyo-captain"}, acknowledgement)
        self.assertEqual({"status": "reported", "winner": "Japan Team"}, captain_report)
        self.assertEqual(
            ["Andy Bogard", "Terry Bogard", "Kyo Kusanagi", "Benimaru Nikaido"],
            preview["storylines"][0]["participants"],
        )
        self.assertIn("invite_sent", [message["kind"] for message in preview["messages"]])
        self.assertEqual("Andy Bogard", next(message for message in preview["messages"] if message["kind"] == "invite_sent")["fromLabel"])
        self.assertIn("community", preview)
        self.assertEqual(
            [{
                "campaign": "rate-cards-unified-pricing",
                "label": "Peak training",
                "participants": ["kyo-captain", "benimaru"],
                "checkedIn": ["kyo-captain"],
                "startsAt": "2026-09-26T12:00:00.000Z",
                "endsAt": "2026-09-26T12:30:00.000Z",
                "bookingStatus": "CONFIRMED",
            }],
            preview["researchActivity"]["campaigns"],
        )
        self.assertNotIn("private-booking-id", json.dumps(preview))
        self.assertNotIn("private-invite-token", json.dumps(preview))
        self.assertEqual(
            [{
                "area": "East 95th",
                "pod": "Test",
                "occupants": [],
                "scheduledCount": 1,
                "historyCount": 0,
                "state": "scheduled",
            }],
            preview["venueActivity"]["pods"],
        )
        self.assertNotIn("pod-test", json.dumps(preview["venueActivity"]))
        self.assertIn("initiatives", preview)
        self.assertIn("invitationConversations", preview)
        self.assertEqual("Andy Bogard", next(person for person in preview["community"]["people"] if person["actorId"] == "andy-bogard")["label"])
        self.assertIn("PODPLAY SIM CLUB // COMMUNITY OBSERVATORY", page)
        self.assertIn("COMMUNITY CONVERSATION", page)
        self.assertIn('class="sim-inspect"', page)
        self.assertNotIn("SIMS · STATUS &amp; MOTIVES", page)
        self.assertIn("STAFF / COMMUNITY INITIATIVES", page)
        self.assertIn("INVITATION CONVERSATIONS", page)
        self.assertIn("BOOKINGS &amp; EVENTS", page)
        self.assertNotIn('id="preview-server"', page)
        self.assertIn('id="product-health"', page)
        self.assertNotIn("Open this event in Preview", page)
        self.assertIn("private Preview locations stay out", page)
        self.assertIn("if(node.innerHTML!==html)", page)
        self.assertNotIn("LOCAL FAKE", page)
        self.assertNotIn("CHARACTER NEEDS", page)
        self.assertNotIn("BOOKING INTENTS", page)


class PreviewMatchLedgerTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.storage = Storage(self.root)
        self.origin = PREVIEW_ORIGIN

    def tearDown(self):
        self.temporary.cleanup()

    def match(self, key, start, event_id):
        return {
            "schemaVersion": 2,
            "targetOrigin": self.origin,
            "phase": "booked",
            "eventId": event_id,
            "plan": {
                "occurrenceKey": key,
                "podId": "pod-test",
                "startTime": start,
                "endTime": start,
            },
            "event": {"status": "CONFIRMED"},
        }

    def test_migrates_single_match_checkpoint_without_losing_it(self):
        legacy = self.match("first", "2026-09-26T12:00:00Z", "event-1")
        legacy["schemaVersion"] = 1
        self.storage.write_json(
            self.storage.state / "preview-manual-match.json", legacy
        )

        ledger = load_match_ledger(self.storage, self.origin)

        self.assertEqual(2, ledger["schemaVersion"])
        self.assertEqual("first", ledger["activeOccurrenceKey"])
        self.assertEqual("event-1", ledger["matches"]["first"]["eventId"])
        self.assertTrue((self.storage.state / "preview-matches.json").is_file())

    def test_upsert_is_occurrence_idempotent_and_active_is_explicit(self):
        ledger = load_match_ledger(self.storage, self.origin)
        save_match(
            self.storage,
            ledger,
            self.match("first", "2026-09-26T12:00:00Z", "event-1"),
        )
        save_match(
            self.storage,
            ledger,
            self.match("second", "2026-09-26T12:30:00Z", "event-2"),
        )
        save_match(
            self.storage,
            ledger,
            self.match("second", "2026-09-26T12:30:00Z", "event-2"),
        )

        self.assertEqual(2, len(ledger["matches"]))
        self.assertEqual("second", select_match(ledger)[0])
        select_active(self.storage, ledger, "first")
        self.assertEqual("first", select_match(ledger)[0])
        index = sanitized_match_index(ledger)
        self.assertEqual(["first", "second"], [row["occurrenceKey"] for row in index["matches"]])
        self.assertTrue(index["matches"][0]["active"])


class TargetPolicyTestCase(unittest.TestCase):
    def config(self, mode=RunMode.PREVIEW_READONLY, **changes):
        values = {
            "mode": mode,
            "preview_origin": PREVIEW_ORIGIN,
            "allowed_preview_origins": (PREVIEW_ORIGIN,),
            "preview_write_confirmation": None,
        }
        values.update(changes)
        return ClubConfig(**values)

    def test_exact_pr_preview_origin_is_approved(self):
        policy = TargetPolicy.from_config(self.config())

        self.assertEqual(5207, policy.pull_request_number)
        self.assertEqual(PREVIEW_ORIGIN, policy.target_origin)
        policy.assert_url(f"{PREVIEW_ORIGIN}/apis/v2/users?limit=1")

    def test_shared_staging_is_rejected_even_when_allowlisted(self):
        origin = "https://pingpod-staging.podplay.app"
        with self.assertRaises(TargetPolicyError):
            TargetPolicy.from_config(
                self.config(
                    preview_origin=origin,
                    allowed_preview_origins=(origin,),
                )
            )

    def test_production_is_rejected_even_when_allowlisted(self):
        origin = "https://pingpod.podplay.app"
        with self.assertRaises(TargetPolicyError):
            TargetPolicy.from_config(
                self.config(
                    preview_origin=origin,
                    allowed_preview_origins=(origin,),
                )
            )

    def test_preview_must_be_in_exact_allowlist(self):
        with self.assertRaises(TargetPolicyError):
            TargetPolicy.from_config(self.config(allowed_preview_origins=()))

    def test_preview_origin_cannot_contain_a_path(self):
        with self.assertRaises(TargetPolicyError):
            TargetPolicy.from_config(
                self.config(preview_origin=f"{PREVIEW_ORIGIN}/admin")
            )

    def test_preview_write_requires_exact_origin_confirmation(self):
        with self.assertRaises(TargetPolicyError):
            TargetPolicy.from_config(self.config(mode=RunMode.PREVIEW_WRITE))

        policy = TargetPolicy.from_config(
            self.config(
                mode=RunMode.PREVIEW_WRITE,
                preview_write_confirmation=PREVIEW_ORIGIN,
            )
        )
        self.assertEqual(PREVIEW_ORIGIN, policy.target_origin)
        self.assertTrue(policy.writes_allowed)

    def test_cross_origin_request_or_redirect_is_rejected(self):
        policy = TargetPolicy.from_config(self.config())
        with self.assertRaises(TargetPolicyError):
            policy.assert_url(
                "https://podify-pr-5208-staging-main-service-bay66ucqza-uk.a.run.app/"
            )

    def test_environment_configuration_is_explicit(self):
        config = ClubConfig.from_environment(
            {
                "PODPLAY_SIM_MODE": "preview-readonly",
                "PODPLAY_SIM_PREVIEW_ORIGIN": PREVIEW_ORIGIN,
                "PODPLAY_SIM_ALLOWED_PREVIEW_ORIGINS": PREVIEW_ORIGIN,
                "PODPLAY_SIM_ADMIN_EMAIL": "admin@example.test",
            }
        )

        self.assertIs(config.mode, RunMode.PREVIEW_READONLY)
        self.assertEqual((PREVIEW_ORIGIN,), config.allowed_preview_origins)
        self.assertEqual("admin@example.test", config.admin_email)

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ConfigurationError):
            ClubConfig.from_environment({"PODPLAY_SIM_MODE": "production"})


class PreviewConnectionTestCase(unittest.TestCase):
    def test_credentials_require_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secrets = root / "secrets"
            secrets.mkdir()
            path = secrets / "preview-credentials.json"
            path.write_text(json.dumps({}), encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaises(CredentialsError):
                PreviewCredentials.load(root)

    def test_firebase_auth_uses_private_cache_then_reuses_it(self):
        calls = []
        now = 1_700_000_000
        payload = base64_url({"exp": now + 3600})
        token = f"header.{payload}.signature"

        def request_json(url, body):
            calls.append((url, body))
            return {
                "idToken": token,
                "refreshToken": "refresh-value",
                "email": "admin@example.test",
            }

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "auth.json"
            authenticator = FirebaseAuthenticator(cache, request_json, now=lambda: now)
            first = authenticator.authenticate("admin@example.test", "password", "api-key")
            second = authenticator.authenticate("admin@example.test", "password", "api-key")

            self.assertEqual("password", first.source)
            self.assertEqual("cache", second.source)
            self.assertEqual(1, len(calls))
            self.assertEqual(0o600, cache.stat().st_mode & 0o777)
            self.assertNotIn("password", cache.read_text(encoding="utf-8"))

    def test_player_login_summary_matches_the_registry_without_echoing_secrets(self):
        identity = ActorIdentity("andy-bogard", "hidden@example.test", "Andy", "Bogard", "secret-value")
        session = AuthSession("hidden@example.test", "cache", "token-value", "firebase-uid")
        summary = verify_player_session(
            "andy-bogard",
            identity,
            session,
            {"id": "user-1", "email": "hidden@example.test", "roles": ["PLAYER"]},
        )

        self.assertEqual("user-1", summary["userId"])
        self.assertEqual(["PLAYER"], summary["roles"])
        self.assertNotIn("secret-value", json.dumps(summary))
        self.assertNotIn("hidden@example.test", json.dumps(summary))
        with self.assertRaises(PreviewReadError):
            verify_player_session(
                "andy-bogard",
                identity,
                session,
                {"id": "user-1", "email": "other@example.test", "roles": ["PLAYER"]},
            )

    def test_session_check_covers_seeded_owner_players_and_front_desk_without_secrets(self):
        self.assertEqual(
            (
                "sofia", "andy-bogard", "terry-bogard",
                "kyo-captain", "benimaru", "blanka", "ryu", "ken-masters", "riley",
            ),
            seed_actor_ids(load_preview_seed(REPO_ROOT)),
        )
        record = session_check_record([
            {"actorId": "sofia", "roles": [], "emailMatched": True, "userId": "owner-id"},
            {"actorId": "andy-bogard", "roles": ["PLAYER"], "emailMatched": True, "userId": "andy-id"},
            {"actorId": "terry-bogard", "roles": ["PLAYER"], "emailMatched": True, "userId": "terry-id"},
            {"actorId": "riley", "roles": ["ADMIN", "FRONT_DESK"], "emailMatched": True, "userId": "riley-id"},
        ])
        self.assertEqual("stateless_preview_session_check", record["mode"])
        self.assertEqual("not_created_or_modified", record["browserSession"])
        self.assertEqual(["sofia", "andy-bogard", "terry-bogard", "riley"], [row["actorId"] for row in record["actors"]])
        self.assertNotIn("owner-id", json.dumps(record))
        self.assertNotIn("riley-id", json.dumps(record))

    def test_preview_client_allows_get_but_rejects_other_origin(self):
        policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_READONLY,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
            )
        )

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def geturl(self):
                return PREVIEW_ORIGIN + "/apis/v2/areas"

            def read(self, _limit):
                return b'{"items":[]}'

        requests = []

        def transport(request, timeout):
            requests.append((request, timeout))
            return Response()

        client = PreviewReadonlyClient(policy, "secret-token", transport=transport)
        result = client.get("/apis/v2/areas")
        self.assertEqual([], result["items"])
        self.assertEqual("GET", requests[0][0].method)
        self.assertEqual("Bearer secret-token", requests[0][0].get_header("Authorization"))
        with self.assertRaises(TargetPolicyError):
            client.get("https://example.com/apis/v2/areas")

    def test_identity_registry_generates_private_stable_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preview-actors.json"
            registry = IdentityRegistry(path)
            first = registry.ensure()
            second = registry.ensure()

            self.assertEqual(first["sofia"].email, second["sofia"].email)
            self.assertEqual(first["sofia"].password, second["sofia"].password)
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertNotIn(first["sofia"].password, repr(first["sofia"]))
            team = registry.ensure(("kyo-captain", "benimaru"))
            self.assertEqual({"kyo-captain", "benimaru"}, set(team))
            riley = registry.ensure(("riley",))
            self.assertEqual(("Riley", "Chen"), (riley["riley"].first_name, riley["riley"].last_name))
            self.assertEqual({"sofia", "andy-bogard", "terry-bogard"}, set(registry.ensure()))

    def test_invite_sent_protocol_records_product_readback_and_optional_link(self):
        policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_READONLY,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
            )
        )
        invitation = {
            "id": "invite-1",
            "status": "INVITATION_EXTENDED",
            "inviteUrl": PREVIEW_ORIGIN + "/invitations/invite-1",
        }

        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            record = record_invite_sent(
                storage, policy, occurrence_key="pod-1@2026-09-25T12",
                event_id="event-1", invitation=invitation,
                sender_actor_id="andy-bogard", recipient_actor_id="terry-bogard",
                observed_at="2026-09-25T12:00:00Z",
            )
            record_invite_sent(
                storage, policy, occurrence_key="pod-1@2026-09-25T12",
                event_id="event-1", invitation=invitation,
                sender_actor_id="andy-bogard", recipient_actor_id="terry-bogard",
                observed_at="2026-09-25T12:00:01Z",
            )

            self.assertEqual("available", record["inviteLink"]["status"])
            self.assertEqual("unobserved", record["delivery"]["email"])
            self.assertEqual(1, len(storage.read_jsonl(storage.state / "notifications.jsonl")))
            channel = storage.read_jsonl(storage.channel_path("club"))
            self.assertEqual("invite_sent", channel[-1]["kind"])

    def test_identity_writer_requires_write_policy(self):
        policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_READONLY,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
            )
        )
        with self.assertRaises(PreviewWriteError):
            PreviewIdentityWriter(policy)

        with self.assertRaises(PreviewWriteError):
            PreviewCreditWriter(policy, "admin-token")

        with self.assertRaises(PreviewWriteError):
            PreviewRoleWriter(policy, "admin-token")

    def test_payment_reference_is_not_a_saved_payment_method(self):
        result = summarize_payment(
            {
                "id": "actor-id",
                "stripe": {"userId": "cus_test"},
                "preferredCard": None,
                "preferredBankAccount": None,
                "paymentElement": None,
                "virtualCredits": 0,
            }
        )

        self.assertFalse(result["hasPaymentMethod"])
        self.assertEqual(0.0, result["virtualCredits"])

    def test_payment_element_proves_saved_payment_method(self):
        result = summarize_payment(
            {"paymentElement": {"id": "pm_test"}, "virtualCredits": 12.5}
        )

        self.assertTrue(result["hasPaymentMethod"])
        self.assertEqual(12.5, result["virtualCredits"])

    def test_missing_virtual_credit_field_remains_unknown(self):
        result = summarize_payment({"paymentElement": None})

        self.assertFalse(result["hasPaymentMethod"])
        self.assertIsNone(result["virtualCredits"])

    def test_candidate_session_uses_available_default_table_rate(self):
        result = select_candidate_session(
            [
                {
                    "id": "session-1",
                    "status": "AVAILABLE",
                    "tablesLeft": 1,
                    "startTime": "2026-09-26T12:00:00.000Z",
                    "endTime": "2026-09-26T12:30:00.000Z",
                    "operatingDate": "2026-09-26",
                    "periodType": "OFF_PEAK",
                    "defaultTable": {"id": "fixed"},
                    "availableTables": {
                        "items": [
                            {"id": "auto", "type": "AUTO", "rate": 10},
                            {"id": "fixed", "type": "FIXED_TABLE", "rate": 15},
                        ]
                    },
                }
            ]
        )

        self.assertEqual("session-1", result["sessionId"])
        self.assertEqual("fixed", result["tableId"])
        self.assertEqual(15.0, result["rate"])

    def test_candidate_session_chooses_nearest_after_safety_lead(self):
        sessions = [
            {
                "id": "later",
                "status": "AVAILABLE",
                "tablesLeft": 1,
                "startTime": "2026-09-24T18:00:00Z",
                "endTime": "2026-09-24T18:30:00Z",
                "availableTables": {"items": [{"id": "later-table", "rate": 0}]},
            },
            {
                "id": "too-soon",
                "status": "AVAILABLE",
                "tablesLeft": 1,
                "startTime": "2026-09-24T17:10:00Z",
                "endTime": "2026-09-24T17:40:00Z",
                "availableTables": {"items": [{"id": "soon-table", "rate": 0}]},
            },
            {
                "id": "nearest",
                "status": "AVAILABLE",
                "tablesLeft": 1,
                "startTime": "2026-09-24T17:40:00Z",
                "endTime": "2026-09-24T18:10:00Z",
                "availableTables": {"items": [{"id": "near-table", "rate": 0}]},
            },
        ]

        result = select_candidate_session(
            sessions,
            not_before=datetime(2026, 9, 24, 17, 30, tzinfo=timezone.utc),
        )
        now = datetime(2026, 9, 24, 17, 0, tzinfo=timezone.utc)
        soon = select_candidate_session(sessions, not_before=now)
        self.assertEqual("too-soon", soon["sessionId"])
        self.assertTrue(early_agreed_start(soon, now)["pulledForward"])
        self.assertIsNone(early_agreed_start(result, now))

        self.assertEqual("nearest", result["sessionId"])

    def test_early_start_follows_the_singles_player_or_both_doubles_captains(self):
        self.assertTrue(may_start_early({"participants": ["andy-bogard"], "journey": "customer_booking"}))
        self.assertTrue(
            may_start_early(
                {"participants": ["andy-bogard", "terry-bogard"], "journey": "customer_booking"}
            )
        )
        self.assertTrue(
            may_start_early(
                {
                    "reason": "team_rivalry_doubles",
                    "journey": "customer_team_signup",
                    "participants": ["andy-bogard", "terry-bogard", "kyo-captain", "benimaru"],
                    "challenger": {"captainActorId": "andy-bogard"},
                    "opponent": {"captainActorId": "kyo-captain"},
                }
            )
        )
        self.assertFalse(
            may_start_early(
                {
                    "journey": "owner_open_play",
                    "participants": ["andy-bogard", "terry-bogard", "king-captain", "mai"],
                }
            )
        )

    def test_candidate_session_honors_agreed_venue_local_window(self):
        sessions = [
            {
                "id": "earlier-evening",
                "status": "AVAILABLE",
                "tablesLeft": 1,
                "startTime": "2026-09-28T21:00:00Z",
                "endTime": "2026-09-28T21:30:00Z",
                "availableTables": {"items": [{"id": "evening-table", "rate": 0}]},
            },
            {
                "id": "next-morning",
                "status": "AVAILABLE",
                "tablesLeft": 1,
                "startTime": "2026-09-29T13:00:00Z",
                "endTime": "2026-09-29T13:30:00Z",
                "availableTables": {"items": [{"id": "morning-table", "rate": 0}]},
            },
        ]

        result = select_candidate_session(
            sessions,
            timezone_name="America/New_York",
            allowed_local_windows=["08:00-12:00"],
        )

        self.assertEqual("next-morning", result["sessionId"])

    def test_candidate_session_can_require_one_contiguous_hour(self):
        sessions = [
            {
                "id": "half-hour", "status": "AVAILABLE", "tablesLeft": 1,
                "startTime": "2026-09-28T21:00:00Z", "endTime": "2026-09-28T21:30:00Z",
                "availableTables": {"items": [{"id": "half-table", "rate": 0}]},
            },
            {
                "id": "hour", "status": "AVAILABLE", "tablesLeft": 1,
                "startTime": "2026-09-28T22:00:00Z", "endTime": "2026-09-28T23:00:00Z",
                "availableTables": {"items": [{"id": "hour-table", "rate": 0}]},
            },
        ]

        result = select_candidate_session(sessions, required_duration_minutes=60)

        self.assertEqual("hour", result["sessionId"])

    def test_booking_evaluator_sends_only_fixed_preview_payload(self):
        policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_READONLY,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
            )
        )

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def geturl(self):
                return PREVIEW_ORIGIN + "/apis/v2/bookings"

            def read(self, _limit):
                return b'{"type":"PREVIEW","status":"DRAFT","summary":{"total":0}}'

        requests = []

        def transport(request, timeout):
            requests.append((request, timeout))
            return Response()

        result = PreviewBookingEvaluator(policy, "secret", transport).evaluate(
            "session-1", "table-1"
        )
        body = json.loads(requests[0][0].data.decode("utf-8"))

        self.assertEqual("PREVIEW", result["type"])
        self.assertEqual("POST", requests[0][0].method)
        self.assertEqual("PREVIEW", body["type"])
        self.assertEqual("USE_NONE", body["passesStrategy"])
        self.assertEqual(0, body["virtualCredits"])
        self.assertNotIn("owner", body)

    def test_booking_evaluator_rejects_order_payload(self):
        with self.assertRaises(PreviewEvaluationError):
            PreviewBookingEvaluator._validate_payload(
                {
                    "type": "ORDER",
                    "items": [],
                    "chargeStrategy": "ONLY_OWNER",
                    "passesStrategy": "USE_NONE",
                    "virtualCredits": 0,
                    "termsAgreed": True,
                    "bookingMode": "USER_BOOKED",
                }
            )

    def test_unified_quote_evaluator_only_posts_the_quote_shape(self):
        policy = TargetPolicy.from_config(ClubConfig(mode=RunMode.PREVIEW_READONLY, preview_origin=PREVIEW_ORIGIN, allowed_preview_origins=(PREVIEW_ORIGIN,)))

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def geturl(self): return PREVIEW_ORIGIN + "/apis/v2/bookings/quotes"
            def read(self, _limit): return b'{"pricing":{"due":{"total":{"amount":2200,"currency":"USD"}}},"issues":[]}'

        requests = []
        result = UnifiedBookingQuoteEvaluator(policy, "token", lambda request, timeout: requests.append(request) or Response()).evaluate("pod-1", "2026-10-21T16:30:00Z", "2026-10-21T17:00:00Z", 2)
        self.assertEqual({"slot": {"pod": {"id": "pod-1"}, "startsAt": "2026-10-21T16:30:00Z", "endsAt": "2026-10-21T17:00:00Z"}, "groupSize": 2}, json.loads(requests[0].data.decode("utf-8")))
        self.assertEqual(22.0, summarize_unified_booking_quote(result)["total"])

    def test_unified_writer_quotes_then_confirms_once(self):
        policy = TargetPolicy.from_config(ClubConfig(mode=RunMode.PREVIEW_WRITE, preview_origin=PREVIEW_ORIGIN, allowed_preview_origins=(PREVIEW_ORIGIN,), preview_write_confirmation=PREVIEW_ORIGIN))

        class Response:
            def __init__(self, body): self.body = body; self.status = 201
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def geturl(self): return PREVIEW_ORIGIN + "/apis/v2/bookings/quotes/q-1/confirm"
            def read(self, _limit): return self.body

        requests = []
        replies = [
            b'{"id":"q-1","pricing":{"due":{"total":{"amount":2200}}},"_links":{"confirm":{"href":"/bookings/quotes/q-1/confirm"}},"requirements":[]}',
            b'{"status":"CONFIRMED","event":{"id":"event-1"}}',
        ]
        writer = UnifiedBookingWriter(policy, "token", lambda request, timeout: requests.append(request) or Response(replies.pop(0)))
        result = writer.order("pod-1", "2026-10-21T16:30:00Z", "2026-10-21T17:00:00Z", 2, "pm-1")
        self.assertEqual(2, len(requests))
        self.assertEqual("/apis/v2/bookings/quotes", requests[0].full_url.removeprefix(PREVIEW_ORIGIN))
        self.assertEqual({"termsAgreed": True, "paymentMethod": {"id": "pm-1"}}, json.loads(requests[1].data.decode("utf-8")))
        self.assertEqual("event-1", summarize_unified_order(result)["eventId"])

    def test_booking_preview_summary_is_sanitized(self):
        result = summarize_booking_preview(
            {
                "type": "PREVIEW",
                "status": "DRAFT",
                "summary": {
                    "total": 0,
                    "currency": "USD",
                    "virtualCredits": 0,
                    "maxChargableAmount": 0,
                    "errors": [],
                },
                "items": [{"errors": []}],
            }
        )

        self.assertTrue(result["readyForOrder"])
        self.assertEqual([], result["errorCodes"])

    def test_payment_seed_refuses_live_stripe_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stripe.env"
            path.write_text("STRIPE_SECRET_KEY=sk_live_forbidden\n", encoding="utf-8")

            with self.assertRaises(PreviewWriteError):
                load_test_stripe_secret(path)

    def test_payment_seed_loads_only_test_stripe_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stripe.env"
            path.write_text(
                "UNRELATED=value\nSTRIPE_SECRET_KEY='sk_test_example'\n",
                encoding="utf-8",
            )

            self.assertEqual("sk_test_example", load_test_stripe_secret(path))

    def test_booking_writer_has_one_write_budget_and_fixed_order_shape(self):
        policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_WRITE,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
                preview_write_confirmation=PREVIEW_ORIGIN,
            )
        )

        class Response:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def geturl(self):
                return PREVIEW_ORIGIN + "/apis/v2/bookings"

            def read(self, _limit):
                return b'{"id":"event-1","type":"ORDER","status":"CONFIRMED"}'

        requests = []

        def transport(request, timeout):
            requests.append((request, timeout))
            return Response()

        writer = PreviewBookingWriter(policy, "actor-token", transport)
        result = writer.order("session-1", "table-1", 25)
        body = json.loads(requests[0][0].data.decode("utf-8"))

        self.assertEqual("event-1", result["id"])
        self.assertEqual("ORDER", body["type"])
        self.assertEqual("ONLY_OWNER", body["chargeStrategy"])
        self.assertNotIn("owner", body)
        with self.assertRaises(PreviewWriteError):
            writer.order("session-1", "table-1", 25)

    def test_candidate_groups_contiguous_half_hour_slots_for_longer_durations(self):
        sessions = [
            {
                "id": f"session-{index}", "status": "AVAILABLE", "tablesLeft": 1,
                "startTime": f"2026-09-26T{10 + index // 2:02d}:{'30' if index % 2 else '00'}:00Z",
                "endTime": f"2026-09-26T{10 + (index + 1) // 2:02d}:{'00' if index % 2 else '30'}:00Z",
                "availableTables": {"items": [{"id": f"table-{index}", "rate": 10}]},
                "defaultTable": {"id": f"table-{index}"},
            }
            for index in range(3)
        ]
        candidate = select_candidate_session(sessions, required_duration_minutes=90)

        self.assertEqual("2026-09-26T10:00:00Z", candidate["startTime"])
        self.assertEqual("2026-09-26T11:30:00Z", candidate["endTime"])
        self.assertEqual(90, candidate["durationMinutes"])
        self.assertEqual(["session-0", "session-1", "session-2"], [item["sessionId"] for item in candidate["items"]])
        with self.assertRaises(PreviewReadError):
            select_candidate_session(sessions, required_duration_minutes=45)

    def test_booking_writer_can_send_a_contiguous_multi_slot_order(self):
        policy = TargetPolicy.from_config(ClubConfig(mode=RunMode.PREVIEW_WRITE, preview_origin=PREVIEW_ORIGIN, allowed_preview_origins=(PREVIEW_ORIGIN,), preview_write_confirmation=PREVIEW_ORIGIN))

        class Response:
            status = 201
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def geturl(self): return PREVIEW_ORIGIN + "/apis/v2/bookings"
            def read(self, _limit): return b'{"id":"event-1","type":"ORDER","status":"CONFIRMED"}'

        requests = []
        writer = PreviewBookingWriter(policy, "actor-token", lambda request, _timeout: requests.append(request) or Response())
        writer.order("session-1", "table-1", 0, [{"sessionId": "session-2", "tableId": "table-2"}])

        self.assertEqual(2, len(json.loads(requests[0].data.decode("utf-8"))["items"]))

    def test_owner_open_play_writer_is_published_free_event_with_bounded_items(self):
        policy = TargetPolicy.from_config(ClubConfig(mode=RunMode.PREVIEW_WRITE, preview_origin=PREVIEW_ORIGIN, allowed_preview_origins=(PREVIEW_ORIGIN,), preview_write_confirmation=PREVIEW_ORIGIN))

        class Response:
            status = 201
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def geturl(self): return PREVIEW_ORIGIN + "/apis/v2/bookings"
            def read(self, _limit): return b'{"id":"event-1"}'

        requests = []
        writer = PreviewOwnerEventWriter(policy, "admin-token", lambda request, _timeout: requests.append(request) or Response())
        writer.create_open_play(
            [{"sessionId": "session-1", "tableId": "table-1"}, {"sessionId": "session-2", "tableId": "table-2"}],
            "Club Lab Open Play free-hour-001",
        )
        body = json.loads(requests[0].data.decode("utf-8"))

        self.assertEqual("ORDER", body["type"])
        self.assertEqual("OPEN_PLAY", body["eventSubtype"])
        self.assertEqual("PUBLISHED", body["eventStatus"])
        self.assertEqual(2, len(body["items"]))

    def test_event_signup_writer_uses_the_customer_self_signup_contract(self):
        policy = TargetPolicy.from_config(ClubConfig(mode=RunMode.PREVIEW_WRITE, preview_origin=PREVIEW_ORIGIN, allowed_preview_origins=(PREVIEW_ORIGIN,), preview_write_confirmation=PREVIEW_ORIGIN))

        class Response:
            status = 201
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def geturl(self): return PREVIEW_ORIGIN + "/apis/v2/events/event-1/signups"
            def read(self, _limit): return b'{"id":"signup-1"}'

        requests = []
        PreviewEventSignupWriter(policy, "actor-token", lambda request, _timeout: requests.append(request) or Response()).signup("event-1", "user-1")
        self.assertEqual(2, len(requests))
        preview_body = json.loads(requests[0].data.decode("utf-8"))
        body = json.loads(requests[1].data.decode("utf-8"))

        self.assertEqual("PREVIEW", preview_body["type"])
        self.assertEqual("ORDER", body["type"])
        self.assertTrue(body["termsAgreed"])
        self.assertEqual("USE_ALL", body["passesStrategy"])
        self.assertNotIn("owner", body)

    def test_captain_signup_detail_resolves_only_its_linked_attendance_invitation(self):
        class Client:
            def get(self, path):
                if path == "/apis/v2/events/event-1/signups/signup-1":
                    return {
                        "id": "signup-1",
                        "summary": {
                            "openInvitation": {"id": "signup-1"},
                            "invitation": {
                                "id": "attendance-1", "status": "ACCEPTED",
                                "openInvitation": {"id": "signup-1"},
                                "inviteeProfile": {"id": "captain-1"},
                            },
                        },
                    }
                if path.startswith("/apis/v2/events/event-1/invitations?"):
                    return {"items": [{
                        "id": "attendance-1", "inviteeProfile": {"id": "captain-1"},
                        "checkIn": {"status": "NOT_CHECKED_IN"},
                    }]}
                self.fail(f"unexpected Preview read: {path}")

        invitation = read_captain_attendance_invitation(Client(), "event-1", "signup-1")
        self.assertEqual("attendance-1", invitation["id"])
        self.assertEqual("NOT_CHECKED_IN", invitation["checkIn"]["status"])

    def test_captain_signup_detail_rejects_an_unlinked_invitation(self):
        class Client:
            def get(self, _path):
                return {
                    "id": "signup-1",
                    "summary": {
                        "openInvitation": {"id": "signup-1"},
                        "invitation": {
                            "id": "attendance-1", "status": "ACCEPTED",
                            "openInvitation": {"id": "another-signup"},
                            "inviteeProfile": {"id": "captain-1"},
                        },
                    },
                }

        with self.assertRaisesRegex(PreviewReadError, "did not resolve"):
            read_captain_attendance_invitation(Client(), "event-1", "signup-1")

    def test_team_member_writer_previews_then_adds_one_named_member(self):
        policy = TargetPolicy.from_config(ClubConfig(mode=RunMode.PREVIEW_WRITE, preview_origin=PREVIEW_ORIGIN, allowed_preview_origins=(PREVIEW_ORIGIN,), preview_write_confirmation=PREVIEW_ORIGIN))

        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def geturl(self): return PREVIEW_ORIGIN + "/apis/v2/events/event-1/team-members"
            def read(self, _limit): return b'{"invitation":{"id":"invite-1"}}'

        requests = []
        result = PreviewTeamMemberWriter(policy, "owner-token", lambda request, _timeout: requests.append(request) or Response()).add_paid_by_leader(
            "event-1", "leader-1", {"fullName":"Terry Bogard", "email":"test@example.com", "userId":"user-2"}
        )
        self.assertEqual("invite-1", result["invitation"]["id"])
        self.assertEqual(2, len(requests))
        preview, ordered = (json.loads(request.data.decode()) for request in requests)
        self.assertEqual("PREVIEW", preview["type"])
        self.assertEqual("ORDER", ordered["type"])
        self.assertEqual("PAID_BY_INVITER", ordered["chargeType"])
        self.assertEqual({"user":{"id":"leader-1"}}, ordered["teamLeader"])

    def test_booking_occurrence_key_is_stable_and_slot_specific(self):
        first = occurrence_key("pod-1", "2026-09-26T12:00:00Z", "andy-bogard")
        second = occurrence_key("pod-1", "2026-09-26T12:00:00Z", "andy-bogard")
        other = occurrence_key("pod-1", "2026-09-26T12:30:00Z", "andy-bogard")

        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_local_slot_claim_serializes_only_an_unbooked_identical_plan(self):
        candidate = {
            "tableId": "w37-table-1",
            "startTime": "2026-09-30T21:30:00Z",
            "endTime": "2026-09-30T22:00:00Z",
        }
        ledger = {
            "intents": {
                "intent:training:japan-team:001": {
                    "status": "preview_ready",
                    "previewPlan": dict(candidate),
                },
                "intent:training:bogard-higashi:001": {
                    "status": "agreed",
                },
            }
        }

        self.assertEqual(
            {"id": "intent:training:japan-team:001"},
            _local_slot_claim(ledger, "intent:training:bogard-higashi:001", candidate),
        )

        # A completed booking is represented by the product read-back, not a
        # continuing local reservation. The next planner must query live
        # availability and is free to select a different table or time.
        ledger["intents"]["intent:training:japan-team:001"]["status"] = "booked"
        self.assertIsNone(
            _local_slot_claim(ledger, "intent:training:bogard-higashi:001", candidate)
        )

    def test_participation_writer_uses_pending_leader_paid_invitation(self):
        policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_WRITE,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
                preview_write_confirmation=PREVIEW_ORIGIN,
            )
        )

        class Response:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def geturl(self):
                return PREVIEW_ORIGIN + "/apis/v2/events/event-1/invitations"

            def read(self, _limit):
                return b'{"id":"invite-1","status":"INVITATION_EXTENDED"}'

        requests = []
        writer = PreviewParticipationWriter(
            policy, "red-token", lambda request, timeout: requests.append(request) or Response()
        )
        actor = ActorIdentity(
            actor_id="terry-bogard",
            email="blue@example.test",
            first_name="Blue",
            last_name="Captain",
            password="secret",
            podplay_user_id="blue-user-1",
        )
        writer.invite("event-1", actor)
        body = json.loads(requests[0].data.decode("utf-8"))

        self.assertEqual("GUEST", body["type"])
        self.assertEqual("PAID_BY_INVITER", body["chargeType"])
        self.assertEqual(actor.podplay_user_id, body["invitee"]["userId"])
        with self.assertRaises(PreviewWriteError):
            writer.invite("event-1", actor)

    def test_acceptance_evaluator_cannot_persist(self):
        policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_READONLY,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
            )
        )

        class Response:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def geturl(self):
                return PREVIEW_ORIGIN + "/apis/v2/events/event-1/invitations/invite-1/acceptance"

            def read(self, _limit):
                return b'{"summary":{"total":0,"errors":[]},"invitation":{"id":"invite-1"}}'

        requests = []
        PreviewAcceptanceEvaluator(
            policy, "blue-token", lambda request, timeout: requests.append(request) or Response()
        ).evaluate("event-1", "invite-1")
        body = json.loads(requests[0].data.decode("utf-8"))

        self.assertEqual("PREVIEW", body["type"])
        self.assertEqual("USE_NONE", body["passesStrategy"])

    def test_participation_writer_check_in_has_empty_body(self):
        policy = TargetPolicy.from_config(
            ClubConfig(
                mode=RunMode.PREVIEW_WRITE,
                preview_origin=PREVIEW_ORIGIN,
                allowed_preview_origins=(PREVIEW_ORIGIN,),
                preview_write_confirmation=PREVIEW_ORIGIN,
            )
        )

        class Response:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def geturl(self):
                return PREVIEW_ORIGIN + "/apis/v2/events/event-1/invitations/invite-1/check-in"

            def read(self, _limit):
                return b'{"id":"invite-1","status":"CHECKED_IN"}'

        requests = []
        PreviewParticipationWriter(
            policy, "blue-token", lambda request, timeout: requests.append(request) or Response()
        ).check_in("event-1", "invite-1")

        self.assertEqual({}, json.loads(requests[0].data.decode("utf-8")))

    def test_match_status_waits_then_passes(self):
        owner = {"checkIn": {"status": "NOT_CHECKED_IN"}}
        blue = {
            "status": "ACCEPTED",
            "checkIn": {"status": "NOT_CHECKED_IN"},
        }
        start = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)

        self.assertEqual(
            "SCHEDULED",
            classify_match_status(blue, owner, start, FIXED_NOW),
        )
        owner["checkIn"]["status"] = "CHECKED_IN"
        blue["checkIn"]["status"] = "CHECKED_IN"
        self.assertEqual("PASS", classify_match_status(blue, owner, start, start))

    def test_check_in_window_opens_one_hour_early_and_allows_late_arrival(self):
        start = datetime(2026, 9, 30, 15, tzinfo=timezone.utc)
        end = datetime(2026, 9, 30, 15, 30, tzinfo=timezone.utc)

        self.assertEqual((False, "opens at 2026-09-30T14:00:00+00:00"), check_in_window(start, end, datetime(2026, 9, 30, 13, 59, 59, tzinfo=timezone.utc)))
        self.assertEqual((True, "open"), check_in_window(start, end, datetime(2026, 9, 30, 14, tzinfo=timezone.utc)))
        self.assertEqual((True, "open"), check_in_window(start, end, datetime(2026, 9, 30, 15, 29, 59, tzinfo=timezone.utc)))
        self.assertEqual((True, "open"), check_in_window(start, end, end))
        self.assertEqual((False, "closed at 2026-09-30T15:30:00+00:00"), check_in_window(start, end, datetime(2026, 9, 30, 15, 30, 1, tzinfo=timezone.utc)))


def base64_url(value):
    import base64

    encoded = base64.urlsafe_b64encode(json.dumps(value).encode("utf-8"))
    return encoded.decode("ascii").rstrip("=")


if __name__ == "__main__":
    unittest.main()
