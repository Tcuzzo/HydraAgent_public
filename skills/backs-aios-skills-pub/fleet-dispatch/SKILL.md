---
name: fleet-dispatch
description: Use when this agent receives an "invoke skill fleet-dispatch" command from a pack peer (Hydra/CuzzoClaw from BACKS Alpha), or when this agent needs to dispatch its own fleet rung for its own work. Calls scripts/fleet.py build --role <role> --spec <spec> against the local fleet ladder, parses FLEET_RECEIPT, returns the answer with provenance. Model-agnostic — the fleet_ladder.yaml owns the rung order, the agent just asks. Trigger words: fleet-dispatch, fleet rung, dispatch, role agent, role bot, invoke skill, fleet ladder.
license: MIT
---

# Fleet Dispatch (pack peer surface)

You receive an "invoke skill fleet-dispatch" command from a pack peer (BACKS Alpha). The command payload is a free-text string with both the role and the spec, like:

```
invoke skill fleet-dispatch --role agent --spec <spec text>
```

Your job is to invoke YOUR local fleet ladder to dispatch that spec on the named rung, and return the answer.

## Steps

1. Parse the command for the role and the spec. The format is:
   `invoke skill fleet-dispatch --role <role> --spec <spec>`
   where `<role>` is one of `agent`, `bot`, `builder`, `grader`, `worker`, ... (see your local `config/fleet_ladder.yaml`), and `<spec>` is the free-text work to hand the model.

2. Invoke your local fleet resolver:
   ```bash
   python3 scripts/fleet.py build --role <role> --spec <spec>
   ```
   The fleet resolver:
   - reads `config/fleet_ladder.yaml` for the live rung order
   - probes each provider's availability (live, never from memory)
   - dispatches to the first AVAILABLE rung
   - prints the model's answer, then a `FLEET_RECEIPT <json>` line on stdout

3. Parse the response:
   - **Answer**: everything on stdout that is NOT `FLEET_STATUS*`, `FLEET_RECEIPT*`, or `fleet:*` lines. Strip those prefixes; the remaining lines are the model's answer.
   - **Receipt**: the JSON after `FLEET_RECEIPT ` on its own line. Carries `model`, `provider`, `route_chosen`, `latency_ms`, `cost_estimate`, `dispatch`.

4. Return BOTH to the caller (BACKS Alpha over SSH). The caller wraps the receipt in its signed ExecutionReceipt, so the full provenance chain is preserved end to end.

## What you never do

- Never hand-build a model call. Never hardcode a model name. Never invent a provider.
- Never skip the fleet resolver and call a provider directly. The resolver owns the rung order and the harness fallback; bypassing it is a defect.
- Never silence a FLEET_RECEIPT. The receipt is the provenance record (inv_9).
- Never fall back silently on a missing credential (inv_2: no default-off capability; the ladder fails loud).

## Failure handling

- If `python3 scripts/fleet.py build` exits non-zero, return the stderr verbatim + the FLEET_RECEIPT if present.
- If the answer is empty, return `{"ok": False, "error": "fleet_empty_answer"}` and the receipt (so the caller knows which rung was attempted).
- Never retry the same provider on the same error in a tight loop. The fleet resolver already retries with bounded backoff. If it gives up, you give up (inv_16: no hammering).