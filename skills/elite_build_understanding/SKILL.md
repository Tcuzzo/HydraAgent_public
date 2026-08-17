---
name: elite_build_understanding
description: >-
  The default diagnostic planner. Design -> Plan -> Build -> Test -> Ship, each
  stage validated by the live understanding organs. Drain-refute honesty: an
  organ that can only confirm is an echo, not understanding. The refute path is
  the honesty proof.
trigger_summary: build plan, design plan, understanding, drain refute, falsify, stage gate, elite build
---

# Elite Build Understanding — the stage-gated planner

## Purpose

Use the live understanding organs to interrogate a build from intent through
delivery: **Design -> Plan -> Build -> Test -> Ship**. This is a validation
discipline, not a new authority layer. The organs return evidence — scores,
verdicts, failures, recovery actions — and the caller revises from that
evidence. Advisory escalation is not a blocking gate.

## Anthropolithic deduction — translate the ask before you score it

Stage 0, before the stage gates. The human writes in compressed prose, metaphor,
and slang. That language is a full grammar carrying a full spec — priority, risk
tolerance, taste, and the reason. Two failures are forbidden:

- **Literalism** — running a metaphor as an instruction. That is hallucination by
  dictionary, and a destructive-action risk too.
- **Caricature** — performing the dialect to sound relatable instead of reading
  it. An agent busy performing is an agent not listening.
- **Over both: do not invent.** A thin anchor gets labelled thin. It is never
  filled in.

Run three pillars, in the human's own order:
1. **Syntactic deconstruction** — split carrier (cadence, heat, repetition marks
   priority) from payload (nouns, verbs, named surfaces, constraints are the
   instruction). Repetition is emphasis, not a second request.
2. **Cultural contextualization** — anchor every reading, strict priority: the
   human's own record and memory > this repo's source truth > the real lived
   vernacular as a valid dialect > model priors, last and never alone.
3. **Intent deduction** — state the directive as four separate things: intended
   capability, current boundary, the route now, the route later. Never lower the
   goal just because the near route is short.

Open with one line — "Read: <the directive in one sentence>" — then execute. The
stated reading is the receipt: if it is wrong, the correction costs one word.

## Stage gates, bound to live calls

| Stage | Question | Move |
| --- | --- | --- |
| Design | Is the spec clear and faithful to the original intent? | Revise the spec from named failures; do not invent a gate result. |
| Plan | Does the plan answer the intent and fit the surface? | Resolve recovery actions before implementation. |
| Build | Does the implementation satisfy the spec without drift? | APPROVED is strong evidence; REVISE and REJECT identify work to repair. |
| Test | Do the tests exercise real behavior and make it testable? | Keep only candidates that pass the behavioral proof and the understanding hook. Sniper-only; assert real side-effects. |
| Ship | Does the candidate apply cleanly, stay loud on failure, and preserve tool truth? | Land only after failures are repaired and the surface proves the behavior. |

## Drain-refute honesty (the honesty proof)

Understanding must refute false claims, not merely agree with the candidate. An
organ that can only confirm is an echo. The refute path is what makes it honest.

- Run `refute_fabricated_claim` on any claim that references a symbol, a quote, a
  file, or a test result. Extract every referenced item and resolve it against
  the installed artifact. Any unresolved item refutes the claim.
- A fabrication should drive the score to the floor, expose the false statement
  in `flagged_claims`, and make `passed` false.

## Confidence and recovery discipline

1. Preserve the original intent as the comparison anchor.
2. Record the dimensional result, not only a top-line score.
3. Treat every named failure as a repair target.
4. Apply the returned recovery actions, then rerun the same validator.
5. Never claim readiness from confidence alone. Tests and the real surface
   proof still decide whether the capability is done.

## Green-but-wrong / echo class

A test that mocks the exact seam under change proves only its own canned answer.
The correct boundary is the outermost model leaf: replace the model call with a
canned verdict, but never replace the validator whose scoring and pass/fail
logic is the proof. Mock the unstable external leaf; never mock the organ whose
behavior is the proof.