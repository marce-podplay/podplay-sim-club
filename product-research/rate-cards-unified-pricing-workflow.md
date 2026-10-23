# Club Lab workflow: Rate Cards and Unified Pricing

This workflow treats **configuration exploration** as its first and complete experiment. It has no product write path. A release being merged is not proof that the Club Lab tenant is configured, that a rate card is effective, or that any player is entitled to a price.

## Roles and authority

| Role | May | May not |
|---|---|---|
| Lead | Define the bounded research question and review evidence | Bypass product authority or claim configuration |
| Sofia / Riley | Address the request, coordinate evidence, write a concise operational diary | Toggle Unified Pricing, publish schedules, quote a customer |
| Authorized PodPlay admin | Read the relevant product configuration and later explicitly choose any change | Treat a read-back as player acceptance |
| Player / captain | In later phases, accept or pass their own journey | Accept for another player or establish pricing from a request |

## State machine

```text
configuration_exploration_requested
  ├─ admin pass → configuration_exploration_passed → stop
  └─ admin accept → configuration_exploration_accepted
                         └─ sanitized read-back
                              ├─ configuration_observed → stop
                              └─ configuration_inconclusive → stop / repeat controlled read

Later, separately approved work (the technical packet is an artifact attached
to `change_proposal_needed`, not an automatic phase transition):
change_proposal_needed + technical_change_packet_prepared →
authorized_pricing_basis_recorded → activation_readback_needed →
prediction_preparation_needed → staff operational check → player-owned journey
→ evidence comparison
```

Nothing advances automatically beyond `configuration_observed`.

## Technical change packet: planning, not configuration

Once the lead has granted a **bounded planning authorization** and a named Club
Lab staff member has accepted the written plan for planning only, Riley or
Sofia may prepare a technical change packet. It is addressed to the authorized
product administrator as a request to **supply an approved pricing basis,
request clarification, or pass**.

The packet carries only the safe configuration summary and a generic admin
navigation path. It asks for the decisions that cannot be safely inferred:

- Rate Card label and validity intent;
- currency and an approved exact-price source;
- price duration and billing policy;
- player-count packages or bands; and
- whether conversion of the Legacy schedule is approved or a schedule must be
  supplied.

The packet is deliberately insufficient to create or publish a Rate Card or
enable Unified Pricing. Those remain a later, explicit product-side decision.
It neither invites players nor creates a price promise. Its output is stored
with the experiment evidence as `technicalChangePacket`, with `remoteWrites: 0`.

## First experiment: configuration exploration only

The authorized admin records only normalized values:

- `feature.bookingEngine`: `LEGACY`, `UNIFIED`, or `UNKNOWN`;
- effective scope: `region`, `area`, `pod`, or `unknown`;
- schedule: `active`, `future_only`, `draft_only`, `missing`, or `unknown`;
- rate card: `effective`, `missing`, or `unknown`;
- activation readiness: `ready`, `generate`, `missing_rate_card`, `blocked_schedule`, `conversion_failed`, or `unknown`.

Product code reviewed for PP-7827 exposes booking-engine resolution at region, area, and pod scope. It does **not** establish a Club-level override. Club Lab therefore proposes the narrowest supported scope—normally pod—for an isolated experiment, but the authorized admin must accept, use another scope, or pass.

Technical handoff for the admin: inspect the effective rate-card and activation-preview surfaces through the authorized admin product session. The application route forms include `GET /pods/{pod}/effective-rate-card` and `GET /pods/{pod}/unified-activation-preview`; never copy identifiers, URLs, responses, credentials, or screenshots containing them into Club Lab state. The reviewed server code requires PodPlay-admin authority for schedule work.

## Evidence and diary

Each run writes two equivalent, local-only artifacts:

- `state/experiments/<experiment-id>/evidence.json` — structured facts for automation;
- `state/experiments/<experiment-id>/evidence.md` — human-readable outcome and concise operational diary.

Diary entries are short rationale summaries, not private chain-of-thought. They must not contain raw identifiers, private URLs, credentials, or customer data. The JSON and Markdown state remain configuration evidence only: no price quote, booking, attendance, entitlement, or regression is implied.

## Uncertainty ledger

The evidence bundle also includes `uncertainty.json`. Unknown configuration
fields are not left blank: each one names its alternatives, the next
discriminating observation, a stop rule, and an expiry. The default maximum age
is seven days and a new Preview season or relevant release change invalidates
the claim. A later price prediction is itself `unknown` until separately
approved; this first configuration phase cannot create one.

## Commands

```console
./club experiment workflow
./club experiment request-configuration --requester riley
./club experiment admin-decision --actor podplay-admin --decision accept
./club experiment discover-configuration
./club experiment record-configuration --actor podplay-admin --booking-engine LEGACY --effective-scope pod --schedule missing --rate-card missing --readiness missing_rate_card
./club experiment diary --actor riley --note "Read-back recorded; configuration exploration stops here."
./club control authorize-rate-cards-planning
./club consultation propose-rate-cards --proposer riley --recipient morgan-vale
./club consultation respond --plan consultation:rate-cards-pod:morgan-vale --actor morgan-vale --decision accept
./club experiment prepare-change-packet --actor riley
./club experiment status
```
