# Community identities and Club Lab contexts

## Purpose

The fictional community persists while Preview deployments and their copied
databases do not.  This model prevents two opposite errors: cloning a player
just because they visit another club, and treating an employee of one club as
an authority in another.

## Identity split

| Subject | Scope | May cross Club Labs? |
|---|---|---|
| Player character and Firebase credential | community-global, private credential store | yes |
| Physical availability, travel buffer, and progression | community-global | yes, never overlapping |
| PodPlay app-side user link | private, per Club Lab context | only after read-back verification |
| Membership, virtual credits, booking, event signup, attendance | product/club-local | no |
| Staff persona, authority, diary, research portfolio | Club Lab-local | no by default |

The global player identity is an ordinary identity, not an authority bypass.
It proves neither membership nor entitlement in a different club.

## Current context

`seed/club-context.json` declares **The Club Lab** with the stable key
`club-lab`.  It is intentionally separate from a Preview URL and tenant
identifier.  A Preview can be replaced during a season without changing this
context.

Riley is an employee of this context.  Future clubs must use distinct staff
actor IDs and separate portfolios.  A future multi-employer staff experiment
is possible, but requires an explicit shift calendar and context switch; it
is not the default because it would mix employer-local observations.

The current staff roster is public, local-only fiction in
`seed/staff-directory.json`. Riley coordinates research. Morgan Vale is the
configuration liaison: Morgan can assess and respond to a written plan, but
has no Preview credential or product-side configuration authority. This keeps
social agreement distinct from actual product authorization.

## Cross-club entry protocol

Before a player takes a product action in a new club:

1. Register the new club context and its own staff roster.
2. Reserve the player's proposed physical time in the community calendar.
3. Authenticate with the existing private Firebase credential.
4. Read back the player profile for that Preview only.
5. Record only `recognized`, `unavailable`, or `inconclusive` publicly.
6. Stop on `unavailable`; do not create or seed a replacement without an
   explicit target-specific decision.

The private registry stores the verified PodPlay user link by context.  A
link from Legacy is never reused against Unified.  A simultaneous Firebase
session is expected to be technically valid, but independent local token
caches are used and product actions remain separately authorized.
