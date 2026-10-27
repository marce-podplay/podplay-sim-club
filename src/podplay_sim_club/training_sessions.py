"""Captain-led two-player training agreements with no preview writes."""

from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from .booking_intents import load_intent_ledger, save_intent
from .protocol import (
    append_protocol_facts_locked,
    commitment_id_for,
    conversation_id_for,
    make_commitment_proposal,
    make_commitment_transition,
    make_message,
    reply_causation,
)
from .storage import Storage
from .time import isoformat


class TrainingSessionError(ValueError):
    pass


BOOKING_AUTHORITY_LIFETIME = timedelta(hours=2)
ACTIONABLE_TRAINING_STATUSES = {"captain_invited", "agreed", "preview_ready", "planned", "awaiting_third_player"}


def invite_third_player(root: Path, intent_id: str, captain: str, third_player: str, now: datetime) -> Dict[str, Any]:
    """Address an optional local third-player request for a team practice.

    This is social evidence only: it neither books nor promises payment.  The
    existing pair remains a fact, while booking pauses until the recipient
    explicitly accepts.
    """
    storage = Storage(root)
    observed_at = isoformat(now)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        original = ledger["intents"].get(intent_id)
        if not isinstance(original, dict) or original.get("journey") != "customer_training":
            raise TrainingSessionError("training agreement is unavailable")
        participants = original.get("participants")
        if (original.get("bookerActorId") != captain or not isinstance(participants, list)
                or len(participants) != 2 or captain not in participants or third_player in participants):
            raise TrainingSessionError("third-player invitation does not match this captain-led pair")
        roster = storage.load_json(storage.root / "characters" / "roster.json", default={})
        known = {row.get("actorId") for row in roster.get("characters", [])
                 if isinstance(row, dict) and isinstance(row.get("actorId"), str)} if isinstance(roster, dict) else set()
        if third_player not in known:
            raise TrainingSessionError("third player is not in the Club roster")
        existing = original.get("thirdPlayerInvitation")
        if isinstance(existing, dict):
            if existing.get("actorId") != third_player:
                raise TrainingSessionError("a different third-player invitation is already awaiting a decision")
            return deepcopy(original)
        intent = deepcopy(original)
        invitation_id = f"{intent_id}:third-player:{third_player}"
        intent.update({
            "status": "awaiting_third_player",
            "thirdPlayerInvitation": {"actorId": third_player, "status": "invited", "invitedAt": observed_at,
                                      "reason": "captain_requested_optional_group_training"},
            "nextAction": "await_third_player_acceptance_before_player_booking",
        })
        save_intent(storage, ledger, intent)
        message = {
            "id": invitation_id, "channel": "club", "kind": "training_third_player_invitation",
            "conversationId": intent.get("conversationId"), "commitmentId": intent.get("commitmentId"),
            "speechAct": "request", "from": captain, "to": [third_player], "intentId": intent_id,
            "text": "Would you join this 30-minute training? The booking party size can be adjusted before any payment. Your reply is not a booking or payment commitment.",
            "observedAt": observed_at, "remoteWrites": 0,
        }
        storage.append_jsonl(storage.channel_path("club"), message)
        message_fact = make_message(
            message_id=invitation_id, conversation_id=str(intent["conversationId"]), correlation_id=f"journey:{intent_id}",
            sender_actor_id=captain, recipient_actor_ids=[third_player], speech_act="request",
            content_code="training.third_player.requested", occurred_at=observed_at,
            commitment_id=str(intent["commitmentId"]),
            parameters={"intentRef": intent_id, "reason": "captain_requested_optional_group_training"},
        )
        append_protocol_facts_locked(storage, [message_fact])
    return intent


def withdraw_third_player_invitation(root: Path, intent_id: str, captain: str, now: datetime) -> Dict[str, Any]:
    """Withdraw an unaccepted optional third-player request.

    This corrects a social plan before a recipient responds; it never affects
    a product booking, payment, or player identity.
    """
    storage = Storage(root)
    observed_at = isoformat(now)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        original = ledger["intents"].get(intent_id)
        invitation = original.get("thirdPlayerInvitation") if isinstance(original, dict) else None
        if (
            not isinstance(original, dict)
            or original.get("journey") != "customer_training"
            or original.get("bookerActorId") != captain
            or original.get("status") != "awaiting_third_player"
            or not isinstance(invitation, dict)
            or invitation.get("status") != "invited"
        ):
            raise TrainingSessionError("third-player invitation is not available to withdraw")
        recipient = invitation.get("actorId")
        if not isinstance(recipient, str):
            raise TrainingSessionError("third-player invitation has no recipient")
        intent = deepcopy(original)
        intent.update({
            "status": "agreed",
            "thirdPlayerInvitation": {**invitation, "status": "withdrawn", "withdrawnAt": observed_at,
                                      "withdrawalReason": "party_size_is_adjustable_at_visible_checkout"},
            "nextAction": "plan_live_w37_availability_then_player_booking",
        })
        save_intent(storage, ledger, intent)
        message_id = f"{intent_id}:third-player:{recipient}:withdrawal"
        message = {
            "id": message_id, "channel": "club", "kind": "training_third_player_withdrawal",
            "conversationId": intent.get("conversationId"), "commitmentId": intent.get("commitmentId"),
            "speechAct": "inform", "from": captain, "to": [recipient], "intentId": intent_id,
            "text": "The optional third-player request was withdrawn: the visible checkout supports the original pair. No booking or payment was made.",
            "observedAt": observed_at, "remoteWrites": 0,
        }
        storage.append_jsonl(storage.channel_path("club"), message)
        append_protocol_facts_locked(storage, [make_message(
            message_id=message_id, conversation_id=str(intent["conversationId"]), correlation_id=f"journey:{intent_id}",
            sender_actor_id=captain, recipient_actor_ids=[recipient], speech_act="inform",
            content_code="training.third_player.withdrawn", occurred_at=observed_at,
            commitment_id=str(intent["commitmentId"]),
            parameters={"intentRef": intent_id, "reason": "party_size_is_adjustable_at_visible_checkout"},
        )])
    return intent


def remaining_team_member(root: Path, intent: Dict[str, Any]) -> Optional[str]:
    """Return the sole rostered teammate not yet named on a team practice.

    This is only a social suggestion for an optional larger practice.  The
    player-facing checkout owns the actual party-size choice and may allow the
    existing pair to book directly.
    """
    team_id = intent.get("trainingTeamId")
    participants = intent.get("participants")
    if not isinstance(team_id, str) or not isinstance(participants, list) or len(participants) != 2:
        return None
    storage = Storage(root)
    roster = storage.load_json(storage.root / "characters" / "roster.json", default={})
    if not isinstance(roster, dict):
        return None
    team = next((row for row in roster.get("teams", []) if isinstance(row, dict) and row.get("id") == team_id), None)
    characters = {
        row.get("id"): row for row in roster.get("characters", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    if not isinstance(team, dict):
        return None
    member_actors = [
        characters.get(member_id, {}).get("actorId")
        for member_id in team.get("members", []) if isinstance(member_id, str)
        and isinstance(characters.get(member_id), dict)
    ]
    remaining = [actor_id for actor_id in member_actors if isinstance(actor_id, str) and actor_id not in participants]
    return remaining[0] if len(remaining) == 1 else None


def accept_third_player(root: Path, intent_id: str, third_player: str, now: datetime) -> Dict[str, Any]:
    """Persist the addressed third player's own response to an Open Pod request."""
    storage = Storage(root)
    observed_at = isoformat(now)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        original = ledger["intents"].get(intent_id)
        if not isinstance(original, dict) or original.get("journey") != "customer_training":
            raise TrainingSessionError("third-player invitation is unavailable")
        invitation = original.get("thirdPlayerInvitation")
        participants = original.get("participants")
        captain = original.get("bookerActorId")
        if (
            original.get("status") != "awaiting_third_player"
            or not isinstance(invitation, dict)
            or invitation.get("actorId") != third_player
            or invitation.get("status") != "invited"
            or not isinstance(participants, list)
            or len(participants) != 2
            or not isinstance(captain, str)
        ):
            raise TrainingSessionError("third-player invitation is not awaiting this player's decision")
        roster = storage.load_json(storage.root / "characters" / "roster.json", default={})
        names = {
            row.get("actorId"): str(row.get("name") or row.get("actorId"))
            for row in roster.get("characters", [])
            if isinstance(row, dict) and isinstance(row.get("actorId"), str)
        } if isinstance(roster, dict) else {}
        if third_player not in names:
            raise TrainingSessionError("third player is not in the Club roster")
        intent = deepcopy(original)
        intent["participants"] = [*participants, third_player]
        intent["status"] = "agreed"
        intent["thirdPlayerInvitation"] = {**invitation, "status": "accepted", "acceptedAt": observed_at}
        intent["nextAction"] = "plan_live_w37_availability_then_player_booking"
        save_intent(storage, ledger, intent)
        message_id = f"{intent_id}:third-player:{third_player}:acceptance"
        message = {
            "id": message_id, "channel": "club", "kind": "training_third_player_acceptance",
            "conversationId": intent.get("conversationId"), "commitmentId": intent.get("commitmentId"), "speechAct": "accept",
            "from": third_player, "to": [captain], "intentId": intent_id,
            "inReplyTo": f"{intent_id}:third-player:{third_player}",
            "text": f"{names[third_player]} accepts the third-player request for this 30-minute training. The lead may now plan one available W37 Open Pod booking.",
            "observedAt": observed_at, "remoteWrites": 0,
        }
        storage.append_jsonl(storage.channel_path("club"), message)
        conversation_id = intent.get("conversationId")
        commitment_id = intent.get("commitmentId")
        if isinstance(conversation_id, str) and isinstance(commitment_id, str):
            fact = make_message(
                message_id=message_id, conversation_id=conversation_id, correlation_id=f"journey:{intent_id}",
                sender_actor_id=third_player, recipient_actor_ids=[captain], speech_act="accept",
                content_code="training.third_player.accepted", occurred_at=observed_at, commitment_id=commitment_id,
                in_reply_to=f"{intent_id}:third-player:{third_player}",
                causation_id=reply_causation(f"{intent_id}:third-player:{third_player}"),
                parameters={"intentRef": intent_id, "reason": invitation.get("reason")}, grounding="understood",
            )
            append_protocol_facts_locked(storage, [fact])
    return intent


def propose(root: Path, scenario_name: str, now: datetime) -> Dict[str, Any]:
    """Let a captain offer one legal training session to a named teammate."""
    storage = Storage(root)
    scenario, captain, teammate = _validated_scenario(storage, scenario_name)
    intent_id = f"intent:training:{scenario['id']}"
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        existing = ledger["intents"].get(intent_id)
        if isinstance(existing, dict):
            return deepcopy(existing)
        observed_at = isoformat(now)
        intent = {
            "schemaVersion": 1,
            "id": intent_id,
            "status": "captain_invited",
            "journey": "customer_training",
            "reason": "captain_teammate_training",
            "createdAt": observed_at,
            "bookerActorId": captain,
            "participants": [captain, teammate],
            "constraints": {
                "durationMinutes": scenario["durationMinutes"],
                "daysAhead": deepcopy(scenario["daysAhead"]),
                "localWindows": deepcopy(scenario["localWindows"]),
                "slotPolicy": scenario["slotPolicy"],
            },
            "nextAction": "await_teammate_acceptance",
            "remoteWrites": 0,
            "conversationId": conversation_id_for(intent_id),
            "commitmentId": commitment_id_for(intent_id),
        }
        save_intent(storage, ledger, intent)
        message = {
            "id": f"{intent_id}:training-invitation",
            "conversationId": intent["conversationId"], "commitmentId": intent["commitmentId"], "speechAct": "propose",
            "channel": "club",
            "kind": "training_invitation",
            "from": captain,
            "to": [teammate],
            "intentId": intent_id,
            "text": "King proposes a 30-minute Women Fighters training session. Mai, accept and the lead may plan one available court.",
            "observedAt": observed_at,
            "remoteWrites": 0,
        }
        storage.append_jsonl(storage.channel_path("club"), message)
        _record_proposal(storage, intent, captain, teammate, message, observed_at)
    return intent


def accept(root: Path, scenario_name: str, now: datetime) -> Dict[str, Any]:
    """Persist only the named teammate's explicit training acceptance."""
    storage = Storage(root)
    scenario, captain, teammate = _validated_scenario(storage, scenario_name)
    intent_id = f"intent:training:{scenario['id']}"
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        intent = ledger["intents"].get(intent_id)
        if not isinstance(intent, dict):
            raise TrainingSessionError("captain must propose training before a teammate can accept")
        if intent.get("status") == "agreed":
            return deepcopy(intent)
        if intent.get("status") != "captain_invited" or intent.get("participants") != [captain, teammate]:
            raise TrainingSessionError("training intent is not awaiting this teammate")
        observed_at = isoformat(now)
        intent = deepcopy(intent)
        intent.update({"status": "agreed", "agreedAt": observed_at, "nextAction": "plan_live_w37_availability_then_player_booking"})
        save_intent(storage, ledger, intent)
        _supersede_prior_team_authority_locked(storage, ledger, intent, observed_at)
        message = {
            "id": f"{intent_id}:training-acceptance",
            "conversationId": intent.get("conversationId"), "commitmentId": intent.get("commitmentId"), "speechAct": "accept",
            "channel": "club",
            "kind": "training_acceptance",
            "from": teammate,
            "to": [captain],
            "intentId": intent_id,
            "inReplyTo": f"{intent_id}:training-invitation",
            "text": "Mai accepts the 30-minute Women Fighters training session. King may book one available court.",
            "observedAt": observed_at,
            "remoteWrites": 0,
        }
        storage.append_jsonl(storage.channel_path("club"), message)
        _record_acceptance(storage, intent, teammate, captain, message, observed_at)
    return intent


def propose_pair(root: Path, captain: str, teammate: str, now: datetime) -> Dict[str, Any]:
    """Create one captain-led 30-minute practice agreement from the roster.

    Unlike the original Women Fighters fixture this is not a centrally scripted
    scenario: any roster captain and compatible teammate may use it after a
    legal social advertisement.  It remains entirely local until the lead
    plans and performs a guarded player-owned booking.
    """
    storage = Storage(root)
    team, names = _validated_pair(storage, captain, teammate)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        active = next((intent for intent in ledger["intents"].values() if _blocks_fresh_pair_proposal(
            intent, team["id"], now
        )), None)
        if isinstance(active, dict):
            return deepcopy(active)
        sequence = 1 + sum(1 for intent in ledger["intents"].values() if isinstance(intent, dict)
                           and intent.get("journey") == "customer_training"
                           and intent.get("trainingTeamId") == team["id"])
        observed_at = isoformat(now)
        intent_id = f"intent:training:{team['id']}:{sequence:03d}"
        intent = {
            "schemaVersion": 1, "id": intent_id, "status": "captain_invited",
            "journey": "customer_training", "reason": "captain_teammate_training",
            "trainingTeamId": team["id"], "createdAt": observed_at,
            "bookerActorId": captain, "participants": [captain, teammate],
            "constraints": {"durationMinutes": 30, "daysAhead": [0, 14], "localWindows": ["00:00-24:00"], "slotPolicy": "nearest_valid_low_contention"},
            "nextAction": "await_teammate_acceptance", "remoteWrites": 0,
            "conversationId": conversation_id_for(intent_id), "commitmentId": commitment_id_for(intent_id),
        }
        save_intent(storage, ledger, intent)
        message = {
            "id": f"{intent_id}:training-invitation", "channel": "club", "kind": "training_invitation",
            "conversationId": intent["conversationId"], "commitmentId": intent["commitmentId"], "speechAct": "propose",
            "from": captain, "to": [teammate], "intentId": intent_id,
            "text": f"{names[captain]} proposes a 30-minute {team['name']} training session. {names[teammate]}, accept and the lead may plan one available W37 court.",
            "observedAt": observed_at, "remoteWrites": 0,
        }
        storage.append_jsonl(storage.channel_path("club"), message)
        _record_proposal(storage, intent, captain, teammate, message, observed_at)
    return intent


def _blocks_fresh_pair_proposal(intent: Any, team_id: str, now: datetime) -> bool:
    """Whether an existing team training is still a live social commitment.

    Historical bookings and old unexecuted agreements are evidence, not a
    permanent veto over a new invitation.  A future booked session remains a
    real commitment; a fresh proposal/agreement remains live for two hours so
    a captain does not spam their teammate while the lead is planning it.
    """
    if not isinstance(intent, dict):
        return False
    if intent.get("journey") != "customer_training" or intent.get("trainingTeamId") != team_id:
        return False
    status = intent.get("status")
    if status == "booked":
        plan = intent.get("previewPlan")
        end = plan.get("endTime") if isinstance(plan, dict) else None
        if not isinstance(end, str):
            return False
        try:
            return datetime.fromisoformat(end.replace("Z", "+00:00")) > now
        except ValueError:
            return False
    if status not in ACTIONABLE_TRAINING_STATUSES:
        return False
    started = intent.get("agreedAt") or intent.get("createdAt")
    if not isinstance(started, str):
        return False
    try:
        return (now - datetime.fromisoformat(started.replace("Z", "+00:00"))) < BOOKING_AUTHORITY_LIFETIME
    except ValueError:
        return False


def accept_intent(root: Path, intent_id: str, teammate: str, now: datetime) -> Dict[str, Any]:
    """Accept a named captain-training invitation after validating its roster pair."""
    storage = Storage(root)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        intent = ledger["intents"].get(intent_id)
        if not isinstance(intent, dict) or intent.get("journey") != "customer_training":
            raise TrainingSessionError("training invitation is unavailable")
        participants = intent.get("participants")
        captain = intent.get("bookerActorId")
        if not isinstance(participants, list) or len(participants) != 2 or not isinstance(captain, str) or teammate not in participants or teammate == captain:
            raise TrainingSessionError("training invitation has no eligible teammate")
        team, names = _validated_pair(storage, captain, teammate)
        if intent.get("trainingTeamId") not in {None, team["id"]}:
            raise TrainingSessionError("training invitation does not match the roster team")
        if intent.get("status") == "agreed":
            return deepcopy(intent)
        if intent.get("status") != "captain_invited":
            raise TrainingSessionError("training invitation is no longer awaiting acceptance")
        observed_at = isoformat(now)
        intent = deepcopy(intent)
        intent.update({"status": "agreed", "agreedAt": observed_at, "nextAction": "plan_live_w37_availability_then_player_booking"})
        save_intent(storage, ledger, intent)
        _supersede_prior_team_authority_locked(storage, ledger, intent, observed_at)
        message = {
            "id": f"{intent_id}:training-acceptance", "channel": "club", "kind": "training_acceptance",
            "conversationId": intent.get("conversationId"), "commitmentId": intent.get("commitmentId"), "speechAct": "accept",
            "from": teammate, "to": [captain], "intentId": intent_id,
            "inReplyTo": f"{intent_id}:training-invitation",
            "text": f"{names[teammate]} accepts {names[captain]}'s 30-minute {team['name']} training session. {names[captain]} may book one available W37 court.",
            "observedAt": observed_at, "remoteWrites": 0,
        }
        storage.append_jsonl(storage.channel_path("club"), message)
        _record_acceptance(storage, intent, teammate, captain, message, observed_at)
    return intent


def reconcile_training_booking_authority(root: Path, now: datetime) -> Dict[str, Any]:
    """Turn stale, unbooked training agreements into explicit history.

    An agreement remains an auditable social fact.  Its short-lived authority
    to begin a new product booking does not.  This keeps the lead queue free
    of abandoned work without rewriting attendance, results, or old bookings.
    """
    storage = Storage(root)
    observed_at = isoformat(now)
    expired_ids: list[str] = []
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        for intent_id, original in ledger["intents"].items():
            if not isinstance(intent_id, str) or not isinstance(original, dict):
                continue
            if (
                original.get("journey") != "customer_training"
                or original.get("status") not in ACTIONABLE_TRAINING_STATUSES
                or _booking_agreement_is_fresh(original, now)
            ):
                continue
            intent = deepcopy(original)
            intent.update({
                "status": "expired",
                "bookingAuthorityExpiredAt": observed_at,
                "nextAction": "history_only_fresh_player_agreement_required",
            })
            save_intent(storage, ledger, intent)
            expired_ids.append(intent_id)
    return {"expiredIntentIds": expired_ids, "observedAt": observed_at}


def _supersede_prior_team_authority_locked(
    storage: Storage, ledger: Dict[str, Any], current: Dict[str, Any], observed_at: str,
) -> None:
    """Keep one fresh booking authority per team while retaining all history."""
    team_id = _training_team_id(storage, current)
    current_id = current.get("id")
    if not isinstance(team_id, str) or not isinstance(current_id, str):
        return
    for intent_id, original in ledger["intents"].items():
        if intent_id == current_id or not isinstance(original, dict):
            continue
        if (
            original.get("journey") != "customer_training"
            or original.get("status") not in ACTIONABLE_TRAINING_STATUSES
            or _training_team_id(storage, original) != team_id
        ):
            continue
        intent = deepcopy(original)
        intent.update({
            "status": "superseded",
            "supersededAt": observed_at,
            "supersededBy": current_id,
            "nextAction": "history_only_newer_team_agreement_exists",
        })
        save_intent(storage, ledger, intent)


def _training_team_id(storage: Storage, intent: Dict[str, Any]) -> Optional[str]:
    explicit = intent.get("trainingTeamId")
    if isinstance(explicit, str) and explicit:
        return explicit
    participants = intent.get("participants")
    captain = intent.get("bookerActorId")
    if not isinstance(participants, list) or not isinstance(captain, str):
        return None
    actor_ids = {actor_id for actor_id in participants if isinstance(actor_id, str)}
    if len(actor_ids) != 2 or captain not in actor_ids:
        return None
    roster = storage.load_json(storage.root / "characters" / "roster.json", default={})
    characters = {
        row.get("id"): row for row in roster.get("characters", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    } if isinstance(roster, dict) else {}
    for team in roster.get("teams", []) if isinstance(roster, dict) else []:
        if not isinstance(team, dict):
            continue
        members = team.get("members")
        captain_character = characters.get(team.get("captainId"))
        captain_actor = captain_character.get("actorId") if isinstance(captain_character, dict) else None
        member_actors = {
            characters.get(member, {}).get("actorId") for member in members
            if isinstance(member, str) and isinstance(characters.get(member), dict)
        } if isinstance(members, list) else set()
        if captain_actor == captain and actor_ids.issubset(member_actors):
            team_id = team.get("id")
            return team_id if isinstance(team_id, str) else None
    return None


def next_booking_task(root: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Find one agreed training intent for the lead's guarded booking bridge."""
    storage = Storage(root)
    if now is not None:
        reconcile_training_booking_authority(root, now)
    ledger = load_intent_ledger(storage)
    candidates = [
        intent for intent in ledger["intents"].values()
        if isinstance(intent, dict)
        and intent.get("journey") == "customer_training"
        and intent.get("status") in {"agreed", "preview_ready"}
        and _ready_for_booking_task(intent)
    ]
    if now is not None:
        candidates = [intent for intent in candidates if _booking_agreement_is_fresh(intent, now)]
    # A blocked rendered journey is durable evidence of an uncertain product
    # state, not permission to replay the same booking.  It should remain
    # available to staff follow-up, while a separate fresh agreement can still
    # enter the single worker lane.
    blocked_intents = _blocked_booking_intents(storage)
    candidates = [intent for intent in candidates if intent.get("id") not in blocked_intents]
    if not candidates:
        return {"action": "pass", "detail": "no agreed training session awaits planning"}
    # A Preview plan is perishable: live availability can change underneath it.
    # Prefer the currently active social agreement, then the newest agreement,
    # rather than letting an old `preview_ready` plan starve fresh player intent.
    active_id = ledger.get("activeIntentId")
    intent = sorted(
        candidates,
        key=lambda row: (
            row.get("id") != active_id,
            -_agreement_timestamp(row),
            str(row.get("id") or ""),
        ),
    )[0]
    return {
        "actorId": "lead",
        "action": "apply_training_booking" if intent.get("status") == "preview_ready" else "plan_training_booking",
        "intentId": intent["id"],
        "bookerActorId": intent["bookerActorId"],
        "participants": list(intent["participants"]),
        "durationMinutes": intent["constraints"]["durationMinutes"],
        "playerActionsAfterBooking": [
            "capture_share_invitation_link",
            "invite_player",
            "accept_invitation",
        ],
        "detail": f"{len(intent['participants'])} named training players agreed; plan live W37 availability before the guarded player booking",
    }


def _blocked_booking_intents(storage: Storage) -> set[str]:
    journeys = storage.load_json(storage.state / "browser-journeys.json", default={})
    jobs = journeys.get("jobs", {}) if isinstance(journeys, dict) else {}
    if not isinstance(jobs, dict):
        return set()
    return {
        intent_id
        for job in jobs.values()
        if isinstance(job, dict)
        and job.get("role") == "player"
        and job.get("journeyAction") == "plan_training_booking"
        and job.get("status") == "blocked"
        and isinstance((intent_id := job.get("intentId")), str)
    }


def _ready_for_booking_task(intent: Dict[str, Any]) -> bool:
    """Whether the agreement's *current* step is booking work.

    ``status == agreed`` records the durable social commitment, not a blanket
    instruction to book again.  Once a visible booking has moved an intent to
    invitation sharing or another named follow-up, it may remain ``agreed``
    while that different player-owned step is outstanding.  Do not let that
    historical agreement occupy the single booking-worker lane.

    Older saved intents did not always carry ``nextAction``.  Treat those as
    compatible booking candidates so the migration is additive rather than a
    silent abandonment of valid agreements.
    """
    status = intent.get("status")
    next_action = intent.get("nextAction")
    if next_action is None:
        return True
    if not isinstance(next_action, str):
        return False
    if status == "agreed":
        return next_action == "plan_live_w37_availability_then_player_booking"
    if status == "preview_ready":
        return next_action in {
            "apply_training_booking",
            "apply_live_w37_booking_after_named_payer_decision",
        }
    return False


def _agreement_timestamp(intent: Dict[str, Any]) -> float:
    """Stable ordering helper; malformed historical timestamps sort last."""
    value = intent.get("agreedAt") or intent.get("createdAt") or ""
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _booking_agreement_is_fresh(intent: Dict[str, Any], now: datetime) -> bool:
    """Keep a historic agreement from becoming a new product booking.

    An accepted agreement is actionable for two hours.  A plan is more
    perishable because court availability changes, so a precomputed
    ``preview_ready`` plan is eligible only for fifteen minutes.  The caller
    supplies `now` only in the live Rally Engine; direct inspection remains
    backwards-compatible and does not silently mutate history.
    """
    stamp = intent.get("agreedAt") or intent.get("createdAt")
    if not isinstance(stamp, str):
        return False
    try:
        agreed_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if now - agreed_at > BOOKING_AUTHORITY_LIFETIME:
        return False
    if intent.get("status") != "preview_ready":
        return True
    plan = intent.get("previewPlan")
    observed_at = plan.get("observedAt") if isinstance(plan, dict) else None
    if not isinstance(observed_at, str):
        return False
    try:
        return now - datetime.fromisoformat(observed_at.replace("Z", "+00:00")) <= timedelta(minutes=15)
    except ValueError:
        return False


def _record_proposal(
    storage: Storage, intent: Dict[str, Any], captain: str, teammate: str,
    message: Dict[str, Any], observed_at: str,
) -> None:
    conversation_id = str(intent["conversationId"])
    commitment_id = str(intent["commitmentId"])
    correlation_id = f"journey:{intent['id']}"
    message_fact = make_message(
        message_id=message["id"], conversation_id=conversation_id,
        correlation_id=correlation_id, sender_actor_id=captain,
        recipient_actor_ids=[teammate], speech_act="propose",
        content_code="training.session.proposed", occurred_at=observed_at,
        commitment_id=commitment_id,
        parameters={"intentRef": intent["id"], "durationMinutes": intent["constraints"]["durationMinutes"]},
    )
    commitment_fact = make_commitment_proposal(
        commitment_id=commitment_id, conversation_id=conversation_id,
        correlation_id=correlation_id, proposer_actor_id=captain,
        debtor_actor_id=teammate,
        beneficiary_actor_ids=[actor for actor in intent["participants"] if isinstance(actor, str)],
        kind="join_team_training",
        content={"capabilityId": "player.accept_team_training", "intentRef": intent["id"]},
        occurred_at=observed_at, causation_id=message_fact["factId"],
    )
    append_protocol_facts_locked(storage, [message_fact, commitment_fact])


def _record_acceptance(
    storage: Storage, intent: Dict[str, Any], teammate: str, captain: str,
    message: Dict[str, Any], observed_at: str,
) -> None:
    conversation_id = intent.get("conversationId")
    commitment_id = intent.get("commitmentId")
    if not isinstance(conversation_id, str) or not isinstance(commitment_id, str):
        # Compatibility intents written before protocol migration remain valid
        # but cannot be misrepresented as newly observed protocol facts.
        return
    correlation_id = f"journey:{intent['id']}"
    message_fact = make_message(
        message_id=message["id"], conversation_id=conversation_id,
        correlation_id=correlation_id, sender_actor_id=teammate,
        recipient_actor_ids=[captain], speech_act="accept",
        content_code="training.session.accepted", occurred_at=observed_at,
        commitment_id=commitment_id, in_reply_to=f"{intent['id']}:training-invitation",
        causation_id=reply_causation(f"{intent['id']}:training-invitation"),
        parameters={"intentRef": intent["id"]}, grounding="understood",
    )
    transition = make_commitment_transition(
        storage, commitment_id=commitment_id, transition="accepted",
        actor_id=teammate, occurred_at=observed_at,
        causation_id=message_fact["factId"],
    )
    append_protocol_facts_locked(storage, [message_fact, transition])


def _validated_scenario(storage: Storage, scenario_name: str) -> tuple[Dict[str, Any], str, str]:
    scenario = storage.load_json(storage.root / "scenarios" / scenario_name)
    roster = storage.load_json(storage.root / "characters" / "roster.json")
    if not isinstance(scenario, dict) or scenario.get("schemaVersion") != 1 or scenario.get("journey") != "customer_training":
        raise TrainingSessionError("training scenario is invalid")
    captain, teammate = scenario.get("captainActorId"), scenario.get("teammateActorId")
    duration = scenario.get("durationMinutes")
    if not isinstance(captain, str) or not isinstance(teammate, str) or captain == teammate:
        raise TrainingSessionError("training scenario needs two distinct named actors")
    if not isinstance(duration, int) or duration < 30 or duration % 30:
        raise TrainingSessionError("training duration must be a positive 30-minute increment")
    characters = {
        row.get("actorId"): row
        for row in roster.get("characters", []) if isinstance(row, dict) and isinstance(row.get("actorId"), str)
    } if isinstance(roster, dict) else {}
    if captain not in characters or teammate not in characters:
        raise TrainingSessionError("training actors are not on the roster")
    team = next((row for row in roster.get("teams", []) if isinstance(row, dict) and row.get("id") == scenario.get("teamId")), None) if isinstance(roster, dict) else None
    if not isinstance(team, dict):
        raise TrainingSessionError("training team is not on the roster")
    actor_members = {
        row.get("actorId") for row in roster.get("characters", [])
        if isinstance(row, dict) and row.get("id") in team.get("members", [])
    }
    if captain not in actor_members or teammate not in actor_members:
        raise TrainingSessionError("training actors must belong to the same team")
    return scenario, captain, teammate


def _validated_pair(storage: Storage, captain: str, teammate: str) -> tuple[Dict[str, Any], Dict[str, str]]:
    roster = storage.load_json(storage.root / "characters" / "roster.json")
    if not isinstance(roster, dict):
        raise TrainingSessionError("character roster is invalid")
    characters = [row for row in roster.get("characters", []) if isinstance(row, dict) and isinstance(row.get("actorId"), str)]
    by_actor = {row["actorId"]: row for row in characters}
    captain_row, teammate_row = by_actor.get(captain), by_actor.get(teammate)
    if not isinstance(captain_row, dict) or not isinstance(teammate_row, dict):
        raise TrainingSessionError("training actors are not enabled on the roster")
    captain_teams = captain_row.get("captainOf")
    if not isinstance(captain_teams, list) or not captain_teams:
        raise TrainingSessionError("only a roster captain may propose team training")
    team = next((row for row in roster.get("teams", []) if isinstance(row, dict)
                 and row.get("id") in captain_teams and teammate_row.get("id") in row.get("members", [])), None)
    if not isinstance(team, dict):
        raise TrainingSessionError("captain and teammate must belong to the same team")
    affinities = captain_row.get("affinities")
    if not isinstance(affinities, list) or teammate_row.get("id") not in affinities:
        raise TrainingSessionError("training teammate is not a compatible roster affinity")
    return team, {captain: str(captain_row.get("name") or captain), teammate: str(teammate_row.get("name") or teammate)}
