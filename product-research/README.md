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
