# Pricing, benefits, and booking authority

## Purpose

Pricing is part of the social simulation because a booking can affect a real
customer-facing entitlement, a payment method, or a time-limited pass. The
model must make those effects understandable before an actor agrees to them.
It must never treat a captain's team role as authority to spend, redeem, or
reveal another player's assets.

This document describes the next capability slice: a read-only quote for the
existing Bogard-Higashi versus Japan Team agreement, followed by an
expectation-aware decision. It does not assert that every PodPlay membership,
package, or pricing rule currently has a particular effect. Those effects are
facts only when the customer surface or the quote has shown them.

## The useful distinction: fact, belief, expectation, and agreement

“Immutable” should describe how the simulator records an observation, not a
claim that PodPlay's price or a customer's entitlement can never change.
Facts are append-only: a later read produces a new fact rather than rewriting
the old one. Current projections may change as newer evidence arrives or an
older observation expires.

| Kind | Example | Who can establish it | How long it is useful |
| --- | --- | --- | --- |
| **Customer-visible account observation** | Mai has an active package item with a remaining quantity and expiry. | Mai's authenticated customer read | Short-lived; quantities and expiry can change. |
| **Customer-visible policy observation** | A membership screen describes a benefit or a booking screen states a cancellation rule. | The affected customer's visible product surface | Versioned; re-check when a quote depends on it. |
| **Availability observation** | This court is offered at this local start time for this duration. | Read-only availability probe | Very short-lived. |
| **Quote observation** | The exact selected doubles session has this total, currency, credit treatment, and errors. | The proposed payer's non-persisting quote | Valid only for its actor, items, strategy, and time. |
| **Actor expectation** | Kyo will pay at most 30 credits, expects a member rate, or prefers to use an expiring pass. | Scenario/actor policy | Fictional and revisable; never proof of an entitlement. |
| **Social agreement** | Four named players agree to a particular match, with a named payer and benefit strategy. | Explicit addressed acceptances | Ends or changes through its own commitment lifecycle. |
| **Execution/read-back** | A booking was created, an invitation was issued, or a pass was actually consumed. | Guarded product write followed by read-back | Durable evidence of that one outcome. |

The distinction resolves common questions:

- A visible membership assignment is not proof of a member discount.
- A pass listed in an account is not proof that it applies to this court, time,
  player count, or booking strategy.
- An expiring pass is a reason to offer a booking; it is not permission to
  consume it.
- A captain agreement is not permission to charge or redeem for teammates.
- A displayed grid rate is not the final price; a quote may add fees, tax, or
  eligibility rules.

## Customer-known facts worth preserving

The simulator should retain only safe summaries that help a customer understand
the proposed booking or explain a mismatch later. It must retain the source,
time observed, freshness, and whether the value is observed, unknown, stale,
or contradicted.

### Account and entitlement facts

For each actor, discover only their own customer-visible state:

- membership assignment and customer-visible benefit wording, when present;
- active package or pass category, remaining quantity, and expiration;
- virtual-credit balance, including an explicit zero;
- whether a default booking payment method is ready, without card or payment
  identifiers;
- customer-visible booking permissions or limits, once a verified surface is
  available.

Do not store transaction history by default, raw account responses, payment
details, coupon codes discovered from a private surface, stable product IDs, or
credentials. A missing field, failed request, or expired observation remains
**unknown**. It never becomes “no membership,” “no credits,” or “free.”

### Booking and operating facts

For a selected venue, duration, and local time, the booking plan needs its own
observations:

- offered area/court and its capacity;
- slot start/end in the venue timezone and whether it is presently bookable;
- club opening/closing boundary that affects the slot, if the customer-facing
  availability or quote exposes it;
- displayed rate or peak/off-peak classification, if exposed;
- quote total, currency, fees/tax if itemized, credit amount, pass strategy,
  membership treatment, and blocking error codes;
- cancellation, lead-time, booking-limit, or waiver restrictions relevant to
  that exact booking.

Opening hours, peak/off-peak rules, and membership eligibility are not merely
money rules. They can turn an otherwise attractive session into an unavailable
or forbidden one. The engine should surface that as a constraint—such as
`outside_opening_hours`, `membership_required`, `booking_limit_reached`, or
`pass_ineligible`—rather than disguising it as a social rejection.

## Scope of a benefit: the payer comes first

Every booking plan must name a **payer/benefit owner** separately from the
captain, attendees, inviter, and booking operator. The safe default is:

> An account benefit affects only the authenticated customer whose quote is
> being read and whose customer flow will execute it.

For a captain-led doubles match, this creates three distinct patterns.

| Pattern | What the captain may do | What must still be true |
| --- | --- | --- |
| **Captain pays** | Captain proposes and, after agreement, reads a quote using their own credits/benefits. | The quote identifies the captain's treatment; teammates accept attendance, not an unspoken charge. |
| **Teammate pays** | Captain proposes; the named teammate reviews their own quote and accepts payer responsibility. | The teammate's own entitlement and payment readiness are fresh; the captain cannot use them on their behalf. |
| **Split or per-player benefit** | Captain may coordinate a request for the supported product flow. | Every payer/benefit owner explicitly accepts, and the product proves that the selected strategy supports it. |

The currently implemented booking preview is intentionally narrower: it is a
single authenticated customer, `ONLY_OWNER` charge strategy, and `USE_NONE`
pass strategy. It is valuable for learning the owner-paid quote, but it cannot
prove split payment, teammate pass redemption, or membership sharing. These
remain unavailable or unknown until a separate, bounded quote contract is
verified.

### Captain authority matrix

| Action | Captain role alone sufficient? | Additional requirement |
| --- | --- | --- |
| Propose a team match | Yes, within team policy | Named recipients and a repair path. |
| Accept an opponent captain's challenge | Yes, for the captain-level challenge | Each teammate still accepts their own participation. |
| Choose themself as payer | No | Their fresh account facts, quote, payment/benefit policy, and explicit payer intent. |
| Consume the captain's own pass | No | A pass-applicable quote plus explicit consent to consume it. |
| Consume a teammate's pass or credits | No | Teammate is authenticated/represented by a supported flow and explicitly accepts. |
| Claim a membership discount for the team | No | Quote proves the scope and treatment for the named payer/participants. |
| Invite all agreed players after booking | Only when the product flow supports it | Read-back of named invitations; an invite is not acceptance. |

## Expiring passes: an opportunity, not a pressure mechanism

An active entitlement close to expiration can create a legitimate, explainable
offer: “Mai's booking pass expires soon; would she like to use it for a session
that she chooses?” It must not create urgency that overrides scheduling,
consent, availability, or the pass's quote-specific eligibility.

Use this flow:

```text
fresh self-discovery -> pass is active and near expiry
  -> offer to its owner with the expiry fact
  -> owner proposes or accepts a specific session
  -> quote using an explicitly supported pass strategy
  -> compare expected vs observed consumption/total
  -> explicit payer approval -> guarded booking -> read-back
```

If any stage is unknown, a normal paid option may be offered only under its own
rules; the system must not silently fall back to consuming virtual credits or a
card. If the owner declines, that is an ordinary valid decision. If the quote
does not apply the pass, create a clarification or Riley question rather than
guessing why.

## Quote comparison: what the lead checks

The lead should create one quote-plan record for the agreed occurrence, not a
generic price belief. It is keyed by the named payer, selected slot/items,
duration, attendee arrangement, benefit strategy, and time of evaluation.

Before booking, compare the quote against each affected actor's relevant
expectations:

| Check | Example result | Safe next action |
| --- | --- | --- |
| Amount and currency | Total exceeds the named payer's acceptable range. | Ask, counter with another time/venue, or pass. |
| Membership treatment | Payer expected a member benefit, but quote does not show it. | Clarify with Riley or revise the plan; do not assert a discount. |
| Pass consumption | Owner expected one expiring pass to be used, but quote says none/ineligible. | Ask or choose a supported alternative; do not book on a surprise charge. |
| Virtual-credit use | Quote uses more/fewer credits than the payer expected. | Reconfirm the payer's choice; do not silently change payment source. |
| Payment readiness | Amount remains chargeable but no eligible default payment method is ready. | Block execution and ask the payer to resolve it. |
| Opening/peak/availability | Proposed time is unavailable, closed, restricted, or a peak rate changes the amount. | Counter with a compatible slot; treat this as product constraint, not rejection. |

A mismatch appends a `pricing_expectation_mismatch` fact that references the
sanitized quote and the expectation code—not the raw account or checkout
payload. It can produce a `clarify` or `complain` message to Riley. It must not
retry checkout, alter membership, add credits, change a pass strategy, or
charge a different customer.

## Bogard-Higashi vs Japan Team: the immediate safe path

The captain agreement establishes demand for a four-person doubles session; it
does not yet decide who pays or which entitlement applies. The next run should
therefore be read-only and ordered as follows:

1. Read each named captain's and prospective payer's own membership assignment,
   packages, virtual credits, and payment readiness. Treat every result as
   actor-specific and time-bounded.
2. Read live availability for a compatible doubles slot. Preserve the venue
   local time, duration, capacity, and any visible hours or peak/off-peak
   treatment.
3. Ask the intended payer to choose an explicit baseline: pay normally, use
   their own credits, or request a supported benefit/pass strategy. If nobody
   takes payer responsibility, stop before the quote.
4. Read a non-persisting quote under that payer's authenticated context and
   exact selected strategy. With today's baseline, this is owner-paid and
   `USE_NONE` for passes.
5. Compare the sanitized quote with the payer's amount, membership, credits,
   and pass expectations, plus each participant's relevant restrictions.
6. On a mismatch, append evidence and offer `clarify`, `counter`, `pass`, or a
   Riley question. On a match, record payer approval, then allow the existing
   team-signup adapter to book and invite all four players.
7. Keep later per-member check-in independent. A paid booking and an invitation
   never demonstrate attendance.

## Minimal contracts for the next slice

The existing self-asset subjects remain the inventory layer. Add only these
safe, composable records before expanding checkout behavior:

```text
booking_plan
  occurrence_key, captain_ids, participant_ids, payer_actor_id
  slot_summary, requested_benefit_strategy, created_at

booking_quote_observed
  booking_plan_ref, payer_actor_id, quote_context_fingerprint
  total, currency, credit_treatment, pass_treatment, membership_treatment
  constraint_codes, observed_at, expires_at, evidence_ref

pricing_expectation_mismatch
  booking_plan_ref, affected_actor_id, expectation_code, observed_code
  resolution: clarify | counter | pass | complaint, evidence_ref

payer_approval
  booking_plan_ref, payer_actor_id, accepted_quote_ref, benefit_strategy
  status: proposed | accepted | rejected | expired
```

`quote_context_fingerprint` must capture the non-secret dimensions that change
a quote—payer, selected items, duration, party/charge strategy, benefit
strategy, and target build—not raw product identifiers. Quote facts should
expire quickly. The current booking write must require an unexpired matching
quote and accepted `payer_approval` as well as the existing four-person social
agreement.

## Observatory: make the business decision inspectable

The local dashboard is where the club owner can understand why a socially
agreed match is ready, blocked, or deliberately paused. It must project safe
facts and beliefs rather than re-render raw API payloads.

Selecting a player should show:

- social context: team, recovery/competition state, commitments, and active
  opportunities;
- **expectations**: price range, currency, payer willingness, and expected
  membership/pass/credit treatment, marked as local policy rather than product
  fact;
- **customer-visible beliefs**: membership wording, package/pass balances and
  expirations, virtual credits, payment readiness, with source, observed time,
  expiry, and status (`observed`, `unknown`, `stale`, `contradicted`);
- a compact evidence trail showing why a belief changed, never raw account,
  payment, or credential material.

Selecting a booking or pre-booking plan should show:

- social agreement versus financial authority: captain, attendees, named payer
  and benefit owner, plus outstanding acceptances and payer approval;
- venue/court rule observations: capacity, local time, bookability,
  opening/peak/lead-time/cancellation conditions when exposed, and an explicit
  `unknown` label when not exposed;
- requested versus observed charge strategy. `ONLY_OWNER`, split, and
  per-player benefit use are different states, not labels for the same thing;
- a safe quote breakdown: total/currency, visible rate/fees/tax, membership,
  pass and credit treatment, constraint/error codes, source/time/expiry;
- comparison results and their next owner: payer clarification, counteroffer,
  Riley question, complaint, approval, or blocked execution.

No participant inherits another participant's membership, pass, credit, or
payment readiness in this view. A field that the current product flow cannot
prove stays `unknown`, even when the social story would make a positive answer
convenient.

## What remains deliberately unknown

Until live customer/UI and quote evidence proves otherwise, do not assume:

- a membership grants a price reduction, guest allowance, booking right, or
  access outside ordinary opening hours;
- a captain's membership, credits, or pass covers teammates;
- a pass can apply to doubles, peak time, a particular court, or the selected
  charge strategy;
- peak/off-peak is defined only by clock time rather than day, venue, product,
  membership, or another rule;
- visible availability represents the final checkout price;
- a zero quote means no payment method, waiver, or other booking restriction is
  required.

This conservative boundary is what lets the community react socially to price
and benefits while keeping entitlement changes and money movement squarely in
the verified customer product flow.
