---
name: "fleet-ladder"
description: "Use before any work is handed to a model (building, grading, or a bounded worker job), when a provider is down and you need the fallback order, or when the rung is frontier/design/reason and must honor research-before-heavy-lift. States the desired selection discipline (task + evidence work the agent must do before dispatch) and points at the configured ladder file as the only place that knows actual routing. Does NOT itself rank, score, or filter candidates; the public pack ships no resolver and no Fleet executable. Trigger words: fleet, ladder, dispatch, fallback, model down, provider down, which model, availability, frontier research, research seams, rung policy, thinking floor, token efficiency, research before builder, capability, oauth, api, quota, essence, checkpoint."
license: "MIT"
---

# Fleet Ladder
**Effort:** light — one cached live probe of the rung before any dispatch. Removes: dispatches to dead providers, model names hardcoded at call sites, frontier heavy-lift without realtime research, and the seat-itself-as-authority mistake.

This skill teaches the **desired selection discipline** an agent must follow
before handing work to a model, and points at the configured ladder file as
the only place that knows the live ordering. It does **not** rank candidates,
does **not** score cost / essence / matrix entries, and ships **no resolver
and no Fleet executable**. Where the live runner on the operator's host does
not implement a step this file describes, that gap is named as a runtime gap —
never silently bypassed and never papered over with a disclaimer.

Capability and availability drive the choice. Model identity records
provenance, never authority. Review independence comes from the execution session.

## When to run it

- Before ANY dispatch to a model: build, grade, review, or bounded worker job.
- When a provider is down and you need to know what falls back to what.
- The moment you catch yourself typing a model name into code or a prompt template.
- Before any frontier / design / reason heavy lift — load rung policy and research seams first.
- At every fallback step: record why (provider error, quota exceeded, transport
  refused), checkpoint the dispatch, and carry the full required context + tools.

## Source of truth (READ — never fork)

Resolution lives only in the deploy ladder files. Skills teach agents to
**honor** them; Patch owns yaml edits. This file does not own routing.

| File | Absolute path (deploy parent) |
|------|-------------------------------|
| Fleet ladder | `<BACKS_DEPLOY_ROOT>/config/fleet_ladder.yaml` |
| GPU nodes | `<BACKS_DEPLOY_ROOT>/config/gpu_nodes.yaml` |
| Public ladder example | `skills/fleet-ladder/ladder.example.yaml` (template only) |

**NEVER** hardcode model IDs in skill text, prompts, or call sites as source of truth.
You may name **roles** in prose (`builder`, `grader`, `worker`, and the
configured planning role — native deployments call this `plan` — **only if
the live ladder actually configures a planning role**), and you may name
**capabilities** the job needs (long context, vision, code, reason). The role
list this skill names is a desire; the role list a live ladder actually
configures is the only authority. This skill does not invent a `planner`
rung; if the live yaml has no configured planning role, do not add one here.

Public pack note: this skill ships no Fleet executable and no resolver. The
actual fallback mechanism lives wherever the operator's host runs the ladder
runner. Current native Fleet public build flags are
`--role --repo --spec --timeout --refresh --dry-run`. Flags this skill
mentions beyond that list are **desired** surface, not deployed surface —
name a gap; do not invent.

## The steps

1. **State the role and the capability the job needs (agent's task work).**
   Every job asks for a role (`builder`, `grader`, `worker`, plus `planner`
   **only when the live ladder actually configures one**) and a capability
   (long context, vision, code-gen, reason, tool-use, …). The agent writes
   this requirement down with the dispatch record. This step is **task work
   the agent does**, not filtering this skill performs.

   Independence requires **fresh, author-disjoint execution**. Surface any
   runtime restriction that blocks a capable reviewer as a named gap.

   Task matrix (capability-fit scores) and essence (per-model know-thyself)
   entries are **inputs the agent plans to consult as available**, not new
   filtering logic this skill implements. A missing matrix/essence read on a
   rung is NOT a waiver: a task-required capability that the agent cannot
   verify is not satisfied by silence or by a "we'll trust the role order"
   default. Distinguish the inability to automatically read a matrix entry
   from the absence of a verified capability. The agent may provide real
   evidence and use configured public role routing only when requirements
   are met; otherwise record the unsatisfied task/gap, never downgrade the
   requirement or quietly proceed.

   The grader's required review is **fresh, author-disjoint execution over
   an author-redacted frozen packet**, with **no author chat / reasoning**
   in the packet. Record the authoring execution and the actual model, provider,
   transport, host, and fallback reason; identify the review session separately.
2. **Read the role order from the configured ladder (only authority).**
   Load `fleet_ladder.yaml` (+ `gpu_nodes.yaml` for local survival / organ
   placement). Each role lists candidates **in the order the live config
   chooses** — Patch owns that order. Selection within that order is the
   agent's job and follows this skill's discipline:

   - **Planner / spec / heavy design lift:** strongest SUITABLE reasoning
     the live ladder actually configures for that role. "Strongest suitable"
     is measured by what the rung can actually carry (context, tools,
     research seams) on a bounded probe — not by a model label.
   - **Worker / bounded implementation:** cheapest SUFFICIENT measured
     bounded implementation the live ladder actually configures. "Cheapest
     sufficient" is measured by an actual bounded dispatch, not a catalog
     listing.
   - **No universal cloud-first / local-last default.** The live config
     owns preference. Local GPU rungs stay on the ladder and obey the GPU
     dispatch law.

   To change or add a model, **Patch edits the yaml** — never skill text,
   never call sites. This skill never reorders a configured rung.
3. **Probe live before you trust — and prove the probe, not just the catalog.**
   A config listing is a claim, not truth. A `models` / `catalog`
   response proves **catalog access**, not working inference, not task
   capability, and not available quota. Distinguish, at minimum:

   - **Provider** (the upstream service: an HTTP host)
   - **Account / auth** (key, oauth, tenant)
   - **Quota / rate limit** (per-account, per-route, per-window)
   - **CLI / transport** (the runner that actually carries the call)
   - **Harness** (this skill and any wrapper around it)
   - **Hardware** (local GPU rungs obey the GPU dispatch law)

   Required probe evidence per rung before dispatching a heavy task: a real
   bounded inference / actual dispatch on that rung (a one-token request at
   minimum, a small bounded call where the ladder offers one), not a
   `models` listing alone. Cache the probe for a sane window; do not hammer
   providers. On fallback, **recheck capability on the new rung** — do not
   assume the same evidence transfers.
4. **Walk down, loudly.** Dispatch to the best AVAILABLE rung whose capability
   matches the task. On transport failure (provider down, oauth token expired,
   api error, quota exceeded), report loudly, checkpoint the dispatch with the
   reason, then try the next rung. Never skip silently. Carry the full required
   context and tools through every fallback step — never silently narrow.
5. **Exhaustion fails loud.** If every rung is down, raise a clear error naming what
   was tried: provider, role, capability, why each rung was refused, and the
   recorded cost / time / output budget of the run that hit the wall.
6. **Log provenance.** Append every dispatch: role, model chosen, rungs skipped and why,
   capability matched, quota budget consumed, and the run-time actuals. Provenance is
   recorded as fact — not as authority and not as a guarantee that a rung is still
   the right one next time. Shared quota is a measured account/route fact: it is
   not assumed because model names match across runs.

## Rung policy (honor frontier research — no fabricated isolation flags)

Encode on the ladder (Patch yaml). This skill states desired policy and
**points at the live yaml**; it does not invent a parallel policy and it
does not invent runner flags. Where the configured runner does not enforce
a step, that gap is named here, not silently asserted fixed.

### Author-disjoint grader (desired policy)
- The grader's required separation from the builder is **fresh,
  author-disjoint EXECUTION over an author-redacted frozen packet** — the
  packet contains no author chat / reasoning, and the grader's session has
  not inherited the builder's conversation or context. That is the
  separation this skill requires.
- Record the actual model, provider, transport, host, fallback reason, and
  authoring execution as provenance. Record the reviewer session separately.
- Current native Fleet public build flags are
  `--role --repo --spec --timeout --refresh --dry-run`. The public pack
  ships no Fleet executable. Earlier drafts of this skill mentioned
  hypothetical flags such as `--builder` or `--capability`; those are **not** part of the deployed CLI surface and
  must not be pasted into a runner or claimed as enforcement. The only
  CLI surface to honor is the list above.

### Frontier / design / reason — HARD research gate
Before any heavy design, solve, or reason lift on a rung that is marked
frontier (or that requires research seams), agents MUST:

1. Inspect the candidate card in `fleet_ladder.yaml` for flags such as:
   - `research_seams: true` / `requires_research_seams: true`
   - `frontier: true` / identity containing frontier / planner duty notes
   - `web_search` **not** set to disabled for that rung (frontier must keep search ON)
2. If any of those fire: run **realtime** research seams **before** the heavy lift:
   - internet / web search
   - GitHub
   - Reddit
   - scientific papers (arxiv / primary papers)
3. Pair with [live-research](../live-research/SKILL.md) for repo-grounded facts; do not
   treat model memory as the research seam.
4. **Never disable `web_search` on frontier / research-required rungs.** Mechanical
   Ollama-compat `web_search="disabled"` args are for non-frontier cloud-compat seats only —
   do not copy them onto frontier cards.
5. Failure to research = **invalid frontier use**. Prefer cheaper builder seats for
   mechanical build when research is not warranted.

### GPU / survival
Read `gpu_nodes.yaml` for local GPU organs. Local survival rungs stay on the
ladder; do not delete the local rail when clouds are preferred. The GPU
dispatch law (one model per card, no spill to system RAM, warm for the whole
loop, unload only at loop end) governs these organs — it is preserved here, not
re-invented.

## Research → token efficiency + thinking baseline

**Purpose of the research seam (token efficiency):** gather frontier specs /
prior art **BEFORE** heavy builder lift so the builder's tokens spend on the
**correct scope** — not rediscovery. Research-first is the efficiency lever;
chopping prompts is not.

### Thinking baseline (medium-or-higher where supported)
- **Semantic baseline:** planning / spec / heavy-lift / long-envelope grading
  runs at **medium-or-higher** reasoning depth **where the model and transport
  actually support it**. "Where supported" means the live runner can set the
  parameter the rung accepts — every provider / transport does not accept the
  same numeric key, and some expose no knob at all. This skill states the
  **semantic** baseline; the existing transport configuration owns the
  supported parameters, and agents refer to those configured transport
  settings rather than asserting a uniform API. **Do not** assert that every
  rung implements the same `thinking_floor` / `thinking_scale` numeric keys;
  **do** require the configured rung to carry out at least medium-equivalent
  depth for the semantic work in question.
- **Autonomic primitives:** agents may scale reasoning **UP** (toward the
  rung's max) or **DOWN** (toward the rung's medium-equivalent floor) based
  on task hardness — never below the semantic floor for that work.
- **Efficiency** = research-first + right rung + thinking-baseline discipline
  — not truncation.

### Explicit ban — no truncation hacks
**FORBIDDEN** as a thinking / token-efficiency control:
- truncating / chopping prompts, context, or history "to save tokens"
- fake efficiency via context amputation
Token efficiency comes from: research seams → correct cheap-builder specs →
right ladder rung → thinking-baseline discipline.

### Route token budgets (capability + host + auth, not provider name)
Route the budget by what the job needs and what the host can carry, not by a
provider label:
- **Sparse routes** (low token budget): short, bounded asks. Long envelopes,
  spec authorship, heavy builds, and long-envelope grading do not go here.
- **Heavy-lift routes**: large context, deep reasoning, long envelopes. These
  carry larger budgets — route design, spec, build, and long-envelope work here.
- Local GPU organs are NOT banned — the GPU dispatch law still governs them.
  Record the budget map in ledgers + ops manual so every session reads it from
  the harness, not from session memory.

### Ladder keys (Patch-owned yaml — this skill does not enforce them)
The proposed `config/fleet_ladder.yaml` keys (`thinking_floor`, `thinking_scale`,
`research_seams`, `requires_research_seams`, `capabilities`) and their shapes are in
[references/ladder-keys.md](references/ladder-keys.md). Absent keys on the live deploy
are a **GAP** for Patch, never a reason to hardcode model IDs; resolve seats via
ladder + `gpu_nodes.yaml` only.

## Hard rules — break one and the skill failed

- **No model name at a call site.** Code asks for a role and a capability; the
  ladder answers with a model. Grep for model-name literals — each one is a bug.
- **No model IDs as skill source of truth.** Point at `fleet_ladder.yaml` + `gpu_nodes.yaml`.
- **The live probe outranks the config — and the probe must prove inference, not
  catalog access.** Checked-and-it-answers is settled; a stale list is not. A
  listing in the catalog is provenance; live availability + a bounded
  inference on the rung is what dispatches. Catalog answers do **not** settle
  capability or quota.
- **The grader's separation is fresh, author-disjoint execution over an
  author-redacted frozen packet.** Provenance on the builder run record
  records the authoring execution and the actual route; the reviewer session
  is recorded separately. Surface any runtime restriction that blocks a capable
  reviewer as a gap; do not silently impose it.
- **Frontier research-before-lift is HARD** when the rung marks frontier / research_seams.
- **Bounded probing.** Probes are cheap, cached, and backoff-aware.
- **No silent fallback.** Every step down the ladder is visible in the log and
  report, with the reason (provider error, quota, oauth expired, transport
  refused) and a checkpoint carrying the full required context + tools.
- **Thinking baseline = medium-or-higher (semantic, where supported).** Never
  drop below the rung's medium-equivalent for the semantic work in question;
  do not assert a numeric key the transport does not accept.
- **No truncation hacks** as token efficiency (no chopping context/history to "save tokens").
- **Route token budgets by capability + host + auth**, not by provider label.
- **Distinguish provider / auth / account / quota / CLI transport / harness /
  hardware** in every probe and every failure. Conflating them is the bug.
- **No universal cloud-first / local-last default.** The live config owns
  preference; the agent reads it.
- **No fabricated runner flags.** This skill names current native Fleet public
  build flags (`--role --repo --spec --timeout --refresh --dry-run`) and no
  others as deployed surface. Hypothetical flags such as `--builder` or `--capability` are not part of the deployed CLI
  surface and must not be pasted into a runner or claimed as enforcement.
- **No resolver / no Fleet executable in this skill.** The public pack ships
  none. Where a step needs runtime support that is not in the public pack,
  name the gap; do not silently bypass.
- **Ground in the BACKS harness, never session or model memory.** BACKS is
  model- and context-agnostic with robust memory lanes — the harness carries
  the knowledge, not the model's head. Essence files (per-model know-thyself)
  load at the dispatch site, not from session chat.
- **Patch owns yaml; Skills own rails.** Do not edit `fleet_ladder.yaml` from this skill.

## Unresolved runtime gaps (surface, never silently fix or bypass)

A few runtime pieces still ride alongside the matrix above. They must be
surfaced by name, never silently asserted fixed and never bypassed by this
skill:

- **Identity-based exclusion on top of author-disjoint review is a separate
  runtime gap.** This skill's desired policy is fresh, author-disjoint
  execution over an author-redacted frozen packet. Any additional rule
  the host applies based on model identity is a runtime defect. Repair
  that restriction through the owning runtime seam; do not exclude a
  capable reviewer because of its model or provider.
- **An old role-prescription block on a previous version of this skill (named
  seat assignments, dated operator rulings pinned to a moment in time) is
  retired here.** If the deploy ladder still reads from those legacy blocks,
  that is a GAP for Patch — do not paste the old assignments into this file
  and do not use them as authority. Read the live yaml.
- **Task-matrix / essence-driven filtering is desired, not implemented.** If
  the live runner cannot read a matrix or essence entry on a given rung, the
  agent **may not proceed** as if the capability were waived: a task-required
  capability that the runner cannot verify is not satisfied by silence or by
  a "trust the role order" default. Distinguish the inability to automatically
  read a matrix entry (a runner gap) from the absence of a verified capability
  (an unsatisfied requirement). The agent records the unsatisfied task/gap
  and uses configured public role routing only when requirements are met;
  it never downgrades a requirement, never quietly proceeds, and never
  synthesizes a missing value here.
- **A planning role is only in scope when the live ladder configures one.**
  This skill names the configured planning role (native `plan`) as a role
  candidates may carry; it does not invent a `planner` rung. If the live yaml
  has no configured planning role, do not add one here.

## Works well with

- [live-research](../live-research/SKILL.md) — realtime seams before frontier heavy lift.
- [model-fusion](../model-fusion/SKILL.md) — panel/judge resolve through this ladder.
- [blind-tribunal](../blind-tribunal/SKILL.md) — jurors graded by capability and
  availability; ladder picks live ones; the grader's required separation is
  fresh, author-disjoint execution over an author-redacted frozen packet (this
  skill's desired policy; runtime-enforced isolation is a separate deploy
  ticket).
- [bounded-loops](../bounded-loops/SKILL.md) — probe cadence, backoff, kill-switches.
- [optimus](../optimus/SKILL.md) — harness boot loads this skill when dispatch is in play.
- [wayfinder](../wayfinder/SKILL.md) — chart route; frontier tickets still honor research gate.
