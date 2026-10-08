"""Typed community affiliations, separate from PodPlay operating franchises.

This belongs to the simulator's Teams and play-format context.  It deliberately
does not use the word ``franchise``: PodPlay already uses that word for an
operational group of areas.  An affinity group is descriptive social context,
not product authority, eligibility, or a rivalry declaration.
"""

from copy import deepcopy
from itertools import combinations
from typing import Any, Dict, Iterable, List


SUPPORTED_KINDS = {"game_series", "region"}
RIVALRY_INTENSITIES = {"friendly", "high"}


class AffinityGroupError(ValueError):
    """Raised for an invalid local community-affiliation configuration."""


def validate_roster_affinity_groups(roster: Any) -> None:
    """Validate only the neutral affiliation vocabulary in a roster."""
    if not isinstance(roster, dict):
        raise AffinityGroupError("roster must be an object")
    groups = roster.get("affinityGroups")
    teams = roster.get("teams")
    if not isinstance(groups, list) or not isinstance(teams, list):
        raise AffinityGroupError("roster needs affinity groups and teams")
    known = set()
    for group in groups:
        if not isinstance(group, dict):
            raise AffinityGroupError("affinity group must be an object")
        group_id, kind, label = group.get("id"), group.get("kind"), group.get("label")
        if not isinstance(group_id, str) or not group_id or group_id in known:
            raise AffinityGroupError("affinity group IDs must be unique nonempty strings")
        if kind not in SUPPORTED_KINDS or not isinstance(label, str) or not label:
            raise AffinityGroupError("affinity group kind or label is invalid")
        known.add(group_id)
    for team in teams:
        if not isinstance(team, dict) or not isinstance(team.get("id"), str):
            raise AffinityGroupError("team is invalid")
        memberships = team.get("affinityGroupIds")
        if not isinstance(memberships, list) or not memberships or any(not isinstance(value, str) or value not in known for value in memberships):
            raise AffinityGroupError("team affinity groups must reference known groups")
        if len(set(memberships)) != len(memberships):
            raise AffinityGroupError("team affinity groups must not repeat")


def groups_for_team(roster: Dict[str, Any], team_id: str) -> List[Dict[str, str]]:
    """Return only safe public labels for a team's configured affiliations."""
    validate_roster_affinity_groups(roster)
    groups = {group["id"]: group for group in roster["affinityGroups"]}
    team = next((row for row in roster["teams"] if row.get("id") == team_id), None)
    if not isinstance(team, dict):
        raise AffinityGroupError("team is not in roster")
    return [
        {"id": group_id, "kind": groups[group_id]["kind"], "label": groups[group_id]["label"]}
        for group_id in team["affinityGroupIds"]
    ]


def team_relationships(roster: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Derive explainable team-context relationships from static roster data.

    Explicit rivalry remains its own source. Shared affinity only records a
    common identity; it never implies a match, friendliness, or a lower
    competitive drive.
    """
    validate_roster_affinity_groups(roster)
    rivalries = {}
    for row in roster.get("rivalries", []):
        if not isinstance(row, dict) or not isinstance(row.get("teams"), list) or len(row["teams"]) != 2:
            continue
        left, right = sorted(row["teams"])
        intensity = row.get("intensity")
        if intensity in RIVALRY_INTENSITIES:
            rivalries[(left, right)] = {"intensity": intensity, "preferredJourney": row.get("preferredJourney")}
    result = []
    teams = sorted((row for row in roster["teams"] if isinstance(row, dict)), key=lambda row: row["id"])
    for left, right in combinations(teams, 2):
        left_id, right_id = left["id"], right["id"]
        shared = sorted(set(left["affinityGroupIds"]).intersection(right["affinityGroupIds"]))
        rivalry = rivalries.get((left_id, right_id))
        result.append({
            "teamIds": [left_id, right_id],
            "sharedAffinityGroupIds": shared,
            "explicitRivalry": deepcopy(rivalry) if rivalry else None,
        })
    return result
