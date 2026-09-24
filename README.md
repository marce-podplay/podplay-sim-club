# PodPlay Sim Club

PodPlay Sim Club is a persistent fictional club whose characters will exercise
an approved PodPlay PR preview, talk to one another, try release features, and
leave inspectable evidence when something does not work. Preview Club is a
clearly marked group of simulated users inside a PR-specific copy of PingPod
staging, not a separate tenant.

The current implementation is the dependency-free **fake-world plus read-only
preview readiness milestone**. Direct calls are limited to
the explicitly allowlisted PR-preview API and Firebase authentication. Signup
may cause the preview application to create a Stripe test customer or send
normal staging email; the simulator does not call those services directly.

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
- a typed preview-adapter boundary around all product operations;
- explicit `fake`, `preview-readonly`, and `preview-write` configuration modes;
- exact-origin PR-preview allowlisting with shared-staging and production denial;
- cross-origin request and redirect rejection in the GET-only HTTP client;
- Firebase password/refresh authentication with a private local token cache;
- an origin-locked, GET-only preview inspector for tenant, identity, areas, and pods.
- an idempotent dry run and exact-confirmation signup path for three persistent actors.
- a GET-only actor, booking-limit, payment-state, waiver, and future-slot readiness report.

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

A season is the lifetime of one PR-specific preview database. Code can be
redeployed without starting a new season. Recreating the database from the
weekly PingPod staging snapshot does start one. Firebase identities live outside
that database and may persist across seasons; PodPlay user rows and roles must be
read back in each season.

## Tests

```console
./club test
```

## Preview target preflight and inspection

Target configuration can be validated without making a network request:

```console
preview_origin=https://podify-pr-5207-staging-main-service-example-uk.a.run.app
./club doctor \
  --mode preview-readonly \
  --preview-origin "$preview_origin" \
  --allow-preview-origin "$preview_origin"
```

The target-policy check passes only for a recognized, exactly allowlisted PR
preview. Supplying a URL alone cannot activate network access.

For the first read-only connection, create the ignored file
`secrets/preview-credentials.json` with mode `0600`:

```json
{
  "schemaVersion": 1,
  "mode": "preview-readonly",
  "previewOrigin": "https://podify-pr-0000-staging-main-service-example-uk.a.run.app",
  "allowedPreviewOrigins": [
    "https://podify-pr-0000-staging-main-service-example-uk.a.run.app"
  ],
  "firebaseApiKey": "the staging Firebase web API key",
  "admin": {
    "email": "admin@example.test",
    "password": "local-only"
  }
}
```

Then run:

```console
./club preview inspect
./club preview readiness
```

This command performs only `GET` requests under `/apis/v2/`, refuses redirects,
caps response sizes and area fan-out, and prints a small summary. ID and refresh
tokens are cached in ignored `secrets/preview-auth-cache.json` with mode `0600`;
passwords and tokens are never rendered or copied into simulator state.

`preview readiness` is also GET-only. It verifies each seeded actor, distinguishes
a real saved payment method from the always-present user link reference, reads
membership and booking limits, and searches pod-local dates from day +2 through
day +14 for a legal slot. Its sanitized result is saved in the ignored file
`state/preview-readiness.json`.

Plan the persistent Preview Club identities and selected low-activity pod:

```console
./club preview seed --dry-run
```

Applying the plan requires repeating the exact target origin:

```console
./club preview seed --apply --confirm-origin "$preview_origin"
```

The first seed creates only Sofia, Red Captain, and Blue Captain through the
normal signup API, verifies each Firebase login and `/users/current` response,
and stores passwords and stable UIDs only in ignored `0600` files. Alex maps to
the existing operator admin and Riley remains file-only. Roles, memberships,
credits, waivers, and bookings are separate later gates.

Configuration may alternatively be exported using the variable names in
`.env.example`. The application does not automatically load `.env` or another
repository's secret store.

The web-v2 checkout has Firebase and E2E configuration that can inform the
future authentication flow. Do not copy its complete `secrets.json`: it contains
many unrelated high-privilege credentials. The simulator will accept only the
minimum actor credentials through ignored local configuration, and no value is
written to world state, journals, issues, or rendered output.

A PodPlay admin such as Marcelo can later bootstrap the fictional users through
supported APIs in the approved preview. That will happen after read-only target,
tenant, and identity inspection succeeds; user creation is not part of this
milestone.

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

Credentials and the local token cache live below the always-ignored `secrets/`
directory.
Do not place tokens in actor identities, seed files, journals, or issues.

## Safety boundary

The GET-only network adapter, narrow identity writer, and target policy enforce:

- a recognized PodPlay PR-preview Cloud Run hostname;
- an exact configured origin allowlist;
- HTTPS without embedded credentials, custom ports, paths, queries, or fragments;
- a second exact-origin confirmation for future preview-write mode;
- rejection when a request or redirect crosses the approved origin.

Identity writes are limited to `POST /apis/v2/users`, require the exact target
origin to be repeated, and are followed by Firebase login and profile read-back.

Before booking, membership, role, credit, waiver, or settings writes are enabled,
they must additionally enforce:

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
