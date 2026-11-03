---
name: backs-build-flow
description: "Use when building, fixing, reviewing, resuming, or delivering work that requires coordinating context, skills, and verification without losing execution momentum."
license: MIT
---

# BACKS Build Flow

One integration owner carries the request from context to verified delivery.
Specialist skills return evidence to that owner; they do not restart the workflow.
For a simple answer with no build or investigation, use only the relevant steps.

**Model agnostic:** resolve roles by required capability and actual availability.
The same capable model may fill multiple roles in separate executions. Required
independent review uses a fresh context that did not participate in authoring or
inherit the author's conversation. No specific model, provider, family, or model
diversity is required. Record the selected route as provenance, not authority.

## Establish enough context to act

1. Preserve the user's objective, constraints, existing authorization, and requested
   outcome. Choose a mode: **build/fix**, **read-only review**, **investigate**, or
   **resume/deliver**. A review does not authorize edits, an install, or deployment.
2. Reuse a current task packet. Otherwise use
   [context-engineer](../context-engineer/SKILL.md) to verify the target, governing
   instructions, affected caller/consumer path, existing work, and acceptance check.
   [building-with-backs](../building-with-backs/SKILL.md) owns checkout and promotion
   resolution. Pin the pre-change `base_sha`; update `candidate_sha` as work commits.
3. Apply [operator-essence](../operator-essence/SKILL.md) for the current operator:
   load the relevant awareness slice, or run its missing-profile insight bootstrap
   once. Qualitative observations and quantitative evidence guide communication
   and verification; they do not grant authority or hardcode a persona. Mark sparse
   evidence provisional and continue useful authorized work. Use
   [intent-compiler](../intent-compiler/SKILL.md) only for ambiguous intent;
   [operator-intent-deduction](../operator-intent-deduction/SKILL.md) owns deciding
   whether a material unresolved choice really needs the operator.
4. **Start execution when you can name the target, next concrete action, constraints,
   and an observable acceptance check.** Further reading must resolve a named
   question that could change that action. Do not repeatedly map the whole repo.

## Route skills once, with an output and an exit

Load actual relevant skill bodies; a name or receipt alone is not application.
Reuse bodies and evidence still present and current in context. After compaction,
reload only what the next action needs. For each selected specialist, state its
purpose, expected evidence, and exit condition; read deeper references on demand.
One current task packet tracks ownership, state, and the next action
([packet and handoff format](references/task-packet.md)). Avoid duplicate ledgers.

For adoption, [absorb](../absorb/SKILL.md) owns capability mapping and native
integration. [rea-investigation](../rea-investigation/SKILL.md) supplies discovery
or verification evidence when relevant; it returns to its caller without
recursively starting Absorb. Ordinary source work uses source and tests directly.

## Execute the bounded RED → GREEN loop

Bind [bounded-loops](../bounded-loops/SKILL.md) before retries: one `mission_id`,
deadline, attempt/round allowance, and remaining resource budget. Child workers,
model changes, repair findings, and review rounds spend that same allowance.

1. **RED:** [red-first](../red-first/SKILL.md) establishes a real failing behavior
   check before the fix. Preserve its sealed file and baseline identity. For a
   review or research task, use its evidence contract without manufacturing edits.
2. **Build:** implement the smallest complete change at the owning seam; update
   affected consumers and compatibility surfaces. Preserve unrelated work.
3. **Lint and test:** [sniper-testing](../sniper-testing/SKILL.md) derives scope from
   `base_sha` through the current candidate, including relevant pending work.
   Execute the repository's applicable lint, format-check, typecheck, and behavior
   tests. Record exact commands, revision, results, and unavailable or inapplicable
   checks. A clean working tree does not erase committed scope.
4. **Quality:** [clean-code-gauntlet](../clean-code-gauntlet/SKILL.md) uses that same
   scope and budget for measured quality, bounded isolated mutation, and independent
   review. Add supplemental tests for new gaps without rewriting the sealed RED.
5. **Respond to evidence:** fix a proven finding and rerun only affected checks.
   Repeated identical failures require a changed hypothesis or tactic. On exhausted
   limits, checkpoint unresolved work; never reset the budget or lower the checks.

Use the repository's required aggregate landing gates once the candidate settles.
Report actual capabilities of this host; do not invent tools, tests, or reviews.

## Finish the requested outcome

Continue through authorized packaging, commit, publication, and verification when
those are part of the request. For external approval that is genuinely required,
first prepare the complete reviewable result. Keep the user informed of findings,
current action, evidence, and where to inspect live work; follow their cadence.

Distinguish **requested task delivered** from the invariant floor's **PROVEN**
capability status. A completed local patch or read-only review does not imply live
deployment. Failed or unavailable required gates remain visible: checkpoint
**STILL-BUILDING** with the exact blocker, remaining allowance, and next action.
In read-only mode return the packet in the response or an already-authorized
external task record; do not create a repository ledger or essence file implicitly.
