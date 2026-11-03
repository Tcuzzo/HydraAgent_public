---
name: "truth-audit"
description: "Use when someone needs to know what is really running and what is really broken before anyone fixes it, and wants the answer as a spec a coder or agent can execute. Read-only live probes (processes, ports, scheduled jobs, launchers, imported source trees, git and remote state, content hashes), parallel read-only lanes, coordinator verification, an append-only gated ledger, root-cause classes, then a repair or uplift spec in waves of disjoint balls with red tests and native proofs, or one self-contained prompt for one coder. Trigger words: audit, truth audit, what is really running, is this the same as GitHub, full audit, find the bugs, repair spec, uplift spec, handoff spec, make a plan for codex, live truth, drift, which code is live."
license: "MIT"
---

# Truth Audit
**Effort:** heavy — parallel read-only lanes, a coordinator pass that re-checks every load-bearing claim, and a tribunal grade of the spec before any builder starts; it pays when a product has drifted across checkouts, machines, or agents and nobody can say which code is live. Removes: fixes built against the wrong tree, bug lists nobody can execute, and builders who rediscover the same facts one at a time.

A truth audit answers two questions in order: what is actually running, and what is
actually wrong with it. It ends in a deterministic spec that any coder or agent can
execute without asking. It changes nothing while it looks. The product is never
fixed by the audit; it is fixed by the balls the audit hands off.

## When to run

- The human asks "what is really running", "is this the same as GitHub", "full audit",
  "find the bugs", or wants a repair, uplift, or handoff spec for another agent.
- Several checkouts, forks, hosts, or agents have touched one product and the story
  no longer adds up.
- Before a large repair, so builders start from verified facts instead of guesses.

Not this skill: one known bug (use [repair-loop](../repair-loop/SKILL.md)); an outage
in progress (use [incident-closure](../incident-closure/SKILL.md)); a defect hunt on
code whose deployment you already trust (use [bughunt](../bughunt/SKILL.md)).

## Load first

1. [invariant-floor](../invariant-floor/SKILL.md) — the laws the audit enforces, law 13 above all.
2. [intent-compiler](../intent-compiler/SKILL.md) — read the ask before probing anything.
3. [live-truth checklist](references/live_truth_checklist.md) — the phase 2 probes.

## The method

### 1. Boot and read the ask

State one line: what you think the human wants. Then four parts: **intended
capability** (what the product should do for them), **current boundary** (what it
does today, as far as you know), **route now** (this audit and its spec), **route
later** (what the spec unlocks). Keep the human's own words where they carry meaning.

### 2. Live truth first (coordinator, read-only)

Before any lane is thrown, the coordinator establishes what runs, using
[the checklist](references/live_truth_checklist.md) (PowerShell and POSIX variants):

- processes with full command lines and parent chains, listening ports to owning
  process, scheduled tasks, services, timers, and what each launcher or shortcut runs;
- which source tree each process imports, asked of the interpreter the process uses
  (`<python> -c "import pkg; print(pkg.__file__)"`), never inferred from folder names;
- git remotes, branches, and dirty state per checkout, and what the remote really
  holds (`git ls-remote`, not a stale local clone);
- **content-hash comparison** of trees with `scripts/cmp_trees.py` (CRLF normalized,
  caches excluded), instead of trusting commit ids or folder names;
- history the repo forgot, recovered read-only from coding-agent session logs (a
  force push or a deleted repo is often recorded in a transcript);
- brand and identity assets by hash, not by look.

Every claim gets the command that proves it and what it printed. Correct your own
mental model here: the window the human uses defines "the product".

### 3. Fan out read-only lanes

One lane per independent concern (authority path: who can spend, delete, or publish; callable API surface,
data quality, every input surface, UI walk with screenshots, remote hosts and jobs,
test theater). Throw each with [the lane prompt](references/lane_prompt.md), following
[leap-protocol](../leap-protocol/SKILL.md) handoff discipline: a complete unbiased spec,
the read-only rules, a budget, and exactly one owned output file in
[the lane format](references/lane_format.md):

```
### <LANE>-NN · P0|P1|P2|P3 · <title>
- status: OPEN · confidence: high|med|low
- surface: ...
- evidence: <commands + observed output, file:line>
- root cause: ...
- fix seam: <files/symbols>
- proof of done: <native observed check, passes without a builder present>
```

Read-only means: GET only against running services; databases copied before they are
opened; never the owner's browser profile or credential store; anything that writes
runs in a sandbox instance with its own home, port, and keyring. Each lane discloses
every side effect it caused (a test that took focus from the owner's window, an
outbound request carrying personal data, a paid model call), including "none".

### 4. Verify every lane before promoting (coordinator)

Spot-check each lane's load-bearing claims against live state yourself before they
enter the ledger. Lessons that are rules:

- **Grep the right file.** The newest file in a log directory may be a lock, an
  archive, or another tool's output. Confirm it is the log by name and content first.
- **A count mismatch usually means a different endpoint, filter, or time window, not
  a lie.** Find which before calling either side wrong.
- **Formatting variants are not missing evidence.** `- evidence (read-only):` and
  `- surface and evidence:` are evidence. The importer tolerates them; so do you.
- **Positive findings are recorded.** "The data is real" becomes an
  ADJUDICATED-NOT-A-BUG entry with its evidence, never dropped. It saves the next
  auditor the same check.

### 5. Append-only ledger with structural gates

`scripts/ledger.py` keeps the ledger as one JSON event per line, each line chained to
the hash of the one before it, with a head file beside it (`ledger.jsonl.head`) that
records the event count and the last line's hash:

```
python scripts/ledger.py --ledger AUD/ledger.jsonl import-lane AUD/lanes/SVC_SERVICE.md --by coordinator
python scripts/ledger.py --ledger AUD/ledger.jsonl status SVC-03 PLANNED --by coordinator --task 1A
python scripts/ledger.py --ledger AUD/ledger.jsonl status SVC-03 IN-BUILD --by coordinator --builder ball-1A-builder
python scripts/ledger.py --ledger AUD/ledger.jsonl amend SVC-03 --by coordinator --evidence "recount: ..."
python scripts/ledger.py --ledger AUD/ledger.jsonl check     # gate: exit 1 on any broken rule
python scripts/ledger.py --ledger AUD/ledger.jsonl render    # STATUS.md, generated
```

Statuses move one step at a time, OPEN, PLANNED, IN-BUILD, PROVEN, CLOSED, or end as
ADJUDICATED-NOT-A-BUG with evidence. The tool refuses duplicate ids, skipped or backward
moves, PROVEN or CLOSED without evidence, a P0/P1 going IN-BUILD without a plan task,
an IN-BUILD move that does not name its builder (`--builder`), the builder or the actor
who moved it IN-BUILD proving it, and anyone who built or proved an entry closing,
adjudicating, or amending it. A lane
re-imported with different evidence is refused until the change is recorded with
`amend`. `check` replays every rule over the whole log, verifies the hash chain (an
edited, inserted or removed line), and compares the head file (truncation at the end).
If someone rewrites the ledger and its head file together, only an outside copy of the
head hash, kept in a receipt or a commit, can show it.

### 6. Root-cause classes, not a bug list

Group findings into a handful of classes and fix each class at its shared primitive,
then sweep the siblings ([seam-engineering](../seam-engineering/SKILL.md)). Classes
that recur across products:

- many sources of truth (forks, stale clones, two runtime roots, uncommitted running code);
- many authorities over one resource (two workers writing one queue, two kill switches, three answers to "what is live");
- silent failure dressed as success (endless retries, green health on a 401, "complete" with zero work);
- inputs the engine never reads (settings with no reader, hard-coded lists, orphan pages);
- undefined trust boundaries (unauthenticated routes, tokens served to any caller, a live path one flag away);
- tests that prove mocks (the seam a test names is the seam it replaces);
- a harness that lets agents pick models or loop blind.

Every open P0/P1 id belongs to exactly one class and at least one ball.

### 7. Owner decisions by deduction

Run [operator-intent-deduction](../operator-intent-deduction/SKILL.md) before asking
anything. Computable questions get computed or simulated. Values and vision questions
get deduced from the human's record and stated, with a one-sentence disagreement if you
have one. Only taste, vision, or destructive and irreversible risk reaches the human,
as one decision with a recommendation ([decision-bar](../decision-bar/SKILL.md)).

When the human holds a factual misbelief the audit disproved, correct it once, plainly,
with the proof: "the window you use is the Python app, not the C# one; here is the
process tree."

### 8. The repair or uplift spec (the deliverable)

Write the plan from [the template](references/plan_template.md), which carries one
fully worked ball. A plan in waves of balls. Every ball has: ledger ids, **Goal**,
**Gate** (a metric), **Scope** (exact files; balls in one wave never share a file),
**Interfaces** (exact signatures other balls rely on), **red tests** by name with their
assertion, implementation decisions only where the tests leave a choice, **native
proof** (a watchdog row or a live observation, never a green test alone), **rollback**,
and **budget**. Every ball doubles as a [karpathy](../karpathy/SKILL.md) experiment:
goal, metric gate, fair-game files, budget.

The plan also carries: a repos and roots table; global constraints (no force push, no
model names at call sites, test-account and sandbox rules, bounded loops, loud failures, red
first); roles (coordinator as single write spine, ledger keeper, builders, readers and
testers, tribunal convened through the configured ladder only); a review focus of five
unpinned failure modes, each with the test that pins it; and a short list of open owner
items with the deduced default.

Two scripts gate the plan: `scripts/ball_scope_check.py --disjoint --plan PLAN.md`
fails when two balls of one wave own the same file, and
`scripts/ball_scope_check.py <ball> --plan PLAN.md` fails a branch that touched
anything outside its ball's scope, or whose pinned files (`path@sha256:<hex>`, the
LEAP content hash) changed at the base since the ball was cut.

### 9. Two output modes

- **Full spec** — the plan above, plus the ledger and lane files it cites.
- **One ball, one coder** — a single self-contained prompt from
  [the coder prompt template](references/coder_prompt.md): role, repo, branch and
  worktree, scope, red tests, constraints, proof, and a receipt of -1 (refuse),
  0 (hold with checkpoint), or 1 (done with evidence). It is a context packet in the
  sense of [context-engineer](../context-engineer/SKILL.md).

### 10. Grade the spec before anyone builds

The spec goes to [blind-tribunal](../blind-tribunal/SKILL.md) before the first builder
starts: envelope = plan, ledger, lanes, status board, with author fields redacted.
Jurors are defined by lens, envelope, and protocol. Seats come from the configured
[fleet-ladder](../fleet-ladder/SKILL.md), and a session that did not write the spec
grades it. The auditor never seats or names a model. Every refusal becomes a plan edit;
reconvene until unanimous, then move entries to PLANNED with their task.

## Hard rules — any one of these fails the skill

- **No mutation during the audit.** No commit, push, restart, install, write route,
  or live database open. Writes happen only in a sandbox the lane declared.
- **Every claim has a re-runnable command.** Command plus observed output, or file:line.
- **Content hashes over names.** A folder name, branch name, or commit id is a claim;
  a hash comparison is evidence.
- **Lanes own one file each.** No lane writes outside its file and scratch.
- **The coordinator verifies before promoting.** Nothing enters the ledger on a lane's
  word alone.
- **The ledger is append-only and gated.** `ledger.py check` passes, or the wave halts.
- **No fake green.** A test result is never a native proof; PROVEN needs an
  observation on the real surface with no builder driving it.
- **No model choice by the auditor.** Roles and capabilities only; the configured
  ladder seats every builder and juror.
- **Side effects are disclosed.** Every lane and the coordinator list what they
  touched, including "none".
- **Plan balls have disjoint scopes and native proofs.** `ball_scope_check.py --disjoint`
  passes, and every ball names a proof that holds after the builder is gone.

## Bundled tools

| file | what it does |
|---|---|
| `scripts/ledger.py` | append-only hash-chained ledger: add, import-lane, status, list, check, render |
| `scripts/ball_scope_check.py` | per-ball scope gate on a branch; `--disjoint` wave overlap gate |
| `scripts/cmp_trees.py` | CRLF-normalized SHA-256 tree comparison; exit 0 same, 1 differ, 2 error |
| `scripts/test_truth_audit_scripts.py` | regression tests for the three tools (stdlib only) |

All are Python 3.10+ stdlib. Copy them into the audit directory (`AUD/tools/`) so the
plan's commands work without this pack installed.

## Report

To the human, in [human-voice](../human-voice/SKILL.md): what is really running (one
short paragraph), the root-cause classes with counts, the one decision that is theirs
if there is one, and where the spec lives. Raw ledger rows stay in the files.

## Works well with

- [live-research](../live-research/SKILL.md) — reads docs and source; this skill probes the running system.
- [parallel-work](../parallel-work/SKILL.md) · [leap-protocol](../leap-protocol/SKILL.md) — lane and ball handoff.
- [seam-engineering](../seam-engineering/SKILL.md) — fixing each root-cause class at its primitive.
- [red-first](../red-first/SKILL.md) · [sniper-testing](../sniper-testing/SKILL.md) — what each ball's red tests and proofs must honor.
- [understanding-gates](../understanding-gates/SKILL.md) — checks the spec still matches the ask.
- [repair-loop](../repair-loop/SKILL.md) — what each builder runs inside its ball.
- [bounded-loops](../bounded-loops/SKILL.md) — budgets for lanes and builders.
- [guided-steps](../guided-steps/SKILL.md) — for owner-only steps the spec cannot automate.
