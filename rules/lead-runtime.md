# Lead runtime

The lead owns the shared world, clock, scenario cursor, preview adapter, action
validation, assertions, journals, and issue identity.

Actors propose speech, intent, and at most one allowed action. The lead validates
the proposal and writes all shared state. Model prose is never HTTP evidence and
never determines assertion success.

The lead must reconcile remote state before retrying a mutation. Production and
shared staging are always outside the target boundary.
