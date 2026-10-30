# Lead and dispatcher operating model

This is the operating agreement for the next coordinator chat. It is intentionally
portable: the coordinator may be Codex, Cursor, or another capable operator, but
the evidence and authority boundaries stay the same.

## Roles

### Lead / dispatcher

The lead is the long-lived reviewer, investigator, and dispatcher. It does not
pretend to be every player on every routine turn. Its responsibilities are to:

- read the secret-free social facts, diaries, contracts, and receipts;
- run bounded Rally ticks to form or retire social agreements;
- decide whether a new product journey is warranted;
- arrange a fresh isolated visible login for the named Sim;
- dispatch a low-cost worker with one durable, player-specific contract;
- audit the returned local evidence and explain only what it proves;
- enter research mode for a novel UI, an ambiguous failure, or a missing
  playbook control; and
- improve a playbook or feature map only after rendered observation.

The lead is not an unattended product integration. It must not replace a
Sim's browser action with an API call or infer success from elapsed time,
engine state, or a worker exit code.

### Social engine

The Python engine owns social state: motives, messages, agreements, intent
lifecycle, staff planning, local facts, and work-item eligibility. It runs in
bounded turns and emits a browser work item only when a named Sim has a legal
next step. It does not launch Chrome, retain a logged-in session, or claim a
product outcome.

### Economical Sim worker

An economical worker (normally a small model) owns exactly one named,
short-lived contract. After the coordinator-established isolated login, it
follows the referenced playbook using the allowed rendered controls, writes a
compact receipt/evidence pointer, and closes the contract. It may report an
unexpected UI or stop safely; it may not invent a control, use a product API,
or decide broader club policy.

### Research-mode operator

Research mode is the lead acting as one character for a narrow experiment.
Use it to learn a UI path, confirm timing, or repair a playbook. Keep it
separate from the ordinary engine: record the rendered evidence and reconcile
only the fact that evidence supports. Promote the discovered procedure to the
worker playbook before asking cheap workers to repeat it.

## The ordinary loop

```text
Rally tick -> social agreement -> player browser work item
    -> isolated visible login -> one worker contract -> rendered evidence
    -> local fact / diary / receipt -> next Rally tick
```

At each arrow, keep the distinctions explicit:

- an intention is not an agreement;
- an agreement is not a booking;
- a booking is not an invitation delivered or accepted;
- an accepted invitation is not check-in;
- a check-in is not attendance, play, or a result.

## Research and timing policy

The preview UI may update after a click. A missing next control is not enough
to classify a blocker. Wait for the mapped control to render first; when the
slot-to-review transition is involved, wait **at least eight seconds** before
declaring the Continue control absent. This rule came from a rendered
experiment: the control was absent at one and three seconds, then visibly
enabled at eight seconds.

Prefer `wait-feature` for known controls. Keep the eight-second settle rule
visible in code and in the booking worker prompt until performance work proves
a shorter rule is reliable.

## Browser isolation and modes

There are two independent switches:

| Research mode | Browser mode | Meaning |
| --- | --- | --- |
| on | visible | Lead learns a new path while observing the Sim journey. |
| on | headless/isolated | Lead investigates without disturbing the user's desktop. |
| off | visible | Economical worker follows a proven playbook; useful when a human is helping. |
| off | headless/isolated | Default routine operation: cheap, isolated, evidence-backed. |

Never use the user's normal Chrome profile. Each Sim uses a temporary isolated
profile and a fresh session. The one-time local credential handoff belongs to
the coordinator/bootstrap, not to the worker prompt or its durable receipt.

## Evidence and privacy

Browser screenshots and `evidence.json` are local journey assets. They may
support claims about rendered text or controls, but never silently prove a
business outcome. Store invite capabilities only in the private local channel;
do not print them in chat, facts, or screenshots. QR values and credentials are
also capabilities and must never enter evidence or messages.

Use facts for externally visible confirmations, receipts for a worker's local
completion, and diaries for the Sim's compact social interpretation. A worker
exit code is diagnostic only.

Bounded dispatcher assignments are recorded one JSON line at a time in ignored
`state/agent-attempts/YYYY-MM-DD.jsonl`. The field list, including
`authorization`, is [agent attempt receipts](agent-attempt-receipts.md).

## How to handle blockers

1. Inspect the existing contract, local journey state, diary, and sanitized
   browser evidence.
2. Distinguish a stale agreement, a missing social prerequisite, a login
   failure, a delayed UI, and a genuine product/UI mismatch.
3. For an unfamiliar product state, make one narrow research run as the named
   Sim; do not repeat unknown product actions blindly.
4. Capture only rendered evidence, then add the smallest reviewed feature-map
   entry or playbook instruction needed.
5. Test the repaired path in research mode once. Only then return it to the
   economical operational worker.

## Dispatcher reconstruction checklist

In a fresh chat, start by reading:

1. `README.md` and `kb/README.md`;
2. `tools/sim-browser/README.md` and the applicable playbook under
   `playbooks/`;
3. this document and `kb/current-operations-handoff-2026-10-30.md`;
4. ignored local state: `state/browser-journeys.json`,
   `state/worker-contracts.json`, `state/worker-evidence/`,
   `state/agent-attempts/`, actor diaries, and any active-session projection; and
5. the latest `git log --oneline` and `git status`.

Run a bounded engine tick only when the user requests progression. Launch only
one fresh worker for a newly queued contract, then read its fact/receipt before
launching another. Do not revive stale, expired, or already observed browser
journeys without a new social agreement.
