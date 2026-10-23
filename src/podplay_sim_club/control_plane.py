"""Local-only operator-to-club season-transition protocol.

The control plane is deliberately separate from community conversation. It
contains only safe season labels and workflow state; it never stores a Preview
URL, a credential, or a product identifier. Riley may turn an acknowledged
operator notice into a normal club announcement, but nobody can turn it into a
booking, entitlement, attendance record, or feature acceptance.
"""

from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict

from .protocol import append_protocol_facts_locked, conversation_id_for, make_message, reply_causation
from .product_research import ProductResearchError, research_probe
from .storage import Storage
from .time import isoformat


STATE_FILE = "control-plane.json"
CONTROL_CHANNEL = "control-plane"
PLAYER_ACTORS = ("andy-bogard", "terry-bogard")
SEASON_PATTERN = re.compile(r"^pr-[1-9][0-9]*$")
FEATURE_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
RELEASE_REFERENCE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9-]{1,63}$")


class ControlPlaneError(ValueError):
    pass


def season_notice(root: Path, season: str) -> Dict[str, Any]:
    """Send the external lead's safe season-transition notice to Riley."""
    if not SEASON_PATTERN.fullmatch(season):
        raise ControlPlaneError("season must be a safe PR label such as pr-7488")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        notices = state["notices"]
        notice_id = f"control:season-transition:{season}"
        notice = notices.get(notice_id)
        if not isinstance(notice, dict):
            observed_at = _now()
            notice = {
                "noticeId": notice_id, "season": season, "status": "addressed_to_staff",
                "recipientActorId": "riley", "createdAt": observed_at, "acknowledgedAt": None,
                "announcedAt": None, "playerAcknowledgements": {}, "remoteWrites": 0,
            }
            notices[notice_id] = notice
            _append_once(storage, {
                "id": notice_id, "channel": CONTROL_CHANNEL, "kind": "season_transition_notice",
                "from": "lead", "to": ["riley"],
                "text": "A new Preview season is active. Re-observe product state; do not treat historical product facts as current.",
                "season": season, "observedAt": observed_at, "remoteWrites": 0,
            })
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_notice(notice)


def release_notice(root: Path, feature_id: str, release_reference: str) -> Dict[str, Any]:
    """Address a safe release notice to staff without asserting configuration."""
    if not FEATURE_PATTERN.fullmatch(feature_id):
        raise ControlPlaneError("feature must be a safe lowercase identifier")
    if not RELEASE_REFERENCE_PATTERN.fullmatch(release_reference):
        raise ControlPlaneError("release reference must be a safe short identifier")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        notice_id = f"control:release-notice:{feature_id}:{release_reference.lower()}"
        notice = state["releaseNotices"].get(notice_id)
        if not isinstance(notice, dict):
            observed_at = _now()
            notice = {
                "noticeId": notice_id,
                "featureId": feature_id,
                "releaseReference": release_reference,
                "status": "addressed_to_staff",
                "recipientActorIds": ["sofia", "riley"],
                "createdAt": observed_at,
                "nextDecision": "acknowledge_or_pass_authorized_read_only_probe",
                "remoteWrites": 0,
            }
            state["releaseNotices"][notice_id] = notice
            _append_once(storage, {
                "id": notice_id, "channel": CONTROL_CHANNEL, "kind": "release_notice",
                "from": "lead", "to": ["sofia", "riley"], "featureId": feature_id,
                "releaseReference": release_reference,
                "text": "A staff configuration release is available for review. It does not prove this Club Lab experience is configured, priced, or ready for a player journey. A named authorized administrator may be asked to accept or pass one read-only probe.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_release_notice(notice)


def authorize_rate_cards_planning(root: Path) -> Dict[str, Any]:
    """Record the external lead's bounded authorization for the next plan step.

    It authorizes communication and preparation only.  The authority boundary
    stays intact: pricing values and any Preview configuration mutation still
    require a separate authorized product-side decision.
    """
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        authorization_id = "control:rate-cards-planning:pp-7827"
        authorization = state["authorizations"].get(authorization_id)
        if not isinstance(authorization, dict):
            observed_at = _now()
            authorization = {
                "authorizationId": authorization_id,
                "featureId": "rate-cards-unified-pricing",
                "releaseReference": "PP-7827",
                "status": "addressed_to_staff",
                "recipientActorIds": ["riley", "morgan-vale"],
                "createdAt": observed_at,
                "allowed": [
                    "request an approved Rate Card pricing basis from product authority",
                    "prepare a pod-scoped technical change packet",
                    "perform further GET-only configuration read-backs",
                ],
                "forbidden": [
                    "create or publish a Rate Card",
                    "enable Unified Pricing",
                    "invite players or claim a price",
                ],
                "nextDecision": "authorized_product_admin_supplies_pricing_basis_or_passes",
                "remoteWrites": 0,
            }
            state["authorizations"][authorization_id] = authorization
            _append_once(storage, {
                "id": authorization_id, "channel": CONTROL_CHANNEL, "kind": "bounded_planning_authorization",
                "from": "lead", "to": authorization["recipientActorIds"],
                "featureId": authorization["featureId"], "releaseReference": authorization["releaseReference"],
                "text": "External authorization: continue Rate Cards planning for one pod. Request an approved pricing basis and prepare the technical packet. Do not configure Preview, publish a Rate Card, or involve players.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_authorization(authorization)


def request_rate_cards_pricing_basis(root: Path) -> Dict[str, Any]:
    """Open the explicit external decision requested by the technical packet.

    The recipient is a real product authority, not a simulated character.  The
    request is intentionally unable to carry guessed prices or authorize a
    Preview write; it only makes the outstanding decision observable in the
    control plane.
    """
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        _require_rate_cards_packet(storage)
        request_id = "control:rate-cards-pricing-basis:pp-7827"
        request = state["configurationRequests"].get(request_id)
        if not isinstance(request, dict):
            observed_at = _now()
            request = {
                "requestId": request_id,
                "featureId": "rate-cards-unified-pricing",
                "releaseReference": "PP-7827",
                "status": "awaiting_external_product_response",
                "recipient": "external-product-authority",
                "createdAt": observed_at,
                "responseOptions": ["supply_approved_pricing_basis", "request_clarification", "pass"],
                "requiredInputs": [
                    "approved rate-card label and validity intent",
                    "approved currency and exact price source",
                    "price duration and billing policy",
                    "player-count pricing packages or bands",
                    "schedule conversion or replacement decision",
                ],
                "pricingBasisContract": _rate_cards_pricing_basis_contract(),
                "doesNotAuthorize": [
                    "create or publish a Rate Card",
                    "enable Unified Pricing",
                    "invite players",
                    "quote a price",
                ],
                "nextDecision": "record_an_explicit_product_configuration_proposal_after_a_basis_is_supplied",
                "remoteWrites": 0,
            }
            state["configurationRequests"][request_id] = request
            _append_once(storage, {
                "id": request_id, "channel": CONTROL_CHANNEL,
                "kind": "external_product_configuration_request", "from": "lead",
                "to": [request["recipient"]], "featureId": request["featureId"],
                "releaseReference": request["releaseReference"],
                "text": "Rate Cards planning is ready for an external product decision. Supply an approved pricing basis, request clarification, or pass. This request does not authorize any Preview configuration change.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            storage.write_json(storage.state / STATE_FILE, state)
        elif "pricingBasisContract" not in request:
            # Safe migration for requests opened before the product-schema
            # review was captured. It cannot change a response or authorize a
            # configuration action.
            request["pricingBasisContract"] = _rate_cards_pricing_basis_contract()
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_configuration_request(request)


def propose_rate_cards_pricing_basis(root: Path) -> Dict[str, Any]:
    """Send a lead-authored, reviewable pricing *proposal* to product authority.

    The proposal is intentionally ordinary control-plane state rather than a
    hidden simulator default. It has no product identifiers and cannot become
    a configuration write until a real authority explicitly accepts or revises
    it in a later operation.
    """
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        request_id = "control:rate-cards-pricing-basis:pp-7827"
        request = state["configurationRequests"].get(request_id)
        if not isinstance(request, dict):
            raise ControlPlaneError("open the external Rate Cards pricing-basis request before proposing prices")
        if request.get("status") not in {"awaiting_external_product_response", "proposal_ready_for_external_review"}:
            raise ControlPlaneError("the Rate Cards pricing request is no longer open for a proposal")
        if not isinstance(request.get("proposedBasis"), dict):
            observed_at = _now()
            request["proposedBasis"] = _club_lab_balanced_usd_30m_proposal()
            request["proposalSource"] = "lead_draft_for_external_review"
            request["proposedAt"] = observed_at
            request["status"] = "proposal_ready_for_external_review"
            request["nextDecision"] = "external_product_authority_accepts_revises_or_passes_the_proposed_basis"
            _append_once(storage, {
                "id": f"{request_id}:lead-proposal", "channel": CONTROL_CHANNEL,
                "kind": "rate_cards_pricing_basis_proposed", "from": "lead",
                "to": [request["recipient"]], "featureId": request["featureId"],
                "releaseReference": request["releaseReference"],
                "text": "A proposed 30-minute public Rate Card basis is ready for review. Please accept, request changes, or pass. It is a proposal only and does not authorize a Preview write or a player price claim.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_configuration_request(request)


def respond_to_rate_cards_pricing_basis(root: Path, *, actor_id: str, decision: str, note: str = "") -> Dict[str, Any]:
    """Record the real product authority's response to a lead proposal.

    This is a technical control-plane response, never a fictional character's
    decision. Even acceptance only makes the approved basis available for a
    later, separately authorized configuration proposal.
    """
    if actor_id != "external-product-authority":
        raise ControlPlaneError("only the external product authority may respond to the Rate Cards pricing proposal")
    if decision not in {"accept", "request_changes", "pass"}:
        raise ControlPlaneError("pricing-basis response must be accept, request_changes, or pass")
    safe_note = _safe_control_note(note)
    if decision == "request_changes" and not safe_note:
        raise ControlPlaneError("a request_changes response needs a concise explanation")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        request_id = "control:rate-cards-pricing-basis:pp-7827"
        request = state["configurationRequests"].get(request_id)
        if not isinstance(request, dict) or not isinstance(request.get("proposedBasis"), dict):
            raise ControlPlaneError("a proposed Rate Cards pricing basis is required before an external response")
        if request.get("status") != "proposal_ready_for_external_review":
            raise ControlPlaneError("the Rate Cards pricing proposal is no longer awaiting an external response")
        observed_at = _now()
        request["externalResponse"] = {
            "actorId": actor_id, "decision": decision, "decidedAt": observed_at,
            "note": safe_note or None,
        }
        request["status"] = {
            "accept": "pricing_basis_accepted_awaiting_configuration_proposal",
            "request_changes": "pricing_basis_changes_requested",
            "pass": "pricing_basis_passed",
        }[decision]
        request["nextDecision"] = {
            "accept": "prepare_and_seek_explicit_approval_for_a_configuration_proposal",
            "request_changes": "lead_revises_or_withdraws_the_pricing_proposal",
            "pass": "close_or_reframe_the_rate_cards_experiment",
        }[decision]
        _append_once(storage, {
            "id": f"{request_id}:external-response", "channel": CONTROL_CHANNEL,
            "kind": "external_product_configuration_response", "from": actor_id,
            "to": ["riley", "morgan-vale"], "featureId": request["featureId"],
            "releaseReference": request["releaseReference"], "decision": decision,
            "note": safe_note or None,
            "text": "External product response recorded. This does not create a Rate Card, enable Unified Pricing, involve players, or claim a price.",
            "observedAt": observed_at, "remoteWrites": 0,
        })
        storage.write_json(storage.state / STATE_FILE, state)
        return _public_configuration_request(request)


def preauthorize_rate_cards_configuration_run(
    root: Path, *, pricing_basis_confirmation: str,
    configuration_proposal_confirmation: str, preview_write_confirmation: str,
) -> Dict[str, Any]:
    """Capture three explicit low-risk workflow confirmations.

    This is a convenience for a future guarded run, not a standing authority
    bypass. The preauthorization is bound to the accepted pricing-basis
    fingerprint and still expires if a fresh dry run or exact Preview target
    confirmation is missing.
    """
    confirmations = {
        "acceptedPricingBasis": pricing_basis_confirmation,
        "configurationProposal": configuration_proposal_confirmation,
        "previewWriteAfterDryRun": preview_write_confirmation,
    }
    if any(value != "yes" for value in confirmations.values()):
        raise ControlPlaneError("each low-risk workflow confirmation must be the exact word 'yes'")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        request = state["configurationRequests"].get("control:rate-cards-pricing-basis:pp-7827")
        if not isinstance(request, dict) or request.get("status") != "pricing_basis_accepted_awaiting_configuration_proposal":
            raise ControlPlaneError("an explicitly accepted Rate Cards pricing basis is required before preauthorization")
        basis = request.get("proposedBasis")
        if not isinstance(basis, dict):
            raise ControlPlaneError("accepted pricing basis is unavailable")
        authorization_id = "control:rate-cards-configuration-run:pp-7827"
        authorization = state["runAuthorizations"].get(authorization_id)
        if not isinstance(authorization, dict):
            observed_at = _now()
            authorization = {
                "authorizationId": authorization_id,
                "featureId": request["featureId"],
                "status": "preauthorized_for_manifest_bound_execution",
                "createdAt": observed_at,
                "basisFingerprint": _fingerprint(basis),
                "confirmations": confirmations,
                "requiredBeforeAnyWrite": [
                    "prepare a configuration proposal from the same basis fingerprint",
                    "perform a fresh GET-only readiness read-back",
                    "render a dry-run change manifest",
                    "confirm the exact approved Preview origin at enactment time",
                    "read back Rate Card, schedule, and booking engine after each bounded write",
                ],
                "doesNotAuthorize": [
                    "a different pricing basis",
                    "a different target",
                    "a write without a current dry-run manifest",
                    "player invitation, price promise, booking, or attendance claim",
                ],
                "remoteWrites": 0,
            }
            state["runAuthorizations"][authorization_id] = authorization
            _append_once(storage, {
                "id": authorization_id, "channel": CONTROL_CHANNEL,
                "kind": "manifest_bound_configuration_run_preauthorized", "from": "lead",
                "to": ["riley", "morgan-vale"], "featureId": request["featureId"],
                "text": "Three explicit low-risk confirmations were recorded for the accepted Rate Cards basis. A fresh dry run, exact target confirmation, bounded writes, and read-backs remain mandatory.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_run_authorization(authorization)


def offer_rate_cards_future_pilot_interest(root: Path, *, actor_id: str) -> Dict[str, Any]:
    """Let Riley invite named players to opt into future research contact only."""
    if actor_id != "riley":
        raise ControlPlaneError("only Riley may offer future pilot interest to players")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        _require_rate_cards_dry_run_manifest(storage)
        offer_id = "control:rate-cards-future-pilot-interest:pp-7827"
        offer = state["playerPilotInterest"].get(offer_id)
        if not isinstance(offer, dict):
            observed_at = _now()
            offer = {
                "offerId": offer_id, "featureId": "rate-cards-unified-pricing",
                "status": "awaiting_player_responses", "from": actor_id,
                "recipientActorIds": list(PLAYER_ACTORS), "createdAt": observed_at,
                "responses": {}, "meaning": "future_research_contact_only", "remoteWrites": 0,
            }
            state["playerPilotInterest"][offer_id] = offer
            _append_once(storage, {
                "id": offer_id, "channel": "club", "kind": "future_rate_cards_pilot_interest",
                "from": actor_id, "to": list(PLAYER_ACTORS), "featureId": offer["featureId"],
                "text": "We are preparing a future pricing research pilot. You may opt in to be contacted later or pass. This is not a price quote, booking, attendance request, or permission to change Club Lab configuration.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            append_protocol_facts_locked(storage, [make_message(
                message_id=offer_id, conversation_id=conversation_id_for(offer_id), correlation_id=offer_id,
                sender_actor_id=actor_id, recipient_actor_ids=list(PLAYER_ACTORS), speech_act="request",
                content_code="rate_cards_future_pilot_interest", occurred_at=observed_at,
                parameters={"meaning": "future_research_contact_only"},
            )])
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_pilot_interest(offer)


def respond_to_rate_cards_future_pilot_interest(root: Path, *, offer_id: str, actor_id: str, decision: str) -> Dict[str, Any]:
    """Record one player's voluntary contact preference, nothing more."""
    if actor_id not in PLAYER_ACTORS:
        raise ControlPlaneError("only a named player may respond to future pilot interest")
    if decision not in {"accept", "pass"}:
        raise ControlPlaneError("future pilot interest response must be accept or pass")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        offer = state["playerPilotInterest"].get(offer_id)
        if not isinstance(offer, dict) or actor_id not in offer.get("recipientActorIds", []):
            raise ControlPlaneError("future pilot interest offer is unavailable for this player")
        if actor_id not in offer["responses"]:
            observed_at = _now()
            offer["responses"][actor_id] = {"decision": decision, "respondedAt": observed_at}
            message_id = f"{offer_id}:{actor_id}:{decision}"
            _append_once(storage, {
                "id": message_id, "channel": "club", "kind": "future_rate_cards_pilot_interest_response",
                "from": actor_id, "to": [offer["from"]], "featureId": offer["featureId"],
                "decision": decision,
                "text": "Player response recorded for future research contact only; it does not authorize configuration, establish a price, or create a booking.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            append_protocol_facts_locked(storage, [make_message(
                message_id=message_id, conversation_id=conversation_id_for(offer_id), correlation_id=offer_id,
                sender_actor_id=actor_id, recipient_actor_ids=[offer["from"]], speech_act="accept" if decision == "accept" else "reject",
                content_code="rate_cards_future_pilot_interest", occurred_at=observed_at,
                in_reply_to=offer_id, causation_id=reply_causation(offer_id),
                parameters={"meaning": "future_research_contact_only"},
            )])
            if len(offer["responses"]) == len(offer["recipientActorIds"]):
                offer["status"] = "player_interest_responses_complete"
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_pilot_interest(offer)


def request_rate_cards_price_verification(root: Path, *, actor_id: str) -> Dict[str, Any]:
    """Let Riley ask the opted-in player for a no-booking display check."""
    if actor_id != "riley":
        raise ControlPlaneError("only Riley may request this player-owned price verification")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        interest = state["playerPilotInterest"].get("control:rate-cards-future-pilot-interest:pp-7827")
        if not isinstance(interest, dict) or interest.get("responses", {}).get("andy-bogard", {}).get("decision") != "accept":
            raise ControlPlaneError("Andy must first opt into future Rate Cards research contact")
        prediction = _verified_rate_cards_prediction(storage)
        request_id = "control:rate-cards-price-verification:pp-7827:andy-bogard"
        request = state["priceVerificationRequests"].get(request_id)
        if not isinstance(request, dict):
            observed_at = _now()
            request = {
                "requestId": request_id, "featureId": "rate-cards-unified-pricing", "from": actor_id,
                "recipientActorId": "andy-bogard", "status": "awaiting_player_accept_or_pass",
                "createdAt": observed_at, "prediction": prediction,
                "meaning": "display_only_no_booking", "remoteWrites": 0,
            }
            state["priceVerificationRequests"][request_id] = request
            _append_once(storage, {
                "id": request_id, "channel": "club", "kind": "rate_cards_price_verification_request",
                "from": actor_id, "to": ["andy-bogard"], "featureId": request["featureId"],
                "text": "Would you like to check one displayed public 30-minute price and report the shown total, or pass? This is not a booking, payment, attendance request, or a promised customer price.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            append_protocol_facts_locked(storage, [make_message(
                message_id=request_id, conversation_id=conversation_id_for(request_id), correlation_id=request_id,
                sender_actor_id=actor_id, recipient_actor_ids=["andy-bogard"], speech_act="request",
                content_code="rate_cards_price_display_verification", occurred_at=observed_at,
                parameters={"meaning": "display_only_no_booking", "prediction": "exact_cents"},
            )])
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_price_verification_request(request)


def respond_to_rate_cards_price_verification(root: Path, *, request_id: str, actor_id: str, decision: str) -> Dict[str, Any]:
    """Record Andy's voluntary decision; acceptance is still not a booking."""
    if actor_id != "andy-bogard" or decision not in {"accept", "pass"}:
        raise ControlPlaneError("only Andy may accept or pass this price-verification request")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        request = state["priceVerificationRequests"].get(request_id)
        if not isinstance(request, dict) or request.get("recipientActorId") != actor_id:
            raise ControlPlaneError("price-verification request is unavailable for this player")
        if request.get("status") == "awaiting_player_accept_or_pass":
            observed_at = _now()
            request["decision"] = {"actorId": actor_id, "decision": decision, "decidedAt": observed_at}
            request["status"] = "accepted_pending_player_owned_display_observation" if decision == "accept" else "passed_by_player"
            message_id = f"{request_id}:{decision}"
            _append_once(storage, {
                "id": message_id, "channel": "club", "kind": "rate_cards_price_verification_response",
                "from": actor_id, "to": [request["from"]], "featureId": request["featureId"], "decision": decision,
                "text": "Player decision recorded. It does not create a booking, payment, attendance record, or regression conclusion.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            append_protocol_facts_locked(storage, [make_message(
                message_id=message_id, conversation_id=conversation_id_for(request_id), correlation_id=request_id,
                sender_actor_id=actor_id, recipient_actor_ids=[request["from"]],
                speech_act="accept" if decision == "accept" else "reject",
                content_code="rate_cards_price_display_verification", occurred_at=observed_at,
                in_reply_to=request_id, causation_id=reply_causation(request_id),
                parameters={"meaning": "display_only_no_booking"},
            )])
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_price_verification_request(request)


def record_rate_cards_price_verification_observation(root: Path, *, request_id: str) -> Dict[str, Any]:
    """Record the normalized, no-booking result of Andy's accepted quote check."""
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        request = state["priceVerificationRequests"].get(request_id)
        if not isinstance(request, dict) or request.get("status") != "accepted_pending_player_owned_display_observation":
            raise ControlPlaneError("an accepted price-verification request is required")
        observed_at = _now()
        request["status"] = "observed_price_with_tax"
        request["observation"] = {
            "currency": "USD", "rateCardSubtotalCents": 1000,
            "checkoutTaxCents": 100, "displayedTotalCents": 1100,
            "result": "rate_card_prediction_matched_before_tax",
            "doesNotMean": ["booking", "payment", "attendance", "regression from one observation"],
        }
        _append_once(storage, {
            "id": f"{request_id}:observation", "channel": "staff-planning", "kind": "rate_cards_price_observed",
            "from": "technical-observer", "to": ["riley", "morgan-vale"], "featureId": request["featureId"],
            "text": "The accepted no-booking quote check observed the predicted Rate Card subtotal and a separate checkout tax line. No booking, payment, or regression conclusion was created.",
            "observedAt": observed_at, "remoteWrites": 0,
        })
        storage.write_json(storage.state / STATE_FILE, state)
        return _public_price_verification_request(request)


def acknowledge_release_notice(root: Path, notice_id: str, actor_id: str) -> Dict[str, Any]:
    """Record named staff receipt of a release notice, without a feature decision.

    Receipt means only that the staff member understands the configuration
    boundary.  It neither grants authority nor requests a Preview operation.
    """
    if actor_id not in {"sofia", "riley"}:
        raise ControlPlaneError("only Sofia or Riley may acknowledge a release notice")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        notice = state["releaseNotices"].get(notice_id)
        if not isinstance(notice, dict):
            raise ControlPlaneError("release notice is unavailable")
        if actor_id not in notice["recipientActorIds"]:
            raise ControlPlaneError("staff member is not addressed by this release notice")
        acknowledgements = notice.setdefault("acknowledgements", {})
        if actor_id not in acknowledgements:
            observed_at = _now()
            acknowledgements[actor_id] = observed_at
            if all(recipient in acknowledgements for recipient in notice["recipientActorIds"]):
                notice["status"] = "staff_acknowledged"
            _append_once(storage, {
                "id": f"{notice_id}:{actor_id}:acknowledged", "channel": CONTROL_CHANNEL,
                "kind": "release_notice_acknowledged", "from": actor_id, "to": ["lead"],
                "featureId": notice["featureId"], "releaseReference": notice["releaseReference"],
                "text": "Release notice received. No Club Lab configuration, entitlement, or player journey has been accepted.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_release_notice(notice)


def acknowledge(root: Path, notice_id: str, actor_id: str) -> Dict[str, Any]:
    """Let the named staff recipient acknowledge the private control notice."""
    storage = Storage(root)
    with storage.writer_lock():
        state, notice = _notice(storage, notice_id)
        if actor_id != "riley":
            raise ControlPlaneError("only Riley may acknowledge this staff notice")
        if notice["status"] == "addressed_to_staff":
            notice["status"] = "staff_acknowledged"
            notice["acknowledgedAt"] = _now()
            _append_once(storage, {
                "id": f"{notice_id}:riley-acknowledged", "channel": CONTROL_CHANNEL,
                "kind": "staff_acknowledged_season_notice", "from": "riley", "to": ["lead"],
                "season": notice["season"],
                "text": "Riley acknowledges the transition notice and will communicate the re-observation boundary to players.",
                "observedAt": notice["acknowledgedAt"], "remoteWrites": 0,
            })
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_notice(notice)


def announce(root: Path, notice_id: str, actor_id: str) -> Dict[str, Any]:
    """Let Riley publish a plain-language awareness message to named players."""
    storage = Storage(root)
    with storage.writer_lock():
        state, notice = _notice(storage, notice_id)
        if actor_id != "riley":
            raise ControlPlaneError("only Riley may make the staff announcement")
        if notice["status"] not in {"staff_acknowledged", "players_informed", "players_acknowledged"}:
            raise ControlPlaneError("Riley must acknowledge the control notice before announcing it")
        announcement_id = f"{notice_id}:community-awareness"
        if notice["announcedAt"] is None:
            observed_at = _now()
            notice["status"] = "players_informed"
            notice["announcedAt"] = observed_at
            text = "Preview season transition: earlier product-side bookings, invitations, benefits, and check-ins are history until re-observed. Community history remains; please acknowledge or ask for clarification."
            _append_once(storage, {
                "id": announcement_id, "channel": "club", "kind": "season_transition_awareness",
                "from": "riley", "to": list(PLAYER_ACTORS), "text": text,
                "season": notice["season"], "observedAt": observed_at, "remoteWrites": 0,
            })
            append_protocol_facts_locked(storage, [make_message(
                message_id=announcement_id, conversation_id=conversation_id_for(notice_id),
                correlation_id=notice_id, sender_actor_id="riley", recipient_actor_ids=list(PLAYER_ACTORS),
                speech_act="inform", content_code="season_transition_awareness", occurred_at=observed_at,
                parameters={"season": notice["season"], "productState": "reobserve"},
            )])
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_notice(notice)


def acknowledge_awareness(root: Path, notice_id: str, actor_id: str) -> Dict[str, Any]:
    """Record player awareness; this is explicitly not acceptance or attendance."""
    if actor_id not in PLAYER_ACTORS:
        raise ControlPlaneError("only a named player may acknowledge season awareness")
    storage = Storage(root)
    with storage.writer_lock():
        state, notice = _notice(storage, notice_id)
        if notice.get("announcedAt") is None:
            raise ControlPlaneError("Riley must inform players before they can acknowledge awareness")
        acknowledgements = notice["playerAcknowledgements"]
        if actor_id not in acknowledgements:
            observed_at = _now()
            announcement_id = f"{notice_id}:community-awareness"
            acknowledgements[actor_id] = observed_at
            message_id = f"{announcement_id}:{actor_id}:acknowledged"
            _append_once(storage, {
                "id": message_id, "channel": "club", "kind": "season_transition_awareness_acknowledged",
                "from": actor_id, "to": ["riley"], "season": notice["season"],
                "text": "Acknowledged: I understand product-side state must be re-observed. This does not accept a feature or confirm attendance.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            append_protocol_facts_locked(storage, [make_message(
                message_id=message_id, conversation_id=conversation_id_for(notice_id), correlation_id=notice_id,
                sender_actor_id=actor_id, recipient_actor_ids=["riley"], speech_act="acknowledge",
                content_code="season_transition_awareness", occurred_at=observed_at, in_reply_to=announcement_id,
                causation_id=reply_causation(announcement_id),
                parameters={"season": notice["season"], "meaning": "awareness_only"},
            )])
            if all(player in acknowledgements for player in PLAYER_ACTORS):
                notice["status"] = "players_acknowledged"
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_notice(notice)


def research_handoff(root: Path, probe_id: str) -> Dict[str, Any]:
    """Address an observed, sanitized research outcome to Sofia and Riley.

    The handoff asks staff to decide whether a separate player-owned UI journey
    should be requested. It is not a request to perform that journey.
    """
    try:
        probe = research_probe(root, probe_id)
    except ProductResearchError as exc:
        raise ControlPlaneError(str(exc)) from exc
    if probe.get("status") != "observed" or not isinstance(probe.get("configuration"), dict):
        raise ControlPlaneError("an observed configuration read-back is required before staff handoff")
    configuration = probe["configuration"]
    summary = configuration.get("summary")
    if not isinstance(summary, dict) or configuration.get("result") != "observed":
        raise ControlPlaneError("research read-back summary is unavailable")
    safe_summary = {
        key: value for key, value in summary.items()
        if key in {"tenantSignupFlagsObserved", "configuredAreaPresent", "configuredPodPresent", "bookingQuotaShapePresent", "waiverRequired", "phoneRequired", "testPaymentsEnabled"} and isinstance(value, bool)
    }
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        handoff_id = f"control:research-handoff:{probe_id}"
        handoff = state["handoffs"].get(handoff_id)
        if not isinstance(handoff, dict):
            observed_at = _now()
            handoff = {
                "handoffId": handoff_id, "probeId": probe_id, "featureId": probe["featureId"],
                "status": "addressed_to_staff", "recipientActorIds": ["sofia", "riley"],
                "createdAt": observed_at, "acknowledgements": {}, "observation": safe_summary,
                "releaseStatus": configuration.get("release", {}).get("status") if isinstance(configuration.get("release"), dict) else "unknown",
                "nextDecision": "request_or_pass_player_owned_ui_journey", "remoteWrites": 0,
            }
            state["handoffs"][handoff_id] = handoff
            _append_once(storage, {
                "id": handoff_id, "channel": CONTROL_CHANNEL, "kind": "handoff_request",
                "from": "lead", "to": ["sofia", "riley"], "probeId": probe_id,
                "text": "Team-signup configuration surfaces were observed. This does not establish entitlement or a successful journey. Decide whether to request, or pass on, a separate player-owned UI verification.",
                "payload": {"featureId": probe["featureId"], "observation": safe_summary, "releaseStatus": handoff["releaseStatus"]},
                "observedAt": observed_at, "remoteWrites": 0,
            })
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_handoff(handoff)


def acknowledge_handoff(root: Path, handoff_id: str, actor_id: str) -> Dict[str, Any]:
    """Record staff receipt only; it does not decide or authorize a journey."""
    if actor_id not in {"sofia", "riley"}:
        raise ControlPlaneError("only Sofia or Riley may acknowledge a research handoff")
    storage = Storage(root)
    with storage.writer_lock():
        state = _load(storage)
        handoff = state["handoffs"].get(handoff_id)
        if not isinstance(handoff, dict):
            raise ControlPlaneError("research handoff is unavailable")
        if actor_id not in handoff["recipientActorIds"]:
            raise ControlPlaneError("staff member is not addressed by this handoff")
        if actor_id not in handoff["acknowledgements"]:
            observed_at = _now()
            handoff["acknowledgements"][actor_id] = observed_at
            if all(recipient in handoff["acknowledgements"] for recipient in handoff["recipientActorIds"]):
                handoff["status"] = "staff_acknowledged"
            _append_once(storage, {
                "id": f"{handoff_id}:{actor_id}:acknowledged", "channel": CONTROL_CHANNEL,
                "kind": "handoff_acknowledged", "from": actor_id, "to": ["lead"],
                "probeId": handoff["probeId"], "text": "Research handoff received. No player journey has been requested or authorized.",
                "observedAt": observed_at, "remoteWrites": 0,
            })
            storage.write_json(storage.state / STATE_FILE, state)
        return _public_handoff(handoff)


def tick(root: Path) -> Dict[str, Any]:
    """Project the explicit transition queue without autonomous social actions."""
    state = _load(Storage(root))
    notices = [_public_notice(value) for value in state["notices"].values() if isinstance(value, dict)]
    notices.sort(key=lambda value: value["noticeId"])
    handoffs = [_public_handoff(value) for value in state["handoffs"].values() if isinstance(value, dict)]
    handoffs.sort(key=lambda value: value["handoffId"])
    release_notices = [_public_release_notice(value) for value in state["releaseNotices"].values() if isinstance(value, dict)]
    release_notices.sort(key=lambda value: value["noticeId"])
    authorizations = [_public_authorization(value) for value in state["authorizations"].values() if isinstance(value, dict)]
    authorizations.sort(key=lambda value: value["authorizationId"])
    run_authorizations = [_public_run_authorization(value) for value in state["runAuthorizations"].values() if isinstance(value, dict)]
    run_authorizations.sort(key=lambda value: value["authorizationId"])
    configuration_requests = [_public_configuration_request(value) for value in state["configurationRequests"].values() if isinstance(value, dict)]
    configuration_requests.sort(key=lambda value: value["requestId"])
    pilot_interest = [_public_pilot_interest(value) for value in state["playerPilotInterest"].values() if isinstance(value, dict)]
    pilot_interest.sort(key=lambda value: value["offerId"])
    price_verifications = [_public_price_verification_request(value) for value in state["priceVerificationRequests"].values() if isinstance(value, dict)]
    price_verifications.sort(key=lambda value: value["requestId"])
    return {"notices": notices, "handoffs": handoffs, "releaseNotices": release_notices, "authorizations": authorizations, "runAuthorizations": run_authorizations, "configurationRequests": configuration_requests, "playerPilotInterest": pilot_interest, "priceVerificationRequests": price_verifications, "remoteWrites": 0, "autonomousMessages": 0}


def snapshot(root: Path) -> Dict[str, Any]:
    return tick(root)


def _load(storage: Storage) -> Dict[str, Any]:
    value = storage.load_json(storage.state / STATE_FILE, default={"schemaVersion": 1, "notices": {}, "handoffs": {}, "releaseNotices": {}, "authorizations": {}, "runAuthorizations": {}, "configurationRequests": {}, "playerPilotInterest": {}, "priceVerificationRequests": {}})
    if isinstance(value, dict) and value.get("schemaVersion") == 1 and isinstance(value.get("notices"), dict):
        value.setdefault("handoffs", {})
        value.setdefault("releaseNotices", {})
        value.setdefault("authorizations", {})
        value.setdefault("runAuthorizations", {})
        value.setdefault("configurationRequests", {})
        value.setdefault("playerPilotInterest", {})
        value.setdefault("priceVerificationRequests", {})
    if (not isinstance(value, dict) or value.get("schemaVersion") != 1
            or not isinstance(value.get("notices"), dict)
            or not isinstance(value.get("handoffs"), dict)
            or not isinstance(value.get("releaseNotices"), dict)
            or not isinstance(value.get("authorizations"), dict)
            or not isinstance(value.get("runAuthorizations"), dict)
            or not isinstance(value.get("configurationRequests"), dict)
            or not isinstance(value.get("playerPilotInterest"), dict)
            or not isinstance(value.get("priceVerificationRequests"), dict)):
        raise ControlPlaneError("control-plane state is invalid")
    return value


def _notice(storage: Storage, notice_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    state = _load(storage)
    notice = state["notices"].get(notice_id)
    if not isinstance(notice, dict):
        raise ControlPlaneError("season notice is unavailable")
    return state, notice


def _append_once(storage: Storage, row: Dict[str, Any]) -> None:
    path = storage.channel_path(str(row["channel"]))
    if not any(existing.get("id") == row["id"] for existing in storage.read_jsonl(path)):
        storage.append_jsonl(path, row)


def _public_notice(notice: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "noticeId": notice["noticeId"], "season": notice["season"], "status": notice["status"],
        "recipientActorId": notice["recipientActorId"], "createdAt": notice["createdAt"],
        "acknowledgedAt": notice["acknowledgedAt"], "announcedAt": notice["announcedAt"],
        "playerAcknowledgements": sorted(notice["playerAcknowledgements"]), "remoteWrites": 0,
    }


def _public_handoff(handoff: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "handoffId": handoff["handoffId"], "probeId": handoff["probeId"], "featureId": handoff["featureId"],
        "status": handoff["status"], "recipientActorIds": handoff["recipientActorIds"],
        "createdAt": handoff["createdAt"], "acknowledgedBy": sorted(handoff["acknowledgements"]),
        "observation": handoff["observation"], "releaseStatus": handoff["releaseStatus"],
        "nextDecision": handoff["nextDecision"], "remoteWrites": 0,
    }


def _public_release_notice(notice: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "noticeId": notice["noticeId"], "featureId": notice["featureId"],
        "releaseReference": notice["releaseReference"], "status": notice["status"],
        "recipientActorIds": notice["recipientActorIds"], "createdAt": notice["createdAt"],
        "acknowledgedBy": sorted(notice.get("acknowledgements", {})),
        "nextDecision": notice["nextDecision"], "remoteWrites": 0,
    }


def _public_authorization(authorization: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "authorizationId": authorization["authorizationId"], "featureId": authorization["featureId"],
        "releaseReference": authorization["releaseReference"], "status": authorization["status"],
        "recipientActorIds": authorization["recipientActorIds"], "createdAt": authorization["createdAt"],
        "allowed": authorization["allowed"], "forbidden": authorization["forbidden"],
        "nextDecision": authorization["nextDecision"], "remoteWrites": 0,
    }


def _require_rate_cards_packet(storage: Storage) -> None:
    experiments = storage.load_json(storage.state / "configuration-experiments.json", default={})
    rows = experiments.get("experiments") if isinstance(experiments, dict) else None
    experiment = rows.get("experiment:rate-cards-unified-pricing:pp-7827") if isinstance(rows, dict) else None
    packet = experiment.get("technicalChangePacket") if isinstance(experiment, dict) else None
    if not isinstance(packet, dict) or packet.get("status") != "awaiting_authorized_pricing_basis":
        raise ControlPlaneError("the current Rate Cards technical change packet is required before requesting a pricing basis")


def _require_rate_cards_dry_run_manifest(storage: Storage) -> None:
    experiments = storage.load_json(storage.state / "configuration-experiments.json", default={})
    rows = experiments.get("experiments") if isinstance(experiments, dict) else None
    experiment = rows.get("experiment:rate-cards-unified-pricing:pp-7827") if isinstance(rows, dict) else None
    manifest = experiment.get("dryRunManifest") if isinstance(experiment, dict) else None
    if not isinstance(manifest, dict) or manifest.get("status") != "ready_for_exact_origin_confirmation":
        raise ControlPlaneError("a current no-write Rate Cards manifest is required before inviting future pilot interest")


def _public_configuration_request(request: Dict[str, Any]) -> Dict[str, Any]:
    public = {
        "requestId": request["requestId"], "featureId": request["featureId"],
        "releaseReference": request["releaseReference"], "status": request["status"],
        "recipient": request["recipient"], "createdAt": request["createdAt"],
        "responseOptions": request["responseOptions"], "requiredInputs": request["requiredInputs"],
        "pricingBasisContract": request["pricingBasisContract"],
        "doesNotAuthorize": request["doesNotAuthorize"], "nextDecision": request["nextDecision"],
        "remoteWrites": 0,
    }
    if isinstance(request.get("proposedBasis"), dict):
        public["proposedBasis"] = request["proposedBasis"]
        public["proposalSource"] = request.get("proposalSource")
        public["proposedAt"] = request.get("proposedAt")
    if isinstance(request.get("externalResponse"), dict):
        public["externalResponse"] = request["externalResponse"]
    return public


def _public_run_authorization(authorization: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "authorizationId": authorization["authorizationId"], "featureId": authorization["featureId"],
        "status": authorization["status"], "createdAt": authorization["createdAt"],
        "basisFingerprint": authorization["basisFingerprint"], "confirmations": authorization["confirmations"],
        "requiredBeforeAnyWrite": authorization["requiredBeforeAnyWrite"],
        "doesNotAuthorize": authorization["doesNotAuthorize"], "remoteWrites": 0,
    }


def _public_pilot_interest(offer: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "offerId": offer["offerId"], "featureId": offer["featureId"], "status": offer["status"],
        "from": offer["from"], "recipientActorIds": offer["recipientActorIds"],
        "createdAt": offer["createdAt"], "responses": offer["responses"],
        "meaning": offer["meaning"], "remoteWrites": 0,
    }


def _verified_rate_cards_prediction(storage: Storage) -> Dict[str, Any]:
    experiments = storage.load_json(storage.state / "configuration-experiments.json", default={})
    rows = experiments.get("experiments") if isinstance(experiments, dict) else None
    experiment = rows.get("experiment:rate-cards-unified-pricing:pp-7827") if isinstance(rows, dict) else None
    prediction = experiment.get("pricePrediction") if isinstance(experiment, dict) else None
    enactment = experiment.get("enactment") if isinstance(experiment, dict) else None
    if not isinstance(prediction, dict) or prediction.get("status") != "ready_for_player_owned_observation" or not isinstance(enactment, dict) or enactment.get("status") != "completed_verified":
        raise ControlPlaneError("a verified Rate Cards price prediction is required before requesting player observation")
    return prediction


def _public_price_verification_request(request: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "requestId": request["requestId"], "featureId": request["featureId"], "from": request["from"],
        "recipientActorId": request["recipientActorId"], "status": request["status"], "createdAt": request["createdAt"],
        "prediction": request["prediction"], "meaning": request["meaning"], "decision": request.get("decision"), "remoteWrites": 0,
    }


def _fingerprint(value: Dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def _rate_cards_pricing_basis_contract() -> Dict[str, Any]:
    """The value-free, code-derived shape an external decision must satisfy."""
    return {
        "assignment": "POD only; the selected target is resolved by the guarded adapter and is never supplied in the request",
        "rateCard": {
            "displayName": "non-empty approved label",
            "publication": "DRAFT or PUBLISHED; PUBLISHED needs a valid-from date",
            "validity": "approved valid-from and optional valid-to intent",
        },
        "pricingConfig": {
            "priceDurationMinutes": "positive approved duration",
            "includedGuests": "approved non-negative count when applicable",
            "billing": [
                "PER_PLAYER with GROUP authorization",
                "PER_PLAYER with OWN_SHARE authorization and an unclaimed-spots policy",
                "COURT with explicit splitting choice",
            ],
            "pricePackages": {
                "minimum": 1,
                "bands": ["OFF_PEAK", "PEAK", "REDUCED"],
                "eachPackage": "display label and one or more player-count price rows",
                "eachPriceRow": "player count and exact non-negative reservation cents; optional exact fees only when approved",
            },
        },
        "schedule": "explicitly choose legacy conversion when supported or provide a replacement schedule decision",
        "source": "reviewed product Rate Card validation; all values remain absent until supplied by product authority",
    }


def _club_lab_balanced_usd_30m_proposal() -> Dict[str, Any]:
    """A lead proposal, not an inferred tenant price or product payload."""
    return {
        "proposalLabel": "Club Lab balanced public pilot",
        "currency": "USD",
        "priceInterpretation": "total booking amount for one 30-minute reservation, before any separately approved fees or discounts",
        "pricingConfig": {
            "priceDurationMinutes": 30,
            "includedGuests": 1,
            "billing": {"pricingModel": "PER_PLAYER", "bookerAuthorizes": "GROUP"},
            "pricePackages": [
                {"band": "OFF_PEAK", "displayName": "Off peak", "pricesByPlayerCount": [
                    {"playerCount": 1, "reservationPriceCents": 1000},
                    {"playerCount": 2, "reservationPriceCents": 2000},
                    {"playerCount": 3, "reservationPriceCents": 3000},
                    {"playerCount": 4, "reservationPriceCents": 4000},
                ]},
                {"band": "PEAK", "displayName": "Peak", "pricesByPlayerCount": [
                    {"playerCount": 1, "reservationPriceCents": 1500},
                    {"playerCount": 2, "reservationPriceCents": 3000},
                    {"playerCount": 3, "reservationPriceCents": 4500},
                    {"playerCount": 4, "reservationPriceCents": 5000},
                ]},
                {"band": "REDUCED", "displayName": "Reduced", "pricesByPlayerCount": [
                    {"playerCount": 1, "reservationPriceCents": 1000},
                    {"playerCount": 2, "reservationPriceCents": 1500},
                    {"playerCount": 3, "reservationPriceCents": 2000},
                    {"playerCount": 4, "reservationPriceCents": 2500},
                ]},
            ],
        },
        "scheduleProposal": {
            "audience": "PUBLIC",
            "defaultBand": "OFF_PEAK",
            "timeWindows": [
                {"band": "REDUCED", "weekdays": [1, 2, 3, 4, 5], "startLocalTime": "06:00", "endLocalTime": "09:00"},
                {"band": "PEAK", "weekdays": [1, 2, 3, 4, 5], "startLocalTime": "17:00", "endLocalTime": "22:00"},
                {"band": "PEAK", "weekdays": [0, 6], "startLocalTime": "09:00", "endLocalTime": "13:00"},
            ],
            "weekdayIndexing": "Sunday-first: 0 Sunday through 6 Saturday; times are local to the selected pod",
        },
        "validity": "draft only until an external product authority supplies publication and validity intent",
        "bounds": "Every proposed reservation price is between USD 10.00 and USD 50.00 per 30-minute reservation.",
        "notAProductPayload": True,
    }


def _safe_control_note(note: str) -> str:
    if not isinstance(note, str) or len(note.strip()) > 280:
        raise ControlPlaneError("control-plane note must be at most 280 characters")
    value = note.strip()
    if re.search(r"https?://|\b(?:password|token|credential|secret)\b", value, flags=re.IGNORECASE):
        raise ControlPlaneError("control-plane note must not contain a private reference")
    return value


def _now() -> str:
    return isoformat(datetime.now(timezone.utc))
