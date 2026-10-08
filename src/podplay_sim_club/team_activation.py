"""Durable, secret-free record that a roster team has verified customer logins."""

from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from .storage import Storage
from .time import isoformat, parse_instant


STATE_FILE = "team-activations.json"


def record_verified_team_activation(
    root: Path, team_id: str, actor_ids: Iterable[str], now: Optional[datetime] = None
) -> dict:
    """Mark a team as ready for a local welcome, never as booked or funded.

    The caller has already verified each customer's Preview identity.  This
    record deliberately retains only team and actor ids, so it cannot expose a
    credential, product user id, or account balance.
    """
    members = tuple(actor_id for actor_id in actor_ids if isinstance(actor_id, str))
    if not isinstance(team_id, str) or not team_id or not members:
        raise ValueError("team activation needs a team and named actors")
    storage = Storage(root)
    observed_at = isoformat(now or parse_instant(None))
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        if not isinstance(state, dict):
            state = {}
        activations = state.setdefault("activations", {})
        current = activations.get(team_id)
        if not isinstance(current, dict):
            current = {
                "teamId": team_id,
                "actorIds": list(members),
                "verifiedAt": observed_at,
            }
            activations[team_id] = current
        else:
            current["actorIds"] = list(members)
            current["verifiedAt"] = observed_at
        state["schemaVersion"] = 1
        storage.write_json(storage.state / STATE_FILE, state)
        return dict(current)
