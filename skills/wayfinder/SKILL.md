---
name: wayfinder
description: >-
  The wayfinder. Use when you are lost, the next step is unclear, or you must
  decide what to work on next. Charts a decision map to the destination instead
  of parking a question on the human. Plan and don't do — each ticket resolves
  a decision, not a slice of build work. Never run lost.
trigger_summary: wayfinder, the path, chart the route, map the work, what next, lost, fog of war, decision map, frontier
license: MIT
---

# Wayfinder

**Effort:** free — pure charting discipline. A decision map built from
evidence already on disk, no extra model calls.

When you do not know the way, the cheap move is to stop and ask the human a
question they hired you to answer. The wayfinder charts the route instead:
build a decision map, resolve unknowns from evidence, and only surface the
calls that are genuinely the human's.

## When to run

- You are lost, or the next step is unclear.
- A large effort needs decomposing before anyone builds.
- You feel the pull to ask "what do you want me to do?"

## The steps

1. **Name the destination.** One named goal, plus a close predicate: how you
   will know it is done. The destination fixes the scope.
2. **Chart what you can see.** Tickets on the frontier — decisions ready to
   resolve now. Each ticket resolves a **decision**, not a slice of build.
3. **Leave the rest in the fog.** Decisions you can feel coming but cannot
   yet pin down go in a **Not yet specified** section — the suspected
   question, the area to revisit. Do not pre-slice the fog into ticket-sized
   pieces; it is coarser than a ticket.
4. **Rule work out loud.** Work beyond the destination goes in an **Out of
   scope** section and never graduates. Close a ticket if it sits past the
   destination; leave one line in Out of scope.
5. **Type every ticket** (see Ticket types below).
6. **Resolve one decision from evidence.** Read the code, the docs, the
   record — deterministic evidence closes a ticket without a guess.
   Resolving a ticket clears the fog ahead of it.
7. **Hand off when the way is clear.** The map is done when nothing is left
   to decide before someone goes and does the thing.

## Fog or ticket?

The test is whether you can state the question **precisely** now — not
whether you can answer it now. Ticket when the question is sharp (even if
blocked). Not-yet-specified when you cannot yet phrase it that sharply.

## Ticket types

Every ticket is **human-in-loop** (worked live with a human) or **agent-alone**.
A human-in-loop ticket only resolves through live exchange — the agent never
stands in for the human's side. An agent answering its own grilling questions
has broken this.

- **Research** (agent-alone) — a background research agent resolves it.
- **Prototype** (human-in-loop) — raise fidelity with a cheap rough artifact
  the human can react to.
- **Grilling** (human-in-loop) — conversation that pulls the decision out.
  The default only for genuinely unresolved human decisions, never
  evidence-answerable work.
- **Task** (either) — manual work that must happen before a decision can be
  made: sign up for a service, provision access, move data. The one type
  that *does* rather than decides; it earns its place by unblocking a
  decision.

## Hard rules

- **Never park a question on the human** that evidence, the code, or
  standing rules can answer. A decision reaches the human ONLY when it is
  genuinely theirs — taste, vision, or destructive/data-loss risk.
- **Refer to work by name, never a bare id.** A wall of #42, #43, #44 is
  illegible; names read at a glance. The id or link rides inside the name —
  it never stands in for it.
- **One decision per session.** Resolve at most one ticket per session,
  research tickets excepted. Charting is a session's work; it hand-resolves
  nothing.
- **Plan, don't do.** The map produces decisions, not deliverables.
- **When the ask itself is the fog** (the destination is unclear because the
  request arrived as prose or metaphor), first read the request with
  `intent-compiler`, then chart from what it actually says.

## Works well with

- `live-research` — resolves the agent-alone research tickets.
- `decision-bar` — which decisions actually reach the human.
- `human-voice` — how the map reads to a human.
- `repo-map` — read the repo's map first; walk the tree raw only when the
  map has no answer.

> Scaffold credit: Matt Pocock, *wayfinder* (mattpocock/skills, MIT). The
> composition and hard rules here are adapted from BACKS AIOS.

## Provenance

Adapted from the BACKS AIOS package (skill `wayfinder`, upstream version
recorded in the original frontmatter). The full operative method above is
the substantive content; BACKS-specific runtime bindings were removed for
this public edition.