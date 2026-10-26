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

## Four operating modes

| Research | Headless | Owner | Intended use |
| --- | --- | --- | --- |
| off | on | economical worker | normal routine Sim journey |
| on | on | coordinator | private workflow discovery |
| on | off | coordinator | supervised rendered UI research |
| off | off | coordinator | exceptional user-assisted fallback |

No mode authorizes integrations or product APIs on behalf of a Sim.
