# Research recognition and virtual-credit rewards

This policy rewards careful participation and evidence, not failures. It is a
local community policy for one disposable Preview season; it neither changes a
customer entitlement nor permits an unapproved Preview write.

## Amounts and eligibility

| Kind | Amount | Earned when | Season limit |
| --- | ---: | --- | --- |
| `participation` | 50 virtual-credit units | A named verifier accepted a bounded probe and its read-back completed (observed or inconclusive). | One per player |
| `finding` | 100 virtual-credit units | The named verifier participated and staff recorded an evidence-backed issue candidate. | One per player and distinct probe |

An expected result, inconclusive read, or disproved concern still earns normal
social recognition when the report was careful. A `finding` reward is not
available for a single failed journey; it needs the issue-candidate evidence
rule already defined by the feature contract.

## Required communication

Recognition is immediate and local: research XP plus a badge. Virtual credits
are different: they use a new, linked reward conversation.

1. Sofia sends an `offer` with the exact amount and reason.
2. The addressed player explicitly `accept`s or `pass`es. Receipt or research
acknowledgement is not reward acceptance.
3. Sofia locally approves an accepted offer.
4. A guarded Preview write may apply it only with exact-origin confirmation.
5. The recipient's own authenticated credit balance is read back. Only then is
the reward `applied`.

`participation` awards the `research-participant` badge and 50 research XP.
`finding` awards `club-scout` and 100 research XP. Those fictional records are
portable; Preview virtual credits are season-bound and must be reconfirmed.

## Commands

```console
./club research reward offer --probe <probe-id> --actor andy-bogard --kind participation
./club research reward respond --reward <reward-id> --actor andy-bogard --decision accept
./club research reward approve --reward <reward-id> --actor sofia
./club research reward apply --reward <reward-id> --confirm-origin "https://podify-pr-…"
./club research reward status
```

The credit writer preserves its 50-unit transaction maximum. A 100-unit
finding reward therefore uses two bounded transactions and requires a final
balance read-back. No raw user ID, credential, payment method, or Preview URL
is stored in reward state, facts, the notebook, or the Observatory.
