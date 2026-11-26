---
name: fleet-dispatch
description: "Use when this agent receives an \"invoke skill fleet-dispatch\" command from a pack peer (another agent on the operator's control host), or when this agent needs to dispatch its own fleet rung for its own work. Uses the active public host's configured dispatch capability and returns evidence of the actual route. No private resolver or deployment is assumed. Trigger words: fleet-dispatch, fleet rung, dispatch, role agent, role bot, invoke skill, fleet ladder."
license: MIT
---

# Fleet dispatch through an available public host

This skill is a delegation protocol, not an installed executable. Public Hydra does not ship the private Fleet runner. Do not discover a private service or deployment when a dispatch capability is missing.

1. Identify the authorized task, required role, tools, time budget and expected result.
2. Inspect the active host's documented public dispatch interface and configured provider. Use only supported parameters. Missing dispatch capability is an explicit unavailable result.
3. Submit through that interface within the task's authorization and budget. Do not invent a provider or model, and do not silently expand the context or recipients.
4. Return the actual answer with the route, command or request result, elapsed time and available execution receipt. Distinguish a process exit from verified task completion.
5. On failure, report the error without credentials or private metadata. Retry only within the existing finite budget; preserve the failed attempt.

Use [fleet-ladder](../fleet-ladder/SKILL.md) for capability and independent-review discipline. A provider name is provenance, not evidence of task competence or reviewer independence.
