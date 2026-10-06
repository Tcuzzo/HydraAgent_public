---
name: "blind-tribunal"
description: "Use when an autonomous change needs an independent grade before landing and no human is in the loop. Convenes eight blind, cross-family jurors (one lens each — defect, proportion, operator consequence, reversibility, state continuity, resource economy, boundary condition, telemetry), each routed by tier to the cheapest model family that is sufficient, over an author-redacted envelope of whole files; every finding becomes a new failing test; loop until every juror passes. Trigger words: blind tribunal, grill tribunal, tribunal, jurors, eight lenses, cross-family grade, convene, blind grade, independent grade, grade before landing."
license: "MIT"
---

# Blind Tribunal
**Effort:** heavy — eight juror seats, one lens each, routed by tier to the cheapest sufficient model family and re-convened on fresh envelopes every round until unanimous; spend it on autonomous changes that land with no human review. Removes: rogue landings gated by nothing but the builder's own word.

The grading loop that lets the human walk away without the agent going rogue.
A panel of jurors reviews the change blind, with authorship stripped. Every
finding becomes a new failing test. The loop repeats until every juror passes.
Nothing lands on the builder's word alone.

## When to run it

- Before landing any autonomous change no human will review.
- Any high-blast-radius change: security-shaped, data-touching, authority-adjacent.
- When one grader is not enough and you want independent lenses on the same artifact.

## The seats

Eight jurors, one lens each. Each is a model from a DIFFERENT family than the builder
(same vendor = same family). A juror asked to check everything checks nothing well.

| Juror | Lens id | Tier | The question it asks |
| --- | --- | --- | --- |
| Defect | `defect` | generalist | What actually breaks? Logic flaws, syntax errors, new defects. |
| Proportion | `proportion` | generalist | Is this the right size? Over-engineered, or scaled to the intent? |
| Consequence | `operator_consequence` | operator safety | If a human operator runs this, what is destructive, unsafe, or harmful? |
| Reversibility | `reversibility` | deep state | Irreversible side effects? If it dies mid-run, can the system roll back cleanly? |
| Continuity | `state_continuity` | deep state | Orphaned variables, clobbered global state, dropped context downstream nodes need? |
| Economy | `resource_economy` | fast structural | Unoptimized loops, redundant network/API calls, memory bloat? |
| Boundary | `boundary_condition` | fast structural | Null, empty, wrong-typed, or malformed inputs — does it fail gracefully? |
| Telemetry | `telemetry` | operator safety | Can a failure here be diagnosed from the logs and error handling? |

## The routing tiers (cheapest sufficient route first)

Route each tier to what the lens needs, not to the biggest model you own:

- **deep state** (reversibility, state_continuity): your largest context + deepest
  reasoning, ideally through an agentic harness that can READ the repository (never
  write) so the juror can trace a state change from producer to consumer and run the
  named tests. Run harness jurors read-only (`codex exec --sandbox read-only`; a
  read-only tool catalog on a claude harness) and void any verdict whose dispatch
  changed repository bytes, naming the paths. Header: *trace every state change across the whole workflow before you judge.*
- **fast structural** (boundary_condition, resource_economy): the cheapest fast route —
  a free local GPU model first, **fused with a cheap cloud verifier** that judges the
  same prompt: the lens passes only when BOTH pass; the verifier never re-seats the
  primary's model; when every cloud rung is down the local verdict stands, flagged
  **UNVERIFIED** in the review and the summary — never silently "verified". A local
  model must see the WHOLE artifact or it does not judge: size the request's context
  window to the prompt (`num_ctx` on Ollama — the server default is 4096 tokens and it
  truncates silently; measured 2026-09-04, a 16B juror "passed" a 124 KB diff it never
  saw, citing a file that does not exist) and refuse, before sending, a prompt the rung
  cannot hold. The verifier never comes from the primary's FAMILY (same vendor =
  same family), and an UNVERIFIED seat is a HOLD — never unanimity, never a pack
  pass. After every local call read the node's own numbers: cap the answer
  (`num_predict`), refuse an answer whose prompt filled the window
  (`prompt_eval_count` + answer budget ≥ `num_ctx` is a measured truncation), and
  check residency (`size_vram == size` on `/api/ps`) — a model spilled to system RAM
  is unloaded and its answer discarded. Header: *work fast and literal from the code
  in front of you.*
- **operator safety** (operator_consequence, telemetry): your strongest coder with
  safety grounding. Header: *think as the human who runs this on their own machine.*
- **generalist** (defect, proportion): a reliable large generalist. Header: *precise
  findings, no invented context.*

Every tier's ladder ends on a local survival rung, so the tribunal still convenes with
zero cloud. The premium accounts an IDE session holds (Claude Opus/Sonnet, Codex) are an
IDE-only lane for the deep-state seats: render the prompt files, pipe them in, drop the
verdicts into the same shape.

**Solo rig.** When only one model family is available, degrade EXPLICITLY: a
fresh context or session that never saw the author's conversation acts as the
blind grader, or the human reviews the redacted envelope. The report must name
the weakened gate ("graded same-family-blind, not cross-family"), never
silently pretend the cross-family gate held.

## The builder is declared, and the exclusion is structural

"Different family than the builder" was a rule jurors were asked to remember. In the
tribunal's own dogfood the operator-safety seat led with the very model that had built
the candidate, and nothing recorded or excluded it — the author graded its own work for
two rounds and every builder-is-not-grader claim on those rounds was unfounded. So:

- **Convene with the builder named** (`--builder <model-or-family>`). The run record
  carries `builder_family`. Every rung of that family is refused out loud, before any
  dispatch, on every ladder. A lens left with no rung HOLDS — it never falls back to the
  builder because the builder was the only seat left.
- **Same vendor = same family** (`gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.5` are one family).
  One declaration excludes the vendor.
- **Prove it on the live ladder, not in a test:** after the first convene with the builder
  declared, the routing table must show the builder's seats fell through to another
  family. If it did not, the exclusion is decoration.

## The footer names the lens, a rejected rung keeps its words, and the floor is three rungs deep

Round 4 held two lenses with zero refusals, and every link was on the record. Three laws came out of it:

- **State the answer shape next to the answer.** The protocol footer carries the literal lens name (`"lens": "defect"`), never the `<your lens>` placeholder. A juror asked to recall the lens from 350 KB earlier, inside an artifact that names all eight lenses, answered the wrong lens three times in two rounds. Fill the placeholder at render time.
- **A rejected rung leaves its words on the record.** A wrong-lens answer or a voided verdict carries a bounded `raw_tail` on the rejected entry, so the next round reads the cause instead of inferring it.
- **Two cloud rungs is not a floor.** Every tier holds at least three rungs with no declared `context_tokens` (they can carry a 120k-token artifact) before its local tail. One wrong lens plus one void must never hold a lens.
- **A structured verdict never shares its budget with thinking.** A reasoning model asked for a bare JSON verdict spent its whole 65536-token budget thinking about a 131k-token artifact and emitted nothing (`finish_reason=length`); the role's time ceiling then killed the next rung mid-walk. Every cloud rung that asks for a JSON-object verdict runs with its reasoning channel off (`reasoning_effort: none`), and the verifier ladder keeps a plain-HTTP third family behind it.
- **Nothing else writes into the tribunal's repo while it convenes.** A concurrent grader's status file inside the checkout changed bytes under a seat, and the organ voided that verdict honestly: it cannot attribute a change. Serialize writers, or convene on a separate worktree of the same commit.

## The envelope

Jurors never see the builder or the conversation. They see one envelope:

- **Whole current files** for every file the change touched, plus its test files.
  Never bare diff hunks — a hunk hides the surrounding contract and induces false findings.
- **The review contract**: the change's intent in one line, and the pass criteria.
- **Zero authorship.** No names, no model ids, no commit authors, no chat history.
  If identity leaks through, the envelope build fails loud — never grade un-blind.
- **No prose about the old behavior.** Describing what the code "used to do" plants
  phantom defects. The files speak for themselves.
- **Prior adjudications ride along.** A finding refuted with evidence in an earlier
  round is appended as `PRIOR_ADJUDICATIONS`; jurors judge it honestly. Never re-seat
  a juror to launder a refusal — fix the envelope or the code.

## Rendering the eight prompts (portable)

One prompt per lens: `<tier header>` + `<lens preamble>` + `<envelope>` + `<output protocol>`.
Any runtime can do it with a dozen lines; keep the lens text in ONE file your tests read.

```python
lenses = {  # lens id -> (tier, preamble)
  "defect": ("generalist", "LENS: DEFECT. Blind juror. Check the code for logic flaws, syntax errors, and verify no new defects exist."),
  "proportion": ("generalist", "LENS: PROPORTION. Blind juror. Evaluate if this solution is over-engineered or appropriately scaled for the intent."),
  "operator_consequence": ("operator_safety", "LENS: OPERATOR CONSEQUENCE. Blind juror. Assess safety, destructive potential, and negative impacts if a human operator executes this code."),
  "reversibility": ("deep_state", "LENS: REVERSIBILITY. Blind juror. Evaluate if this execution leaves irreversible side effects. If it fails mid-execution, can the system cleanly rollback?"),
  "state_continuity": ("deep_state", "LENS: STATE CONTINUITY. Blind juror. Verify this slice does not orphan existing variables, clobber global state, or drop required context for downstream nodes."),
  "resource_economy": ("fast_structural", "LENS: RESOURCE ECONOMY. Blind juror. Check for unoptimized loops, redundant network/API calls, or unnecessary memory bloat."),
  "boundary_condition": ("fast_structural", "LENS: BOUNDARY CONDITION. Blind juror. Assume the inputs to this function are null, empty, unexpected types, or intentionally malformed. Does it fail gracefully?"),
  "telemetry": ("operator_safety", "LENS: TELEMETRY. Blind juror. Evaluate the observability of this slice. Are there sufficient logs or try/except blocks to easily diagnose a failure?"),
}
protocol = ('CRITICAL OUTPUT PROTOCOL: You are a strict deterministic evaluator in an autonomous fail-closed loop. '
  'Return exactly ONE valid JSON object as your ENTIRE response, no markdown fences, no preamble, no postscript: '
  '{"verdict": "pass" | "refuse", "lens": "<your lens>", "findings": ["[severity] file:line claim"], "reasoning": "<concise>"}. '
  'At most 10 findings; cite locations, do not quote code. Judge PRIOR_ADJUDICATIONS honestly. A refuse halts the pipeline.')
body = open("envelope.txt", encoding="utf-8").read()   # the author-redacted envelope
for lens, (tier, preamble) in lenses.items():
    footer = protocol.replace("<your lens>", lens)   # the footer names the lens; the placeholder never reaches a model
    open(f"prompt_{lens}.txt", "w", encoding="utf-8").write(f"{headers[tier]}\n\n{preamble}\n\n{body}\n\n{footer}\n")
```

## The verdict

Strict machine-parseable JSON, one object, no prose:

```json
{"verdict": "pass" | "refuse", "lens": "<lens>",
 "findings": ["[blocker|major|minor|info] file:line claim"], "reasoning": "..."}
```

- A juror that ANSWERED badly (garbage, non-JSON, refusal text, a verdict that is not
  exactly pass or refuse) counts as **refuse**; a juror that NEVER answered on any rung
  (transport failure, unreachable) is a **hold**: re-seat it via
  [fleet-ladder](../fleet-ladder/SKILL.md), never a silent pass. One shot per answering
  juror per round — no retries. A harness juror whose run changed repository bytes is
  voided (a hold).
- A bare pass with zero findings and no evidence is a **low-information vote**. It
  counts, but never as the only proof — two bare passes do not outrank one detailed
  refuse. A strong pass names what it checked.
- **A pass that lists a `[blocker]` or `[major]` finding is not a pass.** It is
  self-contradictory and fails closed to refuse, naming the severity that contradicted
  it. A juror that says "pass" while reporting a deployment-halting failure was reaching
  the lander as a clean seat before this rule.
- **A verdict for a lens other than the one seated is refused**, naming both lenses.
  Filing a juror's answer under the question it was asked — instead of the one it
  answered — hid the mismatch in a raw field nobody read.
- Take the LAST JSON object in the reply that carries a `verdict`; strip code fences;
  anything else fails closed to refuse — including a reply that is not text and a
  `findings` value that is not a list of strings (a malformed pass is not a pass).
- A fused seat records both verdicts: the primary's and the verifier's, with the
  verifier's findings prefixed `[verifier:<model>]`, plus `verified: true|false`.
  Count the unverified seats in the summary and print them on the console line.
- An UNVERIFIED seat is a hold, not a pass: it never makes unanimity, its pack seat
  refuses, and the command exits as a hold — never zero.
- **The out directory is owned before it is swept.** A filename shape (`summary.json`,
  `verdict_*.json`) is not ownership. The organ stamps a directory it claims; a directory
  holding those shapes WITHOUT the stamp is refused — the files and the remedy named,
  nothing deleted. A directory holding only someone else's unrelated files was never at
  risk and is not blocked: friction bought with nothing.
- **One lens blowing up never discards the verdicts already paid for.** Every seat failure
  — not only a ladder error — is recorded per lens, and verdicts, seats, and the summary
  are written BEFORE the run raises.
- **Mutation evidence names a rewritten file.** A juror that writes into an ALREADY-dirty
  file must appear in `changed_paths`; a set difference of dirty paths leaves that list
  empty while the fingerprint still voids the verdict — the guard bites, the evidence lies.
- **Everything the organ writes is owner-only (0600).** Prompts carry the whole candidate
  diff; verdicts carry raw model output.
- **A spilled local model is unloaded only by its LAST holder.** Lenses seat concurrently
  and two can share a card; the first to finish must not pull the model out from under a
  seat still mid-call. Hold a lease per (host, model); report the spill either way.
- Every convene carries a `run_id`: sweep only the files the tribunal owns from the
  out dir first, stamp the id on every verdict, and write `summary.json` last and
  atomically — it is the completion marker. A config defect on a proven rung halts
  LOUD with the finished seats' evidence on disk and the error in the summary.
- The artifact IS the bound diff, byte for byte: the receipt hashes exactly what the
  jurors read (`git diff --binary base...candidate`); a mismatch refuses before any
  juror is paid. Default author aliases come from repo truth (the repo's git identity
  and the candidate's author), never from a flag someone has to remember.

## The loop

1. Red first: commit the failing contract test BEFORE the fix is built, and record
   that commit. The builder may not touch the test ([red-first](../red-first/SKILL.md)).
2. Build to green.
3. Build the envelope from the CURRENT files.
4. Seat the eight jurors by tier — different families than the builder
   ([fleet-ladder](../fleet-ladder/SKILL.md) resolves what is live). Seat them in
   parallel; local cards serialize themselves.
5. The deep-state jurors also verify, not just read: the new tests pass; the regression
   suite is no worse than baseline; and a fake-green check — a test that SHOULD fail
   (the bug re-introduced) does fail. A fake green is a refuse.
6. On any refuse: EVERY finding (blocker, major, and minor) becomes a NEW failing
   test that fails for the finding's real reason. Fix it. Rebuild the envelope on the
   revised files, carrying the adjudications. Re-convene ALL jurors. A verdict on stale
   files is no verdict.
7. Land only on unanimous pass — all eight. Minor findings raised in the final round
   are closed too, never deferred — "fixed the blockers, minors later" is the exact
   leak this skill exists to stop. A finding ends FIXED or refuted with recorded
   evidence, never parked.

## Hard rules — any one broken voids the grade

- The builder never grades its own work: not the same instance, not the same family.
- The verifier never comes from the primary seat's family; a verifier that would be
  the same family holds instead.
- **A juror refusal is only as good as the envelope.** Before writing a test from
  a finding, verify the finding against the actual files. A finding about code the
  envelope never carried means fix the envelope, not the code.
- Measure convergence on NEW findings per round, not the raw total. New findings
  flat or growing two rounds running: stop and escalate to the human. Never grind.
- Never weaken or edit the failing tests to reach a pass. Jurors verify the test
  files are unchanged since the red commit.
- **A survivor is a claim; a green proof is a claim.** Re-run every reported mutation
  survivor by hand, in an isolated tree, with a ceiling that outlives the box's load. A
  timed-out run is not a survivor; a collection error is not a kill; a summary grep that
  matches nothing is not a pass. Every verdict path in a proof harness must be able to
  say INVALID, and a harness whose no-mutation baseline is not clean green refuses to
  emit verdicts at all. Five different harness lies were caught in one night; each one
  made the work look more finished than it was.
- A unanimous pass opens the gate; it is not the finish. Land, then prove the
  capability live on the real surface. Green without live proof is not done.

## Works well with

- [red-first](../red-first/SKILL.md) — the failing contract, committed before the builder runs.
- [sniper-testing](../sniper-testing/SKILL.md) — real side-effects, scoped runs, no mock theater.
- [seam-engineering](../seam-engineering/SKILL.md) — fix the class, sweep siblings, land a guard.
- [repair-loop](../repair-loop/SKILL.md) — the build loop this tribunal grades.
- [fleet-ladder](../fleet-ladder/SKILL.md) — resolves each tier to a live rung, cheapest first.
- [blind-eval](../blind-eval/SKILL.md) — the lighter keep-or-revert gate when the question is taste, not defects.

> Scaffold credit: Matt Pocock, grill-me / grilling (mattpocock/skills, MIT). The
> cross-family blind adversarial tribunal design, the eight lenses, and the per-lens
> routing are BACKS AIOS.
