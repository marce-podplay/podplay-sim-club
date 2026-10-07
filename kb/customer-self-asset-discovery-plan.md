# Customer self-asset discovery plan

## Decision

Milestone 4 discovers only resources that a customer can read through their
own authenticated PodPlay surfaces. It does not discover administrative
inventory, make a booking, apply a coupon, or infer a discount.

Product observations and fictional actor preferences remain different types:

- **product evidence** says what PodPlay returned at a particular time;
- **product beliefs** are the actor's time-bounded interpretation of that
  evidence;
- **actor expectations** describe fictional budget, acceptable price, and
  anticipated benefits;
- **comparisons** belong to the later expectation-aware execution slice.

An absent field, failed request, incompatible response, or expired observation
is never converted to zero, `false`, no membership, or unlimited capacity.

## Source-confirmed customer surfaces

The following contracts are confirmed in the local PodPlay source. A live
Preview read is still required before treating a particular actor's value as
observed:

| Subject | Customer-owned read | Safe interpretation |
| --- | --- | --- |
| Virtual-credit balance | current-user payment-method response | Numeric balance only when the field is explicitly numeric. |
| Virtual-credit history | current-user virtual-credit transactions | Optional later evidence for consumption; not needed for the first balance slice. |
| Owned packages/passes | current-user packages expanded with items | Each active entitlement, remaining item quantity, and expiration independently. |
| Package history | current-user package history | Deferred; the current customer UI has a disabled Used tab and history is not needed to discover current ownership. |
| Active membership | expanded current-user membership enrollment and membership | Assignment, display type/name, ownership, and start date only when present in the response. |
| Enrollment detail | current-user enrollment detail | Optional enrichment for customer-visible membership details and owner billing context. |
| Payment readiness | current-user payment elements expanded with context availability | `ready` means the customer UI can find a default element for the `booking` context; never retain payment element, card, bank, customer, or setup identifiers. |

Coupons are deliberately excluded. PodPlay exposes coupon application through
a quote/checkout input, not a customer coupon inventory. A specific code may
become a grounded actor belief only after the actor receives it from an
authorized message or other evidence. Its validity is a quote-specific
comparison in Milestone 5.

Rating is also excluded from the first implementation pass because this
handoff did not establish a verified player-owned rating surface. It stays
`unknown` until product research identifies and verifies one.

## Canonical belief subjects

Use role-general subject codes:

| Subject | Observed value | Notes |
| --- | --- | --- |
| `virtual_credit_balance` | `{amount, unit}` | Distinct from package credits and fictional spending budget. |
| `owned_package_entitlements` | list of sanitized entitlement summaries | Package/item type, remaining quantity, and expiry remain distinct. |
| `membership_assignment` | `{assigned, type, displayName, isOwner, startDate}` | No inferred booking discount. Missing expansion is `unknown`, not unassigned. |
| `membership_benefits_understood` | sanitized customer-visible benefit statements | Separate from membership assignment and from observed quote treatment. |
| `payment_readiness` | `{ready}` | No payment identifiers or brands are required. |
| `booking_permissions` | sanitized customer-visible booking settings | Existing readiness data may feed this only after evidence conversion. |
| `rating` | product-defined safe summary | Deferred until a customer-owned read is verified. |
| `spending_budget` | fictional amount/range | Actor fixture or policy expectation, never product evidence. |
| `acceptable_booking_price` | fictional amount/range | Compared with a later booking quote. |

Package items must not be collapsed into a single balance. Play passes,
package credits, guest benefits, and other item types retain the product's
customer-visible category and unit.

## Evidence boundary

Each probe produces one sanitized evidence record per subject, not one raw
account dump. A record includes:

- deterministic evidence ID;
- probe and contract version;
- Preview/build fingerprint;
- actor ID;
- observation time;
- result: `observed`, `unknown`, or `error`;
- safe observation or a bounded error code.

Raw responses exist only in process memory. Evidence, facts, beliefs,
dashboards, and messages must exclude emails, Firebase UIDs, PodPlay user IDs,
payment identifiers, credentials, bearer material, and raw response bodies.
One failed endpoint does not discard successful observations from another
endpoint.

## Staleness policy

Staleness is simulator policy and must be configurable rather than inferred
from PodPlay:

- balances and payment readiness: short-lived;
- active package quantities: short-lived;
- membership assignment and visible definitions: longer-lived;
- actor budget and price expectations: expire only under their fictional
  policy, not because a product probe aged.

Initial durations should be introduced as named configuration values with
tests. Until those values are approved, a probe may record `observedAt` while
the belief reducer treats freshness conservatively.

## Implementation slices

### 4A — belief/evidence contracts and unknown semantics

Status: implemented on 2026-10-05. The foundation is local-only; it introduces
no new product request.

- refine belief and evidence schemas with schema version, provenance, result,
  and reducer cursor;
- add `belief.observed`, `belief.unknown`, `belief.expected`, and staleness
  reducer behavior;
- create deterministic, per-subject evidence sanitizers;
- remove missing-to-zero defaults from existing readiness summaries;
- add privacy, replay, contradiction, and expiry tests.

No new product request is added in this slice.

### 4B — player-owned discovery probes

Status: implemented on 2026-10-05; live Preview/UI equivalence is evaluated in 4D.

- add a read-only discovery service that authenticates as the selected actor;
- independently read virtual credits, booking-context payment readiness, owned
  packages/items, and active membership;
- convert each response immediately into a safe subject observation;
- persist sanitized evidence and append belief facts under one discovery
  correlation ID;
- support any seeded player without actor-specific control flow.

No transaction history, coupon validation, checkout, or product write occurs.

The active command is `./club preview discover-assets --actor <actor-id>`.
It uses only identities already present in the private registry. The profile
read is an identity gate; payment and package reads then fail independently.
Named freshness windows are 15 minutes for credit/payment readiness, 30
minutes for package quantities, and 6 hours for membership assignment.
Booking-permission discovery remains outside this first probe because it needs
safe area/pod context without crossing into an admin-owned inventory read.

### 4C — belief projection and observatory

- write `state/projections/beliefs.json` only from belief facts;
- expose source evidence, observed time, freshness, and status per actor;
- show virtual credits, package entitlements, membership assignment,
  understood benefits, and payment readiness as separate cards;
- distinguish explicit zero, empty list, unknown, stale, and contradicted;
- never render raw identifiers or responses.

### 4D — live Preview verification and research promotion

Status: partially verified on 2026-10-05 against PR #7200. King and Mai were
read through their own identities with zero Preview writes. The corrected
payment probe matches the current customer UI contract: a default method is
ready only when its expanded context availability contains `booking` with
`isDefault: true`. King observed ready and Mai observed not ready. Both
observed an explicit zero virtual-credit balance and an explicit empty active
package collection. Membership enrollment was omitted for both, so it remains
unknown.

The UI/source contract comparison is documented in
`product-research/evidence/2026-10-05-pr001-pr002-self-assets.md`. A visual
customer-session comparison for both actors remains open: API authentication
does not establish a browser session, and private credentials must not be
copied into browser automation output. No membership benefit or non-empty
package semantics have therefore been promoted from this run.

- dry-run the discovery plan, then perform read-only actor-owned probes against
  the confirmed Preview origin;
- compare sanitized output with the customer UI for at least two actors;
- record contract mismatches as research evidence, not product defects;
- promote only verified package item and membership benefit semantics.

This slice may complete PR-001 and PR-002. PR-003 and coupon treatment remain
part of Milestone 5; consumption/history investigations remain PR-004 onward.

## Acceptance checks

- Each displayed resource identifies its source evidence and observation time.
- Explicit numeric zero remains observed zero; absence or failure remains
  unknown.
- Two actors with different product responses produce different beliefs through
  the same control flow.
- Package credits, play passes, virtual credits, and fictional budget are not
  merged.
- Membership assignment does not imply a discount or understood benefit.
- A discovered actor can have some observed subjects and other unknown ones.
- Rebuilding projections from the fact journal is byte-stable.
- No discovery request can mutate Preview state or trigger checkout.
- No private identifier or bearer value enters evidence, facts, messages, or
  the dashboard.

## Deferred work

- known-coupon quote validation and price comparisons: Milestone 5;
- transaction/package history and consumption explanations: PR-004 onward;
- customer rating: after a verified customer-owned source is found;
- owner/staff inventory and role discovery: Milestone 7;
- using beliefs to allow/block a booking: only after discovery and dashboard
  equivalence are verified.
