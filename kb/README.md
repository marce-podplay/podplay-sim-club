# Preview Club knowledge base

This directory contains the durable design model for Preview Club. It explains
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
- [Shared-session loop plan](shared-session-loop-plan.md) — the staged path from
  a reason to play through invitation, participation, result, and learning.

The short version is:

```text
motive -> belief -> intention -> social commitment -> legal offer
       -> lead-validated product action -> read-back evidence -> learning
```

Every arrow is a boundary. A desire is not an intention; a message is not an
agreement; an agreement is not a booking; an accepted invitation is not a
check-in; and an elapsed event is not proof that anyone played.
