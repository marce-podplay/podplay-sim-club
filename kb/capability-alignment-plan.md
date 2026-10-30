# Capability alignment implementation plan

## Purpose

Align character behavior, community coordination, product exploration, and bug
detection around the six-layer [capability model](capability-model.md). The
target is a vibrant but bounded club: characters develop high-level social
intentions, communicate them, and react to verified product outcomes without
inventing product facts or embedding one-off journeys in the orchestrator.

This plan has two distinct goals:

1. **Near term:** generate realistic, inspectable journeys that expose Preview
   regressions early.
2. **Possible future:** test whether calibrated model outputs predict aggregate
   real-user behavior. The current fictional community is not predictive
   evidence; prediction requires consented/anonymized observations, calibration,
   holdout validation, and uncertainty reporting.

## Implementation status — 2026-10-05

The first reversible foundation is present:

- JSON schemas define facts, messages, commitments, capabilities, beliefs,
  events, participation, and evidence;
- `schemas/registry.json` records each contract's canonical store and rollout
  status;
- `capabilities/catalog.json` contains the first role-general contracts;
- the validated fact journal rejects duplicate IDs and secret-shaped fields or
  values;
- conversation and commitment projections rebuild deterministically;
- the missed-session retry protocol dual-writes protocol facts while preserving
  its compatibility intent and channel message;
- `./club state facts` validates the journal and `./club state rebuild` replays
  it into projections.

The communication-and-commitment slice is also present:

- `protocol.py` is the single authority for structured speech acts and social
  commitment transitions;
- only the debtor can accept, reject, or counter; either party can cancel;
  only the deterministic lead can expire or resolve a commitment, and
  satisfaction/violation requires sanitized evidence;
- clarification is a message and never silently changes agreement state;
- captain-led training, missed-session retries, and direct team challenges now
  produce conversation and commitment facts alongside compatibility state;
- an addressed rival captain can accept a challenge on a later actor turn,
  without booking a court or implying teammate attendance;
- a captain may receive more than one invitation, but an accepted-or-later
  doubles challenge consumes their social commitment capacity: a later
  invitation is explicitly deferred to Riley as a future request, never
  silently accepted, rescheduled, or turned into a second booking;
- agreement is not indefinite product authority: unbooked training and team
  challenge agreements expire after the planning window, and a newer accepted
  training agreement supersedes older unbooked authority for that same team
  while preserving the prior intent as history.
- result requests/reports and Riley replies now produce communication facts,
  but do not manufacture commitments where none were made;
- commitment projections retain their ordered transition history and evidence
  reference, so a current status can be audited rather than merely displayed.

The current durable world predates dual-writing, so a zero-fact rebuild is
valid. Historical state will be migrated capability by capability rather than
misrepresented as newly observed facts.

The canonical event-and-participation slice is now present for newly planned
social journeys:

- the lead assigns a stable occurrence key from the durable intent before any
  Preview slot is selected;
- planning, rescheduling, booking, invitation acceptance, check-in, finished
  observation, result request, and attested result append lifecycle facts;
- event state and one participation record per actor reduce independently, so
  a partial product read-back cannot replace the participant set;
- the observatory replaces an overlapping compatibility card with the complete
  canonical card rather than merging fields from competing ledgers;
- retained share URLs remain an explicitly approved private dashboard sidecar,
  not journal facts or lifecycle authority.

Pre-journal events and generic legacy booking commands remain visible through
compatibility readers. They are not synthesized into canonical facts.

## Architectural decision

Use three durable concepts:

- **facts** are immutable observations of something that occurred;
- **projections** are rebuildable current views derived from facts;
- **capability contracts** declare when an actor may consider, communicate, and
  execute a behavior.

The runtime becomes one consistent pipeline:

```text
observe product/community
  -> append fact
  -> rebuild affected projections
  -> derive legal opportunities
  -> score motives and policies
  -> advertise legal offers
  -> actor accepts, asks, or passes
  -> update conversation/commitment
  -> social engine emits a browser work item
  -> coordinator dispatches one rendered-browser contract
  -> append rendered evidence
  -> update motives, beliefs, relationships, issues
```

A product claim requires that rendered confirmation. The lead does not execute
a product API on behalf of a Sim. See the
[lead and dispatcher operating model](lead-and-dispatcher-operating-model.md)
and the [operations hand-off](current-operations-handoff-2026-10-30.md).

No model call writes a fact, projection, URL, slot, price, participant state, or
winner directly. It proposes one choice from already legal offers.

The six layers are deliberately orthogonal to product bounded contexts.  The
[domain context map](domain-context-map.md) records those seams and the
anti-corruption contracts required as Team Signup, unified pricing, kids,
Series, and sport-specific ratings evolve.

## Canonical data ownership

Every concept gets one canonical owner. Other files may reference it by ID but
must not carry an independently mutable copy.

| Concept | Canonical source | Notes |
| --- | --- | --- |
| Identity, role, team, static affinity/rivalry | `characters/roster.json` | Remove the competing hardcoded actor directory from `world.py`. |
| Capability definitions | `capabilities/catalog.json` | Committed, versioned, declarative contracts. |
| Immutable runtime history | `state/journal/facts.jsonl` | Secret-free facts with deterministic IDs. |
| Current actor motives/intentions | `state/projections/actors.json` | Rebuilt by reducers; never hand-edited. |
| Product/self beliefs | `state/projections/beliefs.json` | Expected and observed values remain distinct. |
| Relationships | `state/projections/relationships.json` | Static roster inputs plus earned closeness/trust. |
| Conversations | `state/projections/conversations.json` | Threads, addressees, grounding, and last activity. |
| Social commitments | `state/projections/commitments.json` | Proposal/acceptance/satisfaction lifecycle. |
| Product opportunities | `state/projections/opportunities.json` | Invitations, published events, availability, release notes, expiry. |
| Event lifecycle | `state/projections/events.json` | One record per stable local occurrence. |
| Per-person participation | `state/projections/participation.json` | One actor + occurrence record. |
| Sanitized product evidence | `state/evidence/*.json` | Read-back snapshots and comparisons. |
| Suspected product problems | `state/issues/open/*.json` | Evidence-backed, never generated from dialogue alone. |
| Credentials and bearer material | ignored `secrets/` and private state | Never referenced by value from committed/community data. |

Existing ledgers remain compatibility projections during migration. New code
must not introduce another canonical representation.

## Common fact envelope

All new facts use a small shared envelope:

```json
{
  "schemaVersion": 1,
  "factId": "fact:<type>:<deterministic-key>",
  "type": "commitment.accepted",
  "occurredAt": "2026-10-05T15:00:00Z",
  "recordedAt": "2026-10-05T15:00:01Z",
  "actorId": "mai",
  "subjectRef": "commitment:training:123",
  "conversationId": "conversation:training:123",
  "causationId": "fact:message:proposal-123",
  "correlationId": "journey:training:123",
  "source": {"kind": "club_message", "evidenceRef": null},
  "privacy": "community",
  "payload": {}
}
```

Rules:

- `factId` is idempotent and deterministic for product read-backs.
- `occurredAt` is when the represented event happened; `recordedAt` is when the
  club learned it.
- `causationId` explains the immediate trigger; `correlationId` joins the whole
  journey.
- `privacy` is `public`, `community`, `operator`, or `private`. The journal
  accepts only the first three, and operator facts must already be sanitized.
- Product identifiers may appear only when approved as non-secret references;
  credentials, emails, payment IDs, and invitation bearer URLs never appear.

## Projection contracts

### 1. Actor state

```json
{
  "actorId": "king-captain",
  "schemaVersion": 1,
  "motives": {
    "play": {"value": 82, "updatedAt": "...", "drivers": ["fact:..."]},
    "mastery": {"value": 70, "updatedAt": "...", "drivers": []},
    "competition": {"value": 55, "updatedAt": "...", "drivers": []},
    "relatedness": {"value": 76, "updatedAt": "...", "drivers": []},
    "curiosity": {"value": 20, "updatedAt": "...", "drivers": []},
    "frustration": {"value": 10, "updatedAt": "...", "drivers": []}
  },
  "activeIntentions": ["intention:training:123"],
  "cooldowns": {"propose_training:mai": "..."},
  "lastReducedFactId": "fact:..."
}
```

Keep motive dimensions few and interpretable. Personality is a static set of
weights applied while ranking offers, not another set of mutable meters.

### 2. Beliefs and self-assets

```json
{
  "beliefId": "belief:king-captain:pass-balance",
  "actorId": "king-captain",
  "subject": "pass_balance",
  "status": "observed",
  "expectedValue": {"minimum": 2, "unit": "play_pass"},
  "observedValue": {"available": 1, "unit": "play_pass"},
  "sourceEvidenceRef": "evidence:self-assets:123",
  "observedAt": "...",
  "expiresAt": "..."
}
```

Required subjects for players:

- `spending_budget` and `acceptable_booking_price`;
- `membership_assignment` and understood benefits;
- `pass_balance`, `credit_balance`, expiration, and expected consumption;
- `payment_readiness` without payment method identifiers;
- `rating` or fictional skill estimate, with source clearly marked;
- `booking_permissions`, `invitation_permissions`, and `check_in_permissions`;
- `availability`, travel tolerance, and cancellation tolerance.

Required subjects for Sofia/Riley/Alex:

- permitted product roles and verified season assignment;
- club inventory, court status, series visibility, and pricing configuration;
- last successful read-back for the feature they intend to exercise.

An unknown value remains `unknown`; it never becomes zero, false, or unlimited.

### 3. Relationships

```json
{
  "relationshipId": "relationship:king-captain:mai",
  "actors": ["king-captain", "mai"],
  "affinity": "roster_static",
  "closeness": 20,
  "trust": 70,
  "rivalry": 0,
  "lastSharedCompletedOccurrence": null,
  "lastReducedFactId": "fact:..."
}
```

- Closeness rises only from verified shared completion.
- Trust may change after a commitment is satisfied, rejected honestly, allowed
  to expire, or violated.
- A no-show affects reliability/trust only after the participation evidence is
  complete enough to attribute it; an inconclusive event does not.
- Rivalry changes offer weights but does not authorize a challenge by itself.

### 4. Intention

```json
{
  "intentionId": "intention:training:123",
  "actorId": "king-captain",
  "capabilityId": "player.propose_training",
  "goal": "practice_with_team",
  "status": "selected",
  "motiveRefs": ["play", "mastery", "relatedness"],
  "opportunityRefs": ["opportunity:w37:window-123"],
  "createdAt": "...",
  "expiresAt": "..."
}
```

States: `considered -> selected -> communicated -> committed -> executed`, with
terminal `passed`, `rejected`, `expired`, `blocked`, or `abandoned`. Intentions
belong to one actor. Agreements belong to commitments.

### 5. Conversation and message

```json
{
  "messageId": "message:training:proposal-123",
  "conversationId": "conversation:training:123",
  "speechAct": "propose",
  "senderActorId": "king-captain",
  "recipientActorIds": ["mai"],
  "contentCode": "training.same_team.30m",
  "parameters": {"durationMinutes": 30, "teamId": "women-fighters"},
  "inReplyTo": null,
  "grounding": "addressed",
  "commitmentId": "commitment:training:123",
  "observedAt": "..."
}
```

Free-form character text is display content, not protocol semantics. The
structured speech act and parameters drive reducers. Grounding states are
`created`, `addressed`, `acknowledged`, `clarification_requested`, and
`understood`; acceptance is recorded on the commitment, not the message.

### 6. Social commitment

```json
{
  "commitmentId": "commitment:training:123",
  "kind": "join_session",
  "debtorActorId": "mai",
  "creditorActorId": "king-captain",
  "beneficiaryActorIds": ["king-captain", "mai"],
  "content": {"capabilityId": "player.join_training", "occurrenceRef": null},
  "status": "accepted",
  "proposedAt": "...",
  "acceptedAt": "...",
  "expiresAt": "...",
  "satisfactionEvidenceRef": null
}
```

States: `proposed -> accepted | rejected | countered | expired`, then
`accepted -> satisfied | violated | canceled`. A captain-level doubles
commitment names both teams but does not fabricate teammate attendance.

### 7. Opportunity

```json
{
  "opportunityId": "opportunity:published-event:123",
  "kind": "published_event",
  "addressedActorIds": ["andy-bogard", "terry-bogard"],
  "capabilityIds": ["player.accept_event"],
  "hardGates": {"published": true, "availabilityOverlap": true},
  "sourceEvidenceRef": "evidence:event-read:123",
  "startsAt": "...",
  "expiresAt": "..."
}
```

Availability, invitations, open events, staff suggestions, release notes, and
verified asset-discovery needs are opportunities. They are not motives or
commitments.

### 8. Event and participation

Event:

```json
{
  "occurrenceKey": "occurrence:training:123",
  "kind": "training",
  "status": "check_in_open",
  "socialIntentRef": "intention:training:123",
  "commitmentRefs": ["commitment:training:123"],
  "plannedWindow": {"start": "...", "end": "..."},
  "productEvidenceRef": "evidence:event-read:123",
  "result": {"status": "unknown", "winner": null, "evidenceRef": null}
}
```

Participation:

```json
{
  "participationId": "participation:occurrence-123:mai",
  "occurrenceKey": "occurrence:training:123",
  "actorId": "mai",
  "roles": ["teammate"],
  "invitation": "accepted",
  "checkIn": "not_checked_in",
  "attendance": "unknown",
  "resultReporter": false,
  "evidenceRefs": ["evidence:invitation-read:123"]
}
```

The event lifecycle and participant lifecycle never share a single `status`
field. `ended`, `played`, `winner reported`, and `completed` are separate.

### 9. Evidence, expectations, comparisons, and issues

Evidence:

```json
{
  "evidenceId": "evidence:booking-evaluation:123",
  "probeId": "probe:player-booking-evaluation",
  "targetFingerprint": "pr-7200:build-abc",
  "actorId": "king-captain",
  "capabilityId": "player.book_session",
  "observedAt": "...",
  "safeObservation": {"currency": "USD", "amount": 20, "passUse": 1},
  "contractVersion": 2,
  "result": "read_back"
}
```

Comparison:

```json
{
  "comparisonId": "comparison:booking-price:123",
  "expectationRef": "belief:king-captain:acceptable-price",
  "evidenceRef": "evidence:booking-evaluation:123",
  "outcome": "mismatch",
  "severity": "blocks_actor",
  "explanationCode": "price_above_actor_limit"
}
```

Issue states are `observed -> triaged -> reproducible -> contract_confirmed ->
resolved`, with terminal `expected_behavior`, `insufficient_evidence`, or
`not_reproducible`. A complaint may reference a comparison or issue but never
creates one without deterministic evidence.

## Capability contract

Each capability is declarative and role-neutral where possible:

```json
{
  "schemaVersion": 1,
  "id": "player.propose_training",
  "version": 1,
  "roles": ["player"],
  "goal": "practice_with_team",
  "motivation": {"any": ["play", "mastery", "relatedness"], "minimum": 70},
  "requiredBeliefs": ["booking_permissions", "availability"],
  "requiredRelationships": ["same_team", "captain_authority"],
  "requiredOpportunities": [],
  "requiredCommitments": [],
  "speechActs": ["propose"],
  "offer": "propose_team_training",
  "adapter": null,
  "remoteWrite": false,
  "cooldown": {"scope": "actor+target+capability", "minutes": 60},
  "successFacts": ["commitment.proposed"],
  "failureFeedback": {"rejected": {"frustration": 5}, "expired": {"frustration": 10}},
  "privacy": "community"
}
```

Execution capabilities add:

- the deterministic adapter name;
- the exact required accepted commitments;
- product target and write classification;
- preflight probes;
- write budget and idempotency key;
- read-back assertions;
- evidence sanitizer;
- issue policy and retry policy.

The catalog must contain no character IDs, product IDs, URLs, credentials, or
scenario-specific event IDs.

## Role capability families

### Players

- discover own membership, balances, passes, rating, and booking permissions;
- express a price/pass/availability expectation;
- propose, accept, reject, counter, or clarify a training/challenge;
- book, share, redeem, accept, and check in when authorized;
- report a result from replay or designated testimony;
- complain or ask staff when evidence contradicts an expectation;
- learn from completion, rejection, no-show, mismatch, and product failure.

### Riley / club staff

- answer grounded player questions;
- explain a known policy or say evidence is insufficient;
- propose one reschedule after required agreement;
- route a product mismatch to an issue without promising a fix;
- announce verified no-shows, closures, court problems, or policy changes;
- never accept for a player, invent a benefit, or perform owner strategy.

### Sofia / club owner

- observe club activity, eligible demand, court utilization, and issue trends;
- advertise opportunities and release experiments;
- configure or test owner capabilities such as events, series, and pricing only
  through explicit guarded contracts;
- compare expected club outcomes with safe product evidence;
- ask staff or players for reports without fabricating demand or results.

### Alex / platform administrator

- verify season identities, roles, and platform-level prerequisites;
- run narrowly scoped administrative probes;
- separate a configuration problem from a product workflow failure;
- never substitute administrative success for the customer journey.

## Bug-detection model

Early bug detection depends on an observable funnel, not merely the absence of
bookings:

```text
eligible population
 -> legal opportunities
 -> actor intentions
 -> grounded commitments
 -> execution attempts
 -> product writes
 -> successful read-backs
 -> participant outcomes
 -> completed journeys
```

The dashboard must show counts and blockers at every stage. Examples:

- zero bookings with zero eligible opportunities is not a product bug;
- intentions but no commitments may be a communication or simulation issue;
- commitments but no attempts may be a policy/adapter gap;
- attempts with failed read-backs are strong product or contract signals;
- successful writes followed by inconsistent display/read-back are strong
  observability signals;
- a release-correlated drop is a hypothesis until controlled probes reproduce
  it against the product contract.

Every probe run records:

- Preview/build fingerprint and feature configuration;
- capability and contract version;
- actor eligibility and expectations before the attempt;
- sanitized request classification, response/read-back, and duration;
- outcome and exact blocked stage;
- whether the same probe passed on a baseline build.

## Dashboard alignment

Organize the observatory around questions rather than raw pending records:

1. **Club pulse:** who wants to play, train, compete, explore, or ask for help?
2. **Opportunities:** live events, invitations, courts, release experiments,
   and expiring benefits addressed to each role.
3. **Conversations:** proposals awaiting grounding, clarification, acceptance,
   rejection, or staff response.
4. **Commitments:** accepted obligations and whether they were satisfied,
   violated, canceled, or expired.
5. **Events:** canonical lifecycle and per-participant invitation/check-in state.
6. **Self-assets:** safe expected versus observed membership/pass/credit/price
   summaries with staleness labels.
7. **Product health:** journey funnel, blocked stage, last passing baseline,
   evidence-backed issues, and release correlation.
8. **Engine audit:** queue cursor, actors considered, offers generated, passes,
   selected actions, validation failures, and adapter writes/read-backs.

“Pending” must always name the owner, awaited transition, evidence required,
and expiry. A pending record without those fields is a dashboard defect.

## Migration plan

### Milestone 0 — freeze vocabulary and invariants

Deliverables:

- approve this plan and the capability vocabulary;
- add a machine-readable schema registry and stable ID conventions;
- enumerate current message kinds, intent statuses, event records, and actor IDs;
- add boundary tests for “message != acceptance,” “ended != played,” and all
  other invariants in the capability model.

Exit criteria:

- no two new records use different words for the same state;
- each existing state field has a documented target owner or retirement path.

### Milestone 1 — fact journal and reducer framework

Deliverables:

- implement `facts.py` with validation, idempotent append, privacy checks, and
  deterministic fact IDs;
- implement reducer checkpoints and full projection rebuild in a temporary
  directory;
- emit facts alongside existing writes for one read-only journey;
- compare projections with legacy state in tests without changing runtime reads.

Exit criteria:

- replaying the journal produces byte-stable projections;
- duplicate product observations do not duplicate facts;
- no secret-shaped field passes journal validation.

### Milestone 2 — communication and commitments

Deliverables:

- normalize new messages to the message contract;
- project conversations and commitments;
- migrate training, challenges, missed-session retries, result requests, and
  Riley replies first;
- support accept, reject, counter, acknowledge, clarify, expire, and satisfy;
- retain legacy `kind` as a compatibility label.

Exit criteria:

- the engine cannot infer acceptance from delivery or acknowledgement;
- every executable multi-person intent references the accepted commitment;
- unanswered and expired messages have one deterministic next offer.

### Milestone 3 — canonical events and participation

Status: implemented for new intent-driven training, retry, and team-challenge
occurrences. Historical and fixed-tournament records remain compatibility-only.

Deliverables:

- assign one local occurrence key before product planning;
- project event and per-actor participation separately;
- link Preview event references, arcade replay, result request, and final outcome
  through that occurrence key;
- convert existing observatory cards to the projection API;
- stop view-layer merging of partially overlapping event ledgers.

Exit criteria:

- one dashboard event shows every participant's independent state;
- no update to an event can overwrite its participant list;
- missed, inconclusive, played, and completed are distinguishable.

### Milestone 4 — self-asset discovery

Detailed delivery plan: [customer self-asset discovery](customer-self-asset-discovery-plan.md).

Deliverables:

- define belief subjects and staleness policies;
- implement player-owned read-only probes for virtual credits, owned
  packages/items, active membership, payment readiness, and booking settings;
- keep rating unknown until a player-owned product source is verified;
- sanitize outputs into evidence and belief projections;
- show expected, observed, contradicted, unknown, and stale values.

Coupon inventory and quote applicability are not self-asset discovery. Known
codes and their quote-specific outcomes belong to Milestone 5. Owner/staff
role and inventory discovery belongs to the owner/admin slice.

Slice 4A is implemented: strict per-subject sanitizers, immutable sanitized
evidence, belief facts/projections, explicit unknown/expected/observed/
contradicted/stale transitions, and deterministic replay are active locally.
Slice 4B is implemented: selected existing actors can perform independent,
GET-only reads for virtual credits, payment readiness, active package items,
and membership assignment. Responses are sanitized before immutable evidence
and belief facts are stored. Live Preview/UI equivalence is still required by
slice 4D before product semantics are promoted further.

Exit criteria:

- an actor can explain the evidence behind each displayed asset belief;
- missing product data never becomes a zero balance;
- no email, credential, UID, payment ID, token, or bearer URL is exposed.

### Milestone 5 — expectation-aware execution

Deliverables:

- add preflight comparisons for price, currency, discount, pass use, credits,
  cancellation terms, availability, and role permissions;
- create one explicit booking-plan record that names the captain, attendees,
  payer/benefit owner, venue, selected local-time slot, and requested charge
  and benefit strategies before any quote is read;
- project the quote as an explanation, not merely a total: visible rate,
  fees/tax, membership treatment, virtual-credit treatment, pass treatment,
  capacity/opening/peak constraints, cancellation terms, and blocking codes
  are individually `observed`, `unknown`, `stale`, or `not_applicable`;
- model `ONLY_OWNER`, split, and per-player benefit strategies separately.
  The current owner-paid/`USE_NONE` baseline must be displayed as a limitation,
  never relabeled as a split-payment result. No actor can spend or reveal a
  teammate's benefit merely because they are captain or booking owner;
- block checkout when a configured actor boundary is violated;
- offer clarify/complain/pass instead of a blind retry;
- connect comparisons to Riley and evidence-backed issue creation.

Exit criteria:

- every blocked write names the failed expectation and evidence;
- the actor, staff, and dashboard agree on the blocker;
- a mismatch cannot trigger a second charge or duplicate booking.

#### Milestone 5 Observatory read model

The Observatory is an explanation surface, not a copy of private checkout
payloads. It needs two linked inspectors.

**Player inspector** (selected Sim) shows social motives/team cohesion and
offers; local expectations; customer-visible membership/package/pass/credit/
payment-readiness beliefs; and source, observed-at, expiry, and status for
each belief. It never shows account identifiers, payment references, raw
responses, credentials, tokens, emails, UIDs, or bearer URLs.

**Booking-plan inspector** (selected event or pre-booking plan) shows social
agreement and named payer/benefit owner; venue/court capacity, local time and
visible rule constraints; requested versus product-proven charge strategy;
per-owner membership/pass/credit treatment; safe quote composition; and the
comparison, Riley question, payer approval, or blocker that owns the next
transition. Every unavailable field visibly remains `unknown` rather than
being rendered as zero, free, no discount, or split payment.

#### Milestone 5 delivery slices

1. **5A — booking-plan and quote contracts — complete.** The product-neutral
   `booking-plan` contract names participants, owner, payer, venue/window and
   requested funding strategy. Read-only quote evidence carries independently
   status-bearing fields for total, currency, membership, credits, passes,
   capacity, cancellation, and blocking codes. Its projection advances only to
   `awaiting_payer_approval`; no plan can authorize a Preview write yet.
2. **5B — read-only plan inspector — complete.** Shared secret-free inspector
   queries serve both `./club inspect player --actor <id>` / `./club inspect
   booking-plan --id <id>` and the Observatory. Player inspection shows
   motives and time-bounded beliefs; plan inspection shows participants,
   owner/payer separation, requested funding, quote field statuses, evidence
   source, and the next plan stage. The web UI is only a client of these
   projection queries.
3. **5C — owner-paid baseline comparison — complete.** `./club assess
   booking-plan --id <id>` compares a persisted read-only quote with the named
   payer's projected price expectation and payment readiness. Membership and
   pass treatment are relevant only when that payer explicitly expected one.
   The durable result is `within_expectation`, `mismatch`, or `unknown` with
   per-check evidence and safe next actions. A match reaches only
   `request_payer_approval`; a mismatch offers clarify/counter/pass/Riley.
   `./club expect booking-price` records the actor-owned fictional ceiling
   explicitly; it is local policy, never a product entitlement or payment.
4. **5D — payer approval gate.**
   - **5D-a — complete:** `./club payer-approval request --id <id>` creates a
     payer-owned commitment only from a fresh `within_expectation` comparison;
     `./club payer-approval accept --id <id> --payer <actor>` lets only that
     named payer accept it. The approval is quote-bound, expires after fifteen
     minutes, and is visible through the plan inspector.
   - **5D-b — complete:** live read-only team-signup planning now emits the
     canonical plan and sanitized quote facts. Its initial guarded Preview
     booking call refuses unless it can resolve the exact accepted, unexpired
     approval for that plan/quote/payer. Legacy intents without a canonical
     plan remain ineligible rather than receiving retroactive authority.
5. **5E — split/payment-strategy research.** Add a strategy only after a
   customer-visible product flow proves it; unsupported teammate pass or
   credit use remains unknown or unavailable.

### Milestone 6 — capability catalog and generic planner

Deliverables:

- move existing hardcoded offer eligibility into versioned capability records;
- implement catalog validation and role/permission checks;
- rank eligible offers using motives, personality weights, relationships,
  opportunity, frustration, boredom, and cooldown;
- keep execution adapters in deterministic Python.

Exit criteria:

- adding a new team or actor needs roster data, not a new orchestrator branch;
- adding a new high-level behavior needs one capability contract, reducer rules,
  tests, and optionally an adapter;
- capability records contain no scenario identities or product coordinates.

### Milestone 7 — product-health experiments

Deliverables:

- define release-linked probe suites for booking, pricing, invitation,
  redemption, check-in, cancellation, events, series, and role management;
- add baseline comparison and blocked-stage metrics;
- distinguish simulator, configuration, contract, and product failures;
- create a reproducibility workflow before an issue is labeled regression.

Exit criteria:

- “no bookings after unified pricing” can be decomposed by funnel stage;
- every open issue links evidence, contract, build, and reproduction status;
- the club can run social activity and product probes without conflating them.

### Milestone 8 — predictive research, only after validation approval

Deliverables:

- state a specific aggregate prediction target, such as invitation acceptance or
  checkout abandonment under a documented condition;
- collect permitted, anonymized, aggregate comparison data;
- calibrate parameters separately from the fictional character identities;
- evaluate on time-separated holdout data using calibration error, Brier score
  or appropriate task metrics, confidence intervals, and drift monitoring;
- publish limitations and prohibit individual-level decisions without a new
  governance review.

Exit criteria:

- synthetic coverage metrics remain separate from predictive metrics;
- a prediction reports uncertainty and beats a simple baseline out of sample;
- privacy, consent, retention, fairness, and intended-use reviews are complete.

Until then, the simulator predicts only its own configured behavior. Its value
is journey coverage, causal traceability, and fast product feedback.

## Recommended delivery slices

Keep each slice reviewable and reversible:

1. **Schema slice:** IDs, fact envelope, registry, validation, no runtime switch.
2. **Communication slice:** complete for training, retries, direct challenges,
   result requests/reports, and front-desk replies. Legacy scripted tournament
   dialogue remains a compatibility protocol until its replacement slice.
3. **Lifecycle slice:** event + participation projections and dashboard cards.
4. **Asset slice:** membership/package/pass/credit/payment beliefs and safe
   discovery; rating follows only after its player-owned source is verified.
5. **Expectation slice:** checkout comparisons, complaints, and issue evidence.
6. **Catalog slice:** migrate offer eligibility capability by capability.
7. **Owner/admin slice:** events, series, pricing, inventory, and role probes.
8. **Product-health slice:** baseline/release comparison and funnel metrics.

Do not combine a data migration, new preview write adapter, and actor-policy
change in the same slice. Each slice should preserve old readers until the new
projection has replay and equivalence tests.

## Testing strategy

Every capability needs:

- schema-validation tests;
- positive and negative eligibility tests;
- privacy-field rejection tests;
- message grounding and commitment lifecycle tests;
- reducer replay/idempotency tests;
- adapter write-budget and exact-origin tests;
- read-back mismatch tests;
- crash-before/after-write reconciliation tests;
- dashboard projection tests;
- scenario tests with different actors and teams to prevent hardcoding.

The strongest generality test is to instantiate the same capability with a
different team, captain, teammate, venue, duration, and Preview season without
editing Python control flow.

## First implementation backlog

The first backlog should be completed in this order:

1. Add `schemas/` definitions for facts, messages, commitments, events,
   participation, beliefs, evidence, and capability contracts.
2. Add a validated fact journal plus deterministic projection rebuild command.
3. Dual-write the already-general missed-session retry protocol into facts.
4. Project its conversation and commitment; compare against the current intent.
5. Migrate result requests and team challenges to the same protocol.
6. Introduce canonical event/participation projections and switch the dashboard.
7. Implement read-only membership/pass/credit discovery outside Rally Engine.
8. Promote only reviewed product-research findings into belief reducers and
   expectation-aware capability gates.

This order creates the shared foundation before adding more personalities,
meters, product mutations, or predictive claims.
