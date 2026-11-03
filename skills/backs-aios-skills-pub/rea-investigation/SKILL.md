---
name: rea-investigation
description: Use when a shipped executable, package, or requested runtime behavior needs REA investigation for diagnosis, capability discovery, or verification of a native adoption.
license: MIT
---

# REA artifact investigation

Use the installed REA engine and its matching standalone skill for investigations
of shipped binaries, packages, or requested runtime behavior. For ordinary source
repositories, use source and tests directly. This bridge does not install REA,
Ghidra, a provider, or an MCP server and does not assert they are available.

Use the caller's [BACKS Build Flow](../backs-build-flow/SKILL.md) task packet,
integration owner, and permitted `task_mode`. A direct investigation establishes
that packet once. [bounded-loops](../bounded-loops/SKILL.md) owns the deadline and
remaining allowances; provider retries, model changes, and handoffs inherit them.
This is an evidence-producing specialist, not a second adoption or repair owner.

1. Resolve the exact target, its identity/hash, the question, and the phase:
   **discovery** of the upstream artifact or **verification** of a specific native
   result. Read the installed upstream `reverse-engineer-anything` skill and
   relevant references from the host's agent skill directory; avoid
   recursively selecting this bridge. If it is unavailable, say so and continue
   useful investigation through available tools within the existing budget.
2. Use the connected REA tools where available, or the installed REA CLI. For
   native binary analysis, use an existing compatible Ghidra provider where
   supported; report a missing or unavailable provider rather than inventing a
   result or installing dependencies without authorization.
3. Start with read-only static inspection. Record target identity, evidence IDs,
   scope, limits, and unknowns. Static findings do not prove runtime behavior;
   perform live observation only within the user's requested scope and the
   declared tool's execution and mutation boundaries.
4. Return an evidence packet to the caller: target identity, phase, commands/tool
   receipts, evidence IDs, observations, static/runtime distinction, unknowns,
   and the remaining mission budget. The caller owns the next step: Absorb applies
   its license/dependency gates and native integration; a repair owner applies
   root-cause-first and repair-loop. Do not recursively start either lifecycle
   from this bridge. If verification finds a defect, return the observation for
   a sealed RED and the bounded fix/check loop; do not claim the repair happened.
5. Resolve dispatch by role and capability through the configured fleet-ladder;
   never hardcode model identifiers. When independent assessment is required,
   use the blind-tribunal's fresh author-disjoint execution and author-redacted
   packet, subject to capabilities actually provided by the host runtime. A
   missing provider or exhausted budget is a checkpoint marked **STILL-BUILDING**,
   with useful observations and the precise next action; it is never fabricated
   runtime proof. Keep any unblocked investigation within its remaining allowance.

Scaffold: REA by morluto, MIT license, https://github.com/morluto/rea.
The engine, upstream skill, and providers are separate installations.
