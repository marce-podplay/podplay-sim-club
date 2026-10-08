# Preview Club roadmap

The architectural model is maintained in the [knowledge base](kb/README.md),
with the implementation sequence in the
[capability alignment plan](kb/capability-alignment-plan.md). The roadmap orders
delivery; it does not redefine those boundaries.

## Now — Rally Engine foundation

- Durable two-hour/20-minute loop contract and actor handoff rules.
- Grounded missed-session retry: captain proposal, required acceptance, then
  the ordinary training or team-signup adapter.
- Two-Then-One tournament scenario: Bogard-Higashi vs Japan Team, then the
  reported winner vs Women Fighters Team.
- Nearest-fixture policy: a planned tournament event uses the nearest real
  contiguous slot at its approved venue.
- Ticket-scoped, private payment lifecycle ledger for preview actors.

## Next — prove one complete run

- Obtain a legal immediate fixture, register its four players, observe its real
  end, and collect a named player result report.
- Let Sofia announce the final only after that report.
- Reconcile identities/payment ledger after a preview refresh.
- Verify the active player-owned membership, pass/credit, and payment-readiness
  discovery against live Preview customer UI; then add the pricing/benefit
  gate and Observatory inspectors: a selected player shows expectations,
  customer-visible benefit beliefs, source/freshness and uncertainty; a
  selected booking plan shows payer, charge strategy, venue constraints, quote
  treatment, comparisons and approval. A named payer reads a quote, confirms
  its treatment against their expectations, and only then enables a
  team-signup booking.

## Later — extract a reusable skill

After one complete run, promote the stable Rally Engine operating contract into
a Codex skill. Keep tournaments, coaches, food, replays, and new release
journeys as data scenarios rather than separate skills.

## Product-social resilience vision

Preview Club is a small community living alongside a changing product.  The
social engine must therefore respond to verified product conditions rather than
assume that a familiar booking path, price, membership, pass balance, or
weather-dependent plan will keep working.

### Player context

In addition to social motives (play, fun, rivalry, affinity, closeness,
curiosity, frustration, and cooldown), each player may have local,
secret-free expectations for:

- membership status and an expected member discount;
- pass/credit use, including whether a pass may be consumed;
- an acceptable price range for a proposed booking;
- travel or weather tolerance and cancellation preferences.

These are simulation preferences, not an assertion about a real customer’s
finances or product entitlement.  Private payment IDs, account balances,
emails, and credentials remain outside world and channel state.

### Pricing and membership decisions

Before a player commits a booking, the lead reads the product evaluation and
compares only safe, summarized fields with that player’s local expectation:
the displayed amount, currency, membership treatment, and pass strategy.  If
the product price, discount, or pass consumption differs materially from the
offered expectation, the player does not continue the checkout.  The engine
records a compact `pricing_expectation_mismatch` observation and offers Riley
an evidence-based club-channel complaint or question.  It does not retry a
charge, invent a discount, or alter a product membership.

### Release and product-change observability

Every relevant release note (for example unified pricing) is paired with
bounded journey probes: availability, pricing evaluation, booking, invitation,
acceptance, check-in, cancellation policy, and read-back.  The local dashboard
should show the last safe outcome, affected journey, and an issue when a
contract or visible workflow changes.

An absence of new bookings after enabling unified pricing is a useful signal,
not proof of a cause.  The diagnostic compares the release timestamp against
eligible player opportunities, pricing expectation mismatches, failed probes,
passes, explicit player decisions, cancellations, and weather disruptions.
Sofia can promote a feature or ask Riley to explain it, but only players and
the verified product flow can create a booking.

### Durable outcomes

Keep append-only observations and messages as the audit trail, and project
live operational questions into an indexed local read model as the club grows.
That lets the club answer questions such as “did unified pricing reduce
completed bookings?” without rereading every historical conversation, while
preserving the evidence required to distinguish a product regression from a
player simply passing.
