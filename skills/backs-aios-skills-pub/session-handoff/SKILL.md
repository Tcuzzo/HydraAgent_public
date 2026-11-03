---
name: "session-handoff"
description: "Use when a session is ending, context is about to compact, a mission allowance is exhausted, or unfinished work must continue in another agent or harness. Trigger words: handoff, hand off, compact, save state, continue in another session, portable handoff, before restart."
license: "MIT"
---

# Session Handoff
**Effort:** free — one flat file written before the context dies; no model calls, and it cuts cost outright at the next session start. Removes: the fresh agent re-deriving state, re-paying traps already paid for, and decisions lost in a dead context window.

A context window dies; the work must not. Before a session ends or compacts,
write one flat file a brand-new agent can read cold and continue from — what
was being done, where it lives, what is half-done, and the exact next command.
A build handoff must survive the context window. Use the existing
[BACKS Build Flow](../backs-build-flow/SKILL.md) task record; this skill transfers
that task rather than creating another owner, context pass, or mission.

## When to write one

- Before the context window compacts or is cleared.
- When ending a session with work still open.
- Right after landing something big (record the commit id while it is fresh).
- The moment a real decision goes to your human (record what each choice means).

## Where it goes

One known place the next agent will look FIRST. If the next agent shares your
project, use a stable ledger file in the repo and commit the update so it
survives a machine restart, not just a context clear. If the next agent is a
different harness or a fresh login, write a flat portable file to the temp
dir — it is scaffolding, not a tracked artifact.

Honor `task_mode` and the task's write permissions. For a read-only or frozen
review, return the handoff in the response or an already authorized external
record; never write a ledger into the protected repository to satisfy this skill.

## Verify concurrent work first (before writing a word)

Check that work from OTHER sessions is preserved. Run `git status`,
`git log`, and `git worktree list`. Note dirty files and unmerged branches in
the doc honestly. Never alter another session's uncommitted work to make the
handoff look clean — that is the data-loss defect. A handoff that describes a
clean state while another session has work in flight is a false claim.

## What goes in — one short section each

1. **Goal.** The work in one sentence. The next agent must not have to guess
   what "done" means. Carry `mission_id`, `task_mode`, the integration owner,
   requested finish, and already authorized delivery actions.
2. **State.** Landed (commit ids), building, queued. Reference specs, plans,
   issues, and diffs by path or URL — never duplicate their content. Include the
   pinned `base_sha`, current `candidate_sha`, sealed RED paths/commit/hashes,
   check commands/results, and the exact candidate each receipt covers.
3. **Where the work lives.** Branches, worktrees, dirty files. Name the exact
   files the next agent must read first.
4. **The verdict trail.** Who or what graded each piece and what the real
   catches were. A failed verdict with named defects is MORE valuable than a
   green — write the defects verbatim.
5. **Half-done work and the exact next command.** What is mid-flight, and the
   literal command that continues it.
6. **Open decisions.** Anything waiting on your human, and what each choice
   means. A decision must never exist only in a dead context window.
7. **Unmet contracts.** Tests still red, proofs still missing, promises made
   but not yet kept.
8. **Traps.** One line each. A trap you already paid for is worth more than a
   green — write it so the next session does not pay again.
9. **Suggested skills.** Which skills the next agent should load first, and
   one line why. Name actual bodies/evidence already available, what needs
   refreshing, and the operator essence source; do not copy a second profile.
10. **Remaining budget.** Carry the original deadline and total/used/remaining
    attempts, rounds, and other limits from
    [bounded-loops](../bounded-loops/SKILL.md), plus pending calls and the last
    hypothesis/failure. Unknown values stay unknown. Never reset the mission
    budget during handoff, compaction, worker replacement, or model change.

## Hard rules

- **Redact.** No API keys, passwords, tokens, or personal data. No real
  hostnames, internal IPs, or home paths — placeholders only; point at real
  values by env var name. A handoff is the file most likely to leave the
  machine; a secret that leaks through it IS the bug.
- **Absence claims rot fastest.** Before writing "X does not exist" or "X is
  not landed," re-verify at the current commit — parallel work lands while
  you write.
- **Evidence-backed status per item.** Capability delivery uses PROVEN only with
  its required review and real-surface proof; otherwise STILL-BUILDING names the
  missing evidence. A completed review or local patch may satisfy its requested
  finish without being relabeled as a deployed capability.
- **Keep it readable in two minutes** (about 120 lines). When it grows past
  that, archive the oldest blocks by moving them to a history section —
  never by deleting.

## Pickup (the other half)

A session that starts from a handoff reads it FIRST, then verifies the top two
or three claims against `git log` and the live tree before acting on them.
The handoff is a map, not truth — trust it for WHERE to look; verify WHAT it
says.

Resume with the same task packet and remaining allowances. Recheck the deadline
before dispatching work; a fresh session is not a fresh budget. If an allowance
is exhausted, preserve the checkpoint as STILL-BUILDING and complete only useful
authorized work that does not consume that allowance. Keep the original RED seal
and the pinned base; update candidate identity only when the actual bytes change.

## Works well with

- [root-cause-first](../root-cause-first/SKILL.md) — the investigation the next session continues.
- [repair-loop](../repair-loop/SKILL.md) — hand off mid-loop without losing the seam.
- [decision-bar](../decision-bar/SKILL.md) — how open decisions reach your human.
- [repo-map](../repo-map/SKILL.md) — the durable repo index the next session reads first.

> Scaffold credit: Matt Pocock, handoff (mattpocock/skills). The composition and hard rules here are BACKS AIOS.
