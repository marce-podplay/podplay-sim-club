"""Product boundary consumed by deterministic scenarios."""

from typing import Any, Dict, Optional, Protocol, Tuple, runtime_checkable


JsonObject = Dict[str, Any]


@runtime_checkable
class PreviewAdapter(Protocol):
    mode: str

    def is_fresh(self) -> bool:
        ...

    def rehydrate(self) -> None:
        ...

    def booking_for(self, occurrence_key: str) -> Optional[JsonObject]:
        ...

    def create_booking(
        self, occurrence_key: str, pod_id: str, starts_at: str, owner_id: str
    ) -> Tuple[JsonObject, bool]:
        ...

    def invite(self, occurrence_key: str, invitee_id: str) -> JsonObject:
        ...

    def accept(self, occurrence_key: str, invitee_id: str) -> JsonObject:
        ...

    def check_in(self, occurrence_key: str, actor_id: str) -> bool:
        ...

    def event(self, occurrence_key: str) -> JsonObject:
        ...
