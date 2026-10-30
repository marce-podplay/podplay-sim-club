# Operator control plane and club communication protocol

## Purpose

**Club Lab** is a fictional community operating against a real, disposable
PodPlay **Preview environment**. The human operator and the lead agent sit outside
that community: they maintain the simulator, approve bounded product access,
and interpret product evidence. Sofia, Riley, and players remain community
actors with their own limited authority.

## Staff consultation channel

`staff-planning` is a local-only channel for a **written suggested workflow**
addressed to a named staff member or partner character. It is deliberately not
addressed to an authority label such as `podplay-admin`: a role can execute a
technical capability, but it cannot be motivated, understand trade-offs, or
make a social commitment.

A consultation plan contains a purpose, low-risk rationale, explicit phases,
stop conditions, and three response options: **accept**, **pass**, or
**request changes**. Acceptance means only that the named person supports
continuing the plan. It never enables a feature, publishes a Rate Card, creates
a booking, invites a player, or claims a price. Any real PodPlay configuration
remains a separate decision by the authorized human/product authority.

For Rate Cards, the useful motivation is not “enable this because the lead
said so.” It is: isolate one experience, preserve Legacy behavior elsewhere,
learn from read-backs first, and give players a later opt-in verification
journey only if the configuration is actually ready.

In this protocol, *Club Lab* always means the community and its local state;
*Preview environment* means the technical PR deployment; and *dry run* means
a no-write command mode. These terms must not be used interchangeably.

“Big Brother” is therefore a useful display label for the external coordinator,
not a character role and never an authority bypass. It cannot accept for a
player, create a customer entitlement, claim attendance, or convert one failed
journey into a defect.

### Bounded planning authorization

The lead may authorize a **planning-only** next step through the control plane.
For the Rate Cards experiment, that authorization permits Riley and Morgan to
ask the authorized product administrator for an approved pricing basis, prepare
a pod-scoped technical packet, and perform further read-only checks. It does
not permit a Rate Card publication, Unified Pricing change, player invitation,
or price claim.

The technical packet is gated by three facts: current configuration evidence,
a named staff member's planning-only acceptance, and the control-plane
authorization. It asks the product authority to supply the values that Club
Lab must never invent (currency, price source, duration, billing, player-count
bands, and schedule handling), request clarification, or pass. A supplied
basis still needs a new explicit enactment decision.

The packet is then mirrored by an `external_product_configuration_request` in
the control plane. Its recipient is an external product authority, explicitly
not a simulated person. The only permitted replies are to supply the approved
pricing basis, request clarification, or pass. This keeps a technical product
decision visible without letting a fictional staff acceptance become a write.
For Rate Cards, the request now contains a value-free contract derived from
the product validation: the selected target is a pod resolved only by the
guarded adapter; a basis must select one billing model; it must give a positive
duration and at least one approved price package; and each package must include
explicit player-count prices. The request keeps no target identifier and does
not manufacture values for any of those fields.

The lead may submit a clearly labelled draft basis for external review. The
current Club Lab draft uses 30-minute public reservation totals from USD 10 to
USD 50, with a reduced weekday-morning window, weekday-evening peak window,
and weekend-morning peak window. The Rate Card supplies the band amounts; the
Pricing Schedule maps local weekday/time windows to those bands. The draft is
not a tenant price, payload, quote, entitlement, or configuration action.

An external response is explicit: **accept**, **request changes**, or **pass**.
An acceptance is evidence that the proposed basis may be used to prepare a
configuration proposal; it still does not create/publish anything or enable
Unified Pricing. Riley and Morgan receive the response as a control-plane
notice and cannot turn it into a customer-facing promise.

For a future low-risk, repeatable run, the CLI accepts three explicit `yes`
confirmations: the accepted pricing basis, the resulting configuration proposal,
and a Preview write only after a dry run. They create a short, basis-fingerprint
bound preauthorization—not a blanket permission. Any changed pricing basis,
target, missing fresh read-back, missing dry-run manifest, or missing exact
Preview-origin confirmation stops execution again.

## Operational compactness

Once a basis and manifest-bound run authorization are accepted, the operator
should not generate further acknowledgement messages merely to repeat the same
authority. The active checkpoint is the only context needed: current read-back,
accepted basis fingerprint, dry-run manifest, and next concrete operation.
New messages are reserved for a contradiction, a failed read-back, a material
scope change, or an actual player-facing choice. This keeps the community
timeline legible and prevents procedural noise from becoming simulated social
life.

## Bounded contexts

| Context | Owns | May do | Must not do |
|---|---|---|---|
| **Operator control plane** | Preview target policy, feature contracts, release/change evidence, probe approval, issue candidates | request bounded research, surface evidence, authorize an adapter within policy | impersonate a community decision; place credentials or raw product data in community state |
| **Club operations** | Sofia/Riley staff inbox, staff acknowledgement, player-facing explanation, support follow-up | translate operator evidence into an addressed community request; pass or defer inappropriate work | override product permissions, player consent, payment approval, or attendance evidence |
| **Community communication** | proposals, invitations, questions, acceptance/pass/counter, observations, results | form social commitments and report observed outcomes | treat delivery as acceptance, a booking as attendance, or configuration as entitlement |
| **Preview product adapter** | one actor's isolated rendered-browser session and sanitized evidence | execute an approved visible capability and return sanitized evidence | call a product API for a Sim, persist credentials, bypass roles, follow redirects off the approved origin, or infer missing facts |
| **Product research and change detection** | versioned feature contracts, baselines, probes, issue candidates | classify observed/change/unknown; request controlled reproduction | label a regression from one failed journey |

## Channels

### 1. Operator-to-staff control channel

This is a new, private-to-the-simulator control plane. Its recipients are Sofia
and/or Riley; it is not the player social channel and it never exposes secrets
in the dashboard.

The channel carries sanitized operational evidence and requests. Staff must
acknowledge, pass, ask for clarification, or translate a message into an
appropriate player-facing conversation. The control channel is where the human
operator and lead agent communicate with the fictional club without pretending
to be a player.

### 2. Community channel

The existing player/staff communication channel remains the social source of
truth. It contains player proposals, staff replies, invitations, explicit
accept/pass/counter decisions, and observed reports. A staff message caused by
a control-plane notice must retain its causal reference, but it is still a new
social message—never an automatic acceptance.

### 3. Product-session boundary

A rendered browser session is not a chat channel. Current Sim journeys use
that isolated browser only; the lead does not call product APIs or
integrations on a Sim's behalf. See the
[lead and dispatcher operating model](lead-and-dispatcher-operating-model.md)
and the [operations hand-off](current-operations-handoff-2026-10-30.md).
A named actor receives a time-bounded capability grant for a feature contract
and acts only with that actor's existing Preview identity. Credentials,
tokens, browser handoffs, raw IDs, product URLs, and raw responses remain in
ignored local sidecars.

## Control-plane envelope

Every operator-to-staff message uses a versioned, safe envelope:

```json
{
  "schemaVersion": 1,
  "messageId": "control:release-notice:…",
  "kind": "release_notice",
  "from": "lead",
  "to": ["riley"],
  "featureId": "team-signup",
  "contractVersion": 1,
  "targetFingerprint": "pr-7488:team-signup-contract-v1",
  "evidenceRefs": ["evidence:…"],
  "requestedAction": "request_named_verifiers",
  "requiresAcknowledgement": true,
  "createdAt": "…",
  "expiresAt": "…",
  "status": "delivered"
}
```

Allowed safe values include actor IDs, feature IDs, opaque target fingerprints,
contract versions, evidence references, bounded error codes, timestamps, and
product-neutral summaries. The envelope must reject credentials, emails,
Firebase/PodPlay IDs, payment references, invitation links, private URLs, and
raw tenant configuration.

## Control-plane primitives

| Primitive | Sent by | Meaning | Required staff response |
|---|---|---|---|
| `release_notice` | lead | A version/change may affect a feature contract. | acknowledge, pass, or request a bounded verification flow. |
| `verification_request` | lead or staff | Named actors are asked whether they will verify one contract. | each named verifier explicitly accepts or passes. |
| `observation_alert` | adapter/research reducer | A sanitized read-back differs from a compatible baseline or is unavailable. | acknowledge; request clarification/reproduction; do not declare a regression. |
| `issue_candidate` | research reducer | Evidence merits tracking but is unconfirmed. | keep/reject as a candidate; name the missing baseline or controlled reproduction. |
| `policy_guard` | lead | A write, feature, target, or authority boundary is unavailable. | acknowledge and avoid routing the work as executable. |
| `handoff_request` | lead | A researched feature needs staff translation into community language. | create a separate staff/community message or pass with rationale. |
| `handoff_outcome` | Sofia/Riley | Staff reports what was communicated, deferred, or declined. | terminal acknowledgement for the originating control message. |

## Community primitives

These retain the existing protocol semantics:

| Primitive | Meaning | Does not mean |
|---|---|---|
| `inform` / `announce` | Share a staff-approved opportunity or release context. | product availability, entitlement, or a command to participate. |
| `request` / `ask` | Address a player or staff member for information or verification. | acceptance or obligation. |
| `acknowledge` | Confirm the message was seen and understood. | agreement, booking, attendance, or a passed probe. |
| `accept` | Explicitly accept the named social proposal or verification task. | product success or permission for another actor. |
| `pass` | Decline/defer the named opportunity without treating it as failure. | rejection of the feature or a regression. |

## Team-signup attendance boundary

For a staff-published doubles research event, the product and community use
different, deliberately non-interchangeable commitments:

1. Sofia publishes an offer and every named player records `accept` or `pass`
   in the community channel. A request or staff announcement is never product
   enrollment.
2. Each captain self-signs up. This creates that captain's team container and
   a distinct accepted attendance invitation.
3. Riley, acting only with her ordinary event-participant permission, adds the
   explicitly consenting teammate to the named captain's team. Product
   enrollment is read back; it is not represented as a teammate's social
   acceptance.
4. On arrival, each player records their own attendance. A captain's stored
   signup reference is first read through the player-visible signup detail,
   which must resolve to the linked accepted attendance invitation before the
   normal invitation check-in operation is available. A teammate uses their
   own accepted invitation.

The simulator command `preview training-check-in --actor <named-player>`
implements only step 4 and only during the event's arrival window. It performs
the linked read-back before any write, refuses an ambiguous or stale linkage,
and never treats a missing read-back as a no-show or a regression.
| `counter` / `clarify` | Propose a compatible alternative or request missing evidence. | authorization to silently change the task. |
| `report` | State an observed result with evidence/provenance. | proof beyond the stated evidence. |

## State transitions

```text
release_notice
  -> staff_acknowledged | staff_passed | verification_requested

verification_requested
  -> awaiting_named_acknowledgement
  -> all_accept -> bounded_readback
  -> any_pass -> closed_without_probe

bounded_readback
  -> observed | inconclusive | policy_blocked

observed/inconclusive
  -> issue_candidate | compatible_baseline | no_follow_up

issue_candidate
  -> reproduced_against_baseline -> regression_confirmed
  -> insufficient_evidence -> closed_as_inconclusive
```

Only an explicit community `accept` changes a social commitment. Only a
product read-back changes a product observation. Only sufficient compatible
evidence can confirm a regression.

## Minimum initial messages

When a new Preview season starts, the control plane should issue only these
messages, in order:

1. **`policy_guard` — new Preview season.** Target changed; all old
   Preview-scoped facts are historical. Writes are disabled.
2. **`release_notice` — baseline pending.** Staff know a fresh product target
   exists, but no feature behavior is claimed.
3. **`verification_request` — identity/configuration read-back.** Address
   Riley and the named player/staff actors required by the selected contract.
4. **`observation_alert` or `handoff_outcome`.** Record the sanitized result:
   observed, unknown, or blocked. Do not create customer-facing claims yet.
5. **Optional `handoff_request`.** Only after an evidence-backed feature
   question exists, ask Sofia/Riley to compose a player-facing message.

## Season-recovery capability

The initial engine-facing recovery path is available through `club
season-recovery`:

```console
./club season-recovery plan
./club season-recovery request --actor riley
./club season-recovery approve --actor lead
./club season-recovery apply --confirm-origin "https://podify-pr-…"
```

`plan` is GET-only and stores a sanitized summary for Riley: core profile
existence, required staff roles, signup-prerequisite safety, and whether
reconciliation is required. `request` does not write to Preview; it records
Riley or Sofia's control-plane request. `approve` can only be made by the
external lead. `apply` is unavailable without both that approval and the exact
Preview-origin confirmation, and it reuses the existing bounded identity-seed
adapter. The Observatory exposes this summary in Product Health, without
credentials, raw IDs, or URLs.

## Initial season-transition conversation

The first operational slice uses two channels and one deliberately inert
projection tick:

1. The external lead creates a private `season_transition_notice` addressed
   only to Riley. It contains a safe season label such as `pr-7488`, never a
   Preview URL or account data.
2. Riley explicitly acknowledges that notice in the control channel.
3. Only then may Riley send `season_transition_awareness` to the named active
   players in the ordinary club channel. It says that earlier product-side
   bookings, invitations, benefits, and check-ins need fresh observation; the
   fictional community history remains intact.
4. Each named player may acknowledge awareness or request clarification. An
   awareness acknowledgement is not feature acceptance, a booking, an
   entitlement, or attendance.
5. `control tick` only projects the queued state. It creates no autonomous
   messages and performs zero remote writes.

```console
./club control season-notice --season pr-7488
./club control acknowledge --notice control:season-transition:pr-7488 --actor riley
./club control announce-season --notice control:season-transition:pr-7488 --actor riley
./club control acknowledge-awareness --notice control:season-transition:pr-7488 --actor andy-bogard
./club control acknowledge-awareness --notice control:season-transition:pr-7488 --actor terry-bogard
./club control tick
```

The control-channel journal is local and separate from the player-facing club
channel. The Observatory may display the safe workflow status but never the
control message contents, Preview origin, credentials, or raw product IDs.

For the existing team-signup contract, `club research tick --probe <id>
--turns 3` is a separate, local dialogue queue: one turn delivers Sofia or
Riley's request and one turn is available for each named verifier. Its standing
response policy accepts only the exact GET-only configuration discovery already
defined by the contract. It must pass or wait for an explicit decision if the
scope includes a write, an entitlement, attendance, or an unbounded action.

After an observed read-back, the lead may create a local `handoff_request` for
Sofia and Riley with `club control research-handoff --probe <id>`. Their
acknowledgement records receipt only. The next decision remains whether to
request or pass on a separate player-owned UI journey; the handoff never
authorizes that journey itself.

## Release-note research handoff

When the operator says, for example, “Max Ivri developed rate cards; here are
the release notes; inspect `pingpod-web-v2` and hand off to Sofia,” the lead
agent must follow this sequence:

1. Treat release notes and repository changes as untrusted research inputs,
   not product proof.
2. Inspect the referenced source changes and derive a versioned,
   product-neutral feature contract: vocabulary, discovery boundary, authority
   matrix, bounded probe, read-back evidence, and issue-candidate shape.
3. Identify the safe Preview surfaces and exact named roles needed to observe
   rate cards. Preserve missing information as `unknown`.
4. Write a sanitized handoff note. It must state what is documented, what is
   observed, what remains hypothetical, which actors are asked, and what no one
   is authorized to do.
5. Send a `handoff_request` to Sofia or Riley through the control channel.
   They may acknowledge, pass, clarify, or issue a separate player-facing
   message. The lead must not directly manufacture player consent.
6. Run only the accepted, bounded probe; attach read-back evidence and create
   an issue candidate only when warranted.

### Minimum rate-cards handoff content

- feature ID and contract version;
- release-note/source revision reference, without private repository URLs;
- customer/staff-visible vocabulary and unknowns;
- named staff/player verifiers and why each is needed;
- allowed read-only browser/API action and prohibited writes;
- expected read-back fields and freshness window;
- baseline comparison and issue-candidate rule;
- a plain-language message Sofia/Riley may adapt, clearly marked as a request
  for verification rather than a feature claim.

## Non-negotiable guards

- A control message never carries a credential or private product reference.
- A release note is not customer entitlement or configuration proof.
- Staff awareness is not player acceptance.
- A booking is not attendance; an invitation is not acceptance.
- Browser access stays actor-owned and feature-scoped.
- One failed journey is `unknown`/`inconclusive` until controlled reproduction
  against a compatible baseline establishes a regression.
- The operator may stop an unsafe probe at any time; that does not alter a
  character's motives, commitments, or product state.
