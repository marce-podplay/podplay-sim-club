# PodPlay product research lab

This folder is the club's deliberate product-discovery workspace.  It is
outside Rally Engine: findings here do not create motives, bookings, messages,
or preview writes until they are reviewed and promoted into a simulator rule.

The purpose is to learn the real PR-preview behavior through three sources:

1. product UI and approved preview interactions;
2. product/API read-backs and source-code contracts;
3. relevant PodPlay documentation.

Each claim has a status: `hypothesis`, `observed`, `documented`,
`contradicted`, or `promoted`.  A claim becomes `promoted` only when its
evidence is sufficient to define a simulator policy or probe.

## Boundaries

- Use the PR-preview only; never production.
- Prefer read-only inspection first.  A task marks any intentional test write
  (enrollment, booking, payment, invitation, redemption, check-in, or
  cancellation) before it is attempted.
- Do not copy emails, passwords, tokens, payment IDs, invitation URLs, user
  IDs, or personal account details into these files.
- Capture product-visible labels, safe summaries, request/response shapes with
  sensitive fields removed, timestamps, and screenshots only when useful.
- A completed test is evidence about one observed condition, not a permanent
  guarantee of product behavior.

## Change-detection contract (Slice 1)

The first contract is **team signup**. Sofia and Riley may use the display
role “Big Brother” to make a verification request visible, but it grants no
product or social authority: each named player or staff verifier must record
their own `accept` or `pass`. A request is not acceptance, an accepted probe is
not an entitlement, and a booking read-back is not attendance evidence.

Run the local, explicit flow:

```console
./club research contract --feature team-signup
./club research request --feature team-signup --requester sofia \
  --verifier andy-bogard --verifier terry-bogard
./club research acknowledge --probe "probe:feature:…" --actor andy-bogard --decision accept
./club research acknowledge --probe "probe:feature:…" --actor terry-bogard --decision accept
./club research discover --probe "probe:feature:…"
```

The last command authenticates only the already configured Preview admin and
makes four bounded GET reads: tenant signup flags, areas, pods for the
configured area, and the caller's booking-settings shape for the configured
pod. It retains only boolean presence/requirement summaries and an opaque PR
fingerprint. It does not create users, change configuration, create a booking,
or retain raw IDs, tenant setting values, private URLs, credentials, or raw
responses. Preview exposes no supported release/change feed in the reviewed
surface, so that portion is stored as `unknown`, not inferred.

An `issue-candidate` created after a read-back is explicitly **not** a
regression. It needs a passing baseline or a repeated controlled probe before
that label becomes available.

## Promotion path

```text
task → evidence note → reviewed finding → simulator rule/probe → dashboard signal
```

For example, a player may expect a membership discount, but checkout changes
only after a read-back pricing evaluation confirms how the product treated that
membership for that booking.  A mismatch becomes an evidence-based local
complaint; it never authorizes a blind retry or a fabricated credit/pass use.

See [ROADMAP.md](ROADMAP.md), [TASKS.md](TASKS.md), and
[evidence-template.md](evidence-template.md).

## Rate Cards and Unified Pricing

The Rate Cards workflow begins with a separate, admin-owned configuration
exploration. Its first phase is read-only and terminal: it records whether the
feature is configured, then stops. It cannot enable Unified Pricing, publish a
schedule, make a booking, or ask a player to test. The structured JSON and
human-readable evidence are local state, while the durable workflow and admin
handoff are documented in
[rate-cards-unified-pricing-workflow.md](rate-cards-unified-pricing-workflow.md).
