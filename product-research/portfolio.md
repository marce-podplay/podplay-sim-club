# Club Lab research portfolio

The portfolio is a compact index of experiments. The default relationship is
one delivered feature or release note to one experiment. It does not duplicate
the experiment evidence, the community journal, or raw product information.

## Portfolio card

Each card contains only:

- release reference and feature identifier;
- workflow template;
- current phase and concise portfolio status;
- dependency contexts;
- freshness boundary;
- evidence kind and write count.
- unresolved-claim count and the next discriminating observation.

For example, PP-7827 is one Rate Cards experiment using the
`configuration_intensive` template. `unified-pricing` is a dependency context,
not a second completed experiment: it becomes its own card only when there is
a distinct delivered feature or release note to research.

## Templates

| Template | Use it when |
|---|---|
| `configuration_intensive` | An admin must first establish existing feature/schedule state. |
| `staff_operational` | Staff must perform an operational UI workflow after a known baseline. |
| `player_journey` | A named player accepts or passes a predicted journey. |
| `team_event` | Captains, teammates, invitations, signup, and attendance have separate evidence. |
| `pricing` | Effective configuration must be turned into an exact, declared cents prediction. |

Experiments may depend on one another, but they never borrow another
experiment's observation as current evidence. The target Preview season and
release context set the freshness boundary.

## Compact evidence, not an empty void

Active experiments retain a small state card, structured evidence, and short
operational diary entries. Each diary entry explains one decision or unknown;
it is not a private chain-of-thought and cannot contain URLs, identifiers,
credentials, or customer data.

A future compactor may summarize only `closed`, `archived`, or `stale`
experiments. It must retain references to original safe facts, the outcome,
the unresolved question, and the freshness boundary. It never deletes or
rewrites the append-only fact journal, and it is intentionally not implemented
yet.

## Uncertainty support

An unknown is recorded as a claim with alternatives, a named next observation,
a stop rule, and an expiry. The default maximum age for an unobserved
configuration claim is seven days, and any new Preview season or relevant
release change invalidates it immediately. This is a freshness policy, not a
probability: an LLM's fluent confidence is never evidence.
