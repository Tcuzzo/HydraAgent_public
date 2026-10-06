---
name: "guided-steps"
description: "Use when a setup needs steps only a human can do — third-party dashboards, credentials, CI secrets, provisioning, one-off migrations, cutovers. Authors a stage-by-stage interactive script that opens each URL, says what to click and copy, captures values, and writes them where they belong. Trigger words: wizard, human-only steps, provision, credentials, dashboard setup, CI secrets, cutover."
license: "MIT"
---

# Human Steps Wizard
**Effort:** free — authoring discipline plus a static syntax check; no model calls. Removes: re-explaining the same human-only clickpath every run, and secrets pasted into tracked files along the way.

Some steps only a human can do: click through a third-party dashboard, create
credentials, approve a provisioning screen. They are tedious to do by hand and
tedious to re-explain every time. The wizard turns them into a guided run: a
stage-by-stage interactive shell script that opens each URL, says exactly what
to click and copy, captures the values, and writes them where they belong.

## When to use it

- A setup needs a human to drive a UI no API can reach — dashboards, consoles,
  credential screens, CI secret pages, one-off migrations, cutovers.
- The path is long enough that re-explaining it every time hurts.

When NOT to use it: an API can do the step (automate it instead — a wizard is
the last resort), or the procedure is one or two steps (just tell the human in
plain words).

## The shape

One script, two parts:

- **A helper library at the top** — identical in every wizard, never
  hand-edited. It provides: stage headers with progress ("stage 3 of 7"),
  human-voice narration, cross-platform URL opening, hidden entry for
  secrets, idempotent `.env` upserts (update the key if present, append if
  not), writes to your CI provider's secret store, a confirm/pause step, and
  a closing summary of everything captured.
- **The stages below a marker** — the only part you author. One stage per
  human step: open the URL, say what to click and what to copy, capture the
  value, write it to its destination. Set the total stage count so the
  progress display is honest.

## Process

1. **Scope.** Read the env example file, the README, the deploy config, and
   the CI workflows. Every secret or variable they reference is a value the
   wizard must produce. Show the human the ordered stages and the values
   up front — confirm the plan before authoring.
2. **Map each stage's path.** One line per stage: URL → action → value →
   destination. The human sees the whole path before they start.
3. **Author.** Copy the template. Write only the stages; never touch the
   library. Keep the narration in plain words — the person running this may
   not be an engineer.
4. **Verify statically.** Syntax-check the script (`bash -n`, shellcheck),
   make it executable, then walk every stage by hand: is each URL right, each
   instruction clear, each write target correct? Do NOT run it end-to-end —
   it opens browsers and blocks on human input.

## Hard rules

- **Secrets never touch tracked files.** Captured values land in the
  gitignored `.env` or the CI secret store. The script itself carries
  placeholders only; the human pastes real values at run time. A real key,
  hostname, or personal detail in the authored script IS the bug.
- **Every remote write is single-shot and bounded.** A secret-store write is
  an API call: no retry loops, no hammering. Fail loud and let the human
  re-run the stage.
- **Ephemeral by default.** A wizard is built for one run and deleted after.
  Commit it only when the human asks for a repeatable setup path — and a
  committed wizard still carries placeholders only.
- **The confirm step is the human's own pause button, not a gate.** It exists
  so they can check their work — never to put approval friction on them.

## Works well with

- [session-handoff](../session-handoff/SKILL.md) — record which stages ran if the run is split.
- [human-voice](../human-voice/SKILL.md) — the register every stage narrates in.
- [bounded-loops](../bounded-loops/SKILL.md) — the no-hammering rule behind remote writes.

> Scaffold credit: Matt Pocock, wizard (mattpocock/skills). The composition and hard rules here are BACKS AIOS.
