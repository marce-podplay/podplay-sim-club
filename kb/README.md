# Club Lab knowledge base

This directory contains the durable design model for Club Lab. It explains
why the simulator behaves as it does; executable rules remain in `rules/`,
scenario inputs remain in `scenarios/`, and observed product research remains
in `product-research/`.

Start here:

- [Capability model](capability-model.md) — the six generally applicable
  capability layers and their boundaries.
- [State and evidence model](state-and-evidence.md) — a critical review of the
  current JSON/JSONL state and the target event/participation contracts.
- [Research foundations](research-foundations.md) — the small theoretical
  foundation used by the capability model.
- [Current capability inventory](current-capability-inventory.md) — what is
  implemented, partial, or missing today.
- [Capability alignment plan](capability-alignment-plan.md) — canonical data
  structures, migration milestones, bug-detection funnel, and validation path.
- [Customer self-asset discovery plan](customer-self-asset-discovery-plan.md) —
  player-owned probes, evidence boundaries, belief subjects, and Milestone 4
  delivery slices.
- [Pricing, benefits, and booking authority](pricing-and-benefits-model.md) —
  customer-known facts, quote-specific benefit treatment, payer consent, and
  the safe path from a captain agreement to a booking.
- [Domain context map](domain-context-map.md) — the bounded contexts, feature
  seams, authority firewalls, and integration contracts for evolving PodPlay
  capabilities such as team signup, unified pricing, kids, series, and
  multi-sport ratings.
- [Evolution plan](evolution-plan.md) — incremental changes that do not require
  replacing the current engine at once.
- [Preview continuity and portability](preview-continuity-and-portability.md) —
  durable fictional-community state, Preview-season rollover, and fact
  compatibility/freshness rules.
- [Operator control plane and club protocol](operator-control-plane-and-club-protocol.md) —
  the Big Brother boundary, staff handoffs, communication primitives, and
  release-research workflow.
- [Community identities and Club Lab contexts](community-and-club-contexts.md) —
  one persistent player identity across club visits, separate club employees,
  and safe per-context product identity verification.
- [Shared-session loop plan](shared-session-loop-plan.md) — the staged path from
  a reason to play through invitation, participation, result, and learning.
- [Sim browser capability lab](sim-browser-capability-lab.md) — the separation
  between the social engine, a Sim's rendered browser journey, dashboard
  observation, and bounded experiments used to discover reusable routines.
- [Lead and dispatcher operating model](lead-and-dispatcher-operating-model.md) —
  the portable coordinator, engine, economical-worker, research, evidence, and
  browser-isolation agreement.
- [Operations hand-off — 2026-10-30](current-operations-handoff-2026-10-30.md)
  — current journey status, verified timing behaviour, and the reconstruction
  checklist for a fresh chat.

Current player operation is the [lead and dispatcher operating model](lead-and-dispatcher-operating-model.md)
and the [operations hand-off — 2026-10-30](current-operations-handoff-2026-10-30.md).
Those two documents are canonical. Older notes in this directory that describe
a lead-owned Preview adapter, bearer token, or direct product integration are
design history, not authority to call a product API for a Sim.

```text
motive -> belief -> intention -> social commitment -> legal offer
       -> social-engine work item -> one isolated rendered-browser contract
       -> rendered evidence -> learning
```

The social engine forms social agreements and emits work items. The
coordinator/lead handles dispatch, login/bootstrap, research, and evidence
review. A cheap worker performs one named, isolated rendered-browser contract.
Sims do not use product APIs or integrations. A product claim requires the
corresponding rendered confirmation.

Every arrow is a boundary. A desire is not an intention; a message is not an
agreement; an agreement is not a booking; an accepted invitation is not a
check-in; and an elapsed event is not proof that anyone played.
