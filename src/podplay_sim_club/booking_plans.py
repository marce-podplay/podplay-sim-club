"""Product-neutral booking-plan and quote-comparison contracts.

This is deliberately a data boundary.  It does not call Preview, select a
slot, or approve a charge.  Preview-specific adapters translate their current
payloads into these safe records before the social engine may reason about a
plan.
"""

from copy import deepcopy
from hashlib import sha256
from typing import Any, Dict, Iterable, Optional

from .facts import make_fact, reject_private_data
from .time import isoformat, parse_instant


PLAN_STAGES = {"draft", "quoted", "awaiting_payer_approval", "approved", "blocked", "executed", "observed"}
CHARGE_STRATEGIES = {"owner_pays", "split_pending", "per_player"}
BENEFIT_STRATEGIES = {"use_none", "owner_only", "per_player_pending"}
FIELD_STATUSES = {"observed", "unknown", "stale", "not_applicable"}
QUOTE_RESULTS = {"observed", "unknown", "error"}
ERROR_CODES = {"missing_field", "not_supported", "request_failed", "unexpected_shape", "quote_rejected"}
COMPARISON_STATUSES = {"within_expectation", "mismatch", "unknown"}


class BookingPlanValidationError(ValueError):
    """Raised when a plan or quote crosses the product boundary unsafely."""


def booking_plan_id(occurrence_key: str) -> str:
    _nonempty("occurrence_key", occurrence_key)
    return f"booking-plan:{occurrence_key}"


def make_booking_plan(
    *,
    occurrence_key: str,
    owner_actor_id: str,
    participant_actor_ids: Iterable[str],
    payer_actor_id: str,
    venue_ref: str,
    starts_at: str,
    ends_at: str,
    charge_strategy: str = "owner_pays",
    benefit_strategy: str = "use_none",
    stage: str = "draft",
) -> Dict[str, Any]:
    """Create a safe plan before quote or execution.

    ``payerActorId`` identifies who must later approve funding; it does not
    itself contain approval and does not authorize another participant's
    benefits.
    """
    participants = _actor_ids(participant_actor_ids)
    if owner_actor_id not in participants or payer_actor_id not in participants:
        raise BookingPlanValidationError("owner and payer must be named participants")
    if charge_strategy not in CHARGE_STRATEGIES or benefit_strategy not in BENEFIT_STRATEGIES:
        raise BookingPlanValidationError("booking strategy is unsupported")
    if stage not in PLAN_STAGES:
        raise BookingPlanValidationError("booking plan stage is invalid")
    starts = isoformat(parse_instant(starts_at))
    ends = isoformat(parse_instant(ends_at))
    if ends <= starts:
        raise BookingPlanValidationError("booking window must end after it starts")
    plan = {
        "schemaVersion": 1,
        "bookingPlanId": booking_plan_id(occurrence_key),
        "occurrenceKey": occurrence_key,
        "ownerActorId": owner_actor_id,
        "participantActorIds": participants,
        "payerActorId": payer_actor_id,
        "venueRef": _safe_ref("venue_ref", venue_ref),
        "window": {"startsAt": starts, "endsAt": ends},
        "requestedFunding": {
            "chargeStrategy": charge_strategy,
            "benefitStrategy": benefit_strategy,
        },
        "stage": stage,
    }
    validate_booking_plan(plan)
    return plan


def validate_booking_plan(plan: Any) -> None:
    required = {
        "schemaVersion", "bookingPlanId", "occurrenceKey", "ownerActorId",
        "participantActorIds", "payerActorId", "venueRef", "window",
        "requestedFunding", "stage",
    }
    if not isinstance(plan, dict) or set(plan) != required or plan.get("schemaVersion") != 1:
        raise BookingPlanValidationError("booking plan fields do not match the contract")
    if plan.get("bookingPlanId") != booking_plan_id(plan.get("occurrenceKey")):
        raise BookingPlanValidationError("booking plan ID does not match occurrence")
    participants = _actor_ids(plan.get("participantActorIds"))
    if plan.get("ownerActorId") not in participants or plan.get("payerActorId") not in participants:
        raise BookingPlanValidationError("booking plan owner/payer is not a participant")
    _safe_ref("venueRef", plan.get("venueRef"))
    window = plan.get("window")
    if not isinstance(window, dict) or set(window) != {"startsAt", "endsAt"}:
        raise BookingPlanValidationError("booking plan window is invalid")
    starts, ends = isoformat(parse_instant(window["startsAt"])), isoformat(parse_instant(window["endsAt"]))
    if ends <= starts:
        raise BookingPlanValidationError("booking plan window is inverted")
    funding = plan.get("requestedFunding")
    if not isinstance(funding, dict) or set(funding) != {"chargeStrategy", "benefitStrategy"}:
        raise BookingPlanValidationError("requested funding is invalid")
    if funding["chargeStrategy"] not in CHARGE_STRATEGIES or funding["benefitStrategy"] not in BENEFIT_STRATEGIES:
        raise BookingPlanValidationError("requested funding strategy is invalid")
    if plan.get("stage") not in PLAN_STAGES:
        raise BookingPlanValidationError("booking plan stage is invalid")
    reject_private_data(plan, "bookingPlan")


def make_quote_evidence(
    *,
    booking_plan: Dict[str, Any],
    observed_at: str,
    result: str,
    fields: Optional[Dict[str, Dict[str, Any]]] = None,
    error_code: Optional[str] = None,
    source: str = "preview_booking_quote",
) -> Dict[str, Any]:
    """Create a sanitized, product-neutral quote explanation.

    Fields are independently status-bearing.  A missing total, discount, pass
    treatment, or cancellation rule stays ``unknown``—never zero or false.
    """
    validate_booking_plan(booking_plan)
    if result not in QUOTE_RESULTS:
        raise BookingPlanValidationError("quote result is invalid")
    observed = isoformat(parse_instant(observed_at))
    if result == "observed":
        if error_code is not None:
            raise BookingPlanValidationError("observed quote cannot have an error")
        safe_fields = sanitize_quote_fields(fields)
    else:
        if fields is not None or error_code not in ERROR_CODES:
            raise BookingPlanValidationError("unobserved quote requires a bounded error")
        safe_fields = None
    natural = "\0".join((booking_plan["bookingPlanId"], observed, result))
    evidence = {
        "schemaVersion": 1,
        "evidenceId": f"evidence:booking-quote:{sha256(natural.encode('utf-8')).hexdigest()[:20]}",
        "bookingPlanId": booking_plan["bookingPlanId"],
        "observedAt": observed,
        "result": result,
        "errorCode": error_code,
        "fields": safe_fields,
        "source": {"kind": source, "readOnly": True},
    }
    validate_quote_evidence(evidence)
    return evidence


def sanitize_quote_fields(value: Any) -> Dict[str, Dict[str, Any]]:
    names = {
        "total", "currency", "membershipTreatment", "virtualCreditTreatment",
        "passTreatment", "capacityTreatment", "cancellationTerms", "blockingCodes",
    }
    if not isinstance(value, dict) or set(value) != names:
        raise BookingPlanValidationError("quote fields must contain the complete safe vocabulary")
    result = {}
    for name in sorted(names):
        field = value[name]
        if not isinstance(field, dict) or set(field) != {"status", "value"} or field.get("status") not in FIELD_STATUSES:
            raise BookingPlanValidationError(f"quote field is invalid: {name}")
        status, observed = field["status"], field["value"]
        if status != "observed" and observed is not None:
            raise BookingPlanValidationError(f"unobserved quote field carries a value: {name}")
        if status == "observed":
            observed = _sanitize_quote_value(name, observed)
        result[name] = {"status": status, "value": deepcopy(observed)}
    reject_private_data(result, "quote.fields")
    return result


def validate_quote_evidence(evidence: Any) -> None:
    required = {"schemaVersion", "evidenceId", "bookingPlanId", "observedAt", "result", "errorCode", "fields", "source"}
    if not isinstance(evidence, dict) or set(evidence) != required or evidence.get("schemaVersion") != 1:
        raise BookingPlanValidationError("quote evidence fields do not match the contract")
    _nonempty("bookingPlanId", evidence.get("bookingPlanId"))
    observed = isoformat(parse_instant(evidence["observedAt"]))
    if evidence.get("result") not in QUOTE_RESULTS:
        raise BookingPlanValidationError("quote evidence result is invalid")
    natural = "\0".join((evidence["bookingPlanId"], observed, evidence["result"]))
    expected = f"evidence:booking-quote:{sha256(natural.encode('utf-8')).hexdigest()[:20]}"
    if evidence.get("evidenceId") != expected:
        raise BookingPlanValidationError("quote evidence ID does not match observation")
    if evidence["result"] == "observed":
        if evidence.get("errorCode") is not None:
            raise BookingPlanValidationError("observed quote cannot have an error")
        sanitize_quote_fields(evidence.get("fields"))
    elif evidence.get("fields") is not None or evidence.get("errorCode") not in ERROR_CODES:
        raise BookingPlanValidationError("unobserved quote contains unsafe fields")
    if evidence.get("source") != {"kind": "preview_booking_quote", "readOnly": True}:
        raise BookingPlanValidationError("quote evidence source is invalid")
    reject_private_data(evidence, "quoteEvidence")


def make_plan_fact(*, plan: Dict[str, Any], occurred_at: str, correlation_id: str) -> Dict[str, Any]:
    validate_booking_plan(plan)
    return make_fact(
        # A stable occurrence can be replanned onto a different legal window.
        # The plan ID remains stable while each observed window is immutable.
        fact_type="booking_plan.planned",
        natural_key=f"{plan['bookingPlanId']}:{plan['window']['startsAt']}:{plan['window']['endsAt']}",
        subject_ref=plan["bookingPlanId"], correlation_id=correlation_id,
        source_kind="booking_planner", payload={"plan": deepcopy(plan)},
        occurred_at=occurred_at, actor_id=plan["ownerActorId"], privacy="operator",
    )


def make_quote_fact(*, evidence: Dict[str, Any], occurred_at: str, correlation_id: str) -> Dict[str, Any]:
    validate_quote_evidence(evidence)
    return make_fact(
        fact_type=f"booking_quote.{evidence['result']}", natural_key=evidence["evidenceId"],
        subject_ref=evidence["bookingPlanId"], correlation_id=correlation_id,
        source_kind="booking_quote", evidence_ref=evidence["evidenceId"],
        payload={"result": evidence["result"], "errorCode": evidence["errorCode"], "fields": deepcopy(evidence["fields"])},
        occurred_at=occurred_at, privacy="operator",
    )


def compare_quote_to_payer_beliefs(
    *,
    booking_plan: Dict[str, Any],
    quote_evidence: Dict[str, Any],
    beliefs: Iterable[Dict[str, Any]],
    assessed_at: str,
) -> Dict[str, Any]:
    """Compare a baseline owner-paid quote to the named payer's own beliefs.

    The comparison has no execution authority.  It deliberately requires a
    stated local price expectation and an observed ready payment context before
    returning ``within_expectation``.  Benefit fields are checked only if the
    payer made an expectation about the corresponding benefit.
    """
    validate_booking_plan(booking_plan)
    validate_quote_evidence(quote_evidence)
    when = isoformat(parse_instant(assessed_at))
    payer = booking_plan["payerActorId"]
    belief_map = {
        row.get("subject"): row
        for row in beliefs
        if isinstance(row, dict) and row.get("actorId") == payer and isinstance(row.get("subject"), str)
    }
    fields = quote_evidence.get("fields") if quote_evidence.get("result") == "observed" else None
    checks = []
    if not isinstance(fields, dict):
        checks.append(_check("quote", "unknown", "no observed quote", None))
    else:
        checks.append(_price_check(fields, belief_map.get("acceptable_booking_price")))
        checks.append(_payment_check(belief_map.get("payment_readiness")))
        checks.append(_benefit_check("membership", fields.get("membershipTreatment"), belief_map.get("membership_benefits_understood")))
        checks.append(_benefit_check("pass", fields.get("passTreatment"), belief_map.get("owned_package_entitlements")))
        checks.append(_blocking_check(fields.get("blockingCodes")))
    statuses = {row["status"] for row in checks}
    status = "mismatch" if "mismatch" in statuses else "unknown" if "unknown" in statuses else "within_expectation"
    actions = (
        ["clarify", "counter", "pass", "ask_riley"] if status == "mismatch"
        else ["inspect_assets", "clarify", "pass"] if status == "unknown"
        else ["request_payer_approval"]
    )
    natural = "\0".join((booking_plan["bookingPlanId"], quote_evidence["evidenceId"], when))
    comparison = {
        "schemaVersion": 1,
        "comparisonId": f"comparison:booking-quote:{sha256(natural.encode('utf-8')).hexdigest()[:20]}",
        "bookingPlanId": booking_plan["bookingPlanId"],
        "payerActorId": payer,
        "quoteEvidenceRef": quote_evidence["evidenceId"],
        "assessedAt": when,
        "status": status,
        "checks": checks,
        "recommendedActions": actions,
    }
    validate_quote_comparison(comparison)
    return comparison


def validate_quote_comparison(comparison: Any) -> None:
    required = {"schemaVersion", "comparisonId", "bookingPlanId", "payerActorId", "quoteEvidenceRef", "assessedAt", "status", "checks", "recommendedActions"}
    if not isinstance(comparison, dict) or set(comparison) != required or comparison.get("schemaVersion") != 1:
        raise BookingPlanValidationError("quote comparison fields do not match the contract")
    for name in ("comparisonId", "bookingPlanId", "payerActorId", "quoteEvidenceRef"):
        _nonempty(name, comparison.get(name))
    parse_instant(comparison["assessedAt"])
    if comparison.get("status") not in COMPARISON_STATUSES:
        raise BookingPlanValidationError("quote comparison status is invalid")
    checks = comparison.get("checks")
    if not isinstance(checks, list) or not checks:
        raise BookingPlanValidationError("quote comparison needs checks")
    for check in checks:
        if not isinstance(check, dict) or set(check) != {"code", "status", "expected", "observed"}:
            raise BookingPlanValidationError("quote comparison check is invalid")
        if check["status"] not in {"match", "mismatch", "unknown", "not_applicable"}:
            raise BookingPlanValidationError("quote comparison check status is invalid")
    if not isinstance(comparison.get("recommendedActions"), list) or not all(action in {"request_payer_approval", "clarify", "counter", "pass", "ask_riley", "inspect_assets"} for action in comparison["recommendedActions"]):
        raise BookingPlanValidationError("quote comparison actions are invalid")
    reject_private_data(comparison, "quoteComparison")


def make_comparison_fact(*, comparison: Dict[str, Any], occurred_at: str, correlation_id: str) -> Dict[str, Any]:
    validate_quote_comparison(comparison)
    return make_fact(
        fact_type=f"booking_quote.compared.{comparison['status']}", natural_key=comparison["comparisonId"],
        subject_ref=comparison["bookingPlanId"], correlation_id=correlation_id,
        source_kind="booking_quote_comparison", evidence_ref=comparison["quoteEvidenceRef"],
        payload={"comparison": deepcopy(comparison)}, occurred_at=occurred_at,
        actor_id=comparison["payerActorId"], privacy="operator",
    )


def _check(code: str, status: str, expected: Any, observed: Any) -> Dict[str, Any]:
    return {"code": code, "status": status, "expected": deepcopy(expected), "observed": deepcopy(observed)}


def _price_check(fields: Dict[str, Any], belief: Any) -> Dict[str, Any]:
    total, currency = fields.get("total"), fields.get("currency")
    if not _observed(total) or not _observed(currency):
        return _check("amount_and_currency", "unknown", None, None)
    expected = belief.get("expectedValue") if _belief_is(belief, "expected") else None
    if not isinstance(expected, dict) or set(expected) != {"maxAmount", "currency"}:
        return _check("amount_and_currency", "unknown", None, {"amount": total["value"], "currency": currency["value"]})
    amount, unit = expected.get("maxAmount"), expected.get("currency")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not isinstance(unit, str):
        return _check("amount_and_currency", "unknown", None, {"amount": total["value"], "currency": currency["value"]})
    expected_safe = {"maxAmount": float(amount), "currency": unit}
    observed = {"amount": total["value"], "currency": currency["value"]}
    return _check("amount_and_currency", "match" if total["value"] <= amount and currency["value"] == unit else "mismatch", expected_safe, observed)


def _payment_check(belief: Any) -> Dict[str, Any]:
    if not _belief_is(belief, "observed"):
        return _check("payment_readiness", "unknown", None, None)
    observed = belief.get("observedValue")
    if not isinstance(observed, dict) or not isinstance(observed.get("ready"), bool):
        return _check("payment_readiness", "unknown", None, None)
    return _check("payment_readiness", "match" if observed["ready"] else "mismatch", {"ready": True}, {"ready": observed["ready"]})


def _benefit_check(code: str, quote_field: Any, belief: Any) -> Dict[str, Any]:
    # Benefits are relevant only when an explicit local expectation exists.
    expected = belief.get("expectedValue") if _belief_is(belief, "expected") else None
    if expected is None:
        return _check(code, "not_applicable", None, None)
    if not _observed(quote_field):
        return _check(code, "unknown", expected, None)
    return _check(code, "match" if quote_field["value"] == expected else "mismatch", expected, quote_field["value"])


def _blocking_check(field: Any) -> Dict[str, Any]:
    if not _observed(field):
        return _check("blocking_codes", "unknown", None, None)
    values = field["value"]
    return _check("blocking_codes", "match" if values == [] else "mismatch", [], values)


def _observed(field: Any) -> bool:
    return isinstance(field, dict) and field.get("status") == "observed"


def _belief_is(belief: Any, status: str) -> bool:
    return isinstance(belief, dict) and belief.get("status") == status


def _sanitize_quote_value(name: str, value: Any) -> Any:
    if name == "total":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise BookingPlanValidationError("quote total must be a non-negative numeric amount")
        return float(value)
    if name == "currency":
        if not isinstance(value, str) or len(value) != 3 or not value.isalpha() or value.upper() != value:
            raise BookingPlanValidationError("quote currency must be an ISO-like code")
        return value
    if name == "blockingCodes":
        if not isinstance(value, list) or not all(isinstance(code, str) and 0 < len(code) <= 64 for code in value):
            raise BookingPlanValidationError("quote blocking codes are invalid")
        return sorted(set(value))
    if not isinstance(value, str) or not value or len(value) > 160:
        raise BookingPlanValidationError(f"quote treatment is invalid: {name}")
    return value


def _actor_ids(values: Any) -> list[str]:
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes)):
        raise BookingPlanValidationError("participants must be an iterable of actor IDs")
    result = list(values)
    if not result or not all(isinstance(actor_id, str) and actor_id and len(actor_id) <= 100 for actor_id in result):
        raise BookingPlanValidationError("participant actor IDs are invalid")
    if len(set(result)) != len(result):
        raise BookingPlanValidationError("participant actor IDs must be unique")
    return result


def _safe_ref(name: str, value: Any) -> str:
    _nonempty(name, value)
    if len(value) > 160 or any(character.isspace() for character in value) or "/" in value:
        raise BookingPlanValidationError(f"{name} is not a safe opaque reference")
    return value


def _nonempty(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise BookingPlanValidationError(f"{name} is required")
