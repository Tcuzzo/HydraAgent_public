# <one line: what this research loop is for>

## Goal
<!-- Plain English. What are we trying to make better, and why? Write it the way you'd
     explain it to a friend. The loop reads this to understand intent; the blind Tester
     never sees it. Put the most important thing first — success usually comes before
     cost, cost before speed. -->

## Metric
<!-- One line per axis:  name | max|min | gate(optional)
     - The FIRST axis usually carries the hard GATE: a bar that MUST be cleared.
       A gate is a bar to clear, not a number to push past (0.95 and 0.96 rank equal).
     - The rest are optimized in the order written (top one wins ties on the one below). -->
- success_rate | max | 0.95
- cost | min
- p95 | min

## Fair game
<!-- The ONLY files the loop is allowed to change (Karpathy's "edit one file" rule).
     Globs are fine. This section is required and enforced by the engine. -->
- config/*.yaml

## Budget
<!-- How many propose -> test tries. Karpathy ran ~100 a night; start small and grow. -->
rounds: 20

## Sandbox
<!-- true is required. The engine makes a disposable clone of the clean source. -->
required: true

## Eval
<!-- Optional. A command run before each score; a non-zero exit reverts that round.
     Omit this whole section if your Tester does all the evaluation itself. -->
cmd: pytest -q

## Target
<!-- Optional. The git worktree to evolve. Can also be passed in code at run time. -->
dir:
