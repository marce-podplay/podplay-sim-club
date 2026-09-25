"""Deterministic fake-world state machine."""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .actions import ActionValidator
from .booking_intents import load_intent_ledger, save_intent
from .fake_preview import FakePreview
from .preview import PreviewAdapter
from .storage import Storage
from .time import hour_key, isoformat, parse_instant
from .world import add_signal, new_world, project_world


STEPS = (
    "red_proposes",
    "blue_agrees",
    "booking_created",
    "blue_invited",
    "blue_accepts",
    "red_checks_in",
    "blue_checks_in",
    "assert_match",
)

NEED_STEPS = (
    "red_need_rises",
    "red_proposes_from_need",
    "blue_agrees_from_availability",
    "lead_records_booking_intent",
)

PROMOTION_STEPS = (
    "sofia_announces_priority_session",
    "red_accepts_priority_session",
    "blue_accepts_priority_session",
    "lead_records_promotion_intent",
)


class SimulatedCrash(RuntimeError):
    """Fault injection raised after a product commit but before checkpoint."""


PreviewFactory = Callable[[Storage, Dict[str, Any]], PreviewAdapter]


class Orchestrator:
    def __init__(
        self, root: Path, preview_factory: Optional[PreviewFactory] = None
    ):
        self.root = root.resolve()
        self.storage = Storage(self.root)
        self.seed = self.storage.load_json(self.root / "seed" / "tenant.json")
        self.scenario = self.storage.load_json(
            self.root / "scenarios" / "hourly-match.json"
        )
        self.needs_scenario = self.storage.load_json(
            self.root / "scenarios" / "needs-match.json"
        )
        self.promotion_scenario = self.storage.load_json(
            self.root / "scenarios" / "owner-promotion.json"
        )
        if (
            self.seed is None
            or self.scenario is None
            or self.needs_scenario is None
            or self.promotion_scenario is None
        ):
            raise RuntimeError(
                "seed/tenant.json, scenarios/hourly-match.json, and "
                "scenarios/needs-match.json and scenarios/owner-promotion.json are required"
            )
        self._preview_factory = preview_factory or FakePreview
        self.validator = ActionValidator(
            pod_id=self.scenario["podId"],
            allowed_actions=self.scenario["allowedActions"],
        )

    def ensure_world(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        instant = now or parse_instant(None)
        observed_at = isoformat(instant)
        world = self.storage.load_json(self.storage.world_path, default=None)
        preview = self._preview()

        if world is None:
            if preview.is_fresh():
                preview.rehydrate()
            world = new_world(1, observed_at, preview_mode=preview.mode)
            self._ensure_needs_state(world)
            add_signal(
                world,
                self._signal(observed_at, "SEED", "Preview Club opened from seed"),
            )
            self.storage.write_json(self.storage.world_path, world)
            return world

        if preview.is_fresh():
            self.storage.archive_world(world)
            previous_season = world["season"]["id"]
            next_number = int(world["season"]["number"]) + 1
            preview.rehydrate()
            world = new_world(next_number, observed_at, preview_mode=preview.mode)
            self._ensure_needs_state(world)
            message = (
                f"Preview database refreshed after {previous_season}; product state was "
                "reseeded and personal journals were preserved."
            )
            add_signal(world, self._signal(observed_at, "RESET", message, "attention"))
            self._append_unique(
                self.storage.channel_path("system"),
                {
                    "id": f"reset:{world['season']['id']}",
                    "channel": "system",
                    "from": "lead",
                    "kind": "preview_reset",
                    "text": message,
                    "observedAt": observed_at,
                },
            )
            self.storage.write_json(self.storage.world_path, world)
        self._ensure_needs_state(world)
        return world

    def reset_fake_preview(self) -> None:
        preview = self._preview()
        if not isinstance(preview, FakePreview):
            raise RuntimeError("demo-reset is available only with the fake adapter")
        preview.reset_database()

    @property
    def preview_mode(self) -> str:
        return self._preview().mode

    def _preview(self) -> PreviewAdapter:
        preview = self._preview_factory(self.storage, self.seed)
        if not isinstance(preview, PreviewAdapter):
            raise TypeError("preview factory returned an incompatible adapter")
        return preview

    def run_beat(
        self,
        turns: int = 10,
        now: Optional[datetime] = None,
        crash_after_remote: Optional[str] = None,
    ) -> Dict[str, Any]:
        if turns < 1:
            raise ValueError("turns must be at least 1")
        instant = now or parse_instant(None)
        observed_at = isoformat(instant)

        with self.storage.writer_lock():
            world = self.ensure_world(instant)
            preview = self._preview()
            world["clock"].update(
                {"observedAt": observed_at, "simulationTime": observed_at}
            )
            self._age_old_bookings(world, current_hour=hour_key(instant))

            occurrence_key = f"{self.scenario['podId']}@{hour_key(instant)}"
            occurrences = world["scenarios"]["hourly-match"]["occurrences"]
            occurrence = occurrences.setdefault(
                occurrence_key,
                {
                    "key": occurrence_key,
                    "step": 0,
                    "status": "running",
                    "podId": self.scenario["podId"],
                    "startsAt": observed_at,
                },
            )

            executed = 0
            while executed < turns and occurrence["status"] == "running":
                step_index = int(occurrence["step"])
                step_name = STEPS[step_index]
                self._execute_step(
                    world,
                    preview,
                    occurrence,
                    step_name,
                    observed_at,
                    crash_after_remote,
                )
                occurrence["step"] = step_index + 1
                executed += 1
                self.storage.write_json(self.storage.world_path, world)

            world["beat"]["count"] = int(world["beat"]["count"]) + 1
            world["beat"]["lastTurnCount"] = executed
            world["beat"]["lastOccurrenceKey"] = occurrence_key
            self.storage.write_json(self.storage.world_path, world)
            return {
                "occurrenceKey": occurrence_key,
                "turnsExecuted": executed,
                "status": occurrence["status"],
                "step": occurrence["step"],
                "world": project_world(world),
            }

    def run_needs_beat(
        self, turns: int = 4, now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Advance one deterministic need -> agreement -> intent interaction."""

        if turns < 1:
            raise ValueError("turns must be at least 1")
        instant = now or parse_instant(None)
        observed_at = isoformat(instant)

        with self.storage.writer_lock():
            world = self.ensure_world(instant)
            self._ensure_needs_state(world)
            world["clock"].update(
                {"observedAt": observed_at, "simulationTime": observed_at}
            )
            interaction_key = (
                f"needs-match:{world['season']['id']}:"
                f"{self.needs_scenario['interactionId']}"
            )
            interactions = world["scenarios"]["needs-match"]["interactions"]
            interaction = interactions.setdefault(
                interaction_key,
                {
                    "key": interaction_key,
                    "step": 0,
                    "status": "running",
                    "participants": ["red-captain", "blue-captain"],
                    "createdAt": observed_at,
                },
            )

            executed = 0
            while executed < turns and interaction["status"] == "running":
                step_index = int(interaction["step"])
                step_name = NEED_STEPS[step_index]
                self._execute_need_step(
                    world, interaction, step_name, observed_at
                )
                interaction["step"] = step_index + 1
                executed += 1
                self.storage.write_json(self.storage.world_path, world)

            world["beat"]["count"] = int(world["beat"]["count"]) + 1
            world["beat"]["lastTurnCount"] = executed
            world["beat"]["lastOccurrenceKey"] = interaction_key
            self.storage.write_json(self.storage.world_path, world)
            return {
                "interactionKey": interaction_key,
                "turnsExecuted": executed,
                "status": interaction["status"],
                "step": interaction["step"],
                "intentId": interaction.get("intentId"),
                "world": project_world(world),
            }

    def run_promotion_beat(
        self, turns: int = 4, now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Let the owner create near-term demand through an explicit campaign."""

        if turns < 1:
            raise ValueError("turns must be at least 1")
        instant = now or parse_instant(None)
        observed_at = isoformat(instant)

        with self.storage.writer_lock():
            world = self.ensure_world(instant)
            self._ensure_needs_state(world)
            world["clock"].update(
                {"observedAt": observed_at, "simulationTime": observed_at}
            )
            interaction_key = (
                f"owner-promotion:{world['season']['id']}:"
                f"{self.promotion_scenario['campaignId']}"
            )
            interactions = world["scenarios"].setdefault(
                "owner-promotion", {"interactions": {}}
            )["interactions"]
            interaction = interactions.setdefault(
                interaction_key,
                {
                    "key": interaction_key,
                    "step": 0,
                    "status": "running",
                    "participants": deepcopy(self.promotion_scenario["participants"]),
                    "createdAt": observed_at,
                },
            )

            executed = 0
            while executed < turns and interaction["status"] == "running":
                step_index = int(interaction["step"])
                step_name = PROMOTION_STEPS[step_index]
                self._execute_promotion_step(
                    world, interaction, step_name, observed_at
                )
                interaction["step"] = step_index + 1
                executed += 1
                self.storage.write_json(self.storage.world_path, world)

            world["beat"]["count"] = int(world["beat"]["count"]) + 1
            world["beat"]["lastTurnCount"] = executed
            world["beat"]["lastOccurrenceKey"] = interaction_key
            self.storage.write_json(self.storage.world_path, world)
            return {
                "interactionKey": interaction_key,
                "turnsExecuted": executed,
                "status": interaction["status"],
                "step": interaction["step"],
                "intentId": interaction.get("intentId"),
                "world": project_world(world),
            }

    def world_view(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        with self.storage.writer_lock():
            world = self.ensure_world(now)
            return project_world(world)

    def _execute_step(
        self,
        world: Dict[str, Any],
        preview: PreviewAdapter,
        occurrence: Dict[str, Any],
        step: str,
        observed_at: str,
        crash_after_remote: Optional[str],
    ) -> None:
        key = occurrence["key"]
        pod_id = occurrence["podId"]
        actor_id = "lead"
        detail = step

        if step == "red_proposes":
            actor_id = "red-captain"
            detail = "Red proposed a match on the scenario-owned pod."
            self._message(
                key,
                observed_at,
                "red-captain",
                ["blue-captain"],
                "Court 1 this hour?",
                "proposal",
            )
            world["actors"]["red-captain"]["state"] = "waiting_for_reply"

        elif step == "blue_agrees":
            actor_id = "blue-captain"
            detail = "Blue agreed to the proposed match."
            self._message(
                key,
                observed_at,
                "blue-captain",
                ["red-captain"],
                "Yes. See you on Court 1.",
                "agreement",
            )
            world["actors"]["blue-captain"]["state"] = "agreed"

        elif step == "booking_created":
            actor_id = "red-captain"
            request = {
                "type": "book_match",
                "arguments": {"podId": pod_id, "occurrenceKey": key},
            }
            self.validator.validate(request)
            booking, created = preview.create_booking(
                key, pod_id, occurrence["startsAt"], "red-captain"
            )
            if crash_after_remote == step:
                raise SimulatedCrash("fake preview committed booking before checkpoint")
            occurrence["bookingId"] = booking["id"]
            occurrence["eventId"] = booking["eventId"]
            detail = f"Booking {booking['id']} {'created' if created else 'reconciled'}."
            self._upsert_world_booking(world, booking, "current")
            world["pods"][pod_id].update(
                {"state": "reserved", "bookingId": booking["id"]}
            )

        elif step == "blue_invited":
            actor_id = "red-captain"
            self.validator.validate(
                {"type": "invite_player", "arguments": {"podId": pod_id}}
            )
            invitation = preview.invite(key, "blue-captain")
            if crash_after_remote == step:
                raise SimulatedCrash("fake preview committed invitation before checkpoint")
            occurrence["invitationId"] = invitation["id"]
            detail = f"Blue invitation {invitation['id']} recorded."

        elif step == "blue_accepts":
            actor_id = "blue-captain"
            self.validator.validate(
                {"type": "accept_invitation", "arguments": {"podId": pod_id}}
            )
            preview.accept(key, "blue-captain")
            if crash_after_remote == step:
                raise SimulatedCrash("fake preview committed acceptance before checkpoint")
            detail = "Blue accepted the invitation."

        elif step == "red_checks_in":
            actor_id = "red-captain"
            self.validator.validate(
                {"type": "check_in", "arguments": {"podId": pod_id}}
            )
            preview.check_in(key, actor_id)
            if crash_after_remote == step:
                raise SimulatedCrash("fake preview committed Red check-in before checkpoint")
            world["actors"][actor_id].update({"location": pod_id, "state": "playing"})
            detail = "Red checked in."

        elif step == "blue_checks_in":
            actor_id = "blue-captain"
            self.validator.validate(
                {"type": "check_in", "arguments": {"podId": pod_id}}
            )
            preview.check_in(key, actor_id)
            if crash_after_remote == step:
                raise SimulatedCrash("fake preview committed Blue check-in before checkpoint")
            world["actors"][actor_id].update({"location": pod_id, "state": "playing"})
            world["pods"][pod_id]["state"] = "occupied"
            detail = "Blue checked in; the match is live."

        elif step == "assert_match":
            event = preview.event(key)
            expected = {"red-captain", "blue-captain"}
            participants = set(event["participants"])
            checked_in = set(event["checkedIn"])
            passed = expected.issubset(participants) and expected.issubset(checked_in)
            assertion = {
                "id": f"assert-match:{key}",
                "scenario": "hourly-match",
                "occurrenceKey": key,
                "passed": passed,
                "expectedParticipants": sorted(expected),
                "actualParticipants": sorted(participants),
                "actualCheckedIn": sorted(checked_in),
                "observedAt": observed_at,
            }
            self._append_unique(
                self.storage.run_path(key, "assertions.jsonl"), assertion
            )
            if not passed:
                self._record_issue(world, key, assertion)
                occurrence["status"] = "failed"
                detail = "Hourly match assertion failed."
            else:
                occurrence["status"] = "complete"
                detail = "Hourly match passed: one booking and both captains checked in."
            self._upsert_world_booking(world, event, "current")

        event = {
            "id": f"{key}:{step}",
            "scenario": "hourly-match",
            "occurrenceKey": key,
            "step": step,
            "actorId": actor_id,
            "detail": detail,
            "observedAt": observed_at,
        }
        self._append_unique(self.storage.run_path(key, "events.jsonl"), event)
        if actor_id != "lead":
            self._append_unique(self.storage.actor_journal_path(actor_id), event)
        add_signal(world, self._signal(observed_at, step.upper(), detail))

    def _execute_need_step(
        self,
        world: Dict[str, Any],
        interaction: Dict[str, Any],
        step: str,
        observed_at: str,
    ) -> None:
        key = interaction["key"]
        actor_id = "lead"

        if step == "red_need_rises":
            actor_id = "red-captain"
            need = world["needs"][actor_id]
            before = int(need["desireToPlay"])
            need["desireToPlay"] = min(100, before + int(need["driftPerBeat"]))
            detail = (
                f"Red desire-to-play rose from {before} to "
                f"{need['desireToPlay']} (threshold {need['threshold']})."
            )
            world["actors"][actor_id]["state"] = "motivated"

        elif step == "red_proposes_from_need":
            actor_id = "red-captain"
            need = world["needs"][actor_id]
            if int(need["desireToPlay"]) < int(need["threshold"]):
                raise RuntimeError("Red's desire to play has not crossed its threshold")
            text = (
                "I feel like playing. Blue, are you free for a 30-minute "
                "morning match in the next two weeks?"
            )
            self._message(
                key,
                observed_at,
                actor_id,
                ["blue-captain"],
                text,
                "need_driven_proposal",
            )
            interaction["proposalMessageId"] = (
                f"{key}:message:need_driven_proposal:{actor_id}"
            )
            world["actors"][actor_id]["state"] = "waiting_for_reply"
            world["actors"]["blue-captain"]["state"] = "considering"
            detail = "Red asked Blue for a match because desire-to-play crossed its threshold."

        elif step == "blue_agrees_from_availability":
            actor_id = "blue-captain"
            availability = self._needs_availability()
            overlap = set(availability["red-captain"]["localWindows"]) & set(
                availability["blue-captain"]["localWindows"]
            )
            if not overlap:
                raise RuntimeError("captains have no overlapping availability")
            text = (
                "Yes. I can play a 30-minute morning match in that window. "
                "Please find us the nearest valid slot."
            )
            self._message(
                key,
                observed_at,
                actor_id,
                ["red-captain"],
                text,
                "availability_agreement",
            )
            interaction["agreementMessageId"] = (
                f"{key}:message:availability_agreement:{actor_id}"
            )
            interaction["agreedWindow"] = sorted(overlap)[0]
            world["actors"][actor_id]["state"] = "agreed"
            world["actors"]["red-captain"]["state"] = "agreed"
            detail = "Blue found overlapping availability and agreed to the match."

        elif step == "lead_records_booking_intent":
            intent_id = f"intent:{key}"
            intent = {
                "schemaVersion": 1,
                "id": intent_id,
                "status": "agreed",
                "reason": "red_desire_to_play",
                "createdAt": observed_at,
                "participants": ["red-captain", "blue-captain"],
                "requestedBy": "red-captain",
                "constraints": {
                    "durationMinutes": 30,
                    "daysAhead": [0, 14],
                    "localWindows": [interaction["agreedWindow"]],
                    "slotPolicy": "nearest_valid_low_contention",
                },
                "evidence": {
                    "proposalMessageId": interaction["proposalMessageId"],
                    "agreementMessageId": interaction["agreementMessageId"],
                    "need": deepcopy(world["needs"]["red-captain"]),
                },
                "remoteWrites": 0,
            }
            ledger = load_intent_ledger(self.storage)
            save_intent(self.storage, ledger, intent)
            interaction["intentId"] = intent_id
            interaction["status"] = "complete"
            summary = {
                "id": intent_id,
                "status": "agreed",
                "reason": intent["reason"],
                "participants": deepcopy(intent["participants"]),
                "createdAt": observed_at,
            }
            world["bookingIntents"] = [
                row for row in world["bookingIntents"] if row.get("id") != intent_id
            ] + [summary]
            detail = f"Lead recorded booking intent {intent_id}; no product write occurred."

        event = {
            "id": f"{key}:{step}",
            "scenario": "needs-match",
            "interactionKey": key,
            "step": step,
            "actorId": actor_id,
            "detail": detail,
            "observedAt": observed_at,
        }
        self._append_unique(self.storage.run_path(key, "events.jsonl"), event)
        if actor_id != "lead":
            self._append_unique(self.storage.actor_journal_path(actor_id), event)
        add_signal(world, self._signal(observed_at, step.upper(), detail))

    def _execute_promotion_step(
        self,
        world: Dict[str, Any],
        interaction: Dict[str, Any],
        step: str,
        observed_at: str,
    ) -> None:
        key = interaction["key"]
        campaign = self.promotion_scenario
        actor_id = campaign["owner"]

        if step == "sofia_announces_priority_session":
            self._message(
                key,
                observed_at,
                actor_id,
                deepcopy(campaign["participants"]),
                campaign["message"],
                "owner_announcement",
            )
            interaction["announcementMessageId"] = (
                f"{key}:message:owner_announcement:{actor_id}"
            )
            campaign_summary = {
                "id": key,
                "status": "announced",
                "owner": actor_id,
                "message": campaign["message"],
                "createdAt": observed_at,
            }
            world["campaigns"] = [
                row for row in world.get("campaigns", []) if row.get("id") != key
            ] + [campaign_summary]
            detail = "Sofia announced a priority session for the next legal court slot."

        elif step == "red_accepts_priority_session":
            actor_id = "red-captain"
            self._message(
                key,
                observed_at,
                actor_id,
                [campaign["owner"]],
                "I am in for the next legal 30-minute slot.",
                "promotion_response",
            )
            interaction["redResponseMessageId"] = (
                f"{key}:message:promotion_response:{actor_id}"
            )
            world["actors"][actor_id]["state"] = "promotion_ready"
            detail = "Red accepted Sofia's priority-session announcement."

        elif step == "blue_accepts_priority_session":
            actor_id = "blue-captain"
            self._message(
                key,
                observed_at,
                actor_id,
                [campaign["owner"], "red-captain"],
                "I can join the next legal 30-minute slot too.",
                "promotion_response",
            )
            interaction["blueResponseMessageId"] = (
                f"{key}:message:promotion_response:{actor_id}"
            )
            world["actors"][actor_id]["state"] = "promotion_ready"
            detail = "Blue accepted Sofia's priority-session announcement."

        elif step == "lead_records_promotion_intent":
            actor_id = "lead"
            intent_id = f"intent:{key}"
            intent = {
                "schemaVersion": 1,
                "id": intent_id,
                "status": "agreed",
                "reason": "owner_priority_announcement",
                "createdAt": observed_at,
                "participants": deepcopy(campaign["participants"]),
                "requestedBy": campaign["owner"],
                "constraints": {
                    "durationMinutes": campaign["durationMinutes"],
                    "daysAhead": deepcopy(campaign["daysAhead"]),
                    "localWindows": deepcopy(campaign["localWindows"]),
                    "slotPolicy": campaign["slotPolicy"],
                },
                "evidence": {
                    "announcementMessageId": interaction["announcementMessageId"],
                    "redResponseMessageId": interaction["redResponseMessageId"],
                    "blueResponseMessageId": interaction["blueResponseMessageId"],
                },
                "remoteWrites": 0,
            }
            intent_ledger = load_intent_ledger(self.storage)
            save_intent(self.storage, intent_ledger, intent)
            interaction["intentId"] = intent_id
            interaction["status"] = "complete"
            world["bookingIntents"] = [
                row
                for row in world["bookingIntents"]
                if row.get("id") != intent_id
            ] + [
                {
                    "id": intent_id,
                    "status": "agreed",
                    "reason": intent["reason"],
                    "participants": deepcopy(intent["participants"]),
                    "createdAt": observed_at,
                }
            ]
            detail = f"Lead recorded promotion booking intent {intent_id}; no product write occurred."

        event = {
            "id": f"{key}:{step}",
            "scenario": "owner-promotion",
            "interactionKey": key,
            "step": step,
            "actorId": actor_id,
            "detail": detail,
            "observedAt": observed_at,
        }
        self._append_unique(self.storage.run_path(key, "events.jsonl"), event)
        if actor_id != "lead":
            self._append_unique(self.storage.actor_journal_path(actor_id), event)
        add_signal(world, self._signal(observed_at, step.upper(), detail))

    def _ensure_needs_state(self, world: Dict[str, Any]) -> None:
        needs = world.setdefault("needs", {})
        for actor_id, definition in self.needs_scenario["needs"].items():
            needs.setdefault(
                actor_id,
                {
                    "desireToPlay": definition["initialPressure"],
                    "threshold": definition["threshold"],
                    "driftPerBeat": definition["driftPerBeat"],
                },
            )
        world.setdefault("bookingIntents", [])
        world.setdefault("campaigns", [])
        world.setdefault("scenarios", {}).setdefault(
            "needs-match", {"interactions": {}}
        )

    def _needs_availability(self) -> Dict[str, Any]:
        return deepcopy(self.needs_scenario["availability"])

    def _message(
        self,
        key: str,
        observed_at: str,
        sender: str,
        recipients: List[str],
        text: str,
        kind: str,
    ) -> None:
        message = {
            "id": f"{key}:message:{kind}:{sender}",
            "channel": "club",
            "from": sender,
            "to": recipients,
            "kind": kind,
            "text": text,
            "observedAt": observed_at,
            "correlationId": key,
        }
        self._append_unique(self.storage.channel_path("club"), message)

    def _append_unique(self, path: Path, value: Dict[str, Any]) -> None:
        identifier = value.get("id")
        if identifier is not None:
            for existing in self.storage.read_jsonl(path):
                if existing.get("id") == identifier:
                    return
        self.storage.append_jsonl(path, value)

    def _upsert_world_booking(
        self, world: Dict[str, Any], booking: Dict[str, Any], state: str
    ) -> None:
        summary = {
            "id": booking["id"],
            "eventId": booking["eventId"],
            "occurrenceKey": booking["occurrenceKey"],
            "podId": booking["podId"],
            "startsAt": booking["startsAt"],
            "participants": deepcopy(booking["participants"]),
            "checkedIn": deepcopy(booking["checkedIn"]),
            "state": state,
        }
        for index, existing in enumerate(world["bookings"]):
            if existing["id"] == booking["id"]:
                world["bookings"][index] = summary
                return
        world["bookings"].append(summary)

    def _age_old_bookings(self, world: Dict[str, Any], current_hour: str) -> None:
        for booking in world["bookings"]:
            booking_hour = booking["occurrenceKey"].rsplit("@", 1)[-1]
            if booking_hour != current_hour and booking["state"] == "current":
                booking["state"] = "past"
                pod_id = booking["podId"]
                if world["pods"][pod_id]["bookingId"] == booking["id"]:
                    world["pods"][pod_id].update({"state": "empty", "bookingId": None})
                for actor in world["actors"].values():
                    if actor["location"] == pod_id:
                        actor.update({"location": "club", "state": "idle"})

    def _record_issue(
        self, world: Dict[str, Any], key: str, assertion: Dict[str, Any]
    ) -> None:
        issue_id = f"hourly-match-failed:{key}"
        issue = {
            "id": issue_id,
            "code": "hourly_match_failed",
            "detector": "assertion",
            "status": "open",
            "evidence": assertion,
        }
        issue_path = self.storage.state / "issues" / "open" / f"{issue_id}.json"
        self.storage.write_json(issue_path, issue)
        if issue_id not in world["attention"]:
            world["attention"].append(issue_id)

    @staticmethod
    def _signal(
        observed_at: str, kind: str, text: str, level: str = "info"
    ) -> Dict[str, Any]:
        return {
            "observedAt": observed_at,
            "kind": kind,
            "text": text,
            "level": level,
        }
