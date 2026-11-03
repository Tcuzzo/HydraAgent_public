---
name: harness-optimizer
description: Improve an evaluation or agent harness through bounded baseline-versus-candidate measurements, holdouts, leakage checks, and durable evidence without benchmark overfitting.
license: Apache-2.0
---

# Harness Optimizer

Use this skill to sharpen a harness's routing, truth assembly, evidence capture, or evaluation quality. The goal is a measured platform improvement, not a higher score on a memorized benchmark.

## Establish the experiment

1. State the capability being improved and the layer under test: truth source, routing, control flow, prompt/rendering, or evidence handling.
2. Capture a baseline using the current system and fixed inputs. Record configuration, dependency versions, timing, errors, and per-case outcomes.
3. Freeze an evaluation split before tuning:
   - **smoke** for basic viability;
   - **search** for iteration;
   - **canary** for known fragile behavior;
   - **holdout** for unseen generalization;
   - **leakage** for overlap and benchmark-specific shortcuts.
4. Include balanced domain and mixed-domain cases when the harness serves more than one domain.

## Change one confound at a time

- Separate truth-source changes from routing changes.
- Separate control-flow changes from prompt or rendering changes.
- Keep providers, models, and runtime settings fixed unless that layer is the subject of the experiment.
- Do not hard-code benchmark phrases, expected counts, case identifiers, or hidden answers.
- Never edit an evaluation merely to hide a product defect.

## Measure and preserve evidence

Run the candidate through the same buckets and conditions as the baseline. Store a durable run record in the project-approved artifact location with:

- candidate identity and configuration;
- input-set versions or hashes;
- commands or runner settings sufficient to reproduce the run;
- raw outcomes plus a concise comparison;
- regressions, unknowns, and leakage findings;
- propagation targets when a shared substrate changed.

Update the project's capability matrix, dependency ledger, or coverage-debt record when those artifacts exist. Do not create a competing source of truth.

## Decision bar

Accept the candidate only when smoke passes, the target metric materially improves, canaries and holdouts do not regress beyond an explained tolerance, and leakage review finds no benchmark gaming. Reject unexplained uncertainty, single-phrasing gains, and stale scores presented as current evidence.

Classify the result plainly: substrate change, domain uplift, cross-domain uplift, or evaluation-only hardening. Report both wins and regressions.
