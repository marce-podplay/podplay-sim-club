# Preview Club domain context map

Preview Club is not one giant “social engine.”  It is a collection of bounded
contexts connected by explicit facts and commands.  This keeps an evolving
PodPlay feature from rewriting character motivation, and keeps a character
story from claiming a product outcome.

The capability layers in [the capability model](capability-model.md) cut
*through* these contexts: motivation and communication occur in several
contexts, while product execution is deliberately kept at their edges.

## Contexts and ownership

| Context | Owns | Receives | May publish | Must not infer |
| --- | --- | --- | --- | --- |
| **Community and social simulation** | motives, cooldowns, fictional budgets, team cohesion, static affinity | completed/reported session evidence, release notices, safe complaints | intentions and legal offers | a product entitlement, availability, agreement, or outcome |
| **Communication and commitments** | conversations, speech acts, commitments, expiry and resolution rules | addressed messages, evidence references | proposal, acceptance, rejection, clarification, complaint | delivery as agreement; agreement as execution |
| **Teams and play format** | roster membership, captain role, typed affinity groups, explicit team rivalry, training/challenge shape, guest role | social intention, accepted captain commitment | named participant *candidates* and format constraints | teammate consent, attendance, payment authority, ratings |
| **Venue and schedule** | venue/court constraints, time windows, weather/maintenance observations, availability evidence | planning request, product availability read-back | legal slot candidates and scheduling blockers | a reservation or a price |
| **Booking plan and lifecycle** | occurrence key, plan stages, event/participation lifecycle, adapter eligibility | accepted commitments, slot/quote evidence, explicit payer approval | plan, execution request, read-back state | invited attendance; elapsed time as play; a result |
| **Pricing and benefits** | customer-visible asset beliefs, expectations, quote comparisons, payer consent | self-discovery evidence and a quote for one candidate plan | mismatch/complaint, explicit approved funding instruction | balance as spend; membership as discount; pass as applicable; captain as payer |
| **Product execution gateway** | Preview adapter calls, response sanitization, idempotency/read-back evidence | a valid booking-plan command only | product observations and bounded execution result | social acceptance, customer consent, or product state absent a read-back |
| **Participation and results** | invitation/redemption/check-in observations, result request/report evidence | product read-backs, designated reporter attestation, Arcade replay evidence | participation state and attested outcome | attendance, a score, or winner from a booking alone |
| **Club operations** | Sofia/Riley promotions, support replies, incident handling, staff-created opportunities | product/venue evidence, community complaints, release notes | offers, explanations, staff requests and observed operational status | player demand or acceptance |
| **Product research and change detection** | reproducible journey probes, feature contracts, evidence-backed issues | release/change notice and safe journey observations | issue candidate, regression evidence, release impact | a root cause from a single failed journey |
| **Observatory** | read-only projections and navigation | facts, evidence, beliefs, lifecycle projections | no domain command | any new fact, authorization, or product state |
| **Arcade** | deterministic fictional replay and skill-weighted result candidate | a completed, observed local occurrence | replay evidence for a designated reporter | product attendance or a winner until the reporter confirms it |

The names in product engineering (Fernando, Brian, Javier) identify stewardship
and a useful routing destination for evidence.  They grant no simulator
authority.  A specialist can receive an evidence-backed issue; the simulator
still needs the normal product contract and customer/staff authority to act.

### Affinity groups versus PodPlay franchises

PodPlay's `franchise` is an operational venue concept, grouping product areas
by `areaIds`. It must not be reused in the simulation. The Community roster
uses typed `affinityGroups` instead, currently `game_series` and `region`.
They describe shared identity only: same group does not imply a booking,
friendship, eligibility, a discount, or reduced rivalry. Team rivalry remains
an explicit relationship with its own evidence and policy.

## Feature context map

```text
social intention / accepted commitment
             |
             v
   Teams and play format -----> Venue and schedule
             |                         |
             +-----------> Booking plan and lifecycle <------ Pricing and benefits
                                           |
                                           v
                              Product execution gateway
                                           |
                                           v
                         Participation and results -> Community learning

Club operations and product research observe every boundary.
Observatory projects them; it does not participate in them.
```

### Team signup — Fernando

Team signup belongs at the seam between **Teams and play format**, **Booking
plan and lifecycle**, **Pricing and benefits**, and **Participation**.  A team
agreement provides named candidates and a legal format.  It does not prove who
pays, how a split is funded, which teammate can redeem a benefit, or who
attended.  The existing generic team-signup adapter should therefore consume a
versioned booking-plan contract, not social-engine fields directly.

### Unified pricing and split strategy — Brian

Unified pricing is a **Pricing and benefits** concern with an
anti-corruption layer around the Preview quote API.  The social model should
receive only safe concepts such as `withinExpectation`, `membershipTreatment`
and `payerApprovalRequired`; it must not learn implementation-specific quote
fields as rules.

`spot+` and `court+` are legacy *strategy tokens*, not social concepts.  Keep
their mapping in the pricing gateway and retain an `unknown` treatment when a
new unified strategy cannot be proven from a quote.  A change in Brian’s
strategy model can then produce an observable compatibility issue instead of
silently changing a player’s spending story.

### Kids becoming customers — Fernando

Kids are an **Identity and eligibility** extension that crosses the roster,
customer self-discovery, booking planning, and participation.  Model a child
as a distinct actor/customer subject when the product does, with explicit
guardian/age/eligibility evidence.  Do not represent a child merely as an
extra participant name attached to an adult’s entitlement.

The temporary product hack must live behind an eligibility adapter with an
expiry/version marker.  It may yield `eligible`, `ineligible`, or `unknown`;
it never grants a guardian the right to spend, accept, or check in on the
child’s behalf unless Preview evidence exposes that authority.

### Series — Javier

Series is a **Club operations** aggregate: staff creates or updates a series,
then it becomes an advertised opportunity in the community context.  A series
is not a booking, and a player’s interest is not enrollment.  Keep creation,
occurrence generation, enrollment, cancellation, and staff issue triage as
separate lifecycle transitions.  This allows Javier’s refactor to be probed
with staff-owned journeys without contaminating customer booking evidence.

### Ratings and multi-sport

Ratings belong to a new **Skill and sport profile** context.  It owns sport
identity, rating observations, rating provenance, and compatibility between an
activity and a player’s known profile.  The Arcade may consume a sanitized,
fictional skill snapshot to weight a replay, but a replay never writes a real
PodPlay rating.

Multi-rating needs a sport namespace from day one, for example
`table_tennis`, `pickleball`, `cricket`, and `golf_simulator`.  “Has a rating”
must always mean “has this rating for this sport from this source at this
time.”  It does not imply eligibility, booking capability, a team format, or
that the product’s rating feature accepted it.

## Inference firewall

The following transitions require an explicit record at the boundary:

| Starting fact | Does not imply | Required boundary record |
| --- | --- | --- |
| customer has virtual credits | customer chose to spend them | quote treatment plus that customer’s approval |
| captain role | authority to charge a teammate | payer-specific consent/funding instruction |
| membership assignment | booking discount | quote read-back for this booking candidate |
| pass exists | pass applies to activity/time/participant | quote/eligibility read-back |
| team agreement | event exists | booking read-back with occurrence key |
| invitation sent | recipient agreed or attended | redemption/acceptance then participation read-back |
| booking ended | game happened or result exists | observed completion plus attested/replay-confirmed result |
| staff-created series | player enrollment | player-owned enrollment evidence |
| rating exists | a sport/activity is supported | sport-compatible capability/evidence |

## Integration contracts to add before each feature journey

Every new feature should add four small contracts before simulation policy:

1. **Vocabulary:** a versioned, product-neutral fact/command schema.
2. **Gateway translation:** map Preview fields/tokens into that vocabulary;
   preserve unsupported or missing information as `unknown`.
3. **Authority matrix:** identify the actor, role, commitment, and payer or
   guardian consent needed for each command.
4. **Journey probe:** a safe, bounded read/plan/execute/read-back path plus an
   evidence-backed issue shape for a failure.

This is the practical DDD boundary: the community can continue to reason in
terms of training, challenge, benefit expectation, and consent while PodPlay
features evolve underneath it.

## Suggested implementation order

1. Complete the generic booking-plan/quote contract and payer-approval gate
   for current team signup.
2. Add a pricing strategy translation table with legacy `spot+`/`court+` and
   an explicit unknown unified-pricing outcome.
3. Add sport-scoped skill-profile facts and make Arcade read them only.
4. Add a staff-owned series probe/lifecycle without treating it as enrollment.
5. Add child eligibility as a separate identity/authority adapter after the
   product’s temporary hack is documented and safely observable.
