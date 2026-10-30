# Capability model

## Decision

Use six capability layers instead of three broad ability buckets. The user's
three original groups remain visible, but the extra boundaries prevent social
motivation, communication, product facts, and writes from silently authorizing
one another.

## 1. Motivation and affect — *why now?*

Local fictional state that energizes or discourages an actor:

- desire to play and have fun;
- desire to train and improve competence;
- eagerness to compete or answer a rivalry;
- relatedness and team-duty pressure;
- curiosity about a feature or club opportunity;
- frustration after a blocked action, unanswered message, mismatch, or no-show;
- boredom from repeating the same interaction;
- confidence, fatigue, and interaction-specific cooldowns.

Motives may raise or lower the priority of a legal offer. They never prove that
a court, balance, pass, membership, teammate, or product action exists.

## 2. Self-knowledge and product beliefs — *what do I think I have?*

An actor's secret-free, time-bounded understanding of their own situation:

- available money or acceptable price range;
- expected member price and membership benefits;
- expected virtual-credit or pass balances and permitted consumption;
- payment readiness without storing payment identifiers;
- rating, recent practice, and perceived skill;
- availability, travel tolerance, weather tolerance, and cancellation needs;
- understood product capabilities, such as whether the actor can book, invite,
  redeem, check in, or manage a series.

Each belief needs `source`, `observedAt`, `status`, and optional `expiresAt`.
Use `unknown`, `expected`, `observed`, `contradicted`, or `stale`; never turn a
missing read-back into zero. Private credentials, IDs, URLs, and tokens remain
in private ignored registries.

This layer is broader than “assets.” It holds what the actor knows, while the
execution/evidence layer holds what the product actually returned.

## 3. Relationships, roles, and norms — *with whom and under what duty?*

Durable social context:

- team membership and captain/staff/owner duties;
- static affinity and rivalry from the roster;
- closeness earned only after a verified shared completed session;
- trust and reliability changed by satisfied or violated commitments;
- reciprocity, team norms, and club policies;
- permission to speak for a team or perform a product action.

Closeness is not attendance. Affinity is not agreement. A captain can propose
for a team, but teammates are not silently checked in or declared winners.

## 4. Communication and social commitments — *how do we coordinate?*

Use a small vocabulary of structured speech acts:

- `inform` and `report` — put a claim into the channel with evidence status;
- `invite`, `propose`, and `request` — ask another actor to consider an action;
- `offer` — make a resource or benefit available without claiming acceptance;
- `accept`, `reject`, and `counter` — change the social commitment;
- `acknowledge` — confirm receipt or understanding, not acceptance;
- `clarify` — repair missing or ambiguous common ground;
- `complain` — report an expectation mismatch to staff with safe evidence;
- `escalate` — ask an authorized role for help after a bounded failure.

Messages belong to a conversation and may create a commitment:

```text
proposed -> accepted | rejected | countered | expired
accepted -> satisfied | violated | canceled
```

Delivery, understanding, acceptance, and satisfaction are separate facts.
For a private invitation, the recipient first acknowledges the handoff, then
receives a separate explicit decision (`accept`, `ask`, or `pass`). The
dashboard can therefore show both the attention state and the expected next
step without claiming an invitation was redeemed.
Staff can answer, explain, propose, or escalate. Staff cannot make a player
accept. Sofia can advertise and influence; she cannot manufacture demand or a
product outcome.

## 5. Deliberation and policy — *what is a legal next step?*

The deterministic lead combines the other layers. A behavior is eligible only
when all relevant gates pass:

- **motivation:** a motive exceeds its threshold and wins against alternatives;
- **capability:** the actor understands and is allowed to perform the action;
- **opportunity:** a real addressed invitation, compatible schedule, court, or
  product surface exists;
- **social authority:** the correct actor owns the action and required
  commitments are grounded;
- **policy:** cooldown, privacy, venue, duration, price, and preview boundaries
  pass.

The result is a compact legal offer such as `propose_training`,
`accept_invitation`, `ask_staff`, `inspect_assets`, or `complain`. It is not a
remote write. A cheap character interpretation may only accept, ask, or pass
among offers already made legal here.

## 6. Execution, evidence, and learning — *what really happened?*

Player product steps are dispatched rendered-browser journeys. The social
engine emits a work item after a social agreement. The coordinator handles
login/bootstrap, research, and evidence review, and assigns one cheap worker
to one isolated contract. A booking, invitation, acceptance, check-in, or
other product claim requires the corresponding rendered confirmation. The
lead does not call product APIs or integrations on a Sim's behalf. See the
[lead and dispatcher operating model](lead-and-dispatcher-operating-model.md)
and the [operations hand-off](current-operations-handoff-2026-10-30.md).

These are capability boundaries, not lead-owned product API calls:

- product login and safe self-discovery on the isolated browser handoff;
- availability, booking, invitation, redemption, and check-in as named
  browser journeys;
- owner/admin probes, which remain separate research and are not Sim player
  actions;
- sanitized evidence and compact issue creation;
- feedback into beliefs, motives, relationships, and commitments.

Examples:

- A displayed price outside the actor's expectation creates a verified
  mismatch and a possible complaint; it does not create a charge.
- A missed session raises frustration and desire, then offers a captain a new
  proposal; it does not automatically copy the old booking.
- A product error creates an observation. It becomes a regression only after
  the relevant contract and baseline evidence support that conclusion.

## Cross-layer boundaries

These invariants are more important than any individual scenario:

| Do not collapse | Required transition |
| --- | --- |
| desire -> action | desire -> intention -> legal offer |
| message sent -> agreement | delivery/grounding -> explicit acceptance |
| agreement -> booking | lead validates capability and opportunity |
| invite created -> accepted | recipient redeems; product read-back confirms |
| accepted -> attended | product check-in read-back confirms |
| event ended -> played | attendance/replay evidence confirms |
| played -> winner | deterministic replay or named reporter attests |
| expected asset -> actual asset | actor self-discovery reads the product |
| mismatch -> defect | evidence is compared with a documented contract |
| actor proposal -> preview write | social engine emits a work item; coordinator dispatches one rendered-browser contract |

## Capability growth

New behavior should be added as a capability contract, not a character-specific
branch:

1. Define the motive and the observation that can change it.
2. Define the belief/capability and staleness rule.
3. Define the legal speech act or product offer.
4. Define the required social commitment and authority.
5. Define the adapter boundary and read-back evidence.
6. Define feedback after success, failure, rejection, timeout, and mismatch.

The same pattern supports player training, team challenges, price complaints,
membership/pass discovery, and owner/admin feature probes.
