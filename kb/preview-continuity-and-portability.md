# Club Lab continuity and community portability

## Decision

Club Lab owns a durable **fictional community** and observes a disposable
**Preview-environment season**. It must never copy a staging database into this
repository or a laptop as a continuity mechanism. A staging restore is an
external product operation; when it happens, the club detects a new season,
archives the prior local projection, and re-establishes only the explicitly
allowed Preview prerequisites through supported APIs and read-backs.

The “Big Brother” display role can coordinate this work, but it is never an
authority bypass: it cannot accept for a character, carry a product permission
across a new Preview database, or turn a historical message into a current
booking right.

## Where each kind of state belongs

| State | Home | Portability | Rule |
|---|---|---|---|
| Character identity, actor ID, team/staff membership, baseline traits | versioned repo: `characters/roster.json` | portable and reviewed | Treat as an immutable manifest within a roster version. Change through an explicit roster migration, never a Preview read. |
| Scenarios, feature contracts, policies, schemas | versioned repo | portable and reviewed | Product-neutral source code/data. |
| Current fictional motives, relationships, skill/level progression, addressed communication, commitments | local append-only safe facts and rebuildable projections | portable only through an explicit sanitized export/import workflow | Preserve history; rebuild a fresh projection. Do not silently commit a laptop's mutable state. |
| Preview user IDs, Firebase IDs, roles, bookings, invitations, payment/benefit state, raw settings | ignored local sidecars and remote Preview | **not** portable | Scope to one Preview season. Read back again after a reset. |
| Credentials, tokens, email addresses, private URLs | ignored `0600` local files only | never portable | Reject from facts, issues, dashboard, and exports. |

This gives Japan Team, the Street Fighter teams, Sofia, Riley, and other
fictional staff permanent identities in the repository without claiming that
their corresponding Preview product accounts, entitlements, or roles are
permanent.

## Restart and staging-restore protocol

| Situation | Preserve | Invalidate / re-read | Automatic action |
|---|---|---|---|
| Local Observatory process restart | all local safe state | nothing merely because the process restarted | reopen/rebuild local projections; no Preview write |
| Code redeploy against same Preview database | community history and current projection | only observations past their normal TTL | retain the current season |
| Preview database recreated or restored from staging | roster, safe journal, historical communications, fictional level progression | every product ID, role read-back, booking/invitation, entitlement, quote, and product observation | archive the prior season; create a new season; require fresh identity/role/configuration read-backs |
| Different PR/Preview origin | same local fictional community only after an explicit operator choice | all Preview-scoped facts | start a separate season; never merge product facts by matching labels |

The existing fake adapter already demonstrates the first three properties:
season rollover archives `world.json`, preserves actor journals, and reseeds
only the fake product state. The real Preview flow must use the same semantic
boundary—not a database dump.

## Fact admission, compatibility, and freshness

Facts are append-only audit history. “Discard” means *exclude from a current
projection or mark stale*, never erase a valid historical statement.

Every new product-derived fact should carry these safe scope fields:

```json
{
  "rosterVersion": 1,
  "communityEpoch": "local-community-1",
  "previewSeason": "season-004",
  "targetFingerprint": "pr-7200:contract-v1",
  "observedAt": "...",
  "expiresAt": "..."
}
```

`targetFingerprint` is an opaque non-secret label; never use an origin, raw
tenant ID, product ID, or URL as a scope key.

Projection rules:

1. Reject malformed or secret-shaped facts at admission.
2. Keep only facts whose roster version and community epoch are compatible with
   the loaded roster. Unknown versions become historical/unprojected until a
   migration is supplied.
3. Never use a Preview-scoped fact from another season or fingerprint to grant
   present product authority. Surface it as history with `stale` scope.
4. Mark an observation stale after its explicit `expiresAt`; no missing value
   becomes zero or false.
5. Expire a proposal, quote approval, invitation, or booking plan according to
   its own commitment/window rule. A new attempt needs a fresh addressed
   proposal and acceptance.
6. Keep communication history, but only an unexpired addressed request can
   produce a next action. Delivery and acknowledgement remain distinct from
   agreement.
7. A single failed journey creates an `unknown` observation or issue candidate;
   a regression requires a compatible passing baseline or repeated controlled
   probes.

## Explicit portability workflow

The `continuity` command implements the first deliberately narrow bundle:

```console
./club continuity export --output community-portable.json
./club continuity inspect --input community-portable.json
./club continuity import --input community-portable.json
./club continuity import --input community-portable.json --replace-progression
```

It contains a roster fingerprint, safe local social facts, and the fictional
motive progression for rostered characters. Import validates the fingerprint,
merges facts idempotently by fact ID, and rebuilds projections. It excludes all
Preview-derived facts and does not restore a Preview booking, role, credential,
or raw reference. Motives stay local unless `--replace-progression` is explicit.
Conflicting mutable laptop histories should be reviewed as separate branches of
community history rather than auto-merged.

For a new Preview season, restore the repository manifest, optionally import a
sanitized community bundle, then run identity/configuration discovery again.
No staging database export is needed or authorized.
