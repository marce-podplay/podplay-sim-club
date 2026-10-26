# Rally Engine

Rally Engine is the short-lived actor loop for Preview Club. It is the runtime,
not a character and not a product adapter.

## Run contract

- Default lifetime: two hours; cadence: every 20 minutes.
- Each tick reads only durable files first: tournament state, booking intents,
  shared club messages, compact actor memories, and the last read-only product
  snapshot.
- It selects only relevant actors. An actor with no new information records a
  compact pass and consumes no further turn.
- The engine may ask a short-lived actor model to interpret one role. The
  model can propose a message, an intent, a question, or a pass; the
  deterministic action boundary validates and persists it.
- A mutually accepted, fresh player agreement queues one named Sim's browser
  work item. The queue is sequential: check-in, invitation, then booking.
  An observed historical work item never blocks fresher work.
- Product actions are never an emergent model action or an engine integration.
  The named Sim must use the rendered product UI and preserve only sanitized
  visible evidence. A queued job is not a booking, invitation, check-in,
  attendance, play, or result.
- On completion, Rally Engine writes one immutable, secret-free summary under
  `state/runs/`. It includes tick timing, tournament state, fixture plan/event
  references, remote-write count, and the channel message kinds that explain
  the outcome.

## Two-Then-One tournament

1. Sofia announces interest in Bogard-Higashi versus Japan Team.
2. The first real product event uses the nearest real contiguous 60-minute
   slot at the approved venue. A singles player, or both captains of a doubles
   match, may instead take a free court that starts now or within the next 15
   minutes. The lead runtime owns that exception. When no earlier court is
   free, a later start does not invalidate the fixture.
3. Once the event's real end time has passed, Rally Engine asks Andy (or another
   named participant) who won. Check-in or elapsed time never implies a winner.
4. A reported winner unlocks Sofia's final announcement on the next tick. The
   following coordinator tick plans live W37 availability, creates the final's
   free Open Play, and persists its read-back before announcing success.

## Leader, worker, and browser operating agreement

The club has two complementary execution paths.

**Research mode** is led by the coordinator. The coordinator may interpret one
named Sim to discover or repair a workflow, using either a headed browser when
human observation helps or an isolated headless browser when it does not. This
mode produces reviewed playbook changes; it is not the routine way to advance
the club.

**Operations mode** uses a short-lived, lower-cost character worker. The
engine chooses one eligible queued task and the dispatcher gives the worker a
bounded contract: named actor, durable intent reference, playbook, allowed
evidence, and reporting schema. The worker completes at most that task, then
writes a compact diary and sanitized journey packet. It cannot select another
actor, reuse a browser profile, or make a conclusion unsupported by rendered
UI.

The coordinator remains the leader in both modes: it maintains playbooks,
reviews unexpected UI or evidence, reconciles durable state, and decides
whether a deviation needs research. A worker reports facts such as a rendered
control, missing control, or error; it does not report inferred attendance,
play, or product outcomes.

The two switches create four supported combinations:

| Research | Headless browser | Use |
| --- | --- | --- |
| off | on | Default routine operation: one isolated, low-cost worker task. |
| on | on | Private workflow discovery with screenshots and diagnostics. |
| on | off | Supervised visible investigation where human help is useful. |
| off | off | Exceptional supervised fallback when headless infrastructure is the issue. |

Every meaningful worker journey packet contains a before/after screenshot,
screen summary and visible controls, attempted action, sanitized console and
network summary, outcome category, and one diary line. Dashboard screenshots
remain gated on independently observed active live-play evidence.

Use Luna or an equivalent economical model for routine character work when the
dispatcher supports model selection. The durable worker contract keeps the
model interchangeable; no current Python tick silently launches a model.

## Skill decision

Do not create a reusable Codex skill yet. Capture one complete tournament run,
including a result report and preview reset/reconciliation. Then extract only
the stable operational rules into a `rally-engine` skill; keep
Two-Then-One as a scenario JSON file rather than a skill.
