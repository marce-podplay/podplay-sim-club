# Sim dispatcher

The dispatcher is the durable coordinator boundary between the deterministic Rally Engine and a short-lived economical Sim worker.

Its source contract is the reusable personal template, **Sim Club Dispatcher**. The template’s reference is tracked in [the repository](../docs/templates/sim-dispatcher-reference.txt), while its installed personal-skill copy lets a future Codex chat reconstruct the role quickly.

## Handoff sequence

1. Read `state/worker-contracts.json`, pending browser work, secret-free diaries, and journey packets.
2. Review only the oldest eligible contract for one actor and one playbook.
3. Dispatch a worker under the contract’s declared mode. Routine work defaults to economical, headless, and non-research.
4. Require a structured receipt and preserve evidence paths rather than conclusions.
5. Reconcile only rendered UI facts. Escalate UI deviations, ambiguous choices, missing authentication, or policy/payment decisions to the coordinator.

The Python engine creates contracts; it does not start a model or browser. A future dispatcher may use this document and the personal template to launch an eligible worker, or the coordinator may use the same contract in research mode.

## Operations handoff

The routine dispatcher is now executable but intentionally provider-neutral:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m podplay_sim_club.cli worker-dispatch next --worker economical:runner-1
```

It atomically claims the oldest runnable contract and its matching browser job, then prints the secret-free launch packet for exactly one actor and one playbook. An external economical worker performs the browser journey and returns one bounded receipt:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m podplay_sim_club.cli worker-dispatch report \
  --contract '<contract key from the launch packet>' \
  --worker economical:runner-1 \
  --outcome visible_progress \
  --diary 'Visible UI observation recorded; no product outcome inferred.'
```

The command does not launch a model, open a browser, or call a product API. The outer dispatcher owns those integrations and may choose its economical model/provider later.

### Codex CLI launcher

For normal operation, the repository now supplies that outer bridge. It starts
one detached `gpt-6-luna` worker only when `--execute` is explicitly present:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m podplay_sim_club.cli worker-dispatch launch \
  --worker economical:club-ops-1 --execute
```

Without `--execute`, `launch` is a non-mutating queue preview. With it, the
launcher atomically claims one runnable contract, starts a short-lived,
ephemeral Codex worker, and records only an opaque launch reference, process
identifier, and timestamp in the contract. The worker receives the same
secret-free packet/runbook, must bootstrap an isolated browser itself, and
must close the contract with a receipt. The launcher never captures model
output or browser data, and a process that has started is never auto-retried.

Run `worker-dispatch reap` before choosing another worker. It changes only a
dead worker process with no final receipt into `blocked` coordinator review;
it does not assert a product failure and never requeues the browser journey.

If the dispatcher launches the worker separately, that worker reads its already-assigned packet rather than calling `next` a second time:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m podplay_sim_club.cli worker-dispatch active --worker economical:runner-1
```

It may then retrieve its exact runbook, including the prohibitions and receipt
requirements, before it opens the isolated browser:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m podplay_sim_club.cli worker-dispatch runbook --worker economical:runner-1
```

Visible-progress and unexpected-UI reports must name the matching local
`worker-bootstrap.py` receipt. This ties the worker diary to sanitized
rendered-browser evidence without treating either as product proof.

Only an explicit `not_started` receipt releases the local browser job for a later retry. A `blocked` or `pass` receipt quarantines it for coordinator review, because the absence of a receipt cannot prove that no UI action occurred. A visible-progress or unexpected-UI receipt is accepted only after the worker has recorded a visible browser observation.

## Four operating modes

| Research | Headless | Owner | Intended use |
| --- | --- | --- | --- |
| off | on | economical worker | normal routine Sim journey |
| on | on | coordinator | private workflow discovery |
| on | off | coordinator | supervised rendered UI research |
| off | off | coordinator | exceptional user-assisted fallback |

No mode authorizes integrations or product APIs on behalf of a Sim.
