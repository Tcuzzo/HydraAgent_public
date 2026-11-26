---
name: "wayfinder"
description: "Use when you are lost, the way forward is unclear, or you must decide what to work on next. Charts a decision map to the destination instead of parking a question on the human. Trigger words: wayfinder, the path, chart the route, map the work, what next, lost, fog of war, decision map, frontier."
license: "MIT"
---

# Wayfinder
**Effort:** free — pure charting discipline: a decision map built from evidence already on disk, no extra model calls. Removes: questions parked on the human that evidence could answer, and build work started before the decisions ahead of it were made.

When you do not know the way, the cheap move is to stop and ask the human a
question they hired you to answer. The wayfinder charts the route instead:
build a decision map, resolve unknowns from evidence, and send up only the
calls that are genuinely the human's.

## When to run

- You are lost, or the next step is unclear.
- A large effort needs decomposing before anyone builds.
- You feel the pull to ask "what do you want me to do?"

## The steps

1. **Name the destination.** One named goal in your tracker, plus a close
   predicate: how you will know it is done. The destination fixes the scope.
2. **Chart what you can see.** Create tickets on the frontier — the decisions
   ready to resolve now. Each ticket resolves a **decision**, not a slice of
   build work.
3. **Leave the rest in the fog.** Decisions you can feel coming but cannot yet
   pin down go in a **Not yet specified** section: the suspected question, the
   area to revisit. Do not pre-slice the fog into ticket-sized pieces — it is
   coarser than a ticket, and one patch may graduate into several tickets, or none.
4. **Rule work out loud.** Work beyond the destination is not fog — it goes in
   an **Out of scope** section and never graduates. If a live ticket turns out
   to sit past the destination, close it and leave one line in Out of scope.
5. **Type every ticket** (see Ticket types below).
6. **Resolve one decision from evidence.** Read the code, the docs, the record —
   deterministic evidence closes a ticket without a guess. Resolving a ticket
   clears the fog ahead of it: graduate what is now specifiable into fresh
   tickets, one at a time.
7. **Hand off when the way is clear.** The map is done when nothing is left to
   decide before someone goes and does the thing. The pull to just do the work
   is the signal you have reached the edge of the map.

## Fog or ticket?

The test is whether you can state the question **precisely** now — not whether
you can answer it now. Ticket when the question is sharp, even if blocked.
Not-yet-specified when you cannot yet phrase it that sharply.

## Ticket types

Every ticket is **human-in-loop** (worked live with a human) or **agent-alone**.
A human-in-loop ticket only resolves through live exchange — the agent never
stands in for the human's side. An agent answering its own grilling questions
has broken this.

- **Research** (agent-alone) — a background research agent resolves it; findings
  land on a scratch branch with a pointer from the ticket. See
  [live-research](../live-research/SKILL.md).
- **Prototype** (human-in-loop) — raise fidelity with a cheap rough artifact the
  human can react to.
- **Grilling** (human-in-loop) — conversation that pulls the decision out. The
  default type.
- **Task** (either) — manual work that must happen before a decision can be made:
  sign up for a service, provision access, move data. The one type that *does*
  rather than decides; it earns its place by unblocking a decision.

## Hard rules

- **Never park a question on the human** that evidence, the code, or standing
  rules can answer. Only taste, vision, and destructive-risk calls go up — see
  [decision-bar](../decision-bar/SKILL.md).
- **Refer to work by name, never a bare id.** A wall of #42, #43, #44 is
  illegible; names read at a glance. The id or link rides inside the name — it
  never stands in for it.
- **One decision per session.** Resolve at most one ticket per session, research
  tickets excepted. Charting is a session's work; it hand-resolves nothing.
- **Plan, don't do.** The map produces decisions, not deliverables.
- **When the ask itself is the fog** (the destination is unclear because the
  request arrived as prose or metaphor), first read the request with
  [intent-compiler](../intent-compiler/SKILL.md), then chart from what it
  actually says.

## Landing work in production

Read the current target repository's release instructions and the operator's authorization. Identify the reviewed commit, build artifact, deployment owner and rollback path. Use that project's documented promotion mechanism; do not infer a deployment directory, service command or private production reference from this skill.

Before reporting completion, compare the deployed revision and observed behavior with the reviewed artifact. A successful build or merge is separate from a working deployment. Preserve private topology and credentials in the authorized local evidence boundary.

## Works well with

- [live-research](../live-research/SKILL.md) — resolves the agent-alone research tickets.
- [decision-bar](../decision-bar/SKILL.md) — which decisions actually reach the human.
- [human-voice](../human-voice/SKILL.md) — how the map reads to a human.
- [repo-map](../repo-map/SKILL.md) — read the repo's map first; walk the tree raw only when the map has no answer.

> Scaffold credit: Matt Pocock, wayfinder (mattpocock/skills, MIT). The composition and hard rules here are BACKS AIOS.
