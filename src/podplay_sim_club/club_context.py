"""Public, tenant-neutral identity boundary for a fictional Club Lab.

A Club Lab is a durable employer/community context, not a Preview URL and not
a PodPlay tenant.  A Preview target may change during a season; the community
name and its local staff do not.  Players deliberately live above this
boundary and may visit more than one Club Lab.
"""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Tuple

from .players import canonical_actor_id


class ClubContextError(ValueError):
    pass


@dataclass(frozen=True)
class ClubContext:
    key: str
    display_name: str
    staff_actor_ids: Tuple[str, ...]
    player_actor_ids: Tuple[str, ...]


def load_club_context(root: Path) -> ClubContext:
    """Load the local Club Lab's public identity contract.

    This file must contain no target URL, tenant identifier, credential, or
    product user identifier.  A future Club Lab gets a new context file and
    its own staff actors; it does not repurpose this one.
    """
    path = root / "seed" / "club-context.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClubContextError("seed/club-context.json is missing or invalid") from exc
    if not isinstance(raw, dict) or raw.get("schemaVersion") != 1:
        raise ClubContextError("unsupported club context")

    key = raw.get("key")
    display_name = raw.get("displayName")
    staff = raw.get("staffActorIds")
    players = raw.get("playerActorIds")
    if not isinstance(key, str) or not key or not key.replace("-", "").isalnum():
        raise ClubContextError("club context key is invalid")
    if not isinstance(display_name, str) or not display_name.strip():
        raise ClubContextError("club context display name is invalid")
    if not _actor_ids(staff) or not _actor_ids(players):
        raise ClubContextError("club context actors are missing or invalid")
    staff_ids = tuple(canonical_actor_id(actor_id) for actor_id in staff)
    player_ids = tuple(canonical_actor_id(actor_id) for actor_id in players)
    if len(set(staff_ids)) != len(staff_ids) or len(set(player_ids)) != len(player_ids):
        raise ClubContextError("club context actor ids must be unique")
    if set(staff_ids) & set(player_ids):
        raise ClubContextError("a Club Lab staff actor cannot also be a player")
    return ClubContext(key, display_name.strip(), staff_ids, player_ids)


def _actor_ids(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str) and item for item in value
    )
