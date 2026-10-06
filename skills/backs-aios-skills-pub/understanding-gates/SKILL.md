---
name: "understanding-gates"
description: "Use when a build, fix, or uplift is moving from intent toward delivery and you need proof it still matches the original ask. Interrogates Design, Plan, Build, Test, and Ship with approve/revise/reject verdicts, named failures as repair targets, and a rerun after every repair. Trigger words: understanding, stage gates, validate build, spec match, verdict, green but wrong, echo check, done means done."
license: "MIT"
---

# Understanding Gates
**Effort:** light — one validator pass per stage, scored against the original ask and rerun after each repair. Removes: drift validated against a paraphrase — the build that lands green but answers a question nobody asked.

A validation discipline for builds. It interrogates the work at five stages (Design, Plan, Build, Test, Ship), always against the ORIGINAL ask, never against the work's own restatement of it. Each gate returns evidence: scores, a verdict, named failures, and repair actions. It binds the agent, not the human: no new approval step, no friction on the person who asked.

## When to run it

- Any build, fix, or uplift that will land somewhere real.
- Any time you are about to say "done" and the only proof is a green test.
- After every repair, on the same stage that failed.

## Stage 0 — anchor the intent

Before scoring anything, fix the comparison anchor: the human's ORIGINAL words, plus a one-line translated directive (see [intent-compiler](../intent-compiler/SKILL.md)). Every gate scores against that anchor. Never score against your own paraphrase — a paraphrase drifts, and then every gate quietly validates the drift instead of the ask.

## The five gates

Each gate asks one question against the original intent:

| Stage | Question |
| --- | --- |
| Design | Is the spec clear and faithful to the original ask? |
| Plan | Does the plan answer the intent and fit the surface it ships to? |
| Build | Does the code satisfy the spec without drift? |
| Test | Do the tests exercise the real behavior, not a stand-in for it? |
| Ship | Does it apply cleanly, fail loud, and does the delivery claim survive a fact check? |

Score EVERY gate on the same five lenses, each 0–4: spec match, architectural fit, type safety, testability, security — phrased for the stage (at Design, "testability" asks whether the spec is checkable; at Ship, whether the delivery claim is). Roll-up: sum the five lenses (0–20), multiply by 5 — that is the gate's 0–100 verdict score. Record every lens, not just the total — the total hides which lens failed.

## Verdicts

Roll the lenses into a 0–100 score and band it:

- **Approve** (80+): strong evidence. Still not proof of done — see the second law.
- **Revise** (60–79): named failures exist. Each one is a repair target.
- **Reject** (below 60): the work misses the intent. Go back a stage.

A verdict with no named failures behind it is a low-information verdict. Demand the list.

## Repair discipline

1. Keep the original intent as the anchor for every rerun.
2. Record the per-lens scores, not only the top-line number.
3. Treat every named failure as a repair target. No failure is decoration.
4. Repair, then RERUN THE SAME GATE. A repair without a rerun is just a claim.
5. Never promote confidence into readiness. Tests and the real surface decide.

## The two laws

**1. The echo law.** A check that can only agree is an echo, not a validator. The honesty proof is refutation: feed it a claim you know is false and watch it fail that claim. If it passes the lie, the check is theater. Corollary on mocking: mock only the unstable external leaf — a paid API, a flaky network. Never mock the organ whose behavior IS the proof; its scoring, claim extraction, and pass/fail logic must run for real.

**2. Necessary, not sufficient.** A passing test is necessary, never sufficient. Done means the real surface (the one the human actually uses) does the job on its own. Name that surface, trigger the real path, and watch the correct result arrive. Never promote a unit-test receipt into a live-capability claim.

## Hard rules (what fails this skill)

- Scoring against a paraphrase instead of the original ask.
- A revise or reject verdict with no named failures attached.
- Repairing without rerunning the gate that failed.
- Mocking the validator itself, or the exact seam under change.
- Claiming done from a green test with no real-surface proof.

## Keep a build record

For each stage keep: the intent, the exact input artifact, the scores, the named failures, the repair performed, the rerun result, and the real-surface evidence. A record that does not point to reproducible evidence is a banner, not a record.

## Works well with

- [intent-compiler](../intent-compiler/SKILL.md) — translate the ask before you score it.
- [red-first](../red-first/SKILL.md) — the Test gate's contract: failing test committed first.
- [sniper-testing](../sniper-testing/SKILL.md) — real side-effects, no mock theater.
- [blind-tribunal](../blind-tribunal/SKILL.md) — independent graders on top of these gates.
- [repair-loop](../repair-loop/SKILL.md) — the loop that drives revise verdicts to green.
