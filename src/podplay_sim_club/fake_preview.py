"""Deterministic fake PodPlay boundary used before any network integration."""

from copy import deepcopy
import hashlib
from typing import Any, Dict, Optional, Tuple

from .storage import Storage


class FakePreview:
    mode = "fake"

    def __init__(self, storage: Storage, seed: Dict[str, Any]):
        self.storage = storage
        self.seed = seed
        self.data = storage.load_json(storage.fake_preview_path, default=None)
        if self.data is None:
            self.data = self._fresh_data()
            self.rehydrate()

    def _fresh_data(self) -> Dict[str, Any]:
        return {
            "schemaVersion": 1,
            "mode": "fake",
            "seedFingerprint": None,
            "club": None,
            "users": {},
            "bookings": [],
        }

    @property
    def fingerprint(self) -> str:
        configured = self.seed.get("fingerprint", "preview-club-v1")
        return str(configured)

    def is_fresh(self) -> bool:
        return self.data.get("seedFingerprint") != self.fingerprint

    def rehydrate(self) -> None:
        self.data["seedFingerprint"] = self.fingerprint
        self.data["club"] = deepcopy(self.seed["club"])
        self.data["users"] = deepcopy(self.seed["users"])
        self.data.setdefault("bookings", [])
        self.save()

    def reset_database(self) -> None:
        self.data = self._fresh_data()
        self.save()

    def save(self) -> None:
        self.storage.write_json(self.storage.fake_preview_path, self.data)

    @staticmethod
    def _id(prefix: str, key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
        return f"{prefix}-{digest}"

    def booking_for(self, occurrence_key: str) -> Optional[Dict[str, Any]]:
        for booking in self.data["bookings"]:
            if booking["occurrenceKey"] == occurrence_key:
                return booking
        return None

    def create_booking(
        self, occurrence_key: str, pod_id: str, starts_at: str, owner_id: str
    ) -> Tuple[Dict[str, Any], bool]:
        existing = self.booking_for(occurrence_key)
        if existing is not None:
            return existing, False
        booking = {
            "id": self._id("booking", occurrence_key),
            "eventId": self._id("event", occurrence_key),
            "occurrenceKey": occurrence_key,
            "podId": pod_id,
            "startsAt": starts_at,
            "ownerId": owner_id,
            "participants": [owner_id],
            "invitation": None,
            "checkedIn": [],
        }
        self.data["bookings"].append(booking)
        self.save()
        return booking, True

    def invite(self, occurrence_key: str, invitee_id: str) -> Dict[str, Any]:
        booking = self._required_booking(occurrence_key)
        if booking["invitation"] is None:
            booking["invitation"] = {
                "id": self._id("invite", f"{occurrence_key}:{invitee_id}"),
                "inviteeId": invitee_id,
                "accepted": False,
            }
            self.save()
        return booking["invitation"]

    def accept(self, occurrence_key: str, invitee_id: str) -> Dict[str, Any]:
        booking = self._required_booking(occurrence_key)
        invitation = booking.get("invitation")
        if invitation is None or invitation["inviteeId"] != invitee_id:
            raise ValueError("matching invitation is required before acceptance")
        invitation["accepted"] = True
        if invitee_id not in booking["participants"]:
            booking["participants"].append(invitee_id)
        self.save()
        return invitation

    def check_in(self, occurrence_key: str, actor_id: str) -> bool:
        booking = self._required_booking(occurrence_key)
        if actor_id not in booking["participants"]:
            raise ValueError(f"{actor_id} is not a participant")
        if actor_id in booking["checkedIn"]:
            return False
        booking["checkedIn"].append(actor_id)
        self.save()
        return True

    def event(self, occurrence_key: str) -> Dict[str, Any]:
        return deepcopy(self._required_booking(occurrence_key))

    def _required_booking(self, occurrence_key: str) -> Dict[str, Any]:
        booking = self.booking_for(occurrence_key)
        if booking is None:
            raise ValueError(f"booking does not exist for {occurrence_key}")
        return booking
