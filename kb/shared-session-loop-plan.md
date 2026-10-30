# Shared-session loop plan

## Goal

Make a shared PodPlay session a complete, observable social journey rather
than a booking-shaped endpoint. The community should be able to move from a
reason to play through verified participation and a grounded outcome without
ever treating a local desire, message, or elapsed clock as product evidence.

```text
motive -> proposal -> commitment -> legal booking -> owner share offer
  -> verified share capture -> named recipient decision -> redemption/read-back
  -> check-in window -> attendance observation -> replay/result report
  -> relationship, motive, and product-learning consequences
```

The arrows are hard boundaries. A booked court does not imply that an
invitation was opened, a captured link does not imply that it was sent, an
accepted invitation does not imply check-in, and a replay is not PodPlay
attendance evidence.

Current player booking, invitation, and check-in follow the rendered-browser
playbooks, the [lead and dispatcher operating model](lead-and-dispatcher-operating-model.md),
and the [operations hand-off](current-operations-handoff-2026-10-30.md).
Slices below that describe a lead or adapter redeeming a bearer URL are
earlier design notes. They are not authority to call a product API for a Sim.
A product claim still requires the corresponding rendered confirmation.

## Slice 1 — owner share opportunity (implemented)

**Purpose:** once a player-owned training or team-signup booking exists, expose
one legal, named action to its booking owner: capture a private PodPlay share
invitation for the other named participants.

**Inputs:** a current, booked intent with a product event reference, stable
occurrence key, named owner, named recipients, and a session window that has
not ended.

**Outputs:**

- a `capture_share_invitation` legal offer for the owner;
- an observatory lifecycle state, `AWAITING_OWNER_SHARE`, which says exactly
  what is missing without claiming that a link exists;
- no Preview write, channel message, redemption, or acceptance.

**Guards:** ignore legacy bookings, ended windows, unnamed recipients, retained
links, and any booking whose owner is not an active player. The offer is a
local advertisement, not an authorization for the lead to call Preview.

**Acceptance checks:** a future two-player booking yields one offer for its
owner; the recipient gets no acceptance offer yet; a captured-link sidecar or
ended session removes the offer; the dashboard presents the state without a
bearer URL.

## Slice 2 — owner chooses and captures the share invitation (implemented)

The owner may accept or pass the offer. Only an accepted, durable owner choice
authorizes `open_preview_share_invitation`. The adapter uses the owner's
identity, stores the `redeemUrl` only in the ignored 0600 sidecar, reads the
response back, and posts a compact `share_invitation_ready` message addressed
to each named recipient. No URL appears in a fact, dashboard, or channel.

The lead records invitation availability as evidence, not recipient acceptance.
If product capacity is insufficient for the named recipients, it records a
bounded blocker and raises frustration rather than retrying blindly.

## Slice 3 — recipient decision, redemption, and acceptance (implemented)

Each addressed recipient receives an `accept_share_invitation` or `pass`
offer. Acceptance uses only the retained local bearer URL, redeems as that
recipient, evaluates any required product acceptance, and reads the owner view
back. The lifecycle changes to accepted only from that read-back. For teams,
each participant is independent: one acceptance never silently joins another.

## Slice 4 — check-in and session observation (training check-in implemented)

At the verified check-in opening time, each accepted training participant
receives a separate check-in offer. The product adapter validates the window
and reads status back. Team-signup check-in is deliberately still pending its
own per-member adapter; it must not reuse the training path by assumption.
After the session ends, the lead observes the event once. It records either
grounded attendance evidence or `attendance_inconclusive`; it never infers a
no-show, score, or winner from elapsed time alone.

## Slice 5 — replay-assisted result report and social consequences

The isolated Arcade replay may be generated for a booked occurrence. It is
local game evidence only. A designated captain can view it and report the
winner; the reported result is stored as captain attestation with a replay
reference. Verified shared completion can update closeness, mastery, and play
motives. Inconclusive attendance changes none of those relationship facts.

Implemented refinement: a reported training result is an idempotent social
completion. It lowers the just-trained pair's immediate desire for another
practice, raises their team cohesion, and opens a bounded competitive window.
During that window the captain may invite any other ready rostered team captain
to doubles; teams are not pre-ranked as rivals. A recovery window suppresses
another immediate teammate-training offer. The invitation remains an offer
until the other captain explicitly accepts; it never implies a booking,
teammate acceptance, or attendance.

## Slice 6 — club value and product-learning loop

Sofia and Riley use evidence-backed signals: quiet periods, missed windows,
unanswered invitations, pricing surprises, expiring packages, and feature
release notes. Players probe their own assets read-only, maintain separate
expectations and observed beliefs, and ask the club when a quote contradicts a
grounded expectation. Only evidence-backed mismatches become product issues.

## Implementation order and validation

1. Implement Slice 1 with unit tests and observatory visibility. **Complete.**
2. Wire Slice 2 through the existing safe share adapter; test no URL leakage.
   **Complete.**
3. Wire Slice 3 through the existing safe redemption adapter; test one
   recipient at a time and owner read-back. **Complete.**
4. Add Slice 4 training check-in offers and finished-event observation.
   **Training path complete; team-signup extension remains separate.**
5. Connect Slice 5 replay/result reporting and post-session motive changes.
6. Extend Slice 6 only through generic capability contracts and sanitized
   evidence, never actor-specific product scripts.

Each slice must pass `./club test`, preserve historical bookings, leave
credentials and bearer URLs outside the fact journal, and create no Preview
write until its socially authorized adapter is deliberately invoked.
