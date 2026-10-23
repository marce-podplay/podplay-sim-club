# Sim staff QR check-in playbook

Use this only when a named player has already opened the rendered QR arrival
screen and a named staff Sim has an authenticated, rendered staff check-in
screen. The QR is a temporary entry capability, not social-channel content.

## Preconditions

- The player browser visibly shows “Show this at the front desk.”
- The staff browser is on the current staff check-in surface and belongs to a
  staff Sim authorized to operate it.
- The booking’s people, place, and arrival time were visibly compared before
  the scan. A QR never proves the right booking on its own.

## Routine

1. Send a URL-free staff notification that the named player is waiting at the
   front desk. Do not post the QR value.
2. Run `staff-qr-check-in.mjs` with the active player and staff session files.
   It captures QR pixels to a system-temporary file, decodes them in memory
   with macOS Vision, and emits scanner-style keyboard events to the staff
   browser. The temporary image is deleted in all cases.
3. Inspect the rendered staff result. The scan may first navigate staff to a
   named “Customer check-in” drawer; that handoff alone is not a completed
   arrival. Record check-in only when the named player’s rendered switch is
   checked (or the staff UI shows an equivalent explicit success confirmation).
4. Capture a sanitized staff read-back after confirmation. Do not retain the
   QR image, scanner payload, customer IDs, or URLs.

## Stops

- A missing scanner surface, a failed decode, a mismatched player, a payment
  prompt, or an error toast is evidence to report. It is not authority to use
  an API or retry with a different identity.
- Check-in remains arrival evidence only. Wait for independent `PLAYING`
  evidence before putting anyone on the Club Floor or creating an arcade
  receipt.
