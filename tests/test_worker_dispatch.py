from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.journey_policy import dispatch as dispatch_browser_journey, record_observation
from podplay_sim_club.storage import Storage
from podplay_sim_club.worker_dispatch import active_packet, claim, claim_next, queue, report, runbook
from podplay_sim_club.codex_worker_launcher import _prepared_login_receipt, dispatch_once, launch, reap, worker_prompt


class WorkerDispatchTests(unittest.TestCase):
    def test_launcher_preview_is_non_mutating_and_counts_queued_contracts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:launch-preview", "bookerActorId": "andy-bogard",
            }, role="rally")
            queue(root, browser_job=job)

            preview = launch(root, worker_id="economical:runner-1", execute=False)

            self.assertEqual("worker_launch_preview", preview["action"])
            self.assertEqual(1, preview["queuedContracts"])
            self.assertEqual("queued", json.loads((root / "state" / "worker-contracts.json").read_text())["contracts"]["worker:" + job["jobKey"]]["status"])

    def test_dispatcher_cycle_preview_reports_a_single_flight_lane_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:cycle-preview", "bookerActorId": "andy-bogard",
            }, role="rally")
            queue(root, browser_job=job)

            preview = dispatch_once(root, worker_id="economical:dispatcher-1", execute=False)

            self.assertEqual("dispatcher_cycle_preview", preview["action"])
            self.assertEqual(1, preview["queuedContracts"])
            self.assertEqual(0, preview["activeContracts"])
            contract = json.loads((root / "state" / "worker-contracts.json").read_text())["contracts"]["worker:" + job["jobKey"]]
            self.assertEqual("queued", contract["status"])

    def test_dispatcher_cycle_never_overlaps_an_active_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:cycle-active", "bookerActorId": "andy-bogard",
            }, role="rally")
            queue(root, browser_job=job)
            claim_next(root, worker_id="economical:already-running")

            with patch("podplay_sim_club.codex_worker_launcher.reap", return_value={"contractKeys": []}), patch(
                "podplay_sim_club.codex_worker_launcher.launch"
            ) as launch_worker:
                result = dispatch_once(root, worker_id="economical:dispatcher-1", execute=True)

            self.assertEqual("dispatcher_waiting_for_active_worker", result["action"])
            self.assertEqual(1, result["activeContracts"])
            launch_worker.assert_not_called()

    def test_launcher_starts_one_worker_and_persists_only_safe_launch_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:launch", "bookerActorId": "andy-bogard",
            }, role="rally")
            queued = queue(root, browser_job=job)
            reference = hashlib.sha256(queued["contractKey"].encode("utf-8")).hexdigest()[:16]
            receipt_path = root / "state" / "worker-evidence" / reference / "worker-receipt.json"
            receipt_path.parent.mkdir(parents=True)
            receipt_path.write_text(json.dumps({
                "schemaVersion": 1, "actorId": "andy-bogard", "contractRef": reference,
                "status": "browser_ready", "login": "visible_login_settled", "remoteWrites": 0,
            }))
            with patch("podplay_sim_club.codex_worker_launcher.shutil.which", return_value="/opt/codex"), patch(
                "podplay_sim_club.codex_worker_launcher.subprocess.run", return_value=Mock(returncode=0)
            ), patch("podplay_sim_club.codex_worker_launcher._prepared_login_receipt", return_value=True), patch(
                "podplay_sim_club.codex_worker_launcher.subprocess.Popen"
            ) as popen:
                popen.return_value.pid = 4567
                started = launch(root, worker_id="economical:runner-1", execute=True)

            self.assertEqual("economical_worker_started", started["action"])
            self.assertEqual("andy-bogard", started["actorId"])
            command = popen.call_args.args[0]
            self.assertIn("codex-worker-runner.py", command[1])
            self.assertIn("--contract", command)
            self.assertNotIn("@podplay", " ".join(command))
            contract = json.loads((root / "state" / "worker-contracts.json").read_text())["contracts"][queued["contractKey"]]
            self.assertEqual("active", contract["status"])
            self.assertEqual(4567, contract["launcher"]["processId"])
            self.assertEqual("codex-cli-economical-worker", contract["launcher"]["kind"])

    def test_launcher_can_start_the_same_contract_after_dispatcher_next_claimed_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:resume", "bookerActorId": "andy-bogard",
            }, role="rally")
            queued = queue(root, browser_job=job)
            claim_next(root, worker_id="economical:runner-1")
            with patch("podplay_sim_club.codex_worker_launcher.shutil.which", return_value="/opt/codex"), patch(
                "podplay_sim_club.codex_worker_launcher.subprocess.run", return_value=Mock(returncode=0)
            ), patch("podplay_sim_club.codex_worker_launcher._prepared_login_receipt", return_value=True), patch(
                "podplay_sim_club.codex_worker_launcher.subprocess.Popen"
            ) as popen:
                popen.return_value.pid = 457
                started = launch(
                    root, worker_id="economical:runner-1", execute=True,
                    contract_key=queued["contractKey"],
                )
            self.assertEqual("economical_worker_started", started["action"])
            self.assertEqual("active", json.loads((root / "state" / "worker-contracts.json").read_text())["contracts"][queued["contractKey"]]["status"])

    def test_launcher_does_not_delegate_a_public_shell_only_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:no-login-receipt", "bookerActorId": "andy-bogard",
            }, role="rally")
            queued = queue(root, browser_job=job)
            reference = hashlib.sha256(queued["contractKey"].encode("utf-8")).hexdigest()[:16]
            receipt_path = root / "state" / "worker-evidence" / reference / "worker-receipt.json"
            receipt_path.parent.mkdir(parents=True)
            receipt_path.write_text(json.dumps({
                "schemaVersion": 1, "actorId": "andy-bogard", "contractRef": reference,
                "status": "browser_ready", "login": "not_requested", "remoteWrites": 0,
            }))
            with patch("podplay_sim_club.codex_worker_launcher.subprocess.run", return_value=Mock(returncode=0)), patch(
                "podplay_sim_club.codex_worker_launcher.subprocess.Popen"
            ) as popen:
                result = launch(root, worker_id="economical:runner-1", execute=True)

            self.assertEqual("coordinator_browser_not_ready", result["action"])
            popen.assert_not_called()

    def test_launch_marker_binds_the_settled_receipt_to_one_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = Storage(root)
            contract_key = "worker:opaque"
            reference = hashlib.sha256(contract_key.encode("utf-8")).hexdigest()[:16]
            receipt_path = root / "state" / "worker-evidence" / reference / "worker-receipt.json"
            receipt_path.parent.mkdir(parents=True)
            receipt_path.write_text(json.dumps({
                "schemaVersion": 1, "actorId": "andy-bogard", "contractRef": reference,
                "status": "browser_ready", "login": "visible_login_settled", "handoffId": "previoushandoff123",
                "remoteWrites": 0,
            }))
            packet = {"contractKey": contract_key, "actorId": "andy-bogard"}

            self.assertTrue(_prepared_login_receipt(storage, packet=packet, handoff_id="previoushandoff123"))
            self.assertFalse(_prepared_login_receipt(storage, packet=packet, handoff_id="newhandoffmarker456"))

    def test_worker_prompt_is_bounded_and_never_contains_product_secrets(self):
        prompt = worker_prompt(root=REPO_ROOT, worker_id="economical:runner-1", runbook_payload={
            "launchPacket": {
                "contractKey": "worker:opaque", "actorId": "andy-bogard", "browserJobKey": "job:opaque",
                "journeyAction": "plan_training_booking", "playbook": "playbooks/sim-player-booking.md",
            }
        })
        self.assertIn("Do not use product APIs", prompt)
        self.assertIn("worker-dispatch report", prompt)
        self.assertIn("detached runner has already completed", prompt)
        self.assertIn("Begin by observing that current rendered state", prompt)
        self.assertNotIn("password", prompt.lower())

    def test_booking_entry_stops_before_a_volatile_slot_or_payment(self):
        helper = (REPO_ROOT / "tools" / "sim-browser" / "booking-entry.mjs").read_text()
        self.assertIn("booking.area.west_37th_manhattan", helper)
        self.assertIn("booking.experience.open_pod", helper)
        self.assertNotIn("booking.slot.", helper)
        self.assertNotIn("booking.payment.", helper)

    def test_feature_map_contains_the_observed_earliest_open_pod_row(self):
        feature_map = json.loads((REPO_ROOT / "tools" / "sim-browser" / "feature-map.json").read_text())
        self.assertEqual(
            "9:00am 4 open Pods",
            feature_map["features"]["booking.slot.today_0900"]["name"],
        )

    def test_feature_map_allows_observed_evening_rows_without_a_stale_capacity_match(self):
        feature_map = json.loads((REPO_ROOT / "tools" / "sim-browser" / "feature-map.json").read_text())
        self.assertEqual("9:00pm ", feature_map["features"]["booking.slot.tonight_2100"]["namePrefix"])
        self.assertEqual("9:30pm ", feature_map["features"]["booking.slot.tonight_2130"]["namePrefix"])

    def test_detached_worker_rechecks_the_settled_login_boundary(self):
        runner = (REPO_ROOT / "tools" / "sim-browser" / "codex-worker-runner.py").read_text()
        self.assertIn("_prepared_login_receipt(", runner)
        self.assertIn("handoff_id=handoff_id", runner)
        self.assertIn('outcome="not_started"', runner)

    def test_detached_worker_closes_an_unreported_model_turn(self):
        runner = (REPO_ROOT / "tools" / "sim-browser" / "codex-worker-runner.py").read_text()
        self.assertIn("def _finalize_if_unreported", runner)
        self.assertIn("def _capture_final_observation", runner)
        self.assertIn("_finalize_if_unreported(storage=storage, worker_id=worker_id, contract_key=contract_key)", runner)
        self.assertIn("ECONOMICAL_TURN_TIMEOUT_SECONDS = 120", runner)
        self.assertIn("status\": \"model_timeout", runner)

    def test_worker_prompt_includes_a_mandatory_safe_final_report(self):
        prompt = worker_prompt(root=REPO_ROOT, worker_id="economical:runner-1", runbook_payload={
            "launchPacket": {
                "contractKey": "worker:opaque", "actorId": "andy-bogard", "browserJobKey": "job:opaque",
                "journeyAction": "plan_training_booking", "playbook": "playbooks/sim-player-booking.md",
            }
        })
        self.assertIn("MUST run exactly one report command", prompt)
        self.assertIn("--outcome blocked", prompt)

    def test_booking_worker_prompt_carries_preview_slot_authorization(self):
        prompt = worker_prompt(root=REPO_ROOT, worker_id="economical:runner-1", runbook_payload={
            "launchPacket": {
                "contractKey": "worker:opaque", "actorId": "andy-bogard", "browserJobKey": "job:opaque",
                "journeyAction": "plan_training_booking", "playbook": "playbooks/sim-player-booking.md",
            }
        })
        self.assertIn("select the earliest future currently rendered allowlisted Open Pod slot", prompt)
        self.assertIn("wait at least eight seconds", prompt)
        self.assertIn("continue scanning for the next eligible future allowlisted row", prompt)
        self.assertIn("Do not treat the ordinary slot choice as an unresolved approval", prompt)
        self.assertIn("rendered review total is the permitted price source", prompt)

    def test_queued_worker_carries_agreed_participant_and_duration_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intents = root / "state" / "booking-intents.json"
            intents.parent.mkdir(parents=True)
            intents.write_text(json.dumps({"intents": {"intent:agreed": {
                "status": "agreed", "bookerActorId": "andy-bogard",
                "participants": ["andy-bogard", "terry-bogard"],
                "constraints": {"durationMinutes": 30},
            }}}))
            job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:agreed", "bookerActorId": "andy-bogard",
            }, role="rally")
            queue(root, browser_job=job)
            packet = claim_next(root, worker_id="economical:runner-1")["launchPacket"]

            self.assertEqual({"participantCount": 2, "durationMinutes": 30}, packet["agreement"])

    def test_detached_worker_makes_booking_entry_preflight_deterministic(self):
        runner = (REPO_ROOT / "tools" / "sim-browser" / "codex-worker-runner.py").read_text()
        self.assertIn("def _prepare_booking_entry", runner)
        self.assertIn("booking-entry.mjs", runner)
        self.assertIn("rendered_live_availability", runner)
        self.assertIn("booking live availability", runner)
        self.assertIn("AVAILABILITY_SETTLE_SECONDS = 8", runner)
        self.assertIn("booking_entry_blocked", runner)

    def test_reaper_quarantines_a_dead_worker_without_a_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:reap", "bookerActorId": "blanka",
            }, role="rally")
            queued = queue(root, browser_job=job)
            claim_next(root, worker_id="economical:runner-1")
            state_path = root / "state" / "worker-contracts.json"
            state = json.loads(state_path.read_text())
            state["contracts"][queued["contractKey"]]["launcher"] = {
                "kind": "codex-cli-economical-worker", "processId": 4567,
            }
            state_path.write_text(json.dumps(state))

            with patch("podplay_sim_club.codex_worker_launcher.os.kill", side_effect=ProcessLookupError):
                result = reap(root)

            self.assertEqual([queued["contractKey"]], result["contractKeys"])
            contract = json.loads(state_path.read_text())["contracts"][queued["contractKey"]]
            self.assertEqual("reported", contract["status"])
            self.assertEqual("blocked", contract["outcome"])
    def test_operations_worker_claims_oldest_runnable_contract_with_its_browser_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:first", "bookerActorId": "andy-bogard",
            }, role="rally")
            second_job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:second", "bookerActorId": "terry-bogard",
            }, role="rally")
            queue(root, browser_job=second_job)
            queue(root, browser_job=first_job)

            launched = claim_next(root, worker_id="economical:runner-1")

            self.assertEqual("worker_contract_claimed", launched["action"])
            self.assertEqual("andy-bogard", launched["launchPacket"]["actorId"])
            self.assertEqual("playbooks/sim-player-booking.md", launched["launchPacket"]["playbook"])
            self.assertEqual({"research": False, "headless": True, "modelTier": "economical"}, launched["launchPacket"]["mode"])
            journeys = json.loads((root / "state" / "browser-journeys.json").read_text())
            self.assertEqual("active", journeys["jobs"][first_job["jobKey"]]["status"])
            packet = active_packet(root, worker_id="economical:runner-1")
            self.assertEqual(launched["launchPacket"], packet["launchPacket"])
            self.assertEqual("worker_runbook_ready", runbook(root, worker_id="economical:runner-1")["action"])

    def test_visible_worker_receipt_must_match_the_active_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:receipt", "bookerActorId": "andy-bogard",
            }, role="rally")
            queued = queue(root, browser_job=job)
            claim_next(root, worker_id="economical:runner-1")
            record_observation(root, job_key=job["jobKey"], actor_id="andy-bogard", kind="visible_observation")
            contract_key = queued["contractKey"]
            reference = hashlib.sha256(contract_key.encode("utf-8")).hexdigest()[:16]
            receipt_path = root / "state" / "worker-evidence" / reference / "worker-receipt.json"
            receipt_path.parent.mkdir(parents=True)
            receipt_path.write_text(json.dumps({
                "schemaVersion": 1, "actorId": "andy-bogard", "contractRef": reference,
                "status": "browser_ready", "login": "visible_login_settled", "remoteWrites": 0,
            }))

            reported = report(
                root, contract_key=contract_key, worker_id="economical:runner-1",
                outcome="visible_progress", diary_line="Rendered booking entry was observed; no booking outcome inferred.",
                receipt_ref=str(receipt_path.relative_to(root)),
            )

            self.assertEqual("reported", reported["status"])
            contract = json.loads((root / "state" / "worker-contracts.json").read_text())["contracts"][contract_key]
            self.assertEqual(str(receipt_path.relative_to(root)), contract["receiptRef"])

    def test_public_shell_only_receipt_cannot_support_visible_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:public-shell", "bookerActorId": "andy-bogard",
            }, role="rally")
            queued = queue(root, browser_job=job)
            claim_next(root, worker_id="economical:runner-1")
            record_observation(root, job_key=job["jobKey"], actor_id="andy-bogard", kind="visible_observation")
            reference = hashlib.sha256(queued["contractKey"].encode("utf-8")).hexdigest()[:16]
            receipt_path = root / "state" / "worker-evidence" / reference / "worker-receipt.json"
            receipt_path.parent.mkdir(parents=True)
            receipt_path.write_text(json.dumps({
                "schemaVersion": 1, "actorId": "andy-bogard", "contractRef": reference,
                "status": "browser_ready", "login": "not_requested", "remoteWrites": 0,
            }))

            with self.assertRaisesRegex(ValueError, "receipt is incomplete"):
                report(
                    root, contract_key=queued["contractKey"], worker_id="economical:runner-1",
                    outcome="visible_progress", diary_line="Rendered booking entry was observed; no booking outcome inferred.",
                    receipt_ref=str(receipt_path.relative_to(root)),
                )

    def test_operations_worker_skips_a_stale_contract_for_a_runnable_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale = queue(root, browser_job={
                "jobKey": "missing-job", "actorId": "andy-bogard", "intentId": "intent:stale",
                "journeyAction": "plan_training_booking", "playbook": "playbooks/sim-player-booking.md",
            })
            fresh_job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:fresh", "bookerActorId": "terry-bogard",
            }, role="rally")
            queue(root, browser_job=fresh_job)

            launched = claim_next(root, worker_id="economical:runner-1")

            self.assertEqual("worker_contract_claimed", launched["action"])
            self.assertEqual("terry-bogard", launched["launchPacket"]["actorId"])
            contracts = json.loads((root / "state" / "worker-contracts.json").read_text())["contracts"]
            self.assertEqual("retired", contracts[stale["contractKey"]]["status"])
            self.assertEqual("browser_job_no_longer_runnable", contracts[stale["contractKey"]]["retirementReason"])

    def test_not_started_worker_releases_unobserved_job_for_a_later_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:retry", "bookerActorId": "andy-bogard",
            }, role="rally")
            queued = queue(root, browser_job=job)
            claim_next(root, worker_id="economical:runner-1")

            reported = report(
                root, contract_key=queued["contractKey"], worker_id="economical:runner-1",
                outcome="not_started", diary_line="No rendered browser was available to start this journey.",
            )

            self.assertEqual("reported", reported["status"])
            journeys = json.loads((root / "state" / "browser-journeys.json").read_text())
            self.assertEqual("queued", journeys["jobs"][job["jobKey"]]["status"])
            retried = queue(root, browser_job=job)
            self.assertEqual("queued", retried["status"])
            contracts = json.loads((root / "state" / "worker-contracts.json").read_text())["contracts"]
            self.assertEqual("queued", contracts[queued["contractKey"]]["status"])

    def test_blocked_worker_quarantines_the_browser_job_without_a_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = dispatch_browser_journey(root, task={
                "action": "plan_training_booking", "intentId": "intent:uncertain", "bookerActorId": "andy-bogard",
            }, role="rally")
            queued = queue(root, browser_job=job)
            claim_next(root, worker_id="economical:runner-1")

            report(
                root, contract_key=queued["contractKey"], worker_id="economical:runner-1",
                outcome="blocked", diary_line="Worker stopped before it could return a rendered-browser receipt.",
            )

            journeys = json.loads((root / "state" / "browser-journeys.json").read_text())
            self.assertEqual("blocked", journeys["jobs"][job["jobKey"]]["status"])
            self.assertFalse(queued["contractKey"] in [
                item.get("contractKey") for item in json.loads((root / "state" / "worker-contracts.json").read_text())["contracts"].values()
                if item.get("status") == "queued"
            ])

    def test_contract_is_sequential_and_writes_a_bounded_diary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queued = queue(root, browser_job={
                "jobKey": "player:plan:intent:one:andy-bogard", "actorId": "andy-bogard",
                "intentId": "intent:one", "journeyAction": "plan_training_booking",
                "playbook": "playbooks/sim-player-booking.md",
            })
            self.assertEqual("queued", queued["status"])
            active = claim(root, contract_key=queued["contractKey"], worker_id="economical:andy-bogard")
            self.assertEqual("active", active["status"])
            done = report(root, contract_key=queued["contractKey"], worker_id="economical:andy-bogard", outcome="blocked", diary_line="Rendered checkout had no enabled continue control.")
            self.assertEqual("reported", done["status"])
            row = json.loads((root / "state" / "actors" / "andy-bogard" / "journal.jsonl").read_text())
            self.assertEqual("blocked", row["outcome"])
            self.assertEqual(0, row["remoteWrites"])

