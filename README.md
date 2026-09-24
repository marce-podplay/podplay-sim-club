# PodPlay Sim Club

PodPlay Sim Club is a persistent fictional tenant whose characters will exercise
an approved PodPlay PR preview, talk to one another, try release features, and
leave inspectable evidence when something does not work.

The current implementation is the dependency-free **fake-world milestone**. It
does not contact PodPlay, Firebase, Reflag, shared staging, or production.

## What works now

- deterministic hourly-match state machine;
- separate fake-preview product state and simulator memory;
- idempotent booking, invitation, acceptance, and check-in operations;
- crash injection support for commit-before-checkpoint testing;
- atomic `world.json` checkpoints and append-only JSONL journals;
- automatic season transition and reseeding after a fake preview reset;
- root commands for status, live watch, beats, health checks, and reset demos;
- sparse ASCII world view;
- read-only local web observatory;
- dependency-free `unittest` suite.

Actors are deterministic fixtures in this milestone. A later milestone will
replace one actor turn at a time with a short-lived model invocation while
keeping the same validated action boundary.

## Start

Python 3.9 or newer is sufficient. No package installation is required.

```console
./club doctor
./club status
./club run --turns 10
./club watch
```

Start the local webpage:

```console
./club serve
```

Then open <http://127.0.0.1:8787>.

To let the same local process run a beat every 30 minutes:

```console
./club serve --beat-every 30m --turns 10
```

The scheduler is disabled unless `--beat-every` is provided.

## Deterministic demonstrations

Run a fixed hour twice. The second run creates no duplicate booking:

```console
./club run --now 2026-09-23T15:05:00Z --turns 10
./club run --now 2026-09-23T15:30:00Z --turns 10
```

Simulate a fresh preview database while preserving actor journals:

```console
./club demo-reset --confirm
./club status
```

The second command observes the missing seed sentinel, archives the old world,
rehydrates the fake preview, and starts a new season.

## Tests

```console
./club test
```

## Local state

Generated state is written below `state/` and ignored by Git:

```text
state/
  world.json
  fake-preview.json
  writer.lock
  actors/*/journal.jsonl
  channels/*.jsonl
  runs/*/{events,assertions}.jsonl
  seasons/*/world-final.json
  issues/open/*.json
```

The fake preview represents product state. `world.json` is the simulator's
projection. The two are deliberately separate so a process can die after a
remote commit and reconcile on restart.

Credentials will eventually live below the always-ignored `secrets/` directory.
Do not place tokens in actor identities, seed files, journals, or issues.

## Safety boundary

No network adapter exists yet. Before a real adapter is enabled it must enforce:

- an explicit PR-preview allowlist;
- hard denials for production and shared staging;
- Bearer-token handling outside committed files;
- bounded methods, endpoints, writes, retries, and timeouts;
- read-back reconciliation before retrying any mutation;
- sanitized evidence and rendered views.

## Design source

The complete design and implementation handoff live in the adjacent knowledge
base repository:

```text
../marcelo-knowledge-base/podplay-sim-club/
```
