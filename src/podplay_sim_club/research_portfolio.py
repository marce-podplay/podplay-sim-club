"""Small, durable index of release-scoped Club Lab experiments.

The portfolio is a map, not a second evidence store: detailed evidence stays
with its experiment and the append-only fact journal remains the source of
truth.  This deliberately keeps active research compact.
"""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .configuration_experiments import EXPERIMENT_ID, FEATURE_RATE_CARDS, experiment_snapshot
from .facts import reject_private_data
from .storage import Storage
from .time import isoformat


STATE_FILE = "research-portfolio.json"
TEMPLATES = {
    "configuration_intensive": "Admin read-back before any change or player journey.",
    "staff_operational": "Staff workflow after an explicit configuration baseline.",
    "player_journey": "Named player accepts or passes a predicted UI journey.",
    "team_event": "Captain, teammate, invitation, signup, and attendance evidence.",
    "pricing": "Effective configuration, declared cents prediction, then a bounded quote comparison.",
}


class ResearchPortfolioError(ValueError):
    pass


def portfolio_snapshot(root: Path) -> Dict[str, Any]:
    """Return concise cards; callers open an experiment only when needed."""
    storage = Storage(root)
    state = _load(storage)
    entries = [_public(entry) for entry in state["experiments"].values() if isinstance(entry, dict)]
    entries.sort(key=lambda entry: entry["releaseReference"])
    return {
        "templates": deepcopy(TEMPLATES),
        "experiments": entries,
        "compactionPolicy": {
            "availableNow": False,
            "eligibleOnlyWhen": ["closed", "archived", "stale"],
            "sourceOfTruth": "append-only facts and original safe evidence",
            "futureSummaryMustKeep": ["source fact references", "outcome", "open question", "freshness boundary"],
        },
    }


def sync_rate_cards_experiment(root: Path) -> Dict[str, Any]:
    """Register PP-7827 once, deriving its concise status from its workflow."""
    workflow = experiment_snapshot(root)
    matching = next((row for row in workflow["experiments"] if row.get("experimentId") == EXPERIMENT_ID), None)
    if not isinstance(matching, dict):
        raise ResearchPortfolioError("request the Rate Cards configuration exploration before portfolio sync")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        entry = state["experiments"].get(EXPERIMENT_ID)
        if not isinstance(entry, dict):
            entry = {
                "experimentId": EXPERIMENT_ID,
                "releaseReference": "PP-7827",
                "featureId": FEATURE_RATE_CARDS,
                "template": "configuration_intensive",
                "dependencyContexts": ["unified-pricing"],
                "createdAt": _now(),
                "archiveSummary": None,
            }
            state["experiments"][EXPERIMENT_ID] = entry
        entry["phase"] = matching["phase"]
        entry["portfolioStatus"] = _status(matching["phase"])
        entry["freshnessBoundary"] = "current Preview season and the PP-7827 release context"
        entry["evidenceKind"] = "configuration_only"
        claims = matching.get("uncertainty", {}).get("claims", [])
        unresolved = [claim for claim in claims if claim.get("evidenceStatus") in {"not_observed", "inconclusive", "stale"}]
        entry["uncertainty"] = {
            "unresolvedClaimCount": len(unresolved),
            "nextObservation": unresolved[0].get("nextDiscriminatingObservation") if unresolved else None,
        }
        entry["remoteWrites"] = 0
        reject_private_data(entry, "researchPortfolio")
        storage.write_json(storage.state / STATE_FILE, state)
        return _public(entry)


def _status(phase: str) -> str:
    if phase in {"configuration_exploration_requested", "configuration_exploration_accepted"}:
        return "active"
    if phase == "configuration_exploration_passed":
        return "passed"
    if phase == "configuration_inconclusive":
        return "inconclusive"
    if phase == "configuration_observed":
        return "evidence_ready"
    if phase == "change_proposal_needed":
        return "awaiting_admin_configuration_decision"
    return "unknown"


def _load(storage: Storage) -> Dict[str, Any]:
    state = storage.load_json(storage.state / STATE_FILE, default={"schemaVersion": 1, "experiments": {}})
    if not isinstance(state, dict) or state.get("schemaVersion") != 1 or not isinstance(state.get("experiments"), dict):
        raise ResearchPortfolioError("research portfolio state is invalid")
    return state


def _public(entry: Dict[str, Any]) -> Dict[str, Any]:
    value = deepcopy(entry)
    reject_private_data(value, "researchPortfolioPublic")
    return value


def _now() -> str:
    return isoformat(datetime.now(timezone.utc))
