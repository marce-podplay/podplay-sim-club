# Sim player booking playbook

Use this playbook when a named Sim has a queued `plan_training_booking`,
`apply_training_booking`, `plan_team_signup_booking`, or
`apply_team_signup_booking` browser journey. It is a decision aid for the
character, not authority to create a booking.

## Start only from current facts

- Confirm the work item names this Sim as the product actor.
- Read the current local intent: named participants, accepted social decision,
  duration, and local-window constraints.
- Treat prior ledger rows as history. Verify current availability and any
  existing reservation in the rendered product UI.
- Use only the isolated visible browser. Do not substitute a product API,
  control-plane action, stored session, or another Sim's browser.

## Visible booking path

1. Sign in using the one-time browser credential handoff.
2. Complete only any visible onboarding needed to reach Booking. Run
   `node tools/sim-browser/booking-entry.mjs --session <prepared-session>`
   after login. It observes the rendered branch, selects the already-approved
   **New York / New Jersey** region only when that onboarding is actually
   present, confirms it, opens Booking, and stops at the rendered **West 37th
   Manhattan** area control. If onboarding is absent, it follows the normal
   Booking path instead. This routine stops before any date, slot, payment,
   or booking choice.
3. Open Booking and wait for the agreed area control to render before selecting
   it, then select the appropriate pod
   experience.
4. After selecting an experience, wait independently for the intended live
   slot control to render. After selecting that slot, wait independently for
   the rendered **Continue** control before trying to activate it. Do not
   classify an absent control during its loading transition as a product
   failure.
5. Inspect date, time, duration, court/pod, price, and any player-count or
   invitation controls before progressing. Availability labels are volatile:
   map a slot only after this actor sees its current rendered label.
6. Compare the rendered review with the intent. The named booker may continue
   only if it still satisfies the accepted agreement and no separate payer or
   price-evidence gate is outstanding.
7. After a successful visible booking, follow the recipient-owned invitation
   journey separately. Do not infer acceptance, check-in, attendance, play,
   or a result from booking progress.

## Payment-method routine

Use this only when the rendered review explicitly says that the named booker
has no payment method and the current task authorizes a Preview sandbox
booking.

1. Capture the reviewed date, time, group size, venue/pod choice, and total
   before opening **Add card**.
2. In the visible card modal, use the approved Preview test-card routine only
   through the secure rendered fields. Do not log, screenshot unmasked values,
   or copy payment values into a Sim memory.
3. Select the displayed country only if it already matches the actor's
   approved Preview setup; otherwise stop and ask for direction.
4. Activate the visible **Confirm** button, then wait for the review to show a
   masked payment method. A click on Confirm is not evidence that the method
   was accepted.
5. Re-read the booking review. Submit payment only when the masked method,
   total, group size, venue, date, and duration still match the agreement.

For a real product, this routine requires the player's own payment decision.
The Preview sandbox test-card exception is bounded to an explicitly authorized
Club Lab run.

## Evidence and stopping rules

- Record a screenshot and sanitized console/network window after a material
  visible state or an unexpected UI state. These are browser observations,
  not product outcome claims.
- If Booking shows no current reservation or no qualifying slot, record that
  visible fact and return the constraint to the social/staff follow-up flow.
- If the expected visible control, price, or court differs from the agreement,
  stop before confirmation. The actor can request a new social decision; it
  must not improvise consent or payment.
- End the isolated session after the bounded step. A later named Sim gets a
  fresh browser profile and work item.

## Club Floor boundary

The local dashboard may show a Sim inside a pod only after an independently
observed `PLAYING` lifecycle state. A screenshot of booking, invitation, or
check-in is useful local evidence but never substitutes for that state.
