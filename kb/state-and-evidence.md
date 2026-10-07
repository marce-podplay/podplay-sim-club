# State and evidence model

## Critical assessment of the current state

The current files were effective for proving individual journeys, but they are
not yet a coherent community model.

### What is working

- `characters/roster.json` is a useful static source for people, teams,
  captains, affinities, and rivalries.
- append-only `state/channels/club.jsonl` preserves an inspectable community
  history.
- `state/booking-intents.json` keeps local social intent separate from Preview
  writes.
- private ignored registries correctly isolate credentials, product IDs,
  payment methods, and invitation bearer links.
- read-back observations avoid inventing acceptance, attendance, or winners.

### What needs correction

- `world.py` has a small hardcoded actor world that can diverge from the roster.
  The roster should be the canonical identity/role/team source.
- `state/social-engine.json` mixes current meters, processed-message markers,
  cooldowns, offers, opportunities, and migrations. These have different
  lifecycles and should become projections over facts.
- club messages use many ad-hoc `kind` values but do not consistently carry a
  `conversationId`, `speechAct`, `inReplyTo`, `commitmentId`, or grounding
  status. This makes “said,” “understood,” and “agreed” hard to distinguish.
- booking intents currently combine social agreement, planning, product
  execution, participation, result requests, and outcome. Their statuses are
  consequently journey-specific and difficult to compare.
- event data is duplicated across booking intents, observed bookings, match
  ledgers, tournament state, and event observations. Duplication has already
  made participant data vulnerable to being overwritten by a later projection.
- actor Markdown memory is useful narrative context but should not be the
  machine-readable source for assets, commitments, or product evidence.
- schemas have versions, but there is no central schema registry or explicit
  migration path for the community model.

## Target contracts

Do not replace all current files at once. Move toward five projections fed by
an append-only, secret-free fact journal.

### Actor projection

```json
{
  "actorId": "king-captain",
  "motives": {"play": 82, "mastery": 70, "competition": 55, "frustration": 10},
  "beliefRefs": ["belief:king:membership", "belief:king:passes"],
  "relationshipRefs": ["relationship:king:mai"],
  "activeIntentionRefs": ["intention:training:123"]
}
```

### Belief and asset projection

```json
{
  "id": "belief:king:passes",
  "actorId": "king-captain",
  "subject": "pass_balance",
  "status": "observed",
  "safeValue": {"available": 2, "unit": "play_pass"},
  "sourceEvidenceId": "evidence:account-read:123",
  "observedAt": "...",
  "expiresAt": "..."
}
```

Missing values are `unknown`, not zero. Safe summaries may be shown in the
dashboard; secrets and stable product identifiers may not.

### Conversation and commitment projection

```json
{
  "conversationId": "conversation:training:123",
  "messages": ["message:proposal", "message:acceptance"],
  "commitment": {
    "id": "commitment:training:123",
    "debtor": "mai",
    "creditor": "king-captain",
    "content": "join proposed training",
    "status": "accepted"
  }
}
```

A message can be delivered and acknowledged without creating this acceptance.

### Event projection

Use one stable `occurrenceKey` and keep the event lifecycle separate from each
participant:

```text
intent -> planned -> booked/published -> invitation_open
       -> check_in_open -> in_progress -> ended -> observed
       -> result_requested -> completed | missed | inconclusive
```

An event may be `ended` while its outcome is still `inconclusive`.

### Participation projection

One record per actor and occurrence:

```text
addressed -> invited -> accepted | declined | expired
accepted -> checked_in | no_show
checked_in -> attendance_observed
```

Guest, teammate, captain, owner, reporter, and winner are roles or evidence,
not substitutes for participation states.

### Evidence and issue projection

An evidence record captures the sanitized result of one deterministic probe.
An issue links expected and observed contracts. A complaint is a social message
that may reference an issue; it is not itself proof of a defect.

## Storage direction

Keep JSON and JSONL. XML would add verbosity and query tooling, but it would not
solve the real problem: mixed concepts and non-canonical projections. Typed
records, indexes, and explicit schema migrations provide the token and scale
benefits without rewriting every adapter.

Recommended incremental layout:

```text
state/journal/facts.jsonl             append-only safe facts
state/projections/actors.json         rebuildable current motives/intentions
state/projections/beliefs.json        safe self-knowledge
state/projections/commitments.json    grounded social obligations
state/projections/events.json         event lifecycle
state/projections/participation.json  per-actor participation
state/issues/open/                    evidence-backed mismatches
```

The current files can remain compatibility projections until callers migrate.

