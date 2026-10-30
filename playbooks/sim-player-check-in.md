# Sim player arrival and check-in playbook

Use this playbook when a named Sim has a `check_in_training` browser journey.
It is an arrival routine for a booking the Sim can already see in the product.

## Preconditions

- The work item names this participant and a current booking.
- The social state says this player accepted the session, and the visible
  product screen shows the expected venue and arrival window.
- The Sim is in a fresh isolated browser; do not reuse another player's
  session or derive a check-in from a reservation record.

## Visible routine

1. If a first-use **Select Region** dialog is rendered, choose the Sim's
   expected region and use its rendered **Confirm** control. Re-observe the
   product before continuing. This normal preference selection is not a
   check-in and must not be inferred from a blank or loading page.
2. Open the **Reservations list** in the product UI and locate the current
   reservation there. Do not begin from a reservation-detail page: the
   rendered check-in control can be present there without opening the
   front-desk handoff.
   The tab may include a notification suffix (for example, “Reservations New
   notifications”); choose that rendered Reservations control rather than
   requiring an exact text match.
3. Compare the rendered session with the local agreement: people, venue,
   timing, and the available arrival control.
4. Use the visible check-in control only if it is present and the Sim elects
   to arrive. A rendered QR dialog saying to show it at the front desk is an
   arrival handoff, not a completed check-in; keep the code ephemeral and use
   the staff QR playbook. Capture a bounded screenshot plus sanitized
   console/network evidence after the rendered state changes.
5. Record the observation as check-in evidence only. Wait for independently
   observed `PLAYING` before the Club Floor places the Sim in a pod.

## Stops

- Missing reservation, absent check-in control, wrong session, or an error
  state is evidence to report—not permission to retry through an API.
- Check-in never authorizes a result report or a claim that the players
  played.
