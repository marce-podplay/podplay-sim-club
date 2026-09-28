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
- The lead may ask a short-lived actor model to interpret one role. The model
  can propose a message, an intent, a question, or a pass; the deterministic
  action boundary validates and persists it.
- Preview writes are never an emergent model action. They need an existing,
  explicit write gate and current user authorization.
- On completion, Rally Engine writes one immutable, secret-free summary under
  `state/runs/`. It includes tick timing, tournament state, fixture plan/event
  references, remote-write count, and the channel message kinds that explain
  the outcome.

## Two-Then-One tournament

1. Sofia announces interest in Bogard-Higashi versus Japan Team.
2. The first real product event is created only if it starts within 30 minutes;
   otherwise the intent stays blocked and the club channel explains why.
3. Once the event's real end time has passed, Rally Engine asks Andy (or another
   named participant) who won. Check-in or elapsed time never implies a winner.
4. A reported winner unlocks Sofia's announcement and planning of the final
   against Women Fighters Team.

## Model choice

Use Luna for the ephemeral role interpretation when the scheduler supports a
per-run model selection. The current thread heartbeat does not expose that
selection, so it follows this same contract but cannot honestly claim a Luna
pin. The durable files make a later dedicated Luna runner interchangeable.

## Skill decision

Do not create a reusable Codex skill yet. Capture one complete tournament run,
including a result report and preview reset/reconciliation. Then extract only
the stable operational rules into a `rally-engine` skill; keep
Two-Then-One as a scenario JSON file rather than a skill.
