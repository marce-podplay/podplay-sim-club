"""Local consent gate for a staff-published free team-signup event."""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Dict

from .booking_intents import load_intent_ledger, save_intent
from .storage import Storage
from .time import isoformat


class StaffTeamEventError(ValueError):
    pass


def offer(root: Path, challenge_id: str, now: datetime) -> Dict:
    """Let staff offer a free event after (not instead of) captain agreement."""
    storage = Storage(root)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        challenge = ledger["intents"].get(challenge_id)
        if not isinstance(challenge, dict) or challenge.get("journey") != "customer_team_signup":
            raise StaffTeamEventError("accepted team challenge is unavailable")
        if challenge.get("status") not in {"agreed", "preview_blocked"}:
            raise StaffTeamEventError("staff can offer a free event only after captain agreement")
        participants = challenge.get("participants")
        left, right = challenge.get("challenger"), challenge.get("opponent")
        if not isinstance(participants, list) or len(participants) != 4 or not isinstance(left, dict) or not isinstance(right, dict):
            raise StaffTeamEventError("challenge does not describe two complete teams")
        base_id = f"intent:staff-team-event:{challenge_id.rsplit(':', 1)[-1]}"
        existing = ledger["intents"].get(base_id)
        # A finished or inconclusive event is evidence, never standing consent
        # for a replacement.  Retain it and issue a distinct offer requiring
        # fresh individual acknowledgements.
        if isinstance(existing, dict) and existing.get("status") not in {
            "missed_session", "attendance_observed", "attendance_inconclusive", "declined",
        }:
            return deepcopy(existing)
        sequence = 1
        event_id = base_id
        while event_id in ledger["intents"]:
            sequence += 1
            event_id = f"{base_id}:{sequence:03d}"
        observed_at = isoformat(now)
        intent = {
            "schemaVersion": 1, "id": event_id, "journey": "owner_team_signup",
            "status": "awaiting_player_acceptance", "reason": "staff_free_team_research",
            "sourceChallengeId": challenge_id, "createdAt": observed_at,
            "requestedBy": "sofia", "bookerActorId": "sofia", "participants": list(participants),
            "teams": [
                {"captainActorId": left["captainActorId"], "memberActorId": left["teammateActorId"], "teamName": left["teamName"]},
                {"captainActorId": right["captainActorId"], "memberActorId": right["teammateActorId"], "teamName": right["teamName"]},
            ],
            "teamSize": 2, "totalTeams": 2,
            "constraints": deepcopy(challenge["constraints"]), "acknowledgements": {},
            "nextAction": "await_each_named_player_accept_or_pass", "remoteWrites": 0,
        }
        save_intent(storage, ledger, intent)
        storage.append_jsonl(storage.channel_path("club"), {
            "id": f"{event_id}:staff-offer", "channel": "club", "kind": "staff_free_team_event_offer",
            "from": "sofia", "to": list(participants), "intentId": event_id,
            "text": "Sofia offers a free, staff-published 2×2 Preview research event for the agreed teams. Each named player must accept or pass for themselves; this is not attendance.",
            "observedAt": observed_at, "remoteWrites": 0,
        })
        return deepcopy(intent)


def acknowledge(root: Path, intent_id: str, actor_id: str, decision: str, now: datetime) -> Dict:
    if decision not in {"accept", "pass"}:
        raise StaffTeamEventError("decision must be accept or pass")
    storage = Storage(root)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        return acknowledge_locked(storage, ledger, intent_id, actor_id, decision, now)


def acknowledge_locked(storage: Storage, ledger: Dict, intent_id: str, actor_id: str, decision: str, now: datetime) -> Dict:
    """Record one named response while the caller already owns the ledger lock."""
    intent = ledger["intents"].get(intent_id)
    if not isinstance(intent, dict) or intent.get("journey") != "owner_team_signup":
        raise StaffTeamEventError("staff team event is unavailable")
    if actor_id not in intent.get("participants", []):
        raise StaffTeamEventError("only a named player may respond")
    acks = dict(intent.get("acknowledgements") or {})
    if actor_id in acks:
        return deepcopy(intent)
    observed_at = isoformat(now)
    acks[actor_id] = {"decision": decision, "observedAt": observed_at}
    intent = deepcopy(intent)
    intent["acknowledgements"] = acks
    if decision == "pass":
        intent.update({"status": "declined", "nextAction": "retain_as_non_booking_history"})
    elif all(acks.get(actor, {}).get("decision") == "accept" for actor in intent["participants"]):
        intent.update({"status": "ready_to_publish", "nextAction": "create_guarded_free_team_event"})
    save_intent(storage, ledger, intent)
    storage.append_jsonl(storage.channel_path("club"), {
        "id": f"{intent_id}:player-{actor_id}-{decision}", "channel": "club", "kind": "staff_event_player_response",
        "from": actor_id, "to": ["sofia"], "intentId": intent_id,
        "text": f"{actor_id} {'accepts' if decision == 'accept' else 'passes'} the free team-event research request for themself.",
        "observedAt": observed_at, "remoteWrites": 0,
    })
    return deepcopy(intent)
