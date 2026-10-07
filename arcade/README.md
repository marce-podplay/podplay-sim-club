# Preview Club Arcade

The Arcade is a local-only game simulation laboratory for Preview Club. It is
not PodPlay, does not call the Preview API, and never changes a real booking,
check-in, attendance record, or reported result.

Its first game is deterministic table tennis. It can give club characters a
replay to watch and a fictional match outcome without consuming model tokens.

## Boundary

There are two deliberately separate result types:

| Type | Evidence | May advance a real tournament? |
| --- | --- | --- |
| `arcade_replay_captain_confirmation` | local deterministic replay, confirmed by its named fictional reporter | Only in the fictional Club Arcade narrative |
| `captain_attestation` | named captain's result report for a real Preview event | Yes |

An Arcade replay may be attached to a real booking as a **simulated companion**.
For that fictional companion, the replay is the source of truth for its named
reporter: once the scheduled session ends, the reporter can confirm the replay
winner without entering a score. This is never evidence that the real players
attended, played, scored, or won in PodPlay. It never overwrites product
check-in evidence or advances a real product tournament.

## Determinism with variability

Each match receives a durable public seed:

```
sha256(seasonId + occurrenceKey + gameVersion + orderedPlayerIds)
```

The seed initializes a small pseudo-random generator. The same stored match
replays identically on every machine, while a different occurrence can have a
different upset or momentum swing. Randomness is therefore *reproducible*, not
unbounded or model-generated.

The event record stores the seed, engine version, and every rally. Never use a
secret, a Firebase identifier, payment data, or a share-invitation URL as a
seed.

## Player inputs

Arcade skill is a local game rating, not a claim about a real player.

```json
{
  "actorId": "kyo-captain",
  "tableTennis": {
    "serve": 62,
    "attack": 74,
    "defense": 66,
    "composure": 70,
    "teamwork": 58
  }
}
```

Ratings range from 0 to 100. A rally combines relevant ratings with a bounded
seeded noise term. Better ratings improve the probability of a point; they do
not guarantee a winner. For doubles, alternating turns and each pair's
teamwork influence the probability.

## Table-tennis v1 rules

- Singles or doubles.
- First to 11 points; a two-point lead is required.
- Service changes every two points, then every point after 10–10.
- Each rally has a compact record: server, point winner, score, and one neutral
  reason such as `attack_edge`, `forced_error`, or `long_defense`.
- The engine has a maximum rally count and reports an invalid simulation rather
  than silently looping forever.

## Storage and lifecycle

All Arcade data lives under ignored local state:

```
state/arcade/
  ratings.json          # local character game ratings
  matches.json          # seed, configuration, rally log, simulation result
```

An Arcade match moves through:

```
planned → simulated → replayed
```

For a companion replay, store the real `occurrenceKey` as a reference only.
The real booking lifecycle remains independent:

```
booked → invitation accepted → checked in → captain result requested → reported
```

## Observatory

The observatory shows:

- `SIMULATED` badge and game version;
- player/pair names and ratings used;
- current rally and score in proportion to the booked session clock;
- final Arcade winner clearly separated from any captain-attested real result.

Use the explicit attachment command once a two-player training booking exists:

```bash
./club arcade-replay --intent intent:training:women-fighters:001
```

After its scheduled end, `./club runtime rally-tick` can ask the designated
reporter to confirm the replay. The replay result is still not auto-filled:
the reporter action records the confirmation.

## Integration contract

The social engine may use an Arcade match to create local motives such as
practice interest, rematch interest, or fictional confidence. Those changes
must be tied to `arcade_simulation` evidence. It may not mark real closeness,
attendance, payments, benefit consumption, or tournament progression.

The first implementation should expose a pure function with no filesystem or
network access:

```python
simulate_table_tennis(match_config, ratings, seed) -> simulation_record
```

Persistence, dashboard rendering, and social consequences are separate
adapters around that pure core.
