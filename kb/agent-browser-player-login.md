# Agent-browser Preview player login

## Purpose

This procedure creates a real player-facing browser session using an existing
Preview Club identity. It is for manual product research and browser adapters,
not the Rally Engine. It neither creates users nor writes product data beyond
the authentication session itself.

## Secure handoff

Run this in one terminal, using an existing actor only:

```bash
./club preview browser-credential-handoff --actor <actor-id>
```

The process prints an ephemeral loopback port. It binds only to `127.0.0.1`,
expires after 120 seconds, accepts a request only when its `Origin` exactly
matches the configured PR Preview origin, and returns `email` and `password`
once each. It prints neither field and does not write either field to simulator
state, evidence, chat, dashboard, shell history, or logs.

On the Preview login page, use that page's DevTools **Console**. Substitute the
printed port, actor id, requested field, and target control id in this snippet:

```js
(async () => {
  const response = await fetch(
    'http://127.0.0.1:<port>/v1/credential?actor=<actor-id>&field=<field>'
  )
  if (!response.ok) throw new Error('Preview Club handoff failed')
  const { value } = await response.json()
  const input = document.querySelector('#<control-id>')
  const setValue = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype, 'value'
  ).set
  setValue.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
  input.dispatchEvent(new Event('change', { bubbles: true }))
  console.log('preview-club-login-field-filled')
})()
```

Use `email` with `#loginEmail`, then `password` with `#loginPassword`. The
credential stays in the browser execution context; do not evaluate, log,
copy, screenshot, or return its value. Chrome may ask once to let the Preview
origin access the temporary loopback service. That permission must be approved
by the user at action time because it grants the website local-service access.

After setting the email, use the visible **Continue** button. After setting the
password, use the visible **Sign in** button. Verify a successful session by
visibly reaching `/book` or another authenticated player surface; a form fill
or Firebase API token alone is not enough.

## Changing actors

1. Sign out through the visible Preview product UI.
2. Confirm the browser returns to `/login`.
3. Start a new one-time handoff for the next existing actor.
4. Repeat the visible login and verification sequence.

Do not reuse a handoff, actor session, browser profile, Firebase token, or
password across actors. If the handoff expires, start a new one; never print
the registry entry as a workaround.

## Live verification

On 2026-10-06, this procedure was exercised with an existing Preview player:
the visible login flow reached the authenticated W37 booking surface, the
account menu rendered, and the visible **Log out** control returned the browser
to `/login`. No booking, invitation, payment, or account creation was part of
that verification. Re-run the same proof when a Preview season or login UI is
replaced; do not assume an API token proves browser-session behavior.
