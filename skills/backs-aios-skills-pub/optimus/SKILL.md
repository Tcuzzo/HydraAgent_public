---
name: "optimus"
description: "Use when starting or resuming an agent session, job, or autonomous loop that needs the BACKS harness and relevant task skills."
license: "MIT"
---

# Harness Boot
**Effort:** light — one boot pass per session to load the floor and the job's skills, plus a deterministic hook that costs nothing to run. Removes: ungrounded edits — mutations made before the rules were read, and the redo that follows once they are.

One rule: **no code and no job until the harness is loaded.** The harness is the pack's
invariant floor plus the skills that cover this job. Every session, every runtime, every
time. Why: a rule an agent must remember fails exactly when the agent is busiest — so
loading the rules is the first act, and a hook makes it structural instead of advisory.

## When to run

At the start of a session or new job; revalidate after a context reset or handoff.
An inner retry reuses current grounding and the same task packet. Reload missing or
changed bodies; do not reboot the whole lifecycle for every iteration.

## The boot sequence

1. **Load the invariant floor.** Read [invariant-floor](../invariant-floor/SKILL.md)
   before touching anything. This is the floor the whole session stands on.
2. **Apply [Build Flow](../backs-build-flow/SKILL.md).** It owns task mode,
   readiness to act, skill selection, bounded RED/GREEN with scoped lint and tests,
   gauntlet evidence, and delivery. Reuse a verified packet; gather only missing
   facts needed for the next concrete action. Specialists return to one owner.
3. **Apply human context** ([human-calibration](../human-calibration/SKILL.md)) when
   relevant. [operator-essence](../operator-essence/SKILL.md) owns the evidence-backed
   awareness file and missing-file insight pass; do not run a second profile cycle.
4. **Load the session.** The session hook delivers the actual Optimus, Yoke, and Build Flow
   bodies, plus the proven native harness when available. Compaction reloads those
   bodies without revoking earned grounding. A missing marker at a mutating tool
   triggers loading, never a tool refusal. Hosts without a context hook can use:
   ```bash
   node "$HOME/.local/share/backs-aios/current/hooks/aios_gate.js" --load backs-aios:optimus
   ```
   In a Codex shell, pass the active `CODEX_SESSION_ID` as the final argument.
   Python is available through the matching `aios_gate.py` loader. Unknown skills
   return nonzero without inventing a receipt.
5. **Apply the skills the next action needs.** Name each purpose, output, and exit.
   A skill named but not invoked did not happen. Reuse actual bodies still available
   in current context; a remembered summary or file hash does not replace them.
6. Only then: write code, run mutating commands, or change anything.

## Automatic grounding

The existing hook reads the foundation files and delivers their bodies through
host context. It records source hashes only after delivery. Context-loading
receipts are distinct from actual Skill-event evidence; loading a pointer or
hashing a file is not an invocation of that skill.

- Session start and compaction reload the floor without taking tools away.
- A missing session marker triggers automatic loading at the next matched tool.
- Missing or unreadable bodies produce a visible diagnostic and no false receipt;
  the operator and agents retain their tools.
- `--rearm <session_id>` requests another load. It never locks the session.
- `AIOS_GATE=off` remains the operator's existing, reversible kill switch.
- Hooks are local and deterministic. No model call or network dependency is added.

## Hard rules (what fails this skill)

- Any mutation before the harness is loaded.
- A skill named in a report that was never invoked in the session.
- Grounding in session memory, LLM priors, or "I remember" instead of the BACKS
  harness. BACKS is model- and context-agnostic with robust memory lanes — the
  harness (memory files, playbooks, ledgers, ladder yaml) carries the knowledge;
  the model's head does not (operator ruling 2026-09-26).
- A hook that refuses any tool, revokes earned grounding, or reports loading that did not occur.
- A second gate, or any new friction placed on the human. The kill-switch stays theirs.

## Works well with

- [invariant-floor](../invariant-floor/SKILL.md) — the floor boot loads first.
- [human-calibration](../human-calibration/SKILL.md) — the profile step of boot.
- [repair-loop](../repair-loop/SKILL.md) — what a fix job runs after boot.
- [bounded-loops](../bounded-loops/SKILL.md) — budgets for every loop boot starts.
- [wayfinder](../wayfinder/SKILL.md) — when boot shows you do not know the route.


## Fleet ladder rung policy (thin pointer)

Before any model dispatch or frontier design/reason lift, invoke
[fleet-ladder](../fleet-ladder/SKILL.md). It forces agents to **read**
`config/fleet_ladder.yaml` + `config/gpu_nodes.yaml` (deploy paths), honor the live
builder seat, and HARD-gate realtime research seams (web + GitHub + Reddit + papers)
when the rung marks frontier / `research_seams`. Never hardcode model IDs; Patch owns yaml.
Cite: `observe/SECURITY_OPERATOR_BUILDER_SEAT_ORDER_20260926.md`.

Honor [fleet-ladder](../fleet-ladder/SKILL.md): use a medium-or-higher reasoning baseline where supported, research before heavy lifts, and do not truncate context.
