---
name: optimus
description: >-
  Harness-first boot. Load the invariant map and the dev-mode repair loop BEFORE
  any design or edit. No code and no job without loading the harness first. The
  invocation that yokes the agent to the dev-mode floor and turns it
  hallucination-resistant.
trigger_summary: dev mode, harness first, optimus, boot the loop, load invariants, repair loop
---

# Optimus — harness-first dev-mode boot

## Purpose

Every job, every session, every time: ground in the invariant map and the
dev-mode repair loop BEFORE writing code. A skill named but not invoked did not
happen. Work done from model memory of a skill is not work done from the skill.

## The boot sequence

1. **Load the invariant map.** Read `.hydraAgent/policies/backs-invariants.yaml`.
   Every invariant is non-negotiable. The two that are this loop's spine:
   - **Do it right the first time** — fix the class at the seam, completely, or
     the loop keeps iterating. A repair that introduces a new failure condition
     is itself a bug.
   - **Trust but verify** — no blind work. Every claim checked against live
     truth. Capability proven on the real path, never a proxy or a mock.
2. **Load the environment play.** Read
   `.hydraAgent/playbooks/dev-mode-elite-build.yaml`. It bundles this skill with
   `elite_build_understanding` and `the_path` as one repair loop.
3. **Load the wayfinder.** Invoke `the_path` (as Inlightenment). When lost, chart
   the route and resolve unknowns from evidence below the ask-me bar — never
   park a question on the human.
4. **Load the understanding planner.** Invoke `elite_build_understanding`. Run
   Design -> Plan -> Build -> Test -> Ship, each stage validated by the live
   understanding organs.
5. **Name the playbooks you invoked.** An unnamed method is an ungraded method.

## The repair loop (every repair, every time)

- **A. Reproduce on live truth.** Trace producer -> transport -> parser ->
  consumer -> durable outcome. Reproduce WITHOUT editing source. Record the
  command and output.
- **B. RED contract, tamper-proof.** Smallest regression test that fails for
  the REAL reason. Mock only the external leaf — never the seam under test.
- **C. Fix the class at the seam.** Name the flaw class. Sweep for siblings.
  Fix vertically at the shared primitive. Land a structural guard.
- **D. Verify on the real path.** Sniper-test the touched seams only. Assert
  real side-effects.
- **E. Independent grade.** A different model family must pass before landing.
  The builder never grades its own work.
- **F. Concurrent sessions.** Verify other sessions' work is preserved before
  touching the shared tree.
- **G. Land.** Commit only your own files. All invariants met. Report PROVEN or
  STILL-BUILDING.

## Anti-hallucination discipline (why this skill exists)

The harness is what supplies the invariants, the memory lanes, and the repo
truth. Strip those and grounding collapses to model priors — the exact mechanism
that produces invented meaning, fabricated APIs, and confident nonsense. The
boot is a hard dependency, not ceremony.

- **Resolve every symbol against the installed artifact** before you write it.
- **Falsify your own change** — run the test you expect to fail.
- **Never claim green off a proxy or a mock.**
- **A skip is not a pass. A test can itself be wrong.**

## Report

Two words: **PROVEN** (landed, independently graded, demonstrated on the real
path) or **STILL-BUILDING** — plus the plain-language intent and the single
decision in front of the human, if any.