# Research tasks

| ID | Status | Question | Method | Write scope | Output |
|---|---|---|---|---|---|
| PR-001 | in progress | What membership definitions and benefits are visible to a player? | UI + source/doc comparison | read-only | membership catalog evidence |
| PR-002 | in progress | What packages/passes are visible, and what does each claim to cover? | UI + source/doc comparison | read-only | package/pass catalog evidence |
| PR-003 | queued | How does unified pricing present member versus regular price? | same-slot evaluation across approved test actors | read-only | price comparison |
| PR-004 | queued | When does checkout consume virtual credits or passes? | evaluation first, then one approved sandbox purchase | one explicitly logged test purchase | consumption evidence |
| PR-005 | queued | Can an invited guest consume the owner’s pass or their own pass? | owner + guest invitation/redeem flow | one explicitly logged test booking | guest-consumption evidence |
| PR-006 | queued | What is the cancellation/refund/pass-restoration behavior? | product UI/API + isolated approved test | one explicitly logged cancellation | cancellation evidence |
| PR-007 | queued | Which booking screen/API contracts should be monitored after releases? | source contract + UI journey | read-only | journey probe specification |

Use one evidence note per task run.  Mark a task `blocked` only with a precise
missing product capability, permission, or required approval.

## Source review note — 2026-10-05

Local source confirms customer-owned reads for current virtual-credit balance,
owned packages expanded with items, active membership enrollment, and optional
credit/package history. These anchors shape the implementation plan but do not
complete PR-001 or PR-002: live Preview output and customer UI equivalence must
still be observed. Coupon listing is not a customer self-discovery surface;
only a grounded known code may be evaluated against a specific booking quote.

Slice 4B now exposes those first three source-confirmed reads through the
GET-only `preview discover-assets` adapter. Slice 4D performed the first live
King/Mai read and corrected payment readiness to match the customer UI's
default booking-context rule. The live actors had no active package items and
no membership enrollment expansion, so PR-001 and PR-002 remain in progress:
the run verifies empty/unknown semantics but cannot establish benefit text or
non-empty package meaning.
