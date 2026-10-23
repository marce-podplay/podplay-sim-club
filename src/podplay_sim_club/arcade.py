"""Pure, local-only deterministic table-tennis Arcade engine."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import random
from typing import Any, Dict, Iterable, Mapping, Sequence

from .storage import Storage


GAME_VERSION = "table-tennis-v1"
RATINGS_FILE = "arcade/ratings.json"
MATCHES_FILE = "arcade/matches.json"
MAX_RALLIES = 200
RATING_KEYS = ("serve", "attack", "defense", "composure", "teamwork")


class ArcadeError(ValueError):
    """Invalid local Arcade configuration."""


def simulate_table_tennis(
    match_config: Mapping[str, Any], ratings: Mapping[str, Mapping[str, Any]], seed: str
) -> Dict[str, Any]:
    """Return a replayable first-to-11 table-tennis match with no I/O.

    ``seed`` is persisted by the caller.  Ratings affect odds, but every rally
    also has bounded seeded variance, permitting repeatable upsets.
    """
    left = _players(match_config.get("left"), "left")
    right = _players(match_config.get("right"), "right")
    if len(left) != len(right) or len(left) not in {1, 2}:
        raise ArcadeError("table tennis needs balanced singles or doubles sides")
    randomizer = random.Random(_seed_number(seed))
    score = [0, 0]
    rallies = []
    while max(score) < 11 or abs(score[0] - score[1]) < 2:
        if len(rallies) >= MAX_RALLIES:
            raise ArcadeError("simulation exceeded the maximum rally count")
        rally_number = len(rallies)
        server_side = (rally_number // 2) % 2 if max(score) < 10 else rally_number % 2
        server = (left if server_side == 0 else right)[rally_number % len(left)]
        probability = _point_probability(left, right, ratings, server_side, randomizer)
        winner_side = 0 if randomizer.random() < probability else 1
        score[winner_side] += 1
        rallies.append({
            "rally": rally_number + 1,
            "server": server,
            "winnerSide": "left" if winner_side == 0 else "right",
            "winner": (left if winner_side == 0 else right)[rally_number % len(left)],
            "score": {"left": score[0], "right": score[1]},
            "reason": _reason(probability, winner_side, randomizer),
        })
    return {
        "game": GAME_VERSION,
        "kind": "arcade_simulation",
        "seed": seed,
        "left": left,
        "right": right,
        "score": {"left": score[0], "right": score[1]},
        "winnerSide": "left" if score[0] > score[1] else "right",
        "winner": left if score[0] > score[1] else right,
        "rallies": rallies,
        "status": "simulated",
    }


def simulate_and_store(
    root, *, occurrence_key: str, left: Sequence[str], right: Sequence[str], season_id: str = "season-001"
) -> Dict[str, Any]:
    """Persist one isolated simulation and its public replay seed."""
    if not occurrence_key:
        raise ArcadeError("Arcade match needs an occurrence key")
    storage = Storage(root)
    with storage.writer_lock():
        ledger = storage.load_json(storage.state / MATCHES_FILE, default={"schemaVersion": 1, "matches": {}})
        matches = ledger.get("matches") if isinstance(ledger, dict) else None
        if not isinstance(matches, dict):
            raise ArcadeError("Arcade match store has an unsupported schema")
        existing = matches.get(occurrence_key)
        if isinstance(existing, dict):
            return existing
        ratings = load_ratings(storage, list(left) + list(right))
        seed = _public_seed(season_id, occurrence_key, list(left), list(right))
        record = simulate_table_tennis({"left": list(left), "right": list(right)}, ratings, seed)
        record.update({"occurrenceKey": occurrence_key, "seasonId": season_id, "simulatedAt": datetime.now(timezone.utc).isoformat()})
        matches[occurrence_key] = record
        storage.write_json(storage.state / MATCHES_FILE, ledger)
        return record


def attach_training_replay(root, *, intent_id: str, season_id: str = "season-001") -> Dict[str, Any]:
    """Backward-compatible training-only entrypoint for :func:`attach_session_replay`."""
    return attach_session_replay(root, intent_id=intent_id, season_id=season_id, training_only=True)


def attach_session_replay(
    root, *, intent_id: str, season_id: str = "season-001", training_only: bool = False
) -> Dict[str, Any]:
    """Attach a deterministic, fictional replay to a supported player session.

    The replay is an evidence source for the *fictional club narrative* only.
    It never changes the linked PodPlay booking or claims a real check-in.  The
    designated booking captain must still confirm the replay before its winner
    is recorded in the club journal.  The sides come from the socially
    authorized intent: a pair of named training players or the challenger and
    opponent sides of a captain-agreed doubles booking.
    """
    from .booking_intents import load_intent_ledger, save_intent

    storage = Storage(root)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        intent = ledger["intents"].get(intent_id)
        if not isinstance(intent, dict) or intent.get("journey") not in {"customer_training", "customer_team_signup"}:
            raise ArcadeError("Arcade replay needs a supported player-owned session")
        if training_only and intent.get("journey") != "customer_training":
            raise ArcadeError("training replay needs a customer-training intent")
        left, right = _session_sides(intent)
        booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
        plan = intent.get("previewPlan") if isinstance(intent.get("previewPlan"), dict) else {}
        occurrence_key = booking.get("occurrenceKey") or plan.get("occurrenceKey")
        if not isinstance(occurrence_key, str) or not occurrence_key:
            raise ArcadeError("training needs a booked occurrence before it can have a replay")
        record = _simulate_under_lock(storage, occurrence_key, left, right, season_id)
        replay = {
            "occurrenceKey": occurrence_key,
            "status": "ready",
            "evidence": "arcade_replay",
            "reporterActorId": intent.get("bookerActorId") or left[0],
            "winner": list(record["winner"]),
            "winnerSide": record["winnerSide"],
            "score": dict(record["score"]),
            "game": record["game"],
        }
        intent["arcadeReplay"] = replay
        request = intent.get("resultRequest")
        if isinstance(request, dict) and request.get("status") == "requested":
            # Upgrade a previously-issued, evidence-light request instead of
            # making the reporter choose between two competing result sources.
            request["evidence"] = "arcade_replay"
            request["replayOccurrenceKey"] = occurrence_key
            storage.append_jsonl(storage.channel_path("club"), {
                "id": f"arcade:{occurrence_key}:replay-ready",
                "channel": "club", "from": "sofia",
                "to": [replay["reporterActorId"]],
                "kind": "arcade_replay_ready",
                "intentId": intent_id,
                "eventId": booking.get("eventId"),
                "text": "Club Arcade replay is ready. The named reporter can watch it and confirm its fictional winner; no score is required.",
                "payload": {"evidence": "arcade_replay", "occurrenceKey": occurrence_key},
                "observedAt": datetime.now(timezone.utc).isoformat(), "remoteWrites": 0,
            })
        save_intent(storage, ledger, intent, make_active=False)
        return {**record, "replay": replay}


def attach_completed_training_replay(
    root, *, now: datetime | None = None, season_id: str = "season-001"
) -> Dict[str, Any]:
    """Attach one Arcade replay only after a supported session is observed attended.

    This is the narrow handoff between product-derived attendance and the
    fictional Club Arcade.  A booking, acceptance, or check-in alone cannot
    start a replay: the session must have ended and the finished-session
    observer must have recorded ``attendance_observed``.  The return is a
    local narrative action, never a Preview mutation or attendance claim.
    """
    from .booking_intents import load_intent_ledger
    from .time import isoformat, parse_instant

    storage = Storage(root)
    observed_at = now or parse_instant(None)
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        eligible = []
        for intent in ledger["intents"].values():
            if not isinstance(intent, dict) or intent.get("journey") not in {"customer_training", "customer_team_signup"}:
                continue
            if isinstance(intent.get("arcadeReplay"), dict):
                continue
            observation = intent.get("eventObservation")
            try:
                _session_sides(intent)
            except ArcadeError:
                continue
            if (
                intent.get("status") != "attendance_observed"
                or not isinstance(observation, dict)
                or observation.get("outcome") != "attendance_observed"
            ):
                continue
            end_time = observation.get("endTime")
            if not isinstance(end_time, str):
                end_time = (intent.get("booking") or {}).get("endTime") or (intent.get("previewPlan") or {}).get("endTime")
            try:
                ended = isinstance(end_time, str) and parse_instant(end_time) <= observed_at
            except ValueError:
                ended = False
            if ended:
                eligible.append(intent)
        if not eligible:
            return {"action": "pass", "detail": "no attended finished training awaits an Arcade replay", "remoteWrites": 0}
        # Release the lock before delegating to ``attach_training_replay``,
        # which takes the same durable writer lock itself.
        intent_id = sorted(eligible, key=lambda row: str(row.get("id")))[0]["id"]
    replay = attach_session_replay(root, intent_id=intent_id, season_id=season_id)
    return {
        "action": "arcade_replay_attached",
        "intentId": intent_id,
        "occurrenceKey": replay["replay"]["occurrenceKey"],
        "observedAt": isoformat(observed_at),
        "remoteWrites": 0,
    }


def attach_ended_active_session_replay(
    root, *, now: datetime | None = None, season_id: str = "season-001"
) -> Dict[str, Any]:
    """Attach one fictional replay after a locally inferred session ends.

    This path is intentionally distinct from product attendance.  It relies on
    the local active-session rule (open booking window plus visible staff
    check-ins for every named player), and carries that boundary forward into
    the later reporter-confirmed result.
    """
    from .active_sessions import sessions as active_sessions
    from .booking_intents import load_intent_ledger

    storage = Storage(root)
    ended = [row for row in active_sessions(root) if row.get("status") == "ENDED"]
    if not ended:
        return {"action": "pass", "detail": "no ended local active session awaits an Arcade replay", "remoteWrites": 0}
    with storage.writer_lock():
        ledger = load_intent_ledger(storage)
        candidates = []
        for session in ended:
            intent_id = session.get("intentId")
            intent = ledger["intents"].get(intent_id) if isinstance(intent_id, str) else None
            if not isinstance(intent, dict) or isinstance(intent.get("arcadeReplay"), dict):
                continue
            if intent.get("journey") not in {"customer_training", "customer_team_signup"}:
                continue
            booking = intent.get("booking") if isinstance(intent.get("booking"), dict) else {}
            if booking.get("occurrenceKey") != session.get("occurrenceKey"):
                continue
            try:
                _session_sides(intent)
            except ArcadeError:
                continue
            candidates.append((str(intent_id), session))
    if not candidates:
        return {"action": "pass", "detail": "ended local active sessions already have a replay or lack named sides", "remoteWrites": 0}
    intent_id, session = sorted(candidates, key=lambda row: str(row[1].get("endedAt") or ""))[0]
    replay = attach_session_replay(root, intent_id=intent_id, season_id=season_id)
    return {
        "action": "local_active_session_arcade_replay_attached",
        "intentId": intent_id,
        "occurrenceKey": replay["replay"]["occurrenceKey"],
        "endedAt": session.get("endedAt"),
        "remoteWrites": 0,
    }


def _simulate_under_lock(storage: Storage, occurrence_key: str, left: Sequence[str], right: Sequence[str], season_id: str) -> Dict[str, Any]:
    """Store a replay while the caller owns the state writer lock."""
    ledger = storage.load_json(storage.state / MATCHES_FILE, default={"schemaVersion": 1, "matches": {}})
    matches = ledger.get("matches") if isinstance(ledger, dict) else None
    if not isinstance(matches, dict):
        raise ArcadeError("Arcade match store has an unsupported schema")
    existing = matches.get(occurrence_key)
    if isinstance(existing, dict):
        return existing
    ratings = load_ratings(storage, list(left) + list(right))
    seed = _public_seed(season_id, occurrence_key, list(left), list(right))
    record = simulate_table_tennis({"left": list(left), "right": list(right)}, ratings, seed)
    record.update({"occurrenceKey": occurrence_key, "seasonId": season_id, "simulatedAt": datetime.now(timezone.utc).isoformat()})
    matches[occurrence_key] = record
    storage.write_json(storage.state / MATCHES_FILE, ledger)
    return record


def _session_sides(intent: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return balanced Arcade sides from durable, named social roles only."""
    participants = [value for value in intent.get("participants", []) if isinstance(value, str) and value]
    if intent.get("journey") == "customer_training":
        if len(participants) != 2:
            raise ArcadeError("training replay needs exactly two named players")
        return participants[:1], participants[1:]
    if intent.get("journey") == "customer_team_signup":
        challenger = intent.get("challenger")
        opponent = intent.get("opponent")
        if not isinstance(challenger, Mapping) or not isinstance(opponent, Mapping):
            raise ArcadeError("team replay needs named challenger and opponent sides")
        left = [challenger.get("captainActorId"), challenger.get("teammateActorId")]
        right = [opponent.get("captainActorId"), opponent.get("teammateActorId")]
        if not all(isinstance(actor_id, str) and actor_id for actor_id in left + right):
            raise ArcadeError("team replay needs both named captain-teammate pairs")
        if len(set(left + right)) != 4 or not set(left + right).issubset(participants):
            raise ArcadeError("team replay sides must be the session's named participants")
        return list(left), list(right)
    raise ArcadeError("Arcade replay needs a supported player-owned session")


def load_ratings(storage: Storage, actor_ids: Iterable[str]) -> Dict[str, Dict[str, int]]:
    """Return stored local ratings, adding deterministic neutral defaults."""
    path = storage.state / RATINGS_FILE
    value = storage.load_json(path, default={"schemaVersion": 1, "players": {}})
    players = value.get("players") if isinstance(value, dict) else None
    if not isinstance(players, dict):
        raise ArcadeError("Arcade ratings store has an unsupported schema")
    changed = False
    for actor_id in actor_ids:
        if not isinstance(actor_id, str) or not actor_id:
            raise ArcadeError("Arcade player IDs must be non-empty strings")
        if not isinstance(players.get(actor_id), dict):
            players[actor_id] = _default_rating(actor_id)
            changed = True
    normalized = {actor_id: _normalize_rating(players[actor_id]) for actor_id in actor_ids}
    if changed:
        storage.write_json(path, value)
    return normalized


def _players(value: Any, side: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(player, str) and player for player in value):
        raise ArcadeError(f"{side} side needs named players")
    return list(value)


def _point_probability(left, right, ratings, server_side, randomizer) -> float:
    left_power = _side_power(left, ratings, server_side == 0)
    right_power = _side_power(right, ratings, server_side == 1)
    baseline = 0.5 + (left_power - right_power) / 220
    swing = randomizer.uniform(-0.10, 0.10)
    return max(0.18, min(0.82, baseline + swing))


def _side_power(players, ratings, serving: bool) -> float:
    values = [_normalize_rating(ratings[player]) for player in players]
    attack = sum(row["attack"] for row in values) / len(values)
    defense = sum(row["defense"] for row in values) / len(values)
    composure = sum(row["composure"] for row in values) / len(values)
    teamwork = sum(row["teamwork"] for row in values) / len(values) if len(values) == 2 else 50
    serve = sum(row["serve"] for row in values) / len(values) if serving else 50
    return attack * .34 + defense * .26 + composure * .20 + teamwork * .12 + serve * .08


def _reason(probability: float, winner_side: int, randomizer: random.Random) -> str:
    favorite_won = (winner_side == 0 and probability >= .5) or (winner_side == 1 and probability < .5)
    if not favorite_won:
        return "upset_return"
    return randomizer.choice(["attack_edge", "long_defense", "forced_error", "serve_pressure"])


def _default_rating(actor_id: str) -> Dict[str, int]:
    digest = hashlib.sha256(f"arcade-rating-v1:{actor_id}".encode()).digest()
    return {key: 45 + digest[index] % 21 for index, key in enumerate(RATING_KEYS)}


def _normalize_rating(value: Mapping[str, Any]) -> Dict[str, int]:
    if not isinstance(value, Mapping):
        raise ArcadeError("Arcade rating must be an object")
    normalized = {}
    for key in RATING_KEYS:
        number = value.get(key)
        if not isinstance(number, int) or not 0 <= number <= 100:
            raise ArcadeError(f"Arcade rating {key} must be an integer from 0 to 100")
        normalized[key] = number
    return normalized


def _public_seed(season_id: str, occurrence_key: str, left: list[str], right: list[str]) -> str:
    material = "|".join([season_id, occurrence_key, GAME_VERSION, ",".join(left), ",".join(right)])
    return hashlib.sha256(material.encode()).hexdigest()


def _seed_number(seed: str) -> int:
    if not isinstance(seed, str) or len(seed) < 16:
        raise ArcadeError("Arcade seed is invalid")
    return int(seed[:16], 16)
