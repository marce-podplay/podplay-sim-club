# Lead runtime

The lead owns the shared world, clock, scenario cursor, preview adapter, action
validation, assertions, journals, and issue identity.

Actors propose speech, intent, and at most one allowed action. The lead validates
the proposal and writes all shared state. Model prose is never HTTP evidence and
never determines assertion success.

The lead must reconcile remote state before retrying a mutation. Production and
shared staging are always outside the target boundary.

Fake mode is the default. A remote target must be a recognized PR-preview Cloud
Run origin and appear in the exact-origin allowlist. Read-only and write modes
remain distinct, and write mode requires the exact target origin to be repeated
as local confirmation. Redirects may not cross that origin.

## Earlier court

When a court is free, the lead may move a match to a start at the observed
instant or within the next 15 minutes. A singles match needs the player to
have agreed. A doubles match needs both captains to have agreed to the earlier
time. Teammates and guests do not have to agree again, and doubles still uses
one available table. The ordinary 30-minute safety lead stays in force for
every other search.

The earlier court counts only when the product session is available, still has
a table, and its start is at or after now and no later than 15 minutes after
now. If no such court exists, the lead keeps the nearest ordinary slot. It
does not invent a start time.

The move replaces the start on that same intent. It does not create a second
booking. A product write still requires exact-origin confirmation and a
read-back. An event that is already created keeps its start until the lead
can move that same event through a verified write.

## Club staff

Riley is the persistent front desk on the King of Fighters roster, with
`ADMIN` and `FRONT_DESK`. Sofia promotes events. Alex keeps platform
administration.

A reschedule starts as a club-channel message from the singles player or from
both doubles captains. The social engine offers that request to Riley before
every other offer. Riley proposes one move, or replies why it cannot happen.
The lead checks the agreement and the free court, then moves that same event
and reads it back. Riley tells the players the result. No second booking is
created.

## Quiet club

When there is no active event, player action, or front-desk request, Sofia may
advertise an interest check on a durable cooldown. This local message can raise
the addressed players' desire to play, but it is not a product promotion or an
event. A named player must ask Riley for a new session before the lead plans a
slot, and Sofia must still promote a planned event before any preview write.
