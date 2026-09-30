# Product research roadmap

## Phase 1 — visible catalog and account facts

- Inventory membership definitions: name, description, eligibility, displayed
  discount/benefits, and visible duration.
- Inventory package/pass definitions: name, quantity, expiration, eligible
  activity, and any stated guest rules.
- Record which facts are shown on the player account, membership screen, and
  checkout rather than assume that an admin configuration is player-visible.

## Phase 2 — pricing and consumption experiments

- Compare a player with and without an assigned membership on the same W37
  slot, using evaluation before payment.
- Compare virtual credits, pass strategies, member price, and regular price.
- Test whether an owner booking with an invited guest changes pass or credit
  consumption.  Do not infer guest rules from owner-only checkout.
- Record the product's booking summary before approving one sandbox purchase.

## Phase 3 — lifecycle and disruption behavior

- Observe cancellation eligibility, fee/refund/pass restoration language, and
  read-back after an approved sandbox cancellation.
- Determine whether weather is only an external club constraint or has a
  product representation and cancellation route.
- Recheck invitation, acceptance, check-in, late arrival, and no-show behavior
  after pricing or UI releases.

## Phase 4 — simulator promotion

- Add a secret-free player expectation schema.
- Add read-only pricing probes and `pricing_expectation_mismatch` records.
- Add dashboard comparisons: expected price/pass treatment versus observed
  evaluation and final read-back.
- Alert Sofia/Riley when a release is followed by eligible opportunities but
  no bookings, distinguishing product failures from player passes.
