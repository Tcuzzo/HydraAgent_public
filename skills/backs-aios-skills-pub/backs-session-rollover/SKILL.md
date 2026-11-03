---
name: backs-session-rollover
description: Install, repair, and operate automatic context compaction and fresh-session handoff in BACKS build loops. Use for Claude Code, Codex, Hydra, or other frontier coder sessions approaching 125000 active context tokens; prolonged sessions; lost intent after compaction; stale memory; or repeated manual restarts. Preserve the original task and corrections, checkpoint verified work, replace the provider session, and resume the same unfinished step through the existing supervisor.
---

# BACKS Session Rollover

Keep the mission alive while replacing its crowded model session. Prepare a
checkpoint at 110000 active context tokens. Request a fresh session at 125000,
or earlier when the next bounded operation would cross that threshold or the
model has a smaller usable window. Preserve the original request, corrections,
current work, and remaining mission budgets through every rotation.

A skill supplies the procedure; a host observer and supervisor execute the
automatic transition. Do not report automatic handoff as enabled until the
actual host path has run.

For the standalone Bash installer, existing project updates, scheduler loading,
coverage reports and rollback, read [installation and boot](references/install-and-boot.md).
That installer applies native compaction policy and skill registration; its
report keeps live provider handoff unbound until the host integration is proven.

## Ground and bind once

1. Load [Optimus](../optimus/SKILL.md), the current invariant floor and canonical
   law, [Session Handoff](../session-handoff/SKILL.md),
   [Context Compiler](../context-engineer/SKILL.md), and
   [Bounded Loops](../bounded-loops/SKILL.md). Apply the current operator profile.
2. Discover the real provider launcher, active-context telemetry, memory
   authority, task journal, build loop, and writer ownership. Follow the live
   installation over historical paths. Use the [source audit](references/source-audit.md)
   as a search map, then recheck current source. Storage retention is not active
   context compaction.
3. Preserve the configured model, provider, reasoning effort, tools, sandbox,
   credential route, and permission scope. Rotation does not authorize a model
   switch, fresh spend budget, deployment, or unrelated action.
4. Bind the support code into the existing controller. Follow
   [runtime integration](references/runtime-integration.md) and
   [provider controls](references/provider-controls.md). Reuse the runtime's
   durable state store and writer lease. Do not install a competing memory
   database or replace its grounding hook.
5. Merge native compaction settings as a second protection where supported.
   Preserve existing configuration and hooks. Capability-check installed
   CLI/SDK versions and verify the effective setting.

## Preserve intent before compressing history

Maintain these records in the existing private task journal:

- The original operator request, verbatim, in an immutable record.
- An append-only record of corrections, decisions, permissions, and explicit
  cancellations. Pin a version for each checkpoint.
- Current mission ID, step, generation, provider session ID, worktree, branch,
  HEAD, dirty and untracked files, worker receipts, tests, failed approaches,
  next action, and remaining budgets.
- Evidence objects and artifact references with hashes and provenance.

Keep secrets in the existing credential system and private task records out of
public commits. Handoffs carry credential references, never credential values.
Preserve image and specification references when prose cannot replace them.

Compile each handoff from the original records and current evidence. Never
make a chain of summaries the only surviving specification. Keep operator
instructions distinct from inferred lessons, retrieved material, and model
proposals. Resolve conflicts using the latest applicable operator instruction.

## Observe outside the model

Run the observer before each model dispatch, after completed tool results,
after each build iteration, and on native compaction/reset events.

| Condition | Controller action |
| --- | --- |
| Below the preparation threshold | Continue; checkpoint each completed iteration. |
| At or above 110000 | Refresh the handoff while the session still has room. |
| At or above 125000 | Latch rollover; stop dispatching new build work. |
| Pending input crosses the boundary | Request rollover before sending that input. |
| Verified model window is smaller | Use available context minus reserved headroom. |
| Worker reaches its configured age limit | Rotate at a safe boundary; default four hours when age tracking is enabled. |
| Telemetry missing, invalid, or stale | Refresh or obtain a labeled conservative estimate; never treat unknown as zero. |

Use active context occupancy, including cached tokens once. Track cumulative
session spend and subagent spend separately. A display percentage, accumulated
character count, lifetime token count, or output max_tokens is not the required
measurement. Count instructions, tools, retrieved memory, pending tool output,
and reserved completion space in the dispatch budget.

Use scripts/session_guard.py for deterministic policy and checkpoint checks.
Its default reserve is 16000 tokens against the verified model window; size
pending input separately. Record whether telemetry is measured or estimated.
The 125000 setting is a scheduling boundary, not a guarantee that an unbounded
in-flight result can never overshoot it. Bound or externalize large results
before they enter context.

## Rotate at a safe boundary

1. **Latch.** Persist the reason and generation before compaction resets the
   meter. A later low reading cannot cancel a pending rollover.
2. **Quiesce.** Stop new worker dispatch. Finish, drain, or reconcile active
   transactions and worker receipts. Interrupted does not mean completed.
   Keep the operator's UI and controls available.
3. **Checkpoint.** Write a complete generation atomically and retain earlier
   generations. Record the exact continuation action and unchanged mission
   budgets. Preserve uncommitted work without committing, stashing, resetting,
   or deleting another session's files.
4. **Verify.** Check the schema, referenced hashes, immutable intent, correction
   version, Git identity, dirty inventory, and operation states. Follow the
   [checkpoint contract](references/checkpoint-contract.md).
5. **Compact.** Produce the bounded continuation packet. Native /compact may
   also run on the old session if supported; verify its completion event.
   Its summary is supplementary. A validated packet can proceed to a fresh
   session without an additional model summarization call.
6. **Start fresh.** Use the provider adapter and a durable idempotency key.
   Require empty prior history and a new provider session ID. History-preserving
   resume and fork operations do not qualify.
7. **Hydrate and acknowledge.** Load the current BACKS floor, exact intent and
   corrections, compact task state, and only the evidence needed next. Before
   granting writes, independently validate task ID, generation, checkpoint
   digest, instruction hashes, repository fingerprint, step, and next action.
8. **Transfer and resume.** Transfer the single-writer lease with compare-and-
   swap and a fencing token. Old workers must reject stale tokens. Activate
   the successor on the same mission and unfinished step. Retire the old
   worker after the successor accepts work. Keep receipts for both.

Use scripts/rollover_supervisor.py for the ordered transition contract. The
real host must provide its durable store and provider adapter. Bundled
in-memory adapters are tests only. Persist launch identity before launching
and reconcile it after a crash to avoid duplicate successors.

## Keep the next session small

Target at most 30000 tokens for the complete assembled startup context, counted
by the active provider tokenizer or count endpoint. This is a configurable
engineering budget. Account for the mandatory harness and tools before
allocating retrieved memory. The utility's 48000-byte checkpoint bound is not
a token measurement.

Keep exact instruction records addressable and load their applicable parts
faithfully. Retrieve evidence by task, file, failure, or decision. Deduplicate
facts and apply freshness and provenance. Keep traces and old turns in durable
storage instead of replaying them. Do not silently truncate requirements to
meet a budget; partition the work and keep the complete scope indexed.

## Recover without losing the mission

- Retain the last complete checkpoint after compaction, launch, hydration, or
  acknowledgement failure. Do not clear the journal or declare completion.
- Persist and bound rollover retries, default three failures with host backoff.
  Exhaustion yields a recoverable hold and a concrete repair action. It does
  not revoke operator tools or reset mission limits.
- Reconcile uncertain external side effects before repeating them. Persist
  operation IDs and receipts; do not claim universal exactly-once execution.
- Recheck repository and instruction versions before successor activation.
  Operator changes during handoff require a refreshed checkpoint.
- Apply this policy per coordinator and worker context. Parent rollover does
  not silently kill subagents or add their tokens to its own context count.
- Retain existing operator stop, continue, and reversible disable controls.
  Do not modify grounding or permissions to escape a failed handoff.

## Prove the live connection

Run the bundled regression suites, then test the actual launcher and control
surface in an isolated authorized worktree. Fixtures prove controller behavior;
they do not prove a Claude or Codex integration.

Require a trace showing the threshold event, complete checkpoint digest,
different provider session ID with empty history, loaded instruction hashes,
validated repository state, one active writer, and execution of the saved
next step with unchanged budgets. Inject launch failure and a stale handoff
to verify recovery. Exercise the provider routes actually in use.

Report skill/code status separately from live activation. State what was
verified on the operator's surface and the exact remaining blocker. Settings,
a mock, and a skill-loading receipt do not establish automatic continuation.
