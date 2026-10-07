# Evolution plan

This is the short roadmap. The detailed data contracts, migration gates, and
acceptance criteria are in the
[capability alignment implementation plan](capability-alignment-plan.md).

## Phase 1 — make boundaries explicit

- Add `conversationId`, `speechAct`, `commitmentId`, and `inReplyTo` to new
  club messages while retaining `kind` for compatibility.
- Replace automatic missed-session rebooking with a new captain proposal and an
  explicit teammate/opposing-captain acceptance.
- Make result reporting an event capability: deterministic replay or one named
  reporter supplies the winner; elapsed time never does.
- Add contract tests for every negative boundary in the capability model.

## Phase 2 — self-discovery and expectation-aware checkout

- Define safe belief records for membership assignment, understood benefits,
  package/pass entitlements, virtual credits, payment readiness, and booking
  permissions. Keep rating unknown until its player-owned read is verified.
- Add player-owned read-only discovery adapters and staleness rules.
- Keep product assets separate from fictional budget and acceptable price.
- Compare booking evaluation against expectations before payment.
- Offer `clarify` or `complain` to Riley when evidence contradicts an
  expectation; never blindly retry a charge.

Discovery precedes comparison: coupon applicability, price treatment, and
benefit consumption are quote-specific evidence, not account inventory.

The existing [product research lab](../product-research/README.md) supplies the
evidence needed to promote each product fact into a runtime rule.

## Phase 3 — canonical event and participation projections

- Introduce a stable occurrence key across booking intents, Preview events,
  arcade replays, result requests, and outcomes.
- Store participant state independently from event state.
- Rebuild dashboard event cards from projections rather than merging several
  partially overlapping files in the view layer.
- Preserve current ledgers as compatibility inputs during migration.

## Phase 4 — owner/staff feature exploration

- Give Sofia and Riley the same belief/intention/commitment flow as players.
- Add read-only discovery for club inventory, series, pricing configuration,
  court status, and performance summaries.
- Model an admin experiment as hypothesis -> authorized action -> read-back ->
  evidence -> report/complaint, using a disposable Preview fixture where
  required.
- Keep operator/admin credentials and raw product IDs outside community state.

## Phase 5 — general capability registry

Each new capability declares:

- roles allowed to consider it;
- required motives, beliefs, opportunities, and commitments;
- legal speech acts;
- adapter and remote-write classification;
- evidence and staleness rules;
- success, rejection, timeout, mismatch, and failure feedback;
- privacy classification and dashboard-safe fields.

This registry is the scalable replacement for adding a bespoke branch whenever
a new character, team, benefit, or product screen appears.
