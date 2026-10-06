---
name: "model-fusion"
description: "Use when one model's answer is not trustworthy enough — a hard build, fix, or design where you want several models to compete and an independent judge to pick. A panel drafts in parallel, a judge merges the winner, the result is validated against the original intent. Trigger words: fusion, panel, judge, multi-model, ensemble, draft and merge, builder not grader."
license: "MIT"
---

# Model Fusion
**Effort:** heavy — a full panel drafting in parallel plus an independent judge (and optional writer); spend it on hard builds and fixes that ship, never on one-line changes. Removes: betting the change on a single model's draft, and the rework when that one draft is wrong.

Many independent voices beat one voice. A panel of models drafts the same task in
parallel. A judge (a model that wrote none of the drafts) picks or merges the best.
The winner is then checked against what was actually asked.

## When to run it

- Any substantial build, fix, or uplift where quality matters more than speed.
- When you want a specific pair of independent graders, not blind trust in one model.
- NOT for trivial one-line changes. Make the direct change and verify it.

## The three stages

### 1. Panel — drafts in parallel

1. Send the same task, with the same context, to every panel model at once.
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
     then a separate writer model composes the final answer from that analysis.
     Writer and judge are different roles; keep them different models when you can.
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

- Fusion's rung shape: a wide panel of cheap models at the bottom, tighter panels and
  tighter output budgets climbing up — a misconfigured rung fails loud at load time.
- Config format, roles-not-names, and live-probe resolution all belong to
  [fleet-ladder](../fleet-ladder/SKILL.md).

## Hard rules — break one and the skill failed

- **Builder never judges.** The judge authored no candidate. The final grader is a
  different model (ideally a different family) from whoever built the winner.
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
