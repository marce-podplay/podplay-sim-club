"""Deterministic fake-world state machine."""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .actions import ActionValidator
from .fake_preview import FakePreview
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


class SimulatedCrash(RuntimeError):
    """Fault injection raised after a fake remote commit but before checkpoint."""


class Orchestrator:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.storage = Storage(self.root)
        self.seed = self.storage.load_json(self.root / "seed" / "tenant.json")
        self.scenario = self.storage.load_json(
            self.root / "scenarios" / "hourly-match.json"
        )
        if self.seed is None or self.scenario is None:
            raise RuntimeError("seed/tenant.json and scenarios/hourly-match.json are required")
        self.validator = ActionValidator(
            pod_id=self.scenario["podId"],
            allowed_actions=self.scenario["allowedActions"],
        )

    def ensure_world(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        instant = now or parse_instant(None)
        observed_at = isoformat(instant)
        world = self.storage.load_json(self.storage.world_path, default=None)
        preview = FakePreview(self.storage, self.seed)

        if world is None:
            if preview.is_fresh():
                preview.rehydrate()
            world = new_world(1, observed_at)
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
            world = new_world(next_number, observed_at)
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
        return world

    def reset_fake_preview(self) -> None:
        preview = FakePreview(self.storage, self.seed)
        preview.reset_database()

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
            preview = FakePreview(self.storage, self.seed)
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

    def world_view(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        with self.storage.writer_lock():
            world = self.ensure_world(now)
            return project_world(world)

    def _execute_step(
        self,
        world: Dict[str, Any],
        preview: FakePreview,
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
