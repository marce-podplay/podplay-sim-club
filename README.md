# PodPlay Sim Club

PodPlay Sim Club is a persistent fictional club whose characters will exercise
an approved PodPlay PR preview, talk to one another, try release features, and
leave inspectable evidence when something does not work. **Club Lab** is the
clearly marked group of simulated users inside a PR-specific copy of PingPod
staging, not a separate tenant.

### Vocabulary

- **Club Lab** is the fictional community: its players, staff, messages,
  research experiments, and seasons.
- **Preview environment** is the disposable, PR-specific technical deployment
  that Club Lab observes or uses through guarded adapters.
- **Dry run** means a command makes no product write. It does not name an
  environment or a social state.
- **Season** is Club Lab's bounded run against one Preview environment.

## Community identity and Club Lab contexts

People are not copied when a new Preview environment is created.  Sofia and
the other players have one durable fictional identity, one private Firebase
credential, and (when cross-club visits begin) one shared physical-time
calendar.  They may visit different Club Labs, but a product booking,
membership, credit balance, attendance record, and research observation stay
inside the club context where they happened.

**The Club Lab** is the current, single context, declared in
[`seed/club-context.json`](seed/club-context.json).  It can observe a new
Preview URL without becoming a new community.  Its employee Riley is a Club
Lab employee, not a global player identity.  A future **Club Lab Unified** or
**Club Experiment** must declare a different context key and a distinct staff
roster; it must never reuse Riley's staff memory or authority.

Private identity verification is also context-scoped: the same Firebase login
may be accepted by two Preview environments, but its app-side user record is
verified separately in each database.  A missing record is `unavailable`, not
authorization to create a replacement user.  Context-specific auth-token
caches prevent simultaneous future labs from overwriting each other's local
operational state.

When Club Lab staff need agreement on a complex experiment, use the local
`staff-planning` consultation channel. It addresses a named person with a
written, reversible plan and records `accept`, `pass`, or `request_changes`.
It is persuasion and shared planning—not an authority bypass or a Preview
configuration mechanism.

The current implementation is the dependency-free **durable local world plus
guarded PR-preview adapter milestone**. Direct calls are limited to
the explicitly allowlisted PR-preview API and Firebase authentication. Signup
may cause the preview application to create a Stripe test customer or send
normal staging email; the simulator does not call those services directly.

## Documentation map

- [Knowledge base](kb/README.md): capability boundaries, theoretical basis,
  state/evidence model, and evolution plan.
- [Runtime rules](rules/): executable actor, lead, and Rally Engine contracts.
- [Product research lab](product-research/README.md): read-only discovery tasks
  and evidence awaiting promotion into a runtime rule.
- [Roadmap](ROADMAP.md): current delivery sequence.
- [Arcade](arcade/README.md): isolated deterministic game/replay model.

## Capability facts and projections

New capability protocols append validated, secret-free facts and maintain
rebuildable projections under ignored local state. Validate or replay them with:

```console
./club state facts
./club state rebuild
```

The existing world predates this journal, so zero facts is a valid initial
result. Historical files remain compatibility projections until each capability
has an explicit migration; they are not backfilled as if newly observed.

New intent-driven training and team-challenge journeys also project one
canonical event plus one independent participation record per actor. A stable
occurrence key is assigned before slot planning, survives rescheduling, and
links booking, invitation, check-in, observation, and attested-result evidence.
The observatory prefers that complete projection for overlapping new events;
older events continue through compatibility readers without fabricated facts.

The self-asset capability projects typed actor beliefs from strictly sanitized
evidence. Its player-owned Preview adapter performs GET requests only and keeps
virtual credits, package entitlements, membership assignment, payment
readiness, understood benefits, and fictional expectations separate. Explicit
zero and an empty package list remain observations; missing fields, failed
requests, and incompatible responses remain unknown. Raw responses and product
identifiers are never persisted.

## What works now

- deterministic hourly-match state machine;
- deterministic need → proposal → agreement → booking-intent state machine;
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
- an idempotent dry run and exact-confirmation signup path for four persistent core actors, with opt-in team fixtures.
- a GET-only actor, booking-limit, payment-state, waiver, and future-slot readiness report.

Actor eligibility and product boundaries are deterministic. A bounded character
interpretation may select only `accept`, `ask`, or `pass` from legal offers; it
cannot choose product coordinates, credentials, outcomes, or writes.

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

To let the same local process run the bounded actor runtime every 30 minutes:

```console
./club serve --beat-every 30m --turns 10
```

The scheduler is disabled unless `--beat-every` is provided. It runs local
actor turns only: actors read their compact memory and shared channel, persist a
message/intent/pass, and make **zero preview writes**. Start with a manual tick
if you want immediate activity, then leave the observatory running:

```console
./club runtime tick --max-turns 10
./club serve --beat-every 30m --turns 10
```

The observatory polls its local files every two seconds and includes a bounded
chronological Club Message Stream. This is the simulator's parallel channel;
product email remains outside its control.

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

Run the first needs-driven interaction:

```console
./club needs run --turns 4
./club needs status
./club needs promote --turns 4
```

The scenario parameters live in `scenarios/needs-match.json`. Red's
desire-to-play pressure crosses its configured threshold, Andy proposes a match
and offers to book and invite Terry, Terry checks the overlapping availability
and agrees to accept, and the lead records one
durable intent in ignored local state. Repeating the command does not duplicate
messages or intents, and this local interaction performs no product write.

`needs promote` gives Sofia a distinct influence tool: she announces a Preview
Club priority session, Andy and Terry accept, and the lead records a second
intent with flexible availability for the nearest legal slot. It does not claim
that a real discount or product event exists; those need a supported PodPlay
promotion or event adapter. It is a controlled way to create early demand and
exercise the ordinary booking path.

Run a bounded actor-runtime tick when you want durable, low-cost character
coordination without allowing any preview mutations:

```console
./club runtime tick --max-turns 10
```

Each turn rotates through Sofia, the players, admin, CS, and lead. In the Open
Play journey, Sofia requests the event first and promotes it only after a
published event record exists. Actors write compact files under ignored
`state/actors/<actor>/memory.md` plus shared club messages. A turn with no new
information becomes a recorded `pass`, not an unbounded conversation. The first
scenario proposes a free, contiguous one-hour Open Play. Durations are positive
30-minute increments (30, 60, 90, ...); longer selections are adjacent grid
items, not unrelated bookings. Planning remains separate and read-only:

```console
./club preview plan-intent --intent intent:actor-runtime:season-001:free-hour-001
```

If the preview cannot supply a matching contiguous duration, the intent becomes
`preview_blocked` and a secret-free issue is saved under `state/issues/open/`.
It never silently downgrades to unrelated 30-minute bookings.

For an `owner_open_play` intent, create the event before the promotion:

```console
./club preview create-open-play --dry-run --intent intent:actor-runtime:season-001:free-hour-001
./club preview create-open-play --apply --confirm-origin "$PODPLAY_SIM_PREVIEW_ORIGIN" --intent intent:actor-runtime:season-001:free-hour-001
```

The apply command uses the exact-origin write gate, creates one free listed
published Open Play, and reads it back before changing the intent to
`event_created`. A later Sofia turn sends the promotion.

After that promotion, the named players use their own Firebase identities and
the ordinary event-signup endpoint—not an admin attendee shortcut:

```console
./club preview signup-open-play --dry-run --intent intent:actor-runtime:season-001:free-hour-001
./club preview signup-open-play --apply --confirm-origin "$PODPLAY_SIM_PREVIEW_ORIGIN" --intent intent:actor-runtime:season-001:free-hour-001
```

It reads each player's signup first, writes only a missing signup, then reads it
back. It refuses to run before both the owner event and the promotion exist.

## Parallel communication protocol

After an actual invitation is created and read back, the simulator mirrors it in
ignored local files: `state/notifications.jsonl` and the shared
`state/channels/club.jsonl`. Each deterministic `invite_sent` message contains
the product event and invitation IDs, read-back status, sender/recipient actor
IDs, and `email: unobserved`—the simulator never claims delivery of an email it
cannot inspect. If the product response explicitly exposes a same-preview-origin
invite link, it is stored as `inviteLink.url`; otherwise the record says
`not_exposed_by_product`. No link is guessed or manufactured.

For player-created share invitations, PodPlay returns `redeemUrl` while the
share dialog opens. `capture_share_invitation_link` is a player action that
validates that response. With explicit preview authorization, the adapter keeps
the bearer URL in ignored `state/share-invitations.json` with mode `0600`; it is
not copied into the shared channel, journals, or committed files. The local
observatory can show this retained link to the operator. The recipient still has
to independently accept the product invitation. A captured-but-unsent link whose
booking has ended becomes `EXPIRED_UNSENT`; King reports the missed window and
Riley records a fresh request rather than reusing it.

`characters/roster.json` separates stable technical actor IDs from personas.
Andy Bogard (`andy-bogard`) and Terry Bogard (`terry-bogard`) play for
Bogard-Higashi. When Andy wants to play, he books and invites Terry. Joe Higashi is their future
third teammate without a product identity yet. The Japan, Art of Fighting,
Women Fighters, and Korea teams are also ready as dormant rosters. A captain
chooses one teammate for a doubles booking; the third team member may appear as
a guest. Team links express preference rather than a forced booking, and the
same roster can later support a three-player sport.

Rivalries are a distinct motivation in the same roster. A doubles challenge
means a captain selects one teammate and challenges another captain's pair,
which can create a normal customer booking. A broader friendly rivalry can
escalate into Sofia's owner-created Open Play, where the remaining teammates
join as guests or independent attendees. This keeps the causal choice visible:
character rivalry proposes demand; it does not silently write a booking or
event.

The first executable rivalry definition is
`scenarios/andy-vs-kyo-doubles.json`: Andy + Terry challenge Kyo + Benimaru
for a 60-minute doubles session, with Joe and Goro eligible only as guests. To
prepare Kyo and Benimaru's product identities without expanding normal
readiness, use the opt-in seed path:

```console
./club preview seed --dry-run --team japan-team
./club preview seed --apply --confirm-origin "$PODPLAY_SIM_PREVIEW_ORIGIN" --team japan-team
```

The dry run is read-only. The apply command uses the existing exact-origin gate
and creates or reuses only Kyo and Benimaru alongside the original core actors.

The lead records the rivalry agreement before either customer booking or owner
event work. The team-signup adapter maps a captain-agreed four-player intent to
one player-owned booking and verified named participants; it remains guarded by
the same exact-origin and read-back boundaries.

```console
./club challenge --scenario andy-vs-kyo-doubles.json
```

It writes one idempotent `customer_team_signup` intent with all four named
players and a visible `owner_open_play` fallback. A separate preview command may
then plan/apply that socially authorized intent; local conversation alone still
performs zero remote writes.

A season is the lifetime of one PR-specific preview database. Code can be
redeployed without starting a new season. Recreating the database from the
weekly PingPod staging snapshot does start one. Firebase identities live outside
that database and may persist across seasons; PodPlay user rows and roles must be
read back in each season.

At the start of a new season, run the lead-gated identity bootstrap rather than
asking simulated people to sign up again or relying on inbox aliases:

```console
./club season-recovery plan
./club season-recovery request --actor riley
./club season-recovery approve --actor lead
./club season-recovery apply --confirm-origin "$preview_origin"
```

`plan` is GET-only. It stores a one-way, local profile-continuity fingerprint
and distinguishes a continuing target from the strong reset signal that every
previously present product profile is now absent. The plan prepares the next
steps but does not create Firebase identities, send email, or enable player
actions. `apply` remains a separate, exact-origin, lead-approved Preview
bootstrap: it reconciles only the declared product profiles and required staff
roles, then reads every profile back before browser journeys are eligible.

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
./club preview discover-assets --actor andy-bogard --actor terry-bogard
./club preview booking-preview
./club preview plan-intent
./club preview create-open-play --dry-run
./club preview signup-open-play --dry-run
./club preview fund --dry-run
```

This command performs only `GET` requests under `/apis/v2/`, refuses redirects,
caps response sizes and area fan-out, and prints a small summary. ID and refresh
tokens are cached in ignored `secrets/preview-auth-cache.json` with mode `0600`;
passwords and tokens are never rendered or copied into simulator state.

`preview readiness` is also GET-only. It verifies each seeded actor, distinguishes
a real saved payment method from the always-present user link reference, reads
membership and booking limits, and searches for the nearest legal slot at least
30 minutes from now, through day +14. The grid rate is not treated as the final checkout
total because locking fees and tax may be added later. Its sanitized result is
saved in the ignored file `state/preview-readiness.json`.

`preview discover-assets` authenticates only as the existing selected actors;
it never creates an identity and never uses the admin token. Each actor reads
their expanded current-user membership, payment summary, and owned packages.
The adapter immediately reduces those responses into immutable, secret-free
per-subject evidence under `state/evidence/` and belief facts/projections under
`state/journal/` and `state/projections/`. One endpoint failure produces an
unknown/error belief for that subject without discarding successful reads from
the others. Transaction history, coupon discovery, quote evaluation, checkout,
and all product writes are outside this command.

`research` adds a separate product-health boundary for Club Lab. The first
slice is a team-signup configuration probe: Sofia or Riley can request review,
but named players/staff must explicitly accept or pass before the existing
admin performs a bounded, GET-only configuration read-back. It never creates
or reconciles Preview/Firebase users. The local Observatory exposes only safe
probe status and sanitized result summaries; it intentionally no longer emits
Preview origins or event URLs.

Nearest-slot selection is a temporary operator-controlled bootstrap path. The
long-running simulation is intended to create bookings only after changing
character needs produce a proposal, conversation, and agreement. Scenario
inputs can make that interaction happen quickly by shaping need pressure and
overlapping availability, while the lead continues to enforce real preview
time and product constraints.

`preview booking-preview` refreshes that gate and then sends one tightly fixed
`type: PREVIEW` calculation for Andy Bogard. The server implementation returns
before the persistent `ORDER` branch. The local client rejects `ORDER`, multiple
items, passes, acting for another user, credit amounts above the bounded seed,
and payload extensions. It
records only a sanitized price/error summary in
`state/preview-booking-evaluation.json`.

`preview plan-intent` starts from the active character agreement rather than an
operator-created booking request. It filters real availability by the agreed
duration and venue-local time window, performs the same non-persisting booking
calculation, and stores the resulting slot on the durable intent. It never
creates a booking; remote execution remains behind the separate explicit write
gate.

To execute a ready character intent, revalidate it and use the normal explicit
write confirmation:

```console
./club preview book --dry-run --intent "intent:needs-match:season-001:andy-terry-001"
./club preview book --apply --intent "intent:needs-match:season-001:andy-terry-001" \
  --confirm-origin "$preview_origin"
```

The applied match is recorded in both the occurrence ledger and the intent,
then can be joined and checked in with the existing occurrence-targeted commands.

If that calculation requires payment, the seed defines a bounded `$25` virtual
credit balance for each player. Reconcile it first with `preview fund
--dry-run`. Applying credits requires the same exact-origin confirmation as
identity creation:

```console
./club preview fund --apply --confirm-origin "$preview_origin"
```

The admin API records the change as a `CUSTOMER_SERVICE` virtual-credit
transaction with the PP-7444 marker. The command only tops up a balance below
the desired amount, never debits an above-target balance, and verifies the
result through the actor's own read endpoint.

If PodPlay still requires an active payment method after credits cover the
total, inspect the plan and then attach Stripe's standard test Visa through the
normal PodPlay setup-intent flow:

```console
./club preview payment-method --dry-run
./club preview payment-method --apply \
  --confirm-origin "$preview_origin" \
  --stripe-env ../pingpod-web-v2/secrets.json
```

The command reads only `STRIPE_SECRET_KEY` (or its Cypress equivalent) from the
explicit file, refuses keys that do not start with `sk_test_`, uses the fixed
Stripe API origin and `pm_card_visa`, never renders the key or setup secret, and
verifies the resulting default booking payment method with each actor token.
It also records only non-secret identity and payment-element metadata in
`state/preview-payment-methods.json`. On a new preview season, readiness must
reconcile those actors; a card is never detached or deleted automatically.

Keep Stripe's test secret canonical in the web-v2 checkout. If a local reminder
is useful, copy `secrets/local-paths.example.json` to the ignored
`secrets/local-paths.json` and set only `stripeTestEnvPath`; do not copy the
secret value into this repository. Continue to pass that path explicitly to
`--stripe-env` so the payment write remains visible at invocation time.

### Bounded continuous Unified Booking research

The rate-card campaign runner represents ordinary simulated use, rather than a
fictional social-engine result. Its first explicit run may create **at most two**
future 30-minute weekday bookings: Andy/Terry off-peak and Kyo/Benimaru peak.
Each campaign has a fixed roster, one booking quota, a booker-covers invitation,
and teammate terms acknowledgement. Subsequent runs make direct owner read-backs
only; they create no further bookings and add a diary entry only when the exposed
booking state changes.

```console
./club research continuous-unified --apply --confirm-origin "$preview_origin"
```

Product booking IDs and invitation tokens live only in the ignored,
permission-restricted `secrets/unified-research-campaigns.json` ledger. Club
channels and journals contain only the band, duration, declared people, safe
price evidence, and uncertainty boundary. The runner never checks players in,
claims attendance or play, pays, cancels, awards a benefit, or calls a changed
status a regression.

After `booking-preview` reports ready, plan and explicitly create the first
booking:

```console
./club preview book --dry-run
./club preview book --apply --confirm-origin "$preview_origin"
./club preview join --dry-run
./club preview join --apply --confirm-origin "$preview_origin"
./club preview matches
./club preview match-status
./club preview check-in --dry-run
./club preview check-in --apply --confirm-origin "$preview_origin"
./club preview training-check-in --dry-run --intent intent:training:women-fighters-training-001 --actor mai
./club preview training-check-in --apply --confirm-origin "$preview_origin" --intent intent:training:women-fighters-training-001 --actor mai
```

`preview book` stores its occurrence plan before the one allowed `ORDER`, searches
for a matching Red-owned event before every mutation, and reads the event back
afterward. A retry reuses the existing event; the writer object refuses a second
request in the same run. `preview join` then reconciles one leader-paid Blue
invitation, runs a non-persisting acceptance preview, and accepts through Blue's
own token. `preview match-status` is a zero-write assertion that reports waiting,
ready-for-check-in, or pass. `preview check-in` and the per-player
`preview training-check-in` path use the Club arrival window: it opens one hour
before the scheduled start and closes only after the scheduled end, so a late
player can still check in. Neither command infers attendance; `--apply` records
only the selected player's product check-in and reads it back.

Sanitized progress lives in the ignored occurrence ledger
`state/preview-matches.json`. An existing `state/preview-manual-match.json` is
migrated automatically without deleting the legacy checkpoint. `preview
matches` lists every occurrence and marks the active one with `*`; `preview
select --occurrence KEY` changes that default. The `join`, `match-status`, and
`check-in` commands also accept `--occurrence KEY`, so retries cannot
accidentally act on an ambiguous match.

Running `preview match-status` also refreshes the ignored, sanitized
`state/preview-match-status.json` snapshot. The observatory renders only that
remote PR-preview state and the sanitized local ledger of scheduled preview
matches. Scenario and intent files remain the simulator's private planning
mechanism; the local fake world is not presented as product state.

Plan the persistent Club Lab identities and selected low-activity pod:

```console
./club preview seed --dry-run
```

Applying the plan requires repeating the exact target origin:

```console
./club preview seed --apply --confirm-origin "$preview_origin"
```

The first seed creates Sofia, Andy Bogard, Terry Bogard, and the additional
Riley Chen front-desk fixture through the normal signup API, verifies each
Firebase login and `/users/current` response, and stores passwords and stable
Firebase UIDs only in ignored `0600` files. A new preview database is a new
season: the same Firebase identity is reused, while the seed reads back and
replaces the per-season PodPlay user ID. Riley's `ADMIN` and `FRONT_DESK`
roles are reconciled and read back on every season. Alex maps to the existing
operator admin. Roles for other actors, memberships,
waivers, and bookings remain separate gates; player credits and test payment
methods are reconciled by their own explicitly confirmed commands.

Configuration may alternatively be exported using the variable names in
`.env.example`. The application does not automatically load `.env` or another
repository's secret store.

The web-v2 checkout has Firebase and E2E configuration that can inform the
future authentication flow. Do not copy its complete `secrets.json`: it contains
many unrelated high-privilege credentials. The simulator will accept only the
minimum actor credentials through ignored local configuration, and no value is
written to world state, journals, issues, or rendered output.

A PodPlay admin such as Marcelo bootstraps the fictional users and bounded test
credits through supported APIs in the approved preview. Stripe test methods use
the normal customer setup-intent flow and are read back through each actor.

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

## Preview warm-up: player booking plus Sim Club communication

Use the real preview player flow to learn product behavior; use the Sim Club
channel to make the fictional community's intent and evidence visible. Neither
substitutes for the other.

1. Start as a player, not an admin. In the preview, choose the region, area,
   pod experience, and a real available slot. Let the UI reach its order
   summary before submitting.
2. Record only secret-free facts locally: area/pod type, local slot, duration,
   displayed capacity, checkout outcome, and the read-back reservation state.
   Never record order IDs, emails, payment identifiers, passwords, or tokens.
3. Treat a player reservation as a court reservation, not proof that named
   Sim Club players agreed, attended, checked in, or played. Publish the
   corresponding club message and collect those facts through the normal
   fictional actor flow.
4. Use the player-facing invitation UI when it is available. It may require
   recipient email input and can offer split payment or host-paid invitations;
   do not use an admin event merely to bypass that flow.
5. If checkout is visually unresponsive, wait briefly, re-read the rendered
   state, and click once more before diagnosing it. A successful confirmation
   page is the authoritative evidence; a selected slot alone is not a booking.
6. Private actor credentials and auth caches may exist locally for named
   players. Reuse them to log in; never expose, copy into state, or recreate an
   identity just because a session is missing.
   Browser sessions are isolated: a successful API login or login in one
   automation browser does not authenticate another browser profile. Verify the
   player UI actually leaves `/login` and reaches `/book` before treating that
   browser session as usable.
7. Use `./club preview session-check` to test the seeded owner, players, and
   front desk (Sofia, Andy, Terry, and Riley). It creates a short-lived Firebase
   session for each identity, reads `/users/current`, then discards that token
   and writes a secret-free local result under `state/runs/`. It does not create,
   modify, or log out a browser profile; browser login remains a separate
   player-facing interaction. Add `--actor <id>` to narrow the check.

### Agent-browser player login

When a controlled agent browser needs a real player session, start a one-time
loopback handoff rather than printing or copying actor credentials:

```bash
./club preview browser-credential-handoff --actor king-captain
```

It binds only to `127.0.0.1`, lasts two minutes by default, permits requests
only from the exact configured PR Preview origin, and serves each of `email`
and `password` once. An agent-browser DevTools console can fetch one field at a
time and dispatch the normal input events on the visible Preview login form;
the console command contains a loopback port, actor id, and field name, never
the credential. The command never prints credentials or writes them to
simulator state. A successful login must still visibly reach the player
product surface (normally `/book`). Log out through the product UI before
changing actors. The complete secret-preserving console procedure is in
[`kb/agent-browser-player-login.md`](kb/agent-browser-player-login.md). This
command is an explicit browser-session handoff, not a Rally Engine capability
and not a Preview product write.

The September W37 probe demonstrated that Open Pod can expose shared-table
availability and a player booking can reach a zero-total preview order through
credits/sandbox payment. It did not make any participant acceptance, check-in,
or match result true.

## Safety boundary

The GET-only network adapter, narrow mutation writers, and target policy enforce:

- a recognized PodPlay PR-preview Cloud Run hostname;
- an exact configured origin allowlist;
- HTTPS without embedded credentials, custom ports, paths, queries, or fragments;
- a second exact-origin confirmation for future preview-write mode;
- rejection when a request or redirect crosses the approved origin.

Identity, credit, and payment-method seed writes use separate narrow clients,
require the exact target origin to be repeated, cap amounts and destinations,
and are followed by actor-token read-back. Stripe setup refuses live secrets and
is fixed to the test Visa token.

Booking and participation writes enforce bounded request shapes, one-action
writer budgets, and read-back reconciliation. Before membership, role, waiver,
or settings writes are enabled, they must additionally enforce:

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
