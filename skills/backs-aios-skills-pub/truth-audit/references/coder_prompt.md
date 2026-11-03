# Single-ball coder prompt (output mode b)

Use this when the human wants one ball handed to one coder or agent ("make a plan for
codex", "give me the prompt for the builder"). The prompt is self-contained: the
receiver gets no other context, so everything it needs is inside, and nothing it does
not need is.

Fill every `<...>`. Copy the ball's tests and interfaces verbatim from the plan; do not
summarize them.

---

```
ROLE
You are the builder for Ball <id> of the <product> repair plan. You build one ball and
nothing else. Someone else reviews and grades your work; you never grade it.

REPO
- Repo: <path or clone url>   Base: <branch> @ <sha>
- Create your worktree:  git -C <repo> worktree add -b ball/<id> <worktree path> <sha>
- Work only inside that worktree. Do not touch <live data dir>, the main checkout, or
  any other branch.

WHY (two sentences)
<What is broken on the human's surface, in plain words, and what done looks like for them.>

SPEC
Ledger entries: <ids>. Their evidence, root cause and fix seam (verbatim from the lanes):
<paste each block>

SCOPE (you may change only these files)
A path written `path@sha256:<hex>` was pinned when the ball was cut. Before you edit, check it:
  git show <sha>:<path> | sha256sum   -> must start with <hex>
If it does not, the file moved under you: stop and return 0 naming the file (the scope check
below also reports it as DRIFT).
<exact list, copied from the plan>
Anything else you find broken: write it as a block in <audit dir>/lanes/NEW_<id>.md
(format: ### N-<id>-NN · Pn · title, then status / surface / evidence / root cause /
fix seam / proof of done). Do not fix it.

INTERFACES (other balls rely on these exact signatures)
<paste>

RED TESTS (write and commit these first, prove they fail, then never edit them)
<paste each test name with its assertion>
Commit message for the red commit: "red: Ball <id> contract tests"

CONSTRAINTS
- No force push, no history rewrite, no push at all unless told.
- No model names in code, prompts or config; ask for a role and capability.
- Writes and test runs go to the sandbox: <recipe>. Never the owner's port, profile,
  credential store or scheduled tasks.
- Every loop bounded (ceiling + backoff + loud stop). Failures are loud; no swallowed
  exceptions, no "complete" with zero work.
- No drive-by edits, renames or reformatting.
- Budget: <time>, at most <4> build rounds, <0> paid calls. On exhaustion, stop and
  return 0 with a checkpoint.

PROOF YOU RUN BEFORE RETURNING
1. The red tests pass; the full suite of the touched modules passes.
2. python <audit dir>/tools/ball_scope_check.py <id> --plan <audit dir>/PLAN.md --repo <worktree>   -> exit 0
3. <the ball's sandbox proof command and the output it must show>
The native proof (<watchdog row / live observation>) is checked later by a reader, not by you.

RECEIPT (your whole reply, in this shape)
verdict: 1 | 0 | -1
  1 = done: every red test green, scope check 0, sandbox proof shown
  0 = hold: blocked or out of budget; checkpoint below says exactly where
 -1 = refuse: the ball is wrong or unsafe as written; say why in one paragraph
builder id: <the id the coordinator recorded with `ledger.py status <ID> IN-BUILD --builder <builder id>`>
branch: ball/<id> @ <sha>    red commit: <sha>
tests: <command> -> <passed/failed counts>
scope check: <exit code + summary line>
sandbox proof: <command> -> <observed output>
new findings: <path to NEW_<id>.md or "none">
side effects: <anything you did outside the worktree, or "none">
checkpoint (only for 0): <what is done, what is next, what blocks>
```

---

## Notes for the coordinator

- One prompt per ball. Do not batch balls into one prompt; scopes and receipts blur.
- Record the builder when you throw the ball:
  `ledger.py status <ID> IN-BUILD --by coordinator --builder <builder id>`, and put the same
  id in the prompt. The ledger then refuses that builder as prover, closer, adjudicator,
  or amender of its own entries.
- If the receiver is an agent with its own harness, the harness boot (for example the
  optimus skill) runs before this prompt; this prompt does not repeat the floor.
- Do not name a model in the prompt or pick one for it. The configured fleet ladder
  seats the builder.
- A -1 is information, not failure: fix the ball in the plan and re-throw it.
