# Sim browser capability lab

This note defines how Club Lab discovers browser routines without turning the
social engine or the coordinator into an omniscient product operator.

## Separate roles

| Role | May do | Must not do |
| --- | --- | --- |
| Social engine | Form motives, agreements, and secret-free work offers. | Inspect or operate the product. |
| Sim browser journey | Use one named Sim's isolated rendered browser session and make that Sim's visible choices. | Use product APIs, infer outcomes, or reuse another Sim's session. |
| Dashboard observer | Project durable social facts and sanitized local evidence. | Read a Sim's private browser state or claim product outcomes. |
| Capability lab | Learn a bounded visual routine outside the engine. | Create social commitments or present an experiment as a Sim action. |
| Coordinator | Schedule the bounded work and review sanitized evidence. | Treat an experiment or a snapshot as a product outcome. |

The browser runner reports only a sanitized visible observation or an
allowlisted visible action. It does not expose credentials, cookies, page
storage, response bodies, raw private URLs, or opaque product identifiers.

## Observation is not neutral

Loading a page can trigger ordinary browser resource requests, and an action
can materially change product state. Every operation must therefore be
classified before it starts:

1. **Read-only observation** — a rendered accessibility snapshot or sanitized
   console/network evidence. This is evidence of what was rendered, not proof
   of booking, acceptance, attendance, or play.
2. **Sim action** — a named character uses an allowlisted visible control.
   Record the actor, intent, visible control, and result category. Do not infer
   later lifecycle facts.
3. **Capability experiment** — a separately labelled, bounded browser-lab
   session used to discover or verify a repeatable route. It is not social
   engine activity and must not become a character diary entry.

## Browser modes

The normal Sim mode is **headless isolated rendering**. Chrome still renders
the product and exposes its accessibility tree, screenshots, sanitized
console/network evidence, and allowlisted visible inputs through CDP, but it
does not create a desktop window that can receive the operator's keyboard or
pointer.

Use **headed supervised research** only when the operator explicitly wants to
watch, help diagnose, or manually participate in a capability experiment. A
headed session remains a separate temporary profile, but its visible window is
shared-desktop input and must never be mistaken for unattended Sim work.

Start a supervised session with `--headed`; omitting it always selects the
headless default. The session record retains the mode as local operational
metadata.

## Journey card and diary

The preferred live surface is a compact **journey card**, not a continuous
recording or an unfiltered reasoning transcript. It may be shown in Codex or
projected into the local dashboard only from safe local evidence.

Each card has at most four factual lines:

1. **Screen** — a redacted rendered page title or stable visible section.
2. **Observed** — the relevant rendered control, label, loading state, or
   exact error text.
3. **Next** — the narrowest permitted next step from the active playbook.
4. **Outcome** — a visible observation or an allowlisted action, never an
   inferred product result.

The card is stored as `journeyCard` in the local `evidence.json` alongside
the associated screenshot and sanitized console/network window. Later engine
or coordinator work reads this asset as the durable source for the recorded
screen facts; it does not reconstruct them from chat history.

Screenshots are opt-in evidence attachments, captured only at a material
state, an explicit user-requested research point, or an unexpected UI state.
Before a screenshot is surfaced, the runner must treat invitation links,
password/payment fields, bearer-style tokens, personal details, and opaque
product identifiers as sensitive. A sensitive screen gets a text-only card
or a redacted derivative, never the raw frame.

“Thinking aloud” is therefore represented by this short operational diary:
what screen was rendered, what fact mattered, and which playbook-bound action
was next. It is not hidden chain-of-thought, and it never upgrades an
observation into a booking, acceptance, attendance, play, or result claim.

## Capability-lab procedure

Use the lab for a narrowly defined question, for example “where does a player
add a payment method?” or “how does an owner recover an unretained invitation
link?”

1. State the hypothesis, preconditions, and stop condition.
2. Use a separate isolated browser session and label all evidence
   `experimental`.
3. Observe the rendered flow and use only real, visible controls. Capture
   sanitized console/network evidence only for unexpected UI, failures, or
   non-success responses.
4. Write the outcome as facts: labels seen, enabled/disabled controls,
   required timing, and error text. Do not retain secrets in screenshots,
   logs, dashboard facts, or social channels.
5. If the route is repeatable, add a playbook: preconditions, visible steps,
   expected states, recovery branches, and explicit non-claims.
6. Promote the routine only after review. A subsequent real Sim journey must
   still receive a normal social work item and make its own decision.

## Promotion rule

An experimental success never establishes a player action. The engine may use
the resulting playbook to offer a routine, but the eventual product action is
only evidenced when that named Sim performs it in a fresh isolated browser
session.

## Examples

- **Payment method**: lab discovers visible navigation and required fields;
  the playbook documents it; a Sim later decides whether to add a method.
- **Invitation recovery**: lab discovers `Upcoming → Manage booking → Invite
  Players`; the owner playbook records the route; a booking owner later sees
  the matching reservation before generating a current private handoff.

In both cases, a routine is knowledge—not authority.
