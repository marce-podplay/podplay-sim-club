"""Deterministic fake-world state machine."""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .actions import ActionValidator
from .booking_intents import load_intent_ledger, save_intent
from .fake_preview import FakePreview
from .preview import PreviewAdapter
from .storage import Storage
from .time import hour_key, isoformat, parse_instant
from .world import add_signal, new_world, project_world


STEPS = (
    "player_wants_to_play",
    "teammate_agrees",
    "booking_created",
    "teammate_invited",
    "teammate_accepts",
    "booker_checks_in",
    "invitee_checks_in",
    "assert_match",
)

NEED_STEPS = (
    "desire_to_play_rises",
    "player_proposes_match",
    "teammate_agrees_to_play",
    "lead_records_booking_intent",
)

PROMOTION_STEPS = (
    "sofia_announces_event",
    "player_accepts_event",
    "teammate_accepts_event",
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
                    "participants": ["andy-bogard", "terry-bogard"],
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

    def _match_players(self) -> Tuple[str, str]:
        players = self.scenario.get("players")
        if not isinstance(players, list) or len(players) != 2:
            raise RuntimeError("hourly match requires a booker and a teammate")
        return str(players[0]), str(players[1])

    def _need_players(self) -> Tuple[str, str]:
        return (
            str(self.needs_scenario["initiator"]),
            str(self.needs_scenario["candidate"]),
        )

    def _player_name(self, world: Dict[str, Any], actor_id: str) -> str:
        actor = world["actors"].get(actor_id, {})
        name = actor.get("name") if isinstance(actor, dict) else None
        return name if isinstance(name, str) and name else actor_id

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
        booker, teammate = self._match_players()
        booker_name = self._player_name(world, booker)
        teammate_name = self._player_name(world, teammate)
        actor_id = "lead"
        detail = step

        if step == "player_wants_to_play":
            actor_id = booker
            detail = f"{booker_name} wants to play and will book a court for {teammate_name}."
            self._message(
                key,
                observed_at,
                booker,
                [teammate],
                f"I want to play this hour. I will book and invite you, {teammate_name}.",
                "proposal",
            )
            world["actors"][booker]["state"] = "waiting_for_reply"

        elif step == "teammate_agrees":
            actor_id = teammate
            detail = f"{teammate_name} agreed to accept {booker_name}'s invitation."
            self._message(
                key,
                observed_at,
                teammate,
                [booker],
                "Yes. Book it and send the invitation. I will accept.",
                "agreement",
            )
            world["actors"][teammate]["state"] = "agreed"

        elif step == "booking_created":
            actor_id = booker
            request = {
                "type": "book_match",
                "arguments": {"podId": pod_id, "occurrenceKey": key},
            }
            self.validator.validate(request)
            booking, created = preview.create_booking(
                key, pod_id, occurrence["startsAt"], booker
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

        elif step == "teammate_invited":
            actor_id = booker
            self.validator.validate(
                {"type": "invite_player", "arguments": {"podId": pod_id}}
            )
            invitation = preview.invite(key, teammate)
            if crash_after_remote == step:
                raise SimulatedCrash("fake preview committed invitation before checkpoint")
            occurrence["invitationId"] = invitation["id"]
            detail = f"{teammate_name} invitation {invitation['id']} recorded."

        elif step == "teammate_accepts":
            actor_id = teammate
            self.validator.validate(
                {"type": "accept_invitation", "arguments": {"podId": pod_id}}
            )
            preview.accept(key, teammate)
            if crash_after_remote == step:
                raise SimulatedCrash("fake preview committed acceptance before checkpoint")
            detail = f"{teammate_name} accepted the invitation."

        elif step == "booker_checks_in":
            actor_id = booker
            self.validator.validate(
                {"type": "check_in", "arguments": {"podId": pod_id}}
            )
            preview.check_in(key, actor_id)
            if crash_after_remote == step:
                raise SimulatedCrash("fake preview committed booker check-in before checkpoint")
            world["actors"][actor_id].update({"location": pod_id, "state": "playing"})
            detail = f"{booker_name} checked in."

        elif step == "invitee_checks_in":
            actor_id = teammate
            self.validator.validate(
                {"type": "check_in", "arguments": {"podId": pod_id}}
            )
            preview.check_in(key, actor_id)
            if crash_after_remote == step:
                raise SimulatedCrash("fake preview committed invitee check-in before checkpoint")
            world["actors"][actor_id].update({"location": pod_id, "state": "playing"})
            world["pods"][pod_id]["state"] = "occupied"
            detail = f"{teammate_name} checked in; the match is live."

        elif step == "assert_match":
            event = preview.event(key)
            expected = {booker, teammate}
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
                detail = "Hourly match passed: the booking, invitation, and both check-ins are recorded."
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
        booker, teammate = self._need_players()
        booker_name = self._player_name(world, booker)
        teammate_name = self._player_name(world, teammate)
        actor_id = "lead"

        if step == "desire_to_play_rises":
            actor_id = booker
            need = world["needs"][actor_id]
            before = int(need["desireToPlay"])
            need["desireToPlay"] = min(100, before + int(need["driftPerBeat"]))
            detail = (
                f"{booker_name} desire-to-play rose from {before} to "
                f"{need['desireToPlay']} (threshold {need['threshold']})."
            )
            world["actors"][actor_id]["state"] = "motivated"

        elif step == "player_proposes_match":
            actor_id = booker
            need = world["needs"][actor_id]
            if int(need["desireToPlay"]) < int(need["threshold"]):
                raise RuntimeError("desire to play has not crossed its threshold")
            text = (
                f"I want to play. {teammate_name}, are you free for a 30-minute "
                "morning match in the next two weeks? I will book it and invite you."
            )
            self._message(
                key,
                observed_at,
                actor_id,
                [teammate],
                text,
                "need_driven_proposal",
            )
            interaction["proposalMessageId"] = (
                f"{key}:message:need_driven_proposal:{actor_id}"
            )
            world["actors"][actor_id]["state"] = "waiting_for_reply"
            world["actors"][teammate]["state"] = "considering"
            detail = (
                f"{booker_name} asked {teammate_name} to play because "
                "desire-to-play crossed its threshold."
            )

        elif step == "teammate_agrees_to_play":
            actor_id = teammate
            availability = self._needs_availability()
            overlap = set(availability[booker]["localWindows"]) & set(
                availability[teammate]["localWindows"]
            )
            if not overlap:
                raise RuntimeError("players have no overlapping availability")
            text = (
                f"Yes. Book a 30-minute morning match in that window and invite me. "
                "I will accept."
            )
            self._message(
                key,
                observed_at,
                actor_id,
                [booker],
                text,
                "availability_agreement",
            )
            interaction["agreementMessageId"] = (
                f"{key}:message:availability_agreement:{actor_id}"
            )
            interaction["agreedWindow"] = sorted(overlap)[0]
            world["actors"][actor_id]["state"] = "agreed"
            world["actors"][booker]["state"] = "agreed"
            detail = f"{teammate_name} found overlapping availability and agreed to accept the invitation."

        elif step == "lead_records_booking_intent":
            intent_id = f"intent:{key}"
            intent = {
                "schemaVersion": 1,
                "id": intent_id,
                "status": "agreed",
                "reason": "desire_to_play",
                "createdAt": observed_at,
                "participants": [booker, teammate],
                "requestedBy": booker,
                "constraints": {
                    "durationMinutes": 30,
                    "daysAhead": [0, 14],
                    "localWindows": [interaction["agreedWindow"]],
                    "slotPolicy": "nearest_valid_low_contention",
                },
                "evidence": {
                    "proposalMessageId": interaction["proposalMessageId"],
                    "agreementMessageId": interaction["agreementMessageId"],
                    "need": deepcopy(world["needs"][booker]),
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
            detail = f"Lead recorded booking intent {intent_id}; the booker can invite the teammate. No product write occurred."

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
        players = campaign["participants"]
        booker, teammate = str(players[0]), str(players[1])
        actor_id = campaign["owner"]

        if step == "sofia_announces_event":
            self._message(
                key,
                observed_at,
                actor_id,
                deepcopy(players),
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
            detail = "Sofia announced an event players can accept."

        elif step == "player_accepts_event":
            actor_id = booker
            self._message(
                key,
                observed_at,
                actor_id,
                [campaign["owner"]],
                "I want that session. I will book it and invite my teammate.",
                "promotion_response",
            )
            interaction["playerResponseMessageId"] = (
                f"{key}:message:promotion_response:{actor_id}"
            )
            world["actors"][actor_id]["state"] = "promotion_ready"
            detail = f"{self._player_name(world, booker)} accepted Sofia's event and will invite a teammate."

        elif step == "teammate_accepts_event":
            actor_id = teammate
            self._message(
                key,
                observed_at,
                actor_id,
                [campaign["owner"], booker],
                "I want that session too. Send me the invitation and I will accept.",
                "promotion_response",
            )
            interaction["teammateResponseMessageId"] = (
                f"{key}:message:promotion_response:{actor_id}"
            )
            world["actors"][actor_id]["state"] = "promotion_ready"
            detail = f"{self._player_name(world, teammate)} accepted Sofia's event."

        elif step == "lead_records_promotion_intent":
            actor_id = "lead"
            intent_id = f"intent:{key}"
            intent = {
                "schemaVersion": 1,
                "id": intent_id,
                "status": "agreed",
                "reason": "owner_priority_announcement",
                "createdAt": observed_at,
                "participants": deepcopy(players),
                "requestedBy": booker,
                "constraints": {
                    "durationMinutes": campaign["durationMinutes"],
                    "daysAhead": deepcopy(campaign["daysAhead"]),
                    "localWindows": deepcopy(campaign["localWindows"]),
                    "slotPolicy": campaign["slotPolicy"],
                },
                "evidence": {
                    "announcementMessageId": interaction["announcementMessageId"],
                    "playerResponseMessageId": interaction["playerResponseMessageId"],
                    "teammateResponseMessageId": interaction["teammateResponseMessageId"],
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
            detail = f"Lead recorded event intent {intent_id}; the booker can invite the teammate. No product write occurred."

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
        intent_ledger = load_intent_ledger(self.storage)
        for campaign in world["campaigns"]:
            if not isinstance(campaign, dict) or not isinstance(campaign.get("id"), str):
                continue
            intent = intent_ledger["intents"].get(f"intent:{campaign['id']}")
            if isinstance(intent, dict) and isinstance(intent.get("status"), str):
                campaign["status"] = intent["status"]
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
