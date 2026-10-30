# Operations hand-off — 2026-10-30

This snapshot is a starting point for the next chat. Treat ignored local state
and current rendered evidence as more authoritative than this historical note.

## What was established

- The routine architecture is in place: Rally creates durable, named browser
  contracts; the dispatcher launches a single economical worker; the worker
  uses a fresh isolated visible browser journey and leaves sanitized local
  evidence.
- The coordinator remains responsible for login/bootstrap and for reading the
  evidence. The worker interprets only its named character's one playbook
  step.
- Delayed booking continuation was verified in rendered UI. After choosing a
  slot, the Continue control appeared at eight seconds, not at one or three
  seconds. The worker prompt now requires that settle wait.
- A research journey visibly booked a two-player training for Andy and Terry:
  Friday, October 30, 9:00–9:30 AM local product time, West 37th Manhattan,
  Open Pod, group of two. The rendered confirmation said “You're booked!” and
  showed the total. A local `booked_browser_observed` fact was reconciled for
  `intent:training:bogard-higashi:035`.
- This fact proves the rendered owner booking confirmation only. It does not
  prove an invitation link was delivered, Terry accepted, either player
  checked in, attendance, play, or a result.

## Natural next product path

The owner invitation path is the next normal social-engine step for
`intent:training:bogard-higashi:035`:

1. Run a bounded Rally tick and inspect the intent-specific work item.
2. If it queues `capture_share_invitation` for Andy, launch that exact newly
   queued worker contract (not the globally oldest unrelated contract).
3. The owner worker should use the rendered Invite Players flow and the
   controller's private invite capture. Never print or place the link in
   ordinary facts, evidence, or chat.
4. After the private local delivery fact, let the engine create Terry's
   redemption/acceptance work item. A rendered acceptance is required before
   recording that fact.
5. Arrival/check-in must remain a separate, time-windowed journey.

## Important implementation landmarks

Recent commits (already pushed at the time of this note):

- `19bc904` — hardened isolated Sim login handoff.
- `3e84029` — recovered check-in routing and worker launch reuse.
- `001426b` — live evening booking row mappings and snapshot groundwork.
- `33378e7` — visible booking review return control.
- `a79bbbb` / `5996d36` — delayed Continue handling and worker instruction.

Useful locations:

- `tools/sim-browser/controller.mjs` — allowlisted rendered UI driver.
- `tools/sim-browser/feature-map.json` — reviewed controls only.
- `tools/sim-browser/codex_worker_launcher.py` and
  `codex-worker-runner.py` — economical contract launch.
- `tools/sim-browser/booking-entry.mjs` and `login.mjs` — visible setup.
- `playbooks/sim-player-booking.md`, `sim-player-invitation.md`, and
  `sim-player-check-in.md` — product journeys.
- `state/worker-sessions/504e8e202f1c2355.json` — a prior private research
  session may remain; treat it as operationally sensitive and do not expose
  its fields. Prefer a fresh worker session for routine flow.

## Tests and health

The last focused suite passed 149 tests:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest \
  tests.test_worker_dispatch tests.test_journey_policy tests.test_sim_club
```

The public login-shell health monitor was reachable and unchanged at the last
observation. It remains a GET-only probe and proves neither an authenticated
journey nor a booking. The stale `training-arrival-follow-through` automation
was deliberately deleted on 2026-10-30; it should not be recreated for the
expired session.

## Guardrails worth retaining

- Do not use product APIs/integrations on behalf of Sims.
- Product effects require corresponding rendered confirmation.
- Keep one actor/session/contract at a time; do not share browser profiles.
- Treat an unreported worker or a stale contract as diagnostic state, not as
  proof of failure or success.
- Do not make repeated unknown UI attempts. Research, document, test, then
  operationalize.
