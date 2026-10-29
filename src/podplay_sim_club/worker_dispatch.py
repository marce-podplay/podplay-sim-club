"""Secret-free handoff from a browser job to one economical Sim worker.

The Rally Engine chooses work; a separate orchestrator may later launch the
worker.  Keeping the contract durable makes that model/provider interchangeable
and prevents the Python engine from silently opening a browser or a model.
"""

import hashlib
from pathlib import Path
from typing import Any, Dict

from .journey_policy import STATE_FILE as JOURNEY_STATE_FILE
from .storage import Storage
from .time import isoformat, parse_instant


STATE_FILE = "worker-contracts.json"
INTENTS_FILE = "booking-intents.json"
WORKER_OUTCOMES = {"visible_progress", "unexpected_ui", "blocked", "not_started", "pass"}
BOOKING_ACTIONS = {"plan_training_booking", "review_training_availability"}


def _is_economical_worker(worker_id: str) -> bool:
    return isinstance(worker_id, str) and worker_id.startswith("economical:") and len(worker_id) > len("economical:")


def _retire_obsolete_booking_contracts_locked(storage: Storage, contracts: Dict[str, Any], *, observed_at: str) -> None:
    """Remove superseded/expired agreements from the economical worker lane.

    A durable queued contract can outlive the social agreement that created
    it.  Retiring it locally preserves the audit trail while ensuring a stale
    booking never preempts a newer agreed team session.
    """
    ledger = storage.load_json(storage.state / INTENTS_FILE, default={})
    intents = ledger.get("intents", {}) if isinstance(ledger, dict) else {}
    journeys = storage.load_json(storage.state / JOURNEY_STATE_FILE, default={})
    jobs = journeys.get("jobs", {}) if isinstance(journeys, dict) else {}
    jobs_changed = False
    for contract in contracts.values():
        if not isinstance(contract, dict) or contract.get("status") != "queued":
            continue
        job_key = contract.get("browserJobKey")
        job = jobs.get(job_key) if isinstance(jobs, dict) and isinstance(job_key, str) else None
        if not isinstance(job, dict) or job.get("status") != "queued" or job.get("actorId") != contract.get("actorId"):
            contract.update({
                "status": "retired",
                "retiredAt": observed_at,
                "retirementReason": "browser_job_no_longer_runnable",
            })
            continue
        if contract.get("journeyAction") not in BOOKING_ACTIONS:
            continue
        intent_id = contract.get("intentId")
        intent = intents.get(intent_id) if isinstance(intents, dict) and isinstance(intent_id, str) else None
        if isinstance(intent, dict) and intent.get("status") == "agreed":
            continue
        contract.update({
            "status": "retired",
            "retiredAt": observed_at,
            "retirementReason": "agreement_is_no_longer_agreed",
        })
        if isinstance(job, dict) and job.get("status") == "queued":
            job.update({"status": "retired", "retiredAt": observed_at, "retirementReason": "agreement_is_no_longer_agreed"})
            jobs_changed = True
    if jobs_changed:
        storage.write_json(storage.state / JOURNEY_STATE_FILE, {"schemaVersion": 1, "jobs": jobs})


def _launch_packet(contract: Dict[str, Any]) -> Dict[str, Any]:
    """Return only the bounded context that a routine worker needs.

    Credentials, origins, invitation URLs, selectors, and inferred product
    state deliberately remain outside this packet.  The worker must obtain
    any evidence from its isolated rendered-browser journey.
    """
    return {
        "contractKey": contract["contractKey"],
        "actorId": contract["actorId"],
        # The intent reference is a local scheduling fact, not a product ID,
        # URL, credential, or bearer secret. Recipient invitation workers need
        # it to select the already delivered private mailbox entry only after
        # the coordinator completes visible login.
        "intentId": contract.get("intentId"),
        "browserJobKey": contract["browserJobKey"],
        "journeyAction": contract.get("journeyAction"),
        "playbook": contract.get("playbook"),
        "mode": contract.get("mode"),
        "allowedEvidence": contract.get("allowedEvidence"),
        "agreement": contract.get("agreement"),
    }


def _contract_ref(contract_key: str) -> str:
    return hashlib.sha256(contract_key.encode("utf-8")).hexdigest()[:16]


def _receipt_ref(storage: Storage, *, contract: Dict[str, Any], receipt_ref: str | None) -> str | None:
    """Validate a local bootstrap receipt without accepting arbitrary paths."""
    if receipt_ref is None:
        return None
    if not isinstance(receipt_ref, str) or not receipt_ref:
        raise ValueError("worker receipt reference must be a local relative path")
    candidate = (storage.root / receipt_ref).resolve()
    evidence_root = (storage.state / "worker-evidence").resolve()
    expected = evidence_root / _contract_ref(str(contract["contractKey"])) / "worker-receipt.json"
    if candidate != expected or not candidate.is_file():
        raise ValueError("worker receipt is not the expected local bootstrap receipt")
    receipt = storage.load_json(candidate, default={})
    if (
        not isinstance(receipt, dict)
        or receipt.get("schemaVersion") != 1
        or receipt.get("actorId") != contract.get("actorId")
        or receipt.get("contractRef") != _contract_ref(str(contract["contractKey"]))
        or receipt.get("status") != "browser_ready"
        # An economical character worker is only allowed to receive a browser
        # after the coordinator has completed the one-time rendered login.
        # A public-shell-only receipt is useful for diagnostics but cannot
        # support a player journey.
        or receipt.get("login") != "visible_login_settled"
        or receipt.get("remoteWrites") != 0
    ):
        raise ValueError("worker receipt is incomplete or does not match the active contract")
    return receipt_ref


def _agreement_context(storage: Storage, *, browser_job: Dict[str, Any]) -> Dict[str, Any] | None:
    """Carry only stable social facts a player needs at product review."""
    intent_id = browser_job.get("intentId")
    actor_id = browser_job.get("actorId")
    if not isinstance(intent_id, str) or not isinstance(actor_id, str):
        return None
    ledger = storage.load_json(storage.state / INTENTS_FILE, default={})
    intents = ledger.get("intents", {}) if isinstance(ledger, dict) else {}
    intent = intents.get(intent_id) if isinstance(intents, dict) else None
    if not isinstance(intent, dict) or intent.get("status") != "agreed" or intent.get("bookerActorId") != actor_id:
        return None
    participants = [value for value in intent.get("participants", []) if isinstance(value, str)]
    constraints = intent.get("constraints") if isinstance(intent.get("constraints"), dict) else {}
    duration = constraints.get("durationMinutes")
    if not participants or not isinstance(duration, int) or duration <= 0:
        return None
    days_ahead = constraints.get("daysAhead")
    local_windows = constraints.get("localWindows")
    context: Dict[str, Any] = {
        "participantCount": len(participants),
        "durationMinutes": duration,
    }
    # These are social agreement facts, not product-derived availability. They
    # let an economical browser worker judge a rendered time row without
    # guessing at an otherwise invisible scheduling window.
    if (
        isinstance(days_ahead, list)
        and len(days_ahead) == 2
        and all(isinstance(value, int) and value >= 0 for value in days_ahead)
        and days_ahead[0] <= days_ahead[1]
    ):
        context["daysAhead"] = days_ahead
    if isinstance(local_windows, list) and all(isinstance(value, str) for value in local_windows):
        context["localWindows"] = local_windows
    return context


def queue(root: Path, *, browser_job: Dict[str, Any]) -> Dict[str, Any]:
    """Queue one idempotent operations-mode worker contract for a browser job."""
    job_key = browser_job.get("jobKey")
    actor_id = browser_job.get("actorId")
    if not isinstance(job_key, str) or not isinstance(actor_id, str):
        return {"action": "pass", "detail": "browser job has no named worker contract", "remoteWrites": 0}
    storage = Storage(root)
    observed_at = isoformat(parse_instant(None))
    agreement = _agreement_context(storage, browser_job=browser_job)
    contract_key = f"worker:{job_key}"
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        contracts = contracts if isinstance(contracts, dict) else {}
        _retire_obsolete_booking_contracts_locked(storage, contracts, observed_at=observed_at)
        existing = contracts.get(contract_key) if isinstance(contracts.get(contract_key), dict) else None
        if existing is None:
            contracts[contract_key] = {
                "contractKey": contract_key,
                "status": "queued",
                "queuedAt": observed_at,
                "actorId": actor_id,
                "browserJobKey": job_key,
                "intentId": browser_job.get("intentId"),
                "journeyAction": browser_job.get("journeyAction"),
                "playbook": browser_job.get("playbook"),
                "mode": {"research": False, "headless": True, "modelTier": "economical"},
                "allowedEvidence": ["visible_ui", "sanitized_console_network", "journey_packet", "diary_line"],
                "agreement": agreement,
                "remoteWrites": 0,
            }
        elif existing.get("status") == "reported":
            journeys = storage.load_json(storage.state / JOURNEY_STATE_FILE, default={})
            jobs = journeys.get("jobs", {}) if isinstance(journeys, dict) else {}
            matching_job = jobs.get(job_key) if isinstance(jobs, dict) else None
            # A worker that could not begin has explicitly released its local
            # browser job.  Only that state may create a later retry; an
            # observed browser journey is historical evidence, never a cue to
            # repeat a potentially stateful product action.
            if isinstance(matching_job, dict) and matching_job.get("status") == "queued":
                contracts[contract_key] = {
                    **existing,
                    "status": "queued",
                    "queuedAt": observed_at,
                    "retryOfOutcome": existing.get("outcome"),
                    "agreement": agreement,
                }
                for field in ("workerId", "claimedAt", "reportedAt", "outcome", "diaryLine", "receiptRef", "launcher"):
                    contracts[contract_key].pop(field, None)
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "contracts": contracts})
    contract = contracts[contract_key]
    return {"action": "worker_contract_queued", "contractKey": contract_key, "status": contract["status"], "actorId": actor_id, "remoteWrites": 0}


def claim(root: Path, *, contract_key: str, worker_id: str) -> Dict[str, Any]:
    """Let one economical worker take only its named Sim's contract."""
    storage = Storage(root)
    observed_at = isoformat(parse_instant(None))
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        contract = contracts.get(contract_key) if isinstance(contracts, dict) else None
        if not isinstance(contract, dict) or contract.get("status") != "queued":
            raise ValueError("worker contract is unavailable")
        if not _is_economical_worker(worker_id):
            raise ValueError("worker must use the economical worker identity")
        contract.update({"status": "active", "workerId": worker_id, "claimedAt": observed_at})
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "contracts": contracts})
    return {"contractKey": contract_key, "actorId": contract["actorId"], "status": "active", "remoteWrites": 0}


def claim_specific(root: Path, *, contract_key: str, worker_id: str) -> Dict[str, Any]:
    """Claim one named runnable contract without letting older work preempt it.

    Used only by the outer dispatcher when a current session has a named,
    time-sensitive player step.  It preserves the same queue/job checks as
    ``claim_next`` and never launches a browser or model by itself.
    """
    if not _is_economical_worker(worker_id):
        raise ValueError("worker must use the economical worker identity")
    storage = Storage(root)
    observed_at = isoformat(parse_instant(None))
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        contract = contracts.get(contract_key) if isinstance(contracts, dict) else None
        if not isinstance(contract, dict) or contract.get("status") != "queued":
            raise ValueError("named worker contract is unavailable")
        job_key, actor_id = contract.get("browserJobKey"), contract.get("actorId")
        journeys = storage.load_json(storage.state / JOURNEY_STATE_FILE, default={})
        jobs = journeys.get("jobs", {}) if isinstance(journeys, dict) else {}
        job = jobs.get(job_key) if isinstance(jobs, dict) and isinstance(job_key, str) else None
        if not isinstance(job, dict) or job.get("status") != "queued" or job.get("actorId") != actor_id:
            raise ValueError("named worker contract has no runnable browser job")
        job.update({"status": "active", "claimedBy": actor_id, "claimedAt": observed_at, "lastObservedAt": observed_at})
        contract.update({"status": "active", "workerId": worker_id, "claimedAt": observed_at})
        storage.write_json(storage.state / JOURNEY_STATE_FILE, {"schemaVersion": 1, "jobs": jobs})
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "contracts": contracts})
    return {"action": "worker_contract_claimed", "status": "active", "workerId": worker_id, "launchPacket": _launch_packet(contract), "remoteWrites": 0}


def claim_next(root: Path, *, worker_id: str) -> Dict[str, Any]:
    """Atomically launch the oldest runnable contract for one cheap worker.

    This is the operational handoff.  It claims local queue state only: the
    caller still has to run the playbook in an isolated browser and later send
    a bounded receipt.  No model, browser, or product integration is launched
    by this module.
    """
    if not _is_economical_worker(worker_id):
        raise ValueError("worker must use the economical worker identity")
    storage = Storage(root)
    observed_at = isoformat(parse_instant(None))
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        contracts = contracts if isinstance(contracts, dict) else {}
        queued = [
            contract for contract in contracts.values()
            if isinstance(contract, dict) and contract.get("status") == "queued"
        ]
        if not queued:
            return {"action": "pass", "detail": "no queued worker contract", "remoteWrites": 0}
        ordered = sorted(
            queued,
            key=lambda item: (str(item.get("queuedAt") or ""), str(item.get("contractKey") or "")),
        )
        journeys = storage.load_json(storage.state / JOURNEY_STATE_FILE, default={})
        jobs = journeys.get("jobs", {}) if isinstance(journeys, dict) else {}
        contract = next(
            (
                candidate for candidate in ordered
                if isinstance(candidate.get("browserJobKey"), str)
                and isinstance(jobs.get(candidate["browserJobKey"]), dict)
                and jobs[candidate["browserJobKey"]].get("actorId") == candidate.get("actorId")
                and jobs[candidate["browserJobKey"]].get("status") == "queued"
            ),
            None,
        )
        if contract is None:
            return {
                "action": "pass",
                "detail": "no queued worker contract has a runnable browser job",
                "remoteWrites": 0,
            }
        job_key = contract["browserJobKey"]
        actor_id = contract["actorId"]
        job = jobs[job_key]
        job.update({
            "status": "active", "claimedBy": actor_id, "claimedAt": observed_at,
            "lastObservedAt": observed_at,
        })
        contract.update({"status": "active", "workerId": worker_id, "claimedAt": observed_at})
        storage.write_json(storage.state / JOURNEY_STATE_FILE, {"schemaVersion": 1, "jobs": jobs})
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "contracts": contracts})
    return {
        "action": "worker_contract_claimed",
        "status": "active",
        "workerId": worker_id,
        "launchPacket": _launch_packet(contract),
        "remoteWrites": 0,
    }


def active_packet(root: Path, *, worker_id: str) -> Dict[str, Any]:
    """Read the one active packet already assigned to an economical worker."""
    if not _is_economical_worker(worker_id):
        raise ValueError("worker must use the economical worker identity")
    state = Storage(root).load_json(Storage(root).state / STATE_FILE, default={})
    contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
    active = [
        contract for contract in contracts.values()
        if isinstance(contract, dict) and contract.get("status") == "active" and contract.get("workerId") == worker_id
    ]
    if not active:
        return {"action": "pass", "detail": "worker has no active contract", "remoteWrites": 0}
    if len(active) != 1:
        raise ValueError("worker has more than one active contract")
    return {
        "action": "worker_contract_active",
        "status": "active",
        "workerId": worker_id,
        "launchPacket": _launch_packet(active[0]),
        "remoteWrites": 0,
    }


def runbook(root: Path, *, worker_id: str) -> Dict[str, Any]:
    """Give an external economical worker a finite, evidence-first routine."""
    packet_result = active_packet(root, worker_id=worker_id)
    if packet_result.get("action") == "pass":
        return packet_result
    packet = packet_result["launchPacket"]
    return {
        "action": "worker_runbook_ready",
        "workerId": worker_id,
        "launchPacket": packet,
        "steps": [
            "Run tools/sim-browser/worker-bootstrap.py with this worker identity.",
            "Read the named playbook before any rendered UI action.",
            "Use only the isolated browser and allowed visible controls.",
            "Record a browser observation before reporting visible progress or unexpected UI.",
            "Report exactly one bounded outcome with the bootstrap receipt reference when visible evidence exists.",
        ],
        "prohibitions": [
            "No product APIs or integrations on behalf of the Sim.",
            "No credentials, private URLs, raw network payloads, or inferred product outcomes in the receipt.",
            "Do not retry an uncertain browser journey; report blocked for coordinator review.",
        ],
        "remoteWrites": 0,
    }


def recover_unobserved_release(root: Path, *, contract_key: str) -> Dict[str, Any]:
    """Repair an older reported bootstrap failure without inventing UI work.

    This migration-style action is intentionally narrow: it can only release a
    reported ``not_started`` contract whose active browser job has no
    observations at all.  It is for contracts created before ``report`` knew
    to release that job itself.
    """
    storage = Storage(root)
    observed_at = isoformat(parse_instant(None))
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        contract = contracts.get(contract_key) if isinstance(contracts, dict) else None
        if not isinstance(contract, dict) or contract.get("status") != "reported":
            raise ValueError("only a reported worker contract may be recovered")
        if contract.get("outcome") != "not_started":
            raise ValueError("only an unobserved not-started receipt may be recovered")
        journeys = storage.load_json(storage.state / JOURNEY_STATE_FILE, default={})
        jobs = journeys.get("jobs", {}) if isinstance(journeys, dict) else {}
        job_key = contract.get("browserJobKey")
        job = jobs.get(job_key) if isinstance(jobs, dict) and isinstance(job_key, str) else None
        if (
            not isinstance(job, dict)
            or job.get("status") != "active"
            or job.get("observations")
            or job.get("lastObservedAt") != job.get("claimedAt")
        ):
            raise ValueError("browser job is not an unobserved release candidate")
        job.update({"status": "queued", "lastWorkerReleaseAt": observed_at})
        job.pop("claimedBy", None)
        job.pop("claimedAt", None)
        storage.write_json(storage.state / JOURNEY_STATE_FILE, {"schemaVersion": 1, "jobs": jobs})
    return {"action": "worker_contract_recovered", "contractKey": contract_key, "status": "queued", "remoteWrites": 0}


def quarantine_legacy_release(root: Path, *, contract_key: str) -> Dict[str, Any]:
    """Quarantine a pre-safety-fix blocked receipt that was wrongly requeued."""
    storage = Storage(root)
    observed_at = isoformat(parse_instant(None))
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        contract = contracts.get(contract_key) if isinstance(contracts, dict) else None
        if not isinstance(contract, dict) or contract.get("status") != "reported":
            raise ValueError("only a reported worker contract may be quarantined")
        if contract.get("outcome") not in {"blocked", "pass"}:
            raise ValueError("only a blocked or pass receipt may be quarantined")
        journeys = storage.load_json(storage.state / JOURNEY_STATE_FILE, default={})
        jobs = journeys.get("jobs", {}) if isinstance(journeys, dict) else {}
        job_key = contract.get("browserJobKey")
        job = jobs.get(job_key) if isinstance(jobs, dict) and isinstance(job_key, str) else None
        if not isinstance(job, dict) or job.get("status") != "queued":
            raise ValueError("browser job is not a legacy requeue candidate")
        job.update({"status": "blocked", "lastWorkerOutcome": contract["outcome"], "lastWorkerReviewAt": observed_at})
        storage.write_json(storage.state / JOURNEY_STATE_FILE, {"schemaVersion": 1, "jobs": jobs})
    return {"action": "worker_contract_quarantined", "contractKey": contract_key, "status": "blocked", "remoteWrites": 0}


def report(
    root: Path, *, contract_key: str, worker_id: str, outcome: str, diary_line: str,
    receipt_ref: str | None = None,
) -> Dict[str, Any]:
    """Close one worker turn with a bounded, secret-free diary outcome."""
    if outcome not in WORKER_OUTCOMES:
        raise ValueError("worker outcome is not allowed")
    if not isinstance(diary_line, str) or not diary_line.strip() or len(diary_line) > 280:
        raise ValueError("worker diary line must be concise")
    storage = Storage(root)
    observed_at = isoformat(parse_instant(None))
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        contract = contracts.get(contract_key) if isinstance(contracts, dict) else None
        if not isinstance(contract, dict) or contract.get("status") != "active" or contract.get("workerId") != worker_id:
            raise ValueError("only the claiming worker may report")
        validated_receipt = _receipt_ref(storage, contract=contract, receipt_ref=receipt_ref)
        if outcome in {"visible_progress", "unexpected_ui"} and validated_receipt is None:
            raise ValueError("visible worker outcome needs a local bootstrap receipt")
        journeys = storage.load_json(storage.state / JOURNEY_STATE_FILE, default={})
        jobs = journeys.get("jobs", {}) if isinstance(journeys, dict) else {}
        job_key = contract.get("browserJobKey")
        job = jobs.get(job_key) if isinstance(jobs, dict) and isinstance(job_key, str) else None
        if outcome in {"visible_progress", "unexpected_ui"} and (
            not isinstance(job, dict) or job.get("status") != "observed"
        ):
            raise ValueError("visible worker outcome needs a recorded browser observation")
        if outcome == "not_started" and isinstance(job, dict) and job.get("status") == "active":
            # This is the only retryable case: the worker explicitly says it
            # never began a rendered journey.  Any browser uncertainty is
            # quarantined below instead of replaying a possibly stateful UI.
            job.update({"status": "queued", "lastWorkerReleaseAt": observed_at})
            job.pop("claimedBy", None)
            job.pop("claimedAt", None)
            storage.write_json(storage.state / JOURNEY_STATE_FILE, {"schemaVersion": 1, "jobs": jobs})
        elif outcome in {"blocked", "pass"} and isinstance(job, dict) and job.get("status") == "active":
            job.update({"status": "blocked", "lastWorkerOutcome": outcome, "lastWorkerReviewAt": observed_at})
            storage.write_json(storage.state / JOURNEY_STATE_FILE, {"schemaVersion": 1, "jobs": jobs})
        contract.update({"status": "reported", "reportedAt": observed_at, "outcome": outcome, "diaryLine": diary_line.strip()})
        if validated_receipt is not None:
            contract["receiptRef"] = validated_receipt
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "contracts": contracts})
        storage.append_jsonl(storage.actor_journal_path(str(contract["actorId"])), {
            "observedAt": observed_at, "kind": "worker_journey", "contractKey": contract_key,
            "outcome": outcome, "text": diary_line.strip(), "receiptRef": validated_receipt, "remoteWrites": 0,
        })
    return {"contractKey": contract_key, "status": "reported", "remoteWrites": 0}
