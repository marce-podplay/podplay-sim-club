# Research foundations

Preview Club borrows a few organizing ideas. It is not intended to be a
psychological model of real people, and meter values must not be interpreted as
diagnoses or predictions about users.

## Motivation: autonomy, competence, relatedness

Ryan and Deci's Self-Determination Theory organizes motivation around autonomy,
competence, and relatedness. For Preview Club this suggests three useful high
level motives: choosing to play rather than being scripted, wanting to improve,
and wanting to belong to or support a team. Sofia can support these motives but
should not replace player choice.

Source: Richard Ryan and Edward Deci, [Self-Determination Theory and the
Facilitation of Intrinsic Motivation, Social Development, and
Well-Being](https://selfdeterminationtheory.org/SDT/documents/2000_RyanDeci_SDT.pdf)
(2000).

## Behavior gates: capability, opportunity, motivation

The COM-B model treats capability, opportunity, and motivation as interacting
conditions for behavior. It gives the engine a simple rule: high desire alone
is insufficient. A player also needs the capability to act and a real social or
physical opportunity. Completed behavior can then change all three.

Source: Susan Michie, Maartje van Stralen, and Robert West, [The behaviour
change wheel: A new method for characterising and designing behaviour change
interventions](https://pmc.ncbi.nlm.nih.gov/articles/PMC3096582/) (2011).

## Desire is not intention, and intention is not control

Ajzen's Theory of Planned Behavior separates attitudes, social norms, perceived
control, intentions, and behavior. Preview Club does not implement the theory
as psychology; it uses the separation as an engineering discipline. A motive
may create an intention, but availability, assets, authority, and the product
still determine whether the intended action is possible.

Source: Icek Ajzen, [The theory of planned
behavior](https://www.sciencedirect.com/science/article/pii/074959789190020T/pdf)
(1991).

## Communication needs grounding

Clark and Brennan describe communication as coordinated activity built on
common ground. The practical consequence is that a channel append is not
enough. Preview Club must model whether the right addressee received,
understood, acknowledged, accepted, rejected, or asked to repair a message.

Source: Herbert Clark and Susan Brennan, [Grounding in
Communication](https://www.cs.cmu.edu/~illah/CLASSDOCS/Clark91.pdf) (1991).

## Internal intentions differ from social commitments

Singh distinguishes an agent's internal/psychological commitment from an
external/social commitment involving other agents. This maps directly to the
club: “I want to train” is internal; “I accept King's invitation” changes a
social commitment. Product execution should depend on the latter when other
people are involved.

Source: Munindar Singh, [A Conceptual Analysis of Commitments in Multiagent
Systems](https://www.csc2.ncsu.edu/faculty/mpsingh/papers/mas/commit.pdf)
(1996).

## A practical social-concepts toolbox

The theories above prevent a few category errors. The following design concepts
make it easier to write scenarios, explain a character's choice, and decide
what must become durable state. They are modelling tools, not claims about a
real player's psychology.

| Concept | Useful question | Minimal representation | Do not confuse it with |
| --- | --- | --- | --- |
| **Motive** | What makes this attractive to this actor now? | weighted, revisable reason such as mastery, belonging, rest, or rivalry | a promise or an action |
| **Role** | What standing and duties does this actor have in this setting? | team membership plus scoped permissions and duties | ownership of every teammate's decision |
| **Norm** | What is ordinarily expected here? | explicit club/team policy or learned expectation | an enforceable agreement |
| **Trust / reliability** | How much should an actor rely on another for this kind of coordination? | relationship value updated from evidenced follow-through | affection, affinity, or a guarantee |
| **Reciprocity** | Is there a reason to return a prior contribution or favour? | an optional, explainable social reason with a bounded history | an invisible debt that overrides consent |
| **Ritual** | What repeated interaction gives the group rhythm and recognition? | recurring invitation or event pattern, always cancellable | mandatory attendance |
| **Coordination** | What must several people align on before proceeding? | addressed proposal, replies, compatible constraints, and commitment | broadcasting a plan |
| **Reputation** | What safe, relevant history may influence a future decision? | bounded, attributable record of observed outcomes | a global personality score or private inference |

### Motives give colour; commitments create obligations

Motives let a simulation produce intelligible variation. A player may prefer a
training session because it supports competence, accept a casual match because
it supports relatedness, or decline both to preserve autonomy or recover from
fatigue. Those are reasons to *consider*, propose, or accept something; they
cannot silently schedule another person or write to the product.

Use a short explanation alongside a changing motive, for example: “King is
keen to train after losing the last match,” or “Maya declines this week to keep
her evening free.” The explanation should be traceable to scenario facts or
observed history and should remain revisable when new evidence arrives.

### Roles and norms make a group legible

A role answers “who may do what for this group?” A captain may initiate a
challenge, staff may publish a clinic, and a teammate may accept only for
themself. Role authority should be scoped to a specific act and should never
imply consent, attendance, payment, or endorsement by everyone else.

Norms provide the lighter-weight backdrop: a team might normally confirm by
Friday, thank a host, rotate partners, or avoid scheduling after 21:00. Model a
norm as an expectation that can be followed, explained, negotiated, or changed.
Promote it to a commitment only when named parties explicitly make it so.

### Trust, reciprocity, and reputation need evidence boundaries

These concepts make repeated play feel social without turning the model into a
hidden judgement system. Trust should be contextual (“reliable for confirming
early sessions”) and change only after an observable commitment outcome, such
as a timely confirmation, cancellation, or no-show evidence. It should not be
deduced from personality, protected attributes, or a single unrelated event.

Reciprocity is an invitation to respond in kind, not a debt collector. A player
may be more willing to host after being hosted, but can still decline. Likewise,
reputation should be minimal and purpose-bound: only retain the evidence needed
for a future coordination decision, identify its source, and permit correction
when the underlying observation changes.

### Rituals create continuity without coercion

Recurring open play, a monthly captain's challenge, and a post-match recap can
give a club a recognizable social rhythm. A good ritual offers a familiar
opportunity and a graceful absence path: invite, remind within policy, accept a
decline, and allow the pattern to lapse or evolve. It is a design prompt for
recognition and return, not evidence that someone wanted, attended, or enjoyed
the event.

### A compact coordination pattern

For any multi-person activity, make the social path visible:

```text
opportunity -> proposal to named people -> acknowledgement / clarification
            -> accept, reject, or counter -> accepted commitment
            -> permitted product action -> observed outcome -> relationship learning
```

Each stage has a distinct failure mode. A missing reply is not acceptance; an
accepted social plan is not proof that a court can be booked; a completed
booking is not proof of participation; and participation is not proof that the
experience improved a relationship. Keeping these distinctions makes stories
more believable and product writes safer.

### Scenario prompts

When adding a social scenario, answer these questions in order:

1. What motive, role, norm, or prior outcome makes this opportunity meaningful?
2. Who is entitled to propose it, and who must make their own decision?
3. What response would count as grounding, rejection, a counteroffer, or a
   request for clarification?
4. Which commitment, if any, exists before a product action is allowed?
5. What read-back or observation could fairly update trust, reputation, or a
   future motive—and what must remain unknown?

## Engineering synthesis

The engine uses the sources above only as a compact conceptual foundation:

```text
SDT-inspired motives
  + COM-B capability/opportunity gates
  + planned intention
  + grounded speech acts and social commitments
  + verified product evidence
```

This is deliberately smaller than a general cognitive architecture. It keeps
character interpretation at the high-level social layer and leaves URLs,
credentials, prices, times, venues, and writes to deterministic code.
