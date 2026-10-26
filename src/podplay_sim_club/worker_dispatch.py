"""Secret-free handoff from a browser job to one economical Sim worker.

The Rally Engine chooses work; a separate orchestrator may later launch the
worker.  Keeping the contract durable makes that model/provider interchangeable
and prevents the Python engine from silently opening a browser or a model.
"""

from pathlib import Path
from typing import Any, Dict

from .journey_policy import STATE_FILE as JOURNEY_STATE_FILE
from .storage import Storage
from .time import isoformat, parse_instant


STATE_FILE = "worker-contracts.json"
WORKER_OUTCOMES = {"visible_progress", "unexpected_ui", "blocked", "not_started", "pass"}


def _is_economical_worker(worker_id: str) -> bool:
    return isinstance(worker_id, str) and worker_id.startswith("economical:") and len(worker_id) > len("economical:")


def _launch_packet(contract: Dict[str, Any]) -> Dict[str, Any]:
    """Return only the bounded context that a routine worker needs.

    Credentials, origins, invitation URLs, selectors, and inferred product
    state deliberately remain outside this packet.  The worker must obtain
    any evidence from its isolated rendered-browser journey.
    """
    return {
        "contractKey": contract["contractKey"],
        "actorId": contract["actorId"],
        "browserJobKey": contract["browserJobKey"],
        "journeyAction": contract.get("journeyAction"),
        "playbook": contract.get("playbook"),
        "mode": contract.get("mode"),
        "allowedEvidence": contract.get("allowedEvidence"),
    }


def queue(root: Path, *, browser_job: Dict[str, Any]) -> Dict[str, Any]:
    """Queue one idempotent operations-mode worker contract for a browser job."""
    job_key = browser_job.get("jobKey")
    actor_id = browser_job.get("actorId")
    if not isinstance(job_key, str) or not isinstance(actor_id, str):
        return {"action": "pass", "detail": "browser job has no named worker contract", "remoteWrites": 0}
    storage = Storage(root)
    observed_at = isoformat(parse_instant(None))
    contract_key = f"worker:{job_key}"
    with storage.writer_lock():
        state = storage.load_json(storage.state / STATE_FILE, default={})
        contracts = state.get("contracts", {}) if isinstance(state, dict) else {}
        contracts = contracts if isinstance(contracts, dict) else {}
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
                }
                for field in ("workerId", "claimedAt", "reportedAt", "outcome", "diaryLine"):
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


def report(root: Path, *, contract_key: str, worker_id: str, outcome: str, diary_line: str) -> Dict[str, Any]:
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
        storage.write_json(storage.state / STATE_FILE, {"schemaVersion": 1, "contracts": contracts})
        storage.append_jsonl(storage.actor_journal_path(str(contract["actorId"])), {
            "observedAt": observed_at, "kind": "worker_journey", "contractKey": contract_key,
            "outcome": outcome, "text": diary_line.strip(), "remoteWrites": 0,
        })
    return {"contractKey": contract_key, "status": "reported", "remoteWrites": 0}
