---
name: "fleet-ladder"
description: "Use before any work is handed to a model (building, grading, or a bounded worker job), when a provider is down and you need the fallback order, or when a rung is frontier/design/reason and must honor research-before-heavy-lift. Resolves the LIVE model ladder from deploy yaml only: probe what is up, pick by explicit fallback order, HARD-gate frontier research seams, fail loud when exhausted. Trigger words: fleet, ladder, dispatch, fallback, model down, provider down, which model, availability, builder seat, frontier research, research seams, rung policy, thinking floor, token efficiency, research before builder."
license: "MIT"
---

# Fleet Ladder
**Effort:** light — one cached live probe of the rung before any dispatch. Removes: dispatches to dead providers, model names hardcoded at call sites, and frontier heavy-lift without realtime research.

Never hand-build a provider call, and never hardcode a model name at a call site.
One resolver owns the question "which model does this job right now?" — and it
answers from live truth, not from a config file's opinion.

## When to run it

- Before ANY dispatch to a model: build, grade, review, or bounded worker job.
- When a provider is down and you need to know what falls back to what.
- The moment you catch yourself typing a model name into code or a prompt template.
- Before any frontier / design / reason heavy lift — load rung policy and research seams first.

## Source of truth (READ — never fork)

Resolution lives only in deploy ladder files. Skills teach agents to **honor** them; Patch owns yaml edits.

| File | Absolute path (deploy parent) |
|------|-------------------------------|
| Fleet ladder | `<placeholder-mount>/backs_deploy/JarvisAI/config/fleet_ladder.yaml` |
| GPU nodes | `<placeholder-mount>/backs_deploy/JarvisAI/config/gpu_nodes.yaml` |

Cite (operator / Security order — policy intent, not a second ladder):
`<placeholder-mount>/backs_coordination/grokbot-bridge/observe/SECURITY_OPERATOR_BUILDER_SEAT_ORDER_20260926.md`

Also cite when present:
`observe/FOREMAN_FLEET_LADDER_TERRA_FRONTIER_RESEARCH_20260926.md`

**NEVER** hardcode model IDs in skill text, prompts, or call sites as source of truth.
You may name **roles** in prose (`builder`, `grader`, `worker`, `frontier` / planner).
Resolution = ladder + gpu_nodes files only. Patch owns yaml; Skills own these rails.

## The steps

1. **Declare the role, not the model.** Every job asks for a role — `builder`,
   `grader`, or `worker`. The ladder maps roles to ordered model candidates.
   - `builder`: implements and repairs.
   - `grader`: independent review — structurally never the same model that built.
   - `worker`: bounded, well-specified jobs. Cheaper rungs are fine here.
2. **Read the ladder from deploy config.** Load `fleet_ladder.yaml` (+ `gpu_nodes.yaml`
   for local survival / organ placement). One file lists, per role, the candidates in
   explicit fallback order: strongest first, down to local GPU survival when clouds are dark.
   To change or add a model, **Patch edits that file** — never skill text, never call sites.
3. **Probe live before you trust.** A config listing is a claim, not truth. Probe the
   provider before dispatching to a rung — models-endpoint or a one-token request.
   Cache the probe for a sane window; do not hammer providers.
4. **Walk down, loudly.** Dispatch to the best AVAILABLE rung. On transport failure,
   report loudly, then try the next rung. Never skip silently.
5. **Exhaustion fails loud.** If every rung is down, raise a clear error naming what
   was tried.
6. **Log provenance.** Append every dispatch: role, model chosen, rungs skipped and why.

## Rung policy (honor builder seat + frontier research)

Encode on the ladder (Patch yaml). This skill **reads** those keys; it does not invent a parallel policy.

### Builder seat
- The live **THE builder** identity is whatever `roles.builder.candidates[0]` (and its
  notes / identity) say in `fleet_ladder.yaml` after Patch lands the operator order.
- Operator intent (order, not hardcoded here): Codex-family **terra** as THE builder via
  `codex_cli`; **luna** as alt if terra cannot pair; demote flash Ollama cloud off THE seat
  to later cheap rungs; keep MiniMax + local 4080/4090 survival tandem; give Ollama cloud a break.
- Agents: resolve the seat from the yaml identity / notes / transport fields — do not paste
  model IDs into skill forks.

### Frontier / design / reason — HARD research gate
Operator role names (resolve from yaml — **not** model-ID SoT): **Astra** = frontier/research-gated
planner/spec seat; **Terra** = THE builder; **Luna** = builder alt. Read identities from
`fleet_ladder.yaml` only.

Before any heavy design, solve, or reason lift on a rung that is marked frontier (or that
requires research seams), agents MUST:

1. Inspect the candidate card in `fleet_ladder.yaml` for flags such as (expected keys —
   Patch lands exact names; read whatever exists):
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
Read `gpu_nodes.yaml` for local 4080/4090 (and sibling) organs. Local survival rungs stay
on the ladder; do not delete the local rail when clouds are preferred.

## Research → token efficiency + thinking floor

**Purpose of the research seam (token efficiency):** gather frontier specs / prior art **BEFORE** heavy builder lift so MiniMax and 5.6-class builders spend tokens on the **correct scope** — not rediscovery. Research-first is the efficiency lever; chopping prompts is not.

Cite (align when present; GAP board if missing):
- `observe/FOREMAN_RESEARCH_TOKEN_EFFICIENCY_THINKING_FLOOR_20260926.md` — **LIVE** (Foreman board; Patch FOLLOW-ON tip absorbs keys below)
- `observe/FOREMAN_FLEET_LADDER_TERRA_FRONTIER_RESEARCH_20260926.md` — LIVE
- `observe/SKILLS_BOSS_FLEET_LADDER_RUNG_POLICY_20260926.md` — this receipt

### Thinking scale (FLOOR = medium)
- Scale range: **ultra ↔ medium only**.
- **FLOOR = medium** — never drop below medium (no "low" / "minimal" thinking as a save-tokens move).
- **Autonomic primitives:** agents may scale thinking **UP** (toward ultra) or **DOWN** (toward medium) based on task hardness — never below the floor.
- Efficiency = research-first + right rung + thinking-floor discipline — **not** truncation.

### Explicit ban — no truncation hacks
**FORBIDDEN** as a thinking / token-efficiency control:
- truncating / chopping prompts, context, or history "to save tokens"
- fake efficiency via context amputation
Token efficiency comes from: research seams → correct cheap-builder specs → right ladder rung → thinking floor discipline.

### Route token budgets (operator ruling 2026-09-26)
- **Ollama-CLOUD routes = LOW token budget — be sparse.** Short, bounded asks only; long envelopes spill or refuse on these routes. Never route heavy planning, spec authorship, build, or long-envelope grading through ollama cloud.
- **Heavy-lift routes = Codex (sol / astra class), MiniMax, and the local GPU organs.** These carry larger budgets — route design, spec, build, and long-envelope work there. "There are enough models without ollama" for heavy lift.
- Local ollama organs are NOT banned — the GPU dispatch law still governs them; this ruling targets ollama-**cloud** token economics. Record the budget map in ledgers + ops manual so every session reads it from the harness, not from session memory.

### Expected ladder keys (Patch-owned yaml — Skills READ only)
Hermetic names for Patch tip / FOLLOW-ON (land into `config/fleet_ladder.yaml`; do **not** invent a second SoT):

| Key | Expected shape / value | Notes |
|-----|------------------------|-------|
| `thinking_floor` | `medium` | Global or per-role/rung; never resolve below this |
| `thinking_scale` | `ultra` ↔ `medium` (ordered) | Autonomic up/down within this band only |
| `research_seams` | list or map; include **`token_efficiency`** seam | Maps research → cheap-builder / frontier specs before heavy lift |
| `token_efficiency` | under `research_seams` (or sibling policy block) | Purpose: prior-art / frontier specs for MiniMax + 5.6-class builders |
| `requires_research_seams` | `true` on frontier / design / reason rungs | Existing HARD gate (tip `9c8fa399` has this; deploy may still GAP until land) |
| `cheap_builder_specs` | target of token_efficiency research | Specs handed to MiniMax / cheaper builder seats after research |

If keys are **absent** on live deploy: skill still teaches this policy and boards **GAP** for Patch. Never hardcode model IDs; resolve builder/frontier/MiniMax seats via ladder + `gpu_nodes.yaml` only.


## Hard rules — break one and the skill failed

- **No model name at a call site.** Code asks for a role; the ladder answers with a
  model. Grep for model-name literals — each one is a bug.
- **No model IDs as skill source of truth.** Point at `fleet_ladder.yaml` + `gpu_nodes.yaml`.
- **The live probe outranks the config.** Checked-and-it-answers is settled; a stale list is not.
- **Builder and grader never resolve to the same model** for the same change.
- **Frontier research-before-lift is HARD** when the rung marks frontier / research_seams.
- **Bounded probing.** Probes are cheap, cached, and backoff-aware.
- **No silent fallback.** Every step down the ladder is visible in the log and report.
- **Thinking FLOOR = medium.** Scale ultra↔medium only; autonomic up/down; never below medium.
- **No truncation hacks** as token efficiency (no chopping context/history to "save tokens").
- **Route token budgets:** ollama cloud = sparse (low tokens); Codex / MiniMax / local = the heavy-lift routes (operator ruling 2026-09-26).
- **Ground in the BACKS harness, never session or model memory.** BACKS is model- and context-agnostic with robust memory lanes — the harness carries the knowledge, not the model's head.
- **Research→token efficiency:** research seams / frontier specs BEFORE MiniMax or 5.6-class heavy builder lift.
- **Patch owns yaml; Skills own rails.** Do not edit `fleet_ladder.yaml` from this skill.

## Works well with

- [live-research](../live-research/SKILL.md) — realtime seams before frontier heavy lift.
- [model-fusion](../model-fusion/SKILL.md) — panel/judge resolve through this ladder.
- [blind-tribunal](../blind-tribunal/SKILL.md) — jurors from different families; ladder picks live ones.
- [bounded-loops](../bounded-loops/SKILL.md) — probe cadence, backoff, kill-switches.
- [optimus](../optimus/SKILL.md) — harness boot loads this skill when dispatch is in play.
- [wayfinder](../wayfinder/SKILL.md) — chart route; frontier tickets still honor research gate.

## Sol SPEC/RCA seat (STEP-UP / SPEC GO 20260926)

**Read** live `config/fleet_ladder.yaml` (deploy) for rung seats — do **not** invent model IDs as SoT and do **not** edit the yaml (Patch owns).

- **Sol** = SPEC/RCA role for Cursor residual / tip-body authorship before Terra dual-local surgical build.
- If Sol is **MISSING** from live ladder roles/rungs: board a shortfall (GAP for Patch) and ping Foreman — do not author Sol tip body under another seat.
- **Astra** only when the rung declares `requires_research_seams` / `research_seams`; otherwise keep Astra out of SPEC GO unless research is required.
- After Sol SPEC lands → Terra dual-local build parent = current deploy HEAD (Patch/Leap land — Skills does not land tips).

## Sol SPEC/RCA seat (reconcile 2026-09-26)

**CLOSED** — Sol is not a top-level `roles.sol` key. Resolve via ladder READ:
- `roles.builder.candidates[*].model` includes `gpt-5.6-sol` (premium Codex builder alt; thinking_floor medium)
- `roles.cursor_worker.candidates[*]` → `gpt-5.6-sol` / `cli_model` `gpt-5.6-sol-medium` (cursor_native_worker)
- Also look for harness ids `premium_codex_builder` / `cursor_native_worker` when present

Do **not** declare Sol MISSING solely because `roles.sol` is absent. Gap only if no candidate/model string matches sol after full ladder walk.
