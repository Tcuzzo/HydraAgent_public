---
name: "live-research"
description: "Use when reasoning about a codebase, API, or system whose real shape matters. Runs a parallel research agent that reads the live truth (the project's own READMEs, section docs, actual source) so conclusions ground in what is really there, not model memory. Trigger words: live research, ground the reasoning, read the real source, check what is actually there, primary sources, background research, verify against the repo, what do the docs say."
license: "MIT"
---

# Live Research
**Effort:** light — one background research agent reading the live source while the main lane keeps working. Removes: conclusions built on model memory that the real repo then refutes — the rework of shipping a guess.

Model memory is a guess about how a project looked at training time. The live
truth is what sits on disk and in the official docs right now. This skill runs
both lanes at once: while the main lane reasons about a target, a research agent
reads the real thing, and its findings merge into the reasoning **before** any
conclusion is drawn.

## When to run

- You are about to reason about a project's structure, an API's contract, or a
  library's behavior — and you have not read the current source.
- A design, fix, or claim depends on facts that could have drifted since your
  training data.
- A question needs real-world facts the working context cannot answer alone.

## The steps

1. **Spawn the researcher in parallel.** The moment reasoning about a target
   starts, dispatch a background research agent at the same target. The main
   lane keeps working; the researcher reads. Never block the work on legwork an
   agent can do alone.
2. **Read the live truth, nearest first.** The project's own README, then the
   section docs closest to the target, then the actual source structure — real
   directory listing, real file contents, real signatures. For facts outside the
   project: official docs, source code, specs, first-party APIs. A blog that
   summarizes the docs is not a primary source.
3. **Stream findings back before conclusions.** Findings flow to the main lane
   as they land, and the reasoning folds them in and corrects course. A
   conclusion drawn before the researcher reports on that point is a guess —
   mark it as one until the live truth confirms or kills it.
4. **Pin every claim to the source that owns it.** Each finding carries its
   source inline: a file path, a quoted line, a link, a commit. A claim that
   cannot be pinned is marked unverified, loudly — never dressed up as fact.
5. **Write one cited file.** Findings land in a single Markdown file, each claim
   with its source. Save it where the project already keeps such notes; if no
   convention exists, pick a sensible spot and say where, so the next agent
   finds it.
6. **Recall before re-reading.** Check notes from earlier sessions first — the
   same source may already be pulled. Reuse the cached finding and cite the same
   source. Minutes of recall beat hours of rediscovery.

## Hard rules

- **No conclusion before the merge.** If the researcher has not reported on a
  point, the main lane may not state that point as settled.
- **Primary sources only.** Follow every claim back to the source that owns it.
  A secondary write-up is a pointer, not proof.
- **Headless, never watched.** Background research uses a headless fetch path —
  never a live browser a human is watching; that is a different lane.
- **Unverifiable means say so.** A finding with no primary source ships flagged,
  never silently blended into the rest.
- **Zero human friction.** This skill adds no approval step and no gate. It is
  method discipline, not a checkpoint.

## What comes back

One grounded, cited Markdown file — plus a reasoning lane that was corrected
mid-flight instead of after the conclusion shipped. The main lane reads the file
and moves.

## Works well with

- [wayfinder](../wayfinder/SKILL.md) — research tickets are the agent-alone type this skill resolves.
- [root-cause-first](../root-cause-first/SKILL.md) — the same source-first discipline, aimed at bugs.
- [repo-map](../repo-map/SKILL.md) — read the repo's map first; walk the tree raw only when the map has no answer.

> Scaffold credit: Matt Pocock, research (mattpocock/skills, MIT). The composition and hard rules here are BACKS AIOS.
