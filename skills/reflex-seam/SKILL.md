---
name: reflex-seam
description: "Use when a turn might be settled before the full model loop, or when a file edit should face the sniper, lint, and mutation gauntlet. Covers the reflex seam, the reflex rung, and reflex-bench."
allowed-tools: fs_read list_directory grep shell
---

# Reflex seam

Use when an agent is about to spend a full model loop on a turn that a cheaper check can settle, or when a file edit should be rejected by tests before another model turn.

## The three steps

1. **R1, the rule reflex.** A deterministic regex maps a clear intent onto a tool hint (`fs_read`, `list_directory`, `grep`). No model runs. If nothing matches, the hint is empty and the next step still runs.
2. **Reflex rung.** `hydra/model_routing.yaml` names a rung called `reflex` and nothing else for that entry. The decision service resolves it. The answer is one schema-checked decision: `next_tool`, `done`, `retry`, `stop`, or `escalate`.
3. **Full model loop.** It runs only when the decision is `escalate`, or when the reflex rung is unavailable. Unavailability falls through to today's loop. There is no hidden default model and no crash.

Engage the tier with `HYDRA_REFLEX=1`. Leave it unset and the loop behaves as it does today.

## The gauntlet hook

After a file edit (`fs_edit`, `fs_write`, `apply_patch`), the gauntlet can run three gates:

- **Sniper.** Only the tests that mention the changed files.
- **Lint.** `ruff check` on the changed files, plus `ruff format --check` when format is configured.
- **Mutation.** A time-boxed operator flip inside the changed function (a built-in flip; an external mutator is not required). A mutant the tests still pass is a failure.

A failing gate returns a structured verdict and restores the pre-edit bytes. The loop stops on that verdict and does not spend another model turn judging the edit.

The hook is off unless `HYDRA_REFLEX_GAUNTLET=1` or `.hydraAgent/reflex_gauntlet.json` sets `enabled` to true. A tool that is not installed is reported as `skipped (tool missing)`. A skip is never a pass.

## Measurement

`hydra reflex-bench` replays a fixed set of scripted tasks with a stub decision client and writes a JSON receipt of big-model loop counts with and without the tier. That receipt is stub-only. Live measurement needs the real reflex rung.
