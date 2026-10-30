# Agent attempt receipts

Local record of one bounded dispatcher assignment or player contract. Write one
JSON object per line to ignored `state/agent-attempts/YYYY-MM-DD.jsonl`. Do not
add a line per chat message.

Do not record secrets, raw URLs, credentials, invite links, QR values, or tool
output.

Fields:

- `attemptId`
- `startedAt` / `finishedAt`
- repository and branch
- `taskType`: `coding` | `research` | `operational-player-contract` | `review`
- actor or contract reference, when the assignment has one
- `modelRequested`: the model the lead asked for, or null when none was named
- `modelDisplayed`: the exact model shown in Cursor’s picker, or `"unknown"`
- `routerMode`: `direct` | `auto-cost` | `auto-balance` | `auto-intelligence` | `unknown`
- `modelVerified`: true only when the picker or request details displayed the model
- `scope`
- `outcome`: `completed` | `blocked` | `stopped` | `failed`
- `changedFiles`
- `testsRun`
- `evidenceRefs`
- `escalationReason`, when there is one
- `authorization`: `user_requested` | `lead_authorized` | `not_authorized` | `unknown`

`authorization` says whether that assignment was requested. Do not fill it in
after the fact for an older line. A receipt written before this field existed
stays `unknown`.

For routine bounded work, prefer the cheapest suitable model. When Cursor Auto
hides the underlying model, record `modelDisplayed` as `"unknown"` and
`modelVerified` as false.
