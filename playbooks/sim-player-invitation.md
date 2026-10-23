# Sim player invitation playbook

Use this playbook when a named Sim has a `capture_share_invitation` or
`redeem_share_invitation` browser journey. An invitation is a separate player
decision after booking; it is never implied by a team agreement.

## Owner routine

- Start only when the named booking owner has a current, visibly confirmed
  booking and the local intent says an invitation handoff is ready.
- In the rendered product, find the invitation/share control and inspect its
  recipient, event, and timing context before creating or sending anything.
- Record only the visible action and sanitized browser evidence. Do not put a
  share URL, key, token, or raw product identifier into the dashboard, diary,
  or social channel.
- If the product supplies only a generic link rather than a recipient picker,
  record that limitation. Creating the link is not delivery to a named Sim.
- After a player visibly copies a generic invitation link, retain it only in
  the owner's private secure-channel memory. When the owner chooses to send,
  deliver it immediately to the named recipients' private mailboxes and post a
  URL-free plain-language notification in the Club channel. No acknowledgement
  handshake is required before delivery.

### Recovering or re-creating an owner link

Use this recovery routine when the booking is visibly confirmed but its prior
invitation was not retained, has not been delivered, or the owner needs a new
current link:

1. In the owner's rendered Reservations/Upcoming view, open the card's
   **Manage booking** control. Follow the new same-origin reservation tab if
   the product opens one.
2. Confirm the rendered venue, start time, owner, and open-player count still
   match the social intent. Stop and record a visible mismatch rather than
   reusing a link from another booking.
3. Choose **Invite Players**. If the product asks who pays, the owner makes
   that visible choice; it is not inferred from the plan or a prior booking.
4. Choose **Send invite link** and wait for the rendered confirmation such as
   “Your invite link is ready.” Capture only that newly visible link into the
   owner's private secure-channel memory. Never log, screenshot, or post the
   link itself.
5. Deliver the retained current link to the intended recipients' private
   mailboxes immediately, then record a URL-free Club-channel note. If the
   link cannot be found, the button is absent, or the page shows an error,
   record the exact visible state and leave the booking unchanged.

Generating a replacement link does not imply that an earlier link was invalid,
that anyone accepted it, or that the session changed. It only refreshes the
owner's private handoff material after a visible owner action.

## Recipient routine

- Start only when this named recipient has a delivered private mailbox item and
  makes a distinct local decision to inspect it.
- Open the handoff in the recipient's fresh isolated browser. Check the
  rendered booking, venue, time, and acceptance control against the social
  agreement.
- Accept only after the recipient sees the expected session and chooses to do
  so. Otherwise record the visible mismatch, ask a social question, or pass.

## Boundary

An invitation link, a rendered invitation, and an acceptance click are
different facts. None proves arrival, check-in, play, attendance, a score, or
a result.
