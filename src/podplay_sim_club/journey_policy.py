"""Authority boundary for Club Lab product journeys.

The community may deliberate, and the control plane may administer narrowly
scoped tenant settings.  Neither is a substitute for a Sim using the product
as that Sim.  In particular, bookings, invitation handoffs, check-ins, staff
rate-card work, and price verification are browser journeys with captured
observable evidence.
"""

from pathlib import Path
from typing import Any, Dict, Optional

from .storage import Storage
from .time import isoformat, parse_instant


STATE_FILE = "browser-journeys.json"
OBSERVATION_KINDS = {
    "visible_observation",
    "visible_action_completed",
    "unexpected_ui",
    "browser_pass",
}


BOOKING_PLAYBOOK_ACTIONS = {
    "plan_training_booking",
    "review_training_availability",
    "apply_training_booking",
    "plan_team_signup_booking",
    "apply_team_signup_booking",
}

INVITATION_PLAYBOOK_ACTIONS = {
    "capture_share_invitation",
    "redeem_share_invitation",
}

CHECK_IN_PLAYBOOK_ACTIONS = {"check_in_training"}


def _playbook_for(action: str) -> Optional[str]:
    """Return a local decision aid, never a product-action capability."""
    if action in BOOKING_PLAYBOOK_ACTIONS:
        return "playbooks/sim-player-booking.md"
    if action in INVITATION_PLAYBOOK_ACTIONS:
        return "playbooks/sim-player-invitation.md"
    if action in CHECK_IN_PLAYBOOK_ACTIONS:
        return "playbooks/sim-player-check-in.md"
    return None


def _actor_for(task: Dict[str, Any]) -> Optional[str]:
    """Return only an explicitly named product user for a task."""
    # Planning tasks may identify the coordinator as ``actorId`` while the
    # product action belongs to the player who owns the booking.  Prefer that
    # named product user; check-in tasks only have ``actorId`` and still work.
    for key in ("bookerActorId", "ownerActorId", "recipientActorId", "actorId"):
        value = task.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def browser_journey_required(*, task: Dict[str, Any], role: str) -> Dict[str, Any]:
    """Describe one browser-owned task without touching the product.

    The returned job is deliberately not an assertion that a browser action
    happened.  The runner must separately record a visible observation or an
    allowlisted visible action.
    """
    action = task.get("action", "product_journey") if isinstance(task, dict) else "product_journey"
    playbook = _playbook_for(action)
    return {
        "action": "browser_journey_required",
        "journeyAction": action,
        "role": role,
        "actorId": _actor_for(task) if isinstance(task, dict) else None,
        "intentId": task.get("intentId") if isinstance(task, dict) and isinstance(task.get("intentId"), str) else None,
        "workflow": "isolated_visible_browser",
        "detail": (
            "named Sim must use an authenticated browser and record only visible UI, "
            "sanitized console/network evidence, or an explicit pass; no integration acted on their behalf"
        ),
        "playbook": playbook,
        "remoteWrites": 0,
    }


def dispatch(root: Path, *, task: Dict[str, Any], role: str, observed_at: Optional[str] = None) -> Dict[str, Any]:
    """Persist one secret-free browser work item for the sequential runner.

    The queue is intentionally idempotent by its task identity.  It stores no
    origin, credentials, selector, URL, or product outcome.  This gives the
    Rally Engine a durable hand-off without silently invoking a product API.
    """
    job = browser_journey_required(task=task, role=role)
    if job["journeyAction"] == "pass":
        return job
    actor = job.get("actorId")
    if not isinstance(actor, str):
        job["detail"] = "browser journey awaits an explicitly named Sim; no browser was opened"
        return job
    key = ":".join(str(job.get(field) or "-") for field in ("role", "journeyAction", "intentId", "actorId"))
    observed = observed_at or isoformat(parse_instant(None))
    storage = Storage(root)
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        jobs = state.get("jobs", {}) if isinstance(state, dict) else {}
        jobs = jobs if isinstance(jobs, dict) else {}
        existing = jobs.get(key) if isinstance(jobs.get(key), dict) else {}
        jobs[key] = {
            # Preserve the runner-owned lifecycle and observation history when
            # a recurring Rally tick rediscovers the same product step.
            **{
                field: existing[field]
                for field in ("claimedBy", "claimedAt", "lastObservedAt", "lastObservation", "observations")
                if field in existing
            },
            "jobKey": key,
            "status": existing.get("status", "queued"),
            "queuedAt": existing.get("queuedAt", observed),
            "lastDispatchedAt": observed,
            "actorId": actor,
            "role": role,
            "journeyAction": job["journeyAction"],
            "intentId": job.get("intentId"),
            "workflow": job["workflow"],
            "playbook": job.get("playbook"),
        }
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "jobs": jobs})
    return {**job, "jobKey": key, "status": "queued"}


def pending_or_new(root: Path, *, task: Dict[str, Any], role: str) -> bool:
    """Whether a browser task should occupy the sequential runner.

    An observed task is historical evidence, not an outstanding instruction.
    This lets a fresh booking agreement proceed past an old, already-observed
    check-in or invitation job while preserving queued and active work as the
    runner's current priority.
    """
    job = browser_journey_required(task=task, role=role)
    actor = job.get("actorId")
    if job.get("journeyAction") == "pass" or not isinstance(actor, str):
        return False
    key = ":".join(str(job.get(field) or "-") for field in ("role", "journeyAction", "intentId", "actorId"))
    state = Storage(root).load_json(Storage(root).state / STATE_FILE, default={})
    jobs = state.get("jobs", {}) if isinstance(state, dict) else {}
    existing = jobs.get(key) if isinstance(jobs, dict) else None
    return not isinstance(existing, dict) or existing.get("status") in {"queued", "active"}


def claim(root: Path, *, job_key: str, actor_id: str, observed_at: Optional[str] = None) -> Dict[str, Any]:
    """Let the named Sim exclusively take the next browser step.

    Claiming only changes the local work queue.  It does not open a browser,
    authenticate, or make any assertion about the product.
    """
    if not isinstance(job_key, str) or not job_key or not isinstance(actor_id, str) or not actor_id:
        raise ValueError("browser journey claim needs a job key and named actor")
    observed = observed_at or isoformat(parse_instant(None))
    storage = Storage(root)
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        jobs = state.get("jobs") if isinstance(state, dict) else None
        job = jobs.get(job_key) if isinstance(jobs, dict) else None
        if not isinstance(job, dict):
            raise ValueError("browser journey is unavailable")
        if job.get("actorId") != actor_id:
            raise ValueError("only the named Sim may claim this browser journey")
        if job.get("status") not in {"queued", "observed", "active"}:
            raise ValueError("browser journey is no longer available")
        claimed_by = job.get("claimedBy")
        if isinstance(claimed_by, str) and claimed_by != actor_id:
            raise ValueError("browser journey is already claimed by another Sim")
        job.update({"status": "active", "claimedBy": actor_id, "claimedAt": job.get("claimedAt", observed), "lastObservedAt": observed})
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "jobs": jobs})
    return {"jobKey": job_key, "actorId": actor_id, "status": "active", "remoteWrites": 0}


def record_observation(
    root: Path, *, job_key: str, actor_id: str, kind: str, observed_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a bounded browser observation without claiming a product result.

    The evidence itself remains in the browser runner's local evidence folder;
    this queue deliberately retains only a category and timestamp.  A visible
    click means only that the runner sent it to an allowlisted control, not
    that booking, invitation, payment, or check-in succeeded.
    """
    if kind not in OBSERVATION_KINDS:
        raise ValueError("browser observation kind is not allowed")
    observed = observed_at or isoformat(parse_instant(None))
    storage = Storage(root)
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        jobs = state.get("jobs") if isinstance(state, dict) else None
        job = jobs.get(job_key) if isinstance(jobs, dict) else None
        if not isinstance(job, dict):
            raise ValueError("browser journey is unavailable")
        if job.get("actorId") != actor_id or job.get("claimedBy") != actor_id:
            raise ValueError("only the claiming Sim may record this browser observation")
        if job.get("status") != "active":
            raise ValueError("browser journey must be active before recording an observation")
        history = job.get("observations") if isinstance(job.get("observations"), list) else []
        history.append({"kind": kind, "observedAt": observed})
        job.update({"status": "observed", "lastObservation": kind, "lastObservedAt": observed, "observations": history[-20:]})
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "jobs": jobs})
    return {"jobKey": job_key, "actorId": actor_id, "status": "observed", "observation": kind, "remoteWrites": 0}
