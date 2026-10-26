# Sim Browser Controller

An isolated Chrome DevTools Protocol (CDP) observer for one Club Lab character
at a time. It is deliberately separate from the user's normal Chrome profile
and from product API integrations.

## What it does

- starts a temporary Chrome profile with a loopback-only CDP port;
- opens an exact, supplied origin in that profile;
- takes CDP page screenshots;
- records a short, sanitized browser-evidence window: console severity,
  JavaScript exceptions, and network method/route/status/timing;
- removes its temporary profile on `close`.

It never reads or writes cookies, headers, request bodies, response bodies,
local storage, passwords, or raw URLs into evidence. It has no command for
calling a product API or evaluating arbitrary page JavaScript. The only
available UI actions are allowlisted in `feature-map.json` and use visible
controls. Secure login fill reads one field through the existing local,
exact-origin handoff and enters it through CDP keyboard input into the focused
visible control. The credential never enters page JavaScript, logs,
screenshots, evidence, or durable storage.

## Sequential Sim workflow

1. Start one actor's isolated browser. Do not reuse a profile across actors.
2. Use the existing one-time `preview browser-credential-handoff` only to
   establish that actor's visible product session.
3. Start `observe` before the actor performs the visible UI step.
4. Store the PNG plus `evidence.json` as local memory only when an unexpected
   UI state, console warning/error, or non-success request occurs.
5. Sign out in the product UI, then `close` the isolated browser before the
   next actor.

## Rally Engine bridge

When Rally discovers a fresh, named player-owned product step, it writes a
secret-free work item in `state/browser-journeys.json`. The work item contains
only the named Sim, the local intent reference, and the next journey category.
It does not start Chrome, hold a session, or assert a product outcome by
itself. A queued task may later be executed by a routine isolated character
worker or by the coordinator in research mode; both follow the same playbook
and evidence contract.

The sequential browser runner claims that work item and records only a visible
observation or an allowlisted action. This separation prevents an unattended
engine tick from substituting an integration call for a Sim's browser journey.

### Economical worker bootstrap

`worker-bootstrap.py` is the bounded operations entrypoint for a worker whose
contract is already active. It reads its own secret-free packet, starts the
declared headless actor browser, captures a short sanitized observation, and
writes `state/worker-evidence/<opaque local reference>/worker-receipt.json`.
It times out each local helper within 5–60 seconds. It does not log in, choose
a booking control, or report a product outcome.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python tools/sim-browser/worker-bootstrap.py --worker economical:runner-1
```

The economical worker then follows the packet's playbook, records a visible
browser observation when warranted, and closes its exact worker contract.

Claim and record the local work item around the browser session:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m podplay_sim_club.cli browser-journey claim --job JOB_KEY --actor ACTOR_ID
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m podplay_sim_club.cli browser-journey observe --job JOB_KEY --actor ACTOR_ID --kind visible_observation
```

The permitted observation kinds are `visible_observation`,
`visible_action_completed`, `unexpected_ui`, and `browser_pass`. They are
deliberately descriptions of the rendered interaction, not claims that a
booking, invitation, payment, or check-in succeeded. The queue stores only
the category and timestamp; screenshots and sanitized console/network evidence
remain in the local browser-evidence folder.

Time-slot controls are intentionally not generic. A Sim must first observe the
current date, time, and availability label in the rendered booking screen;
only then may a reviewed, exact control be added to the allowlist.

### Booking entry branch

After visible login, use `booking-entry.mjs` to handle the two rendered
branches—region onboarding present or already complete—and wait for the West
37th area control. It stops before date, slot, pod, review, payment, or
booking confirmation:

```sh
node tools/sim-browser/booking-entry.mjs --session /tmp/kyo.json
```

### Visible password login

After starting the isolated browser and one-time local credential handoff, use
`login.mjs` for the rendered email → password → redirect sequence. It waits
for enabled controls and reports only whether the visible login settled; it
never logs fields or product values.

```sh
node tools/sim-browser/login.mjs --session /tmp/kyo.json --handoff-port 49152
```

For a booking work item, read
[`playbooks/sim-player-booking.md`](../../playbooks/sim-player-booking.md)
before choosing the next visible control. It gates progress on current social
facts and rendered product evidence; it does not grant booking authority.

Use [`sim-player-invitation.md`](../../playbooks/sim-player-invitation.md) for
owner or recipient invitation work, and
[`sim-player-check-in.md`](../../playbooks/sim-player-check-in.md) for an
arrival work item. Do not map or invoke a control from either routine until it
has been observed in the rendered product.

### Staff QR arrival handoff

Some open-access venues expose a customer QR instead of completing check-in
directly. In that mode, the customer presents the code and a staff scanner
performs the normal front-desk check-in. The QR value is an entry capability:
never put it in a message, evidence bundle, or durable state.

[`staff-qr-check-in.mjs`](staff-qr-check-in.mjs) is a narrow bridge for this
physical-scanner workflow. It takes an active player QR screen and an active
staff check-in screen, captures QR pixels only to a system-temporary file,
decodes them locally using macOS Vision, and sends the scanner's normal
keyboard-shaped payload to the rendered staff page. It prints no QR data and
deletes the temporary directory even after failure.

```sh
node tools/sim-browser/staff-qr-check-in.mjs \
  --player-session /tmp/player.json \
  --staff-session /tmp/staff.json
```

The helper only dispatches scanner input. A rendered staff success state is
still required before recording a check-in. Read
[`sim-staff-qr-check-in.md`](../../playbooks/sim-staff-qr-check-in.md) first.

## Commands

```sh
node tools/sim-browser/controller.mjs start --actor andy-bogard --origin https://preview.example --path /login?loginMode=password --session /tmp/andy.json
node tools/sim-browser/controller.mjs observe --session /tmp/andy.json --seconds 15 --out /tmp/andy-evidence
node tools/sim-browser/controller.mjs snapshot --session /tmp/andy.json
node tools/sim-browser/controller.mjs act --session /tmp/andy.json --feature login.email --handoff-port 49152
node tools/sim-browser/controller.mjs act --session /tmp/andy.json --feature login.continue
node tools/sim-browser/controller.mjs wait-feature --session /tmp/andy.json --feature login.email --seconds 20
node tools/sim-browser/controller.mjs wait-login-redirect --session /tmp/andy.json --seconds 30
node tools/sim-browser/controller.mjs capture-invite-link --session /tmp/andy.json --intent intent:training:example
node tools/sim-browser/controller.mjs close --session /tmp/andy.json
```

After a visible sign-in, use `wait-login-redirect` before classifying a
redirect screen as unexpected. It checks the rendered accessibility tree only,
for at most 60 seconds, and requires three consecutive non-redirect checks
before reporting a settled route. If the rendered Email control returns, it
reports `login_redirect_returned_to_login` rather than a successful session.
It performs no navigation or product call.

Use `wait-feature` after a visible navigation instead of guessing a fixed page
delay. It polls only an allowlisted role/name until the exact enabled control
is rendered, for at most 60 seconds.

When a recipient has already completed visible login, open a delivered private
invite in that same isolated session. This reads the private mailbox locally,
validates that the invite stays on the configured product origin, and navigates
only to its `/invites/...` path; the capability URL is never printed or copied
into browser evidence:

```sh
node tools/sim-browser/controller.mjs open-private-invite \
  --session /tmp/recipient.json --intent intent:training:example
```

The commands print only local paths and safe status. Treat session files as
private operational state because they contain a local debugging port and the
configured origin.

## Evidence contract

`evidence.json` intentionally contains only:

- timestamp, actor label, screenshot filename;
- console source/level/category and redacted text;
- exception text and line/column after redaction;
- HTTP method, a templated route, status, and duration.

It does **not** establish booking, invitation acceptance, check-in, play,
attendance, a score, or a regression. A human-readable Sim report must state
only what was visibly observed.

## UI driver boundary

`act` can only use feature names in `feature-map.json`; it accepts neither a
CSS selector nor a script. Each action dispatches a real mouse event to a
visible element. Add booking, invitation, rate-card, and check-in controls
only after their product UI has been mapped and reviewed. This prevents an
agent from silently substituting a raw API call for a character's journey.
