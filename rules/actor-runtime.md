# Actor runtime

An actor is one short-lived incarnation of a persistent fictional character.
It reconstructs identity from its immutable card, bounded memory, unread
messages, current clock, world projection, and scenario context.

An actor may speak, ask for help, select an offered intention, propose one
allowed action, and propose a memory update. It may not select arbitrary URLs,
tenants, pods, credentials, or feature flags and may not edit shared world state.

The six capability layers and their boundaries are defined in
[`kb/capability-model.md`](../kb/capability-model.md). Runtime rules implement
that model; they must not create a scenario-specific shortcut around it.

## Motives and offers

The deterministic social engine owns motives and eligibility. Desire to play
drifts with the clock and falls after a completed session. Static roster
affinity makes a teammate invitation legal; closeness is durable and rises only
after a shared completed event. A published event addressed to an available
player is an advertised opportunity. Curiosity comes from a relevant release
note and is consumed after one attempt. A blocked product path or unexplained
mismatch can advertise a question to Riley. An evidenced missed session can
advertise a fresh captain proposal, but the required teammate or opposing
captain must accept it again.

An actor receives only these legal advertisements and may accept, ask, or pass.
It never creates a booking through dialogue. An interaction-specific cooldown
prevents the same unanswered offer from repeating on every Rally tick without
blocking a distinct invitation, acceptance, or short training attempt.

When the club has neither a live opportunity nor a player or staff interaction,
Sofia may receive one quiet-club advertisement. Her legal response is a public
interest check addressed to enabled players. It can increase their desire to
play; it is not a promo code, an event publication, or a booking. Depending on
the legal offer, a captain may propose directly to a teammate/rival captain or
a player may ask Riley for help.

For a high-intensity rivalry, a captain also receives a direct challenge offer.
That route sends an unbooked invitation to the rival captain and keeps the
intent at `captain_invited` until the rival captain accepts. It never treats a
teammate or captain as accepted merely because they were named.

After a missed player-owned event, the elapsed event stays history. A fresh
`session_retry_proposal` creates a new social commitment with no copied product
artifacts. Only `session_retry_acceptance` makes the new intent eligible for
the ordinary training or team-signup adapter.

## Event-driven rounds

Rally keeps a durable rotating community queue. A normal tick processes the
next ten configured people, then persists the cursor after the final person it
visited. A message may trigger one follow-up batch of up to ten more people,
so a tick has a hard cap of two batches. A partial final batch reaches the end
of the queue and resets the cursor only for the next scheduled tick; it does
not wrap around within the same run. Every actor either selects one legal local
action or records a pass. If a batch produces no club message, the tick stops
and waits for the next scheduled run. These extra rounds are local-only;
preview writes remain a later lead action with the normal validation and
confirmation.

`state/actor-interpretations.json` records every selected turn as skipped or
actionable, with its round and compact reason.

The lead may pull a start forward to now or the next 15 minutes when a court
is free. Singles needs the player's agreement. Doubles needs both captains.
The actor does not choose that clock.

## Player-owned share invitations

After a player has booked and the product has read the booking back, the
booker may use `capture_share_invitation_link`. Opening PodPlay's share dialog
creates (or returns) an unlisted open invitation; the product response contains
the same-preview `redeemUrl` that its Copy button uses. The adapter may retain
that bearer URL in the ignored, mode-0600 private share-invitation store after
an explicit preview authorization. It validates the preview origin and never
writes the URL to a journal or shared channel; the local observatory may expose
the retained link only as a local operator aid. A later named recipient may open
the live handoff link and choose whether to accept; neither capture nor delivery
implies acceptance. If the booking ends before delivery, the owner reports that
missed window and asks Riley for a new session instead of reusing the link.

Riley is Preview Club staff, not a player and not the platform administrator.
Player requests outrank every other staff offer. Riley may answer a player or
propose a reschedule of an agreed match. Riley may not choose the start time.
