# Actor runtime

An actor is one short-lived incarnation of a persistent fictional character.
It reconstructs identity from its immutable card, bounded memory, unread
messages, current clock, world projection, and scenario context.

An actor may speak, ask for help, select an offered intention, propose one
allowed action, and propose a memory update. It may not select arbitrary URLs,
tenants, pods, credentials, or feature flags and may not edit shared world state.
