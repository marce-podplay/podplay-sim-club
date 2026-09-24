"""World creation and read-only projection."""

from copy import deepcopy
from typing import Any, Dict


ACTORS = {
    "sofia": {"name": "Sofia", "role": "owner", "glyph": "S"},
    "alex": {"name": "Alex", "role": "podplay_admin", "glyph": "A"},
    "riley": {"name": "Riley", "role": "customer_success", "glyph": "C"},
    "red-captain": {"name": "Red Captain", "role": "player", "glyph": "R"},
    "blue-captain": {"name": "Blue Captain", "role": "player", "glyph": "B"},
}


def new_world(season_number: int, observed_at: str) -> Dict[str, Any]:
    actors = {}
    for actor_id, definition in ACTORS.items():
        actor = deepcopy(definition)
        actor.update(
            {
                "location": "outside" if actor_id in {"alex", "riley"} else "club",
                "state": "idle",
                "unread": 0,
            }
        )
        actors[actor_id] = actor

    return {
        "schemaVersion": 1,
        "season": {"id": f"season-{season_number:03d}", "number": season_number},
        "clock": {
            "source": "fake-preview",
            "observedAt": observed_at,
            "simulationTime": observed_at,
            "rate": 1,
        },
        "preview": {"mode": "fake", "reachable": True, "approved": True},
        "beat": {"count": 0, "lastTurnCount": 0},
        "actors": actors,
        "pods": {
            "pod-1": {"name": "POD-1", "state": "empty", "bookingId": None},
            "pod-2": {"name": "POD-2", "state": "empty", "bookingId": None},
        },
        "bookings": [],
        "signals": [],
        "attention": [],
        "scenarios": {"hourly-match": {"occurrences": {}}},
    }


def add_signal(world: Dict[str, Any], signal: Dict[str, Any]) -> None:
    world["signals"].append(signal)
    world["signals"] = world["signals"][-50:]


def project_world(world: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schemaVersion": world["schemaVersion"],
        "observedAt": world["clock"]["observedAt"],
        "season": world["season"]["id"],
        "preview": deepcopy(world["preview"]),
        "beat": deepcopy(world["beat"]),
        "people": deepcopy(world["actors"]),
        "pods": deepcopy(world["pods"]),
        "bookings": deepcopy(world["bookings"][-12:]),
        "signals": deepcopy(world["signals"][-12:]),
        "attention": deepcopy(world["attention"]),
    }
