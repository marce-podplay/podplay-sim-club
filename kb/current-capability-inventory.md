# Current capability inventory

This is an implementation assessment, not a promise that every product path is
available in every Preview season.

| Layer | Present now | Partial or missing |
| --- | --- | --- |
| Motivation and affect | desire-to-play drift, curiosity, frustration, rivalry/affinity inputs, interaction cooldowns, owner nudges, missed-session feedback | mastery, confidence, team duty, fatigue, boredom, competing motives, personality weights |
| Self-knowledge and product beliefs | payment readiness summaries, preview readiness, booking evaluation, local pricing research plan, typed sanitized evidence and replayable belief transitions, player-owned GET probes for credits, packages, membership, and payment readiness | live Preview/UI equivalence, booking-permission context, rating source, expectation-aware execution |
| Relationships, roles, and norms | teams, captains, affinities, rivalries, staff/owner roles, closeness after completed events | trust/reliability, reciprocity, explicit norms, role authority projection, consequences of satisfied/violated commitments |
| Communication and commitments | addressed club messages, structured speech acts, reply links, proposal/acceptance, centralized rejection/counter/cancel/expiry/resolution authority, validated fact journal, replayable transition histories | structured facts for historical messages and the fixed tournament, delivery state, policy offers that exercise rejection/counter/clarification |
| Deliberation and policy | hard availability/addressing gates, thresholds, per-interaction cooldowns, role-specific offers, deterministic queue, initial role-general capability catalog | catalog-driven eligibility, preference arbitration, safe availability overlap for every actor, expectation-aware checkout gate |
| Execution, evidence, and learning | guarded Preview login/read/write adapters, booking/invitation/redemption/check-in, event observations, result/replay support, issue files, deterministic projection rebuild command, canonical event and per-actor participation projections, immutable sanitized self-asset evidence | owner/admin series probes, fixed-tournament and historical-state migrations |

## Immediate conclusion

The engine is strongest at guarded product execution and weakest at typed
self-knowledge and grounded, reusable communication. Adding more motive meters
before those two gaps are fixed would produce more offers without making actors
better able to reason about price, benefits, invitations, or failures.

The missed-session retry implemented alongside this assessment is the template:

1. evidence raises a motive;
2. policy offers an authorized captain a speech act;
3. the addressee explicitly accepts;
4. only the grounded agreement becomes eligible for an existing adapter;
5. product read-back supplies the next evidence.

Use the same template for pricing complaints, membership questions, pass
consumption disputes, and owner/admin feature experiments.

The customer self-asset boundary and active player-owned probe are specified in
[customer-self-asset-discovery-plan.md](customer-self-asset-discovery-plan.md).
Virtual credits, owned package items, membership assignment, understood
benefits, and payment readiness are separate beliefs. Coupon applicability is
quote-specific rather than customer inventory; product rating remains unknown
until a player-owned source is verified. The existing readiness summary no
longer converts a missing virtual-credit field into zero.
## Structured protocol coverage

This section separates capabilities already represented by structured facts
from compatibility-only journeys. It is not evidence that a Preview product
action occurred.

## Structured communication and commitments

| Capability | Protocol state | Product effect |
| --- | --- | --- |
| Propose/accept team training | Message + proposed/accepted commitment | None; lead receives an agreed local intent |
| Propose/accept missed-session retry | Message + proposed/accepted commitment | None; prior booking artifacts are not copied |
| Challenge/accept rival captain | Message + proposed/accepted commitment | None; teammates are beneficiaries, not inferred acceptors |
| Request/report event result | Request/report messages | None; report requires the existing result evidence boundary |
| Reply to player request | Acknowledge, clarify, or propose message | None by itself; Riley cannot move a booking |

All commitment state changes pass through `src/podplay_sim_club/protocol.py`.
Free-form display text has no authority to accept, book, attend, check in, or
declare a winner.

## Canonical lifecycle coverage

New intent-driven training, retry, and team-challenge journeys receive a stable
occurrence key before product planning. Planning/rescheduling, booking,
invitation read-back, check-in, event observation, result request, and attested
result reduce into one event projection plus independent actor participation
records. The observatory uses that complete projection whenever it overlaps a
legacy card.

## Still compatibility-only

- the fixed tournament coordinator dialogue;
- older channel history created before fact dual-writing;
- legacy intents without conversation or commitment IDs;
- generic pre-intent booking/invitation/check-in commands;
- product events and channel history created before lifecycle dual-writing.

Historical compatibility state is not backfilled as if newly observed.
