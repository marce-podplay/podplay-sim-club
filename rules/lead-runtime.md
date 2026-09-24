# Lead runtime

The lead owns the shared world, clock, scenario cursor, preview adapter, action
validation, assertions, journals, and issue identity.

Actors propose speech, intent, and at most one allowed action. The lead validates
the proposal and writes all shared state. Model prose is never HTTP evidence and
never determines assertion success.

The lead must reconcile remote state before retrying a mutation. Production and
shared staging are always outside the target boundary.

Fake mode is the default. A remote target must be a recognized PR-preview Cloud
Run origin and appear in the exact-origin allowlist. Read-only and write modes
remain distinct, and write mode requires the exact target origin to be repeated
as local confirmation. Redirects may not cross that origin.
