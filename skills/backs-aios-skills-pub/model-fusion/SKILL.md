---
name: "model-fusion"
description: "Use when a task benefits from comparing independent candidate drafts within its remaining budget, or the operator requests fusion, a panel, an ensemble, or draft-and-merge. Drafting and judging use separate executions; the same capable configured model may fill every role."
license: "MIT"
---

# Model Fusion
**Effort:** heavy — a panel drafting in parallel plus an independent judge and optional writer. Use it when comparison can resolve a material uncertainty within the task's remaining budget.

Independent drafting executions produce alternatives for the same task. A judge
that authored none of the drafts picks or merges candidates against explicit
criteria. The winner is then checked against what was actually asked. A panel is
an optional method, not a requirement for ordinary builds or fixes.

**Model agnostic:** resolve each role by capability, actual availability and live
configuration. One capable configured model may fill every role in separate
executions. Independent reviewers use fresh contexts without the authoring
conversation. Different models, families and providers are optional; their names
do not determine eligibility or establish independence.

## When to run it

- When competing approaches can resolve a material task uncertainty and justify
  the additional work within the shared mission allowance.
- When the operator requests independent candidate comparison or a panel.
- NOT for trivial one-line changes. Make the direct change and verify it.

## The three stages

### 1. Panel — drafts in parallel

1. Send the same task, with the same context, to each configured drafting execution.
2. Each drafter works alone. No drafter sees another's work.
3. A drafter that errors, times out, or returns blank is logged and dropped.
   It never kills the round. Log the drop loudly — never swallow it.
4. Collect every non-empty candidate.

### 2. Judge — an outsider picks and merges

1. Before judging, run a cheap mechanical gate on each candidate: does it apply
   cleanly? Does it parse? Run the probe on a throwaway copy, never the live tree.
   Candidates that fail the gate are out before the judge sees them.
2. Two judge shapes — pick one per config:
   - **Synthesis:** the judge analyzes every candidate (strengths, defects, conflicts),
     then a separate writer execution composes the final answer from that analysis.
     Writer and judge are separate roles; they may use the same capable model.
   - **Selection:** the judge picks the single best candidate that passed the gate.
     Cheaper. Use it when merging adds nothing.
3. If the judge or writer is unavailable, degrade LOUDLY to selection over the same
   candidates. Never waste the panel silently; never pretend synthesis happened.
4. If no candidate survives the gate, append the best error to the prompt and rerun
   the panel — bounded, at most 2 repair rounds. On exhaustion return failure with
   the full error list. Never return an empty or no-op result as success.

### 3. Validate — check the winner against intent

1. Re-read the original ask. Does the winner do what was asked — all of it, and
   nothing it wasn't asked to do?
2. Check semantic correctness, style fit with the surrounding code, and that it
   still applies cleanly.
3. Low confidence is surfaced as an escalation flag, not hidden. Then prove it the
   normal way: failing test first, green, live behavior. A merged draft that never
   ran is a guess.

## The ladder

- Select panel size, candidate routes and output budgets from the task's needs and
  remaining [bounded-loops](../bounded-loops/SKILL.md) allowance. A new panel or
  repair round does not reset that allowance. Report unsupported configuration.
- Config format, roles-not-names, and live-probe resolution all belong to
  [fleet-ladder](../fleet-ladder/SKILL.md).

## Hard rules — break one and the skill failed

- **Builder never judges.** The judge authored no candidate. The final grader is
  chosen live by capability and availability from the configured rung matrix;
  the authoring execution that built is recorded as provenance and
  disqualified; the reviewer runs in a fresh author-disjoint session.
- **No hardcoded model names** at any call site. Roles in code, models in config.
- **No silent degradation.** Dropped drafters, judge fallback, gate failures, and
  exhaustion are all loud. An ungradeable result never passes by default.
- **Bounded repair.** Panel reruns have a hard cap. Exhaustion is a loud failure,
  not an infinite loop.
- **Green tests alone are not done.** The winner is proven on live behavior.

## Works well with

- [fleet-ladder](../fleet-ladder/SKILL.md) — resolve which models are up before the panel fires.
- [blind-tribunal](../blind-tribunal/SKILL.md) — the fail-closed grading court when the primary grader dies.
- [red-first](../red-first/SKILL.md) — the failing test that the winning draft must turn green.
- [blind-eval](../blind-eval/SKILL.md) — keep-or-revert taste gate when no test can decide.
