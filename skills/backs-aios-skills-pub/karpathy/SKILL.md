---
name: karpathy
description: Improve a clean Git project through a bounded, measured experiment in a disposable clone. The human writes a Markdown goal, metric gate, Fair game files, and budget; the engine rejects out-of-scope edits and keeps only proven improvements in the clone.
license: MIT
version: "1.1"
---

# karpathy — program the Markdown, not the code

A portable, **stdlib-only** measured experiment loop. Drop this folder into any agent
or repo. You write *what "better" means* in plain English (a `program.md`); a model
loops on its own: **propose a change → run a short experiment → measure the one
metric → keep it if it's better, throw it away if it's not → repeat.**

This gives an agent a bounded way to test proposed improvements before shipping:
give it this folder + the prompt in `USE_THIS_PROMPT.md`, and make the independent
tester and metric visible. A kept change is evidence under that test, not proof of
general capability.

## Where it comes from (and why it's yours, not a copy)
Andrej Karpathy's `autoresearch` (github.com/karpathy/autoresearch) crystallized one
idea: *the human edits a Markdown program, not the Python.* His agent edits one file,
runs a short experiment, scores one metric, keeps or discards, repeats ~100×/night.
This is that idea rebuilt with a hard gate, an enforced Fair game file list,
and a disposable Git clone. Studied and redesigned, not vendored.

## Files in this bundle
- `evolve.py` — clone→propose→test→keep/revert with a hard gate and Fair game.
- `program.py` — the Markdown front-end: `parse_program()` + `run_program()`.
- `program.template.md` — copy this to write your own research program.
- `example.py` — **run it** (`python3 example.py`) to watch a real keep + a real
  revert on a throwaway git repo. Your proof it works.
- `USE_THIS_PROMPT.md` — the prompt to paste into a new agent before it ships.

## The program.md (what you write)
See `program.template.md`. The shape:

```markdown
# <one line: what this loop is for>

## Goal
<plain English — what we're improving and why>

## Metric
- success_rate | max | 0.95   <!-- first axis carries the hard BAR (must clear 0.95) -->
- cost         | min          <!-- then optimize: lower is better -->
- p95          | min          <!-- then: lower latency -->

## Fair game
- config/*.yaml               <!-- the ONLY files the proposer may change -->

## Budget
rounds: 20                    <!-- how many tries (Karpathy ran ~100/night) -->

## Sandbox
required: true                <!-- required; engine mutates a disposable clone -->

## Eval
cmd: pytest -q                <!-- optional: run before scoring; non-zero exit -> revert -->
```

- **Metric line** = `name | max|min | gate?`. The first axis usually carries the
  **hard bar** — a level to *clear*, not a number to push past (0.95 and 0.96 rank
  equal, so you can't nudge it up to "pay for" a cost regression). Remaining axes
  optimize **lexicographically** in the order written.
- The bar is checked first. A higher-priority regression or tie does not prove
  uplift. If the program has only one gated metric, it also ranks candidates.
  Any edit outside Fair game is rejected before scoring.

## Use it (Python)
```python
from program import parse_program, run_program

prog = parse_program("my-study.program.md")
result = run_program(
    prog,
    target_dir="/path/to/a/clean/git/repo",  # source stays untouched
    propose=my_proposer,    # a model that edits a fair-game file in place
    test=my_blind_tester,   # a blind scorer: returns {name: value, ...}
    sandbox_root="/writable/experiments",  # optional; use if default temp is restricted
)
# -> includes final_champion, log and sandbox_dir for review
```

- **Proposer** = a caller-supplied model or function. Keep selected source local
  unless the operator explicitly authorizes sending it to a remote provider.
- **Tester** = a *blind* scorer that turns runtime state into the numbers and never
  sees the proposer's reasoning (no self-grading). If you give an `## Eval` command,
  it runs first and a non-zero exit reverts the round.
- A kept round is committed only in the disposable clone. A rejected round
  resets its index and worktree and removes generated files. New scorer scratch
  files are removed before a candidate is judged. The return value includes
  every round's action and reason, plus an `errors` list. Review the champion
  diff and independent evaluation before applying it to the real project.
- Call `evolve.cleanup_sandbox(result["sandbox_dir"])` after preserving any proof
  you need. It refuses paths outside a marked disposable clone. You can set
  `KARPATHY_SANDBOX_ROOT` instead of passing `sandbox_root` for a host-wide
  writable temp location; keep that root outside the source checkout.

## Laws baked in
- **Disposable clone required.** A path check and Git reset protect the repo;
  they do not confine a proposer process. Run untrusted code in an OS sandbox.
- **Prove uplift, never regress** — only a candidate that clears the bar AND strictly
  beats the current best is kept; everything else is discarded.
- **Never grade your own work / no fake-green** — the Tester is injected + blind;
  keep/revert is enforced against real git state.
- **Never lower the bar to fake a pass.** Fixing the score by weakening the test is
  forbidden — keep the eval/bar OUT of the fair-game files.

Requires: Python 3.9+ and `git`. The source must be a clean Git top-level.
