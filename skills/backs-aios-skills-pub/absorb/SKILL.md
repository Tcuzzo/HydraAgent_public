---
name: "absorb"
description: "Use when a needed capability may already exist in an open-source project, or the operator asks to absorb, adopt, port, re-engineer, ingest a repo, evaluate prior art, or make an existing capability native."
license: "MIT"
---

# Absorb — Adopt Prior Art, Don't Reinvent It
**Effort:** light — a repo ingest (metadata probe + shallow clone) and an independent grade of the port. Removes: reinventing a capability prior art already solved — the from-scratch duplicate you'd owe every bug for.

**Capability is king.** A repo is a vehicle for a capability. When you need something
an existing project already does, do not build a duplicate from scratch, and do not
clone-and-paste. Find the best prior art, extract the capability, re-engineer it to
fit your harness, and cite the scaffold. The citation is a fact, not a decoration.

## When to run

- You are asked to add a capability (a tool, skill, agent, pipeline) that open source
  likely already solves.
- You are about to `git clone` and copy code verbatim — stop; this is the path instead.
- Skip it for a single snippet, a config value, or a fact lookup. Just read those.

## Steps

Use the current [BACKS Build Flow](../backs-build-flow/SKILL.md) task packet and
integration owner. Direct invocation creates that packet once; a nested call
returns its adoption evidence to the caller. Reuse valid context, operator essence,
and already loaded skill bodies. [bounded-loops](../bounded-loops/SKILL.md) owns
the mission's deadline and remaining attempt/round allowances; discovery, builds,
mutation runs, reviews, model changes, and handoffs all spend that same budget.
Neither a new phase nor a new worker resets it. Respect the packet's `task_mode`:
review or diagnosis does not authorize native edits or delivery.

1. **Hunt prior art first.** Search before you build. A duplicate you invent is worse
   than a scaffold you adopt: you inherit zero field testing and you owe all the bugs.
2. **Discovery beyond the README.** Pull the project metadata (license, activity,
   language) from the platform API. Shallow-clone into a scratch directory. Read the
   code and the tests. For shipped artifacts or requested runtime behavior that
   source inspection cannot answer, call
   [rea-investigation](../rea-investigation/SKILL.md) with the exact target and a
   bounded question. REA returns evidence to this caller; it does not restart
   Absorb. Record upstream identity, observations, limits, and unknowns before
   choosing what to adopt. Static findings do not establish runtime behavior.
3. **Run the trust gates.**
   - *License:* permissive (MIT / Apache / BSD / MPL) = safe to re-engineer.
     Copyleft (GPL / AGPL) = technique-only — re-engineer the idea, never copy the
     code. No license = treat as all-rights-reserved, technique-only.
     Non-commercial terms = a blocker; take it to your human.
   - *Shady scan:* use `rg` for cloak / spam / fake-review / scam patterns. Flag loudly.
   - *No wild installs:* never `pip install` / `npm install` an unvetted dependency
     (typo-squatting is a real supply-chain attack). Re-engineer as thin code over
     your own primitives instead.
   - *Is the capability real?* Verify claims against independent evidence. A seller's
     blog is a claim, not evidence. Verdict: real / hype / scam / unverifiable.
   - *Bounded egress:* anything the adopted version fetches must be throttled,
     cached, and killable.
4. **Deconstruct into a capability map.** For every ability the project provides,
   record: what it does, how, its load-bearing seams, its bloat or risk, what you can
   reuse from your own stack, and whether it lands native or behind a thin adapter.
   Every capability is **preserved or refuted with evidence**. A silently dropped
   capability is a defect.
5. **Write the re-engineering spec.** The seams to build, the bloat you are dropping
   (recorded loudly, never silently), and one failing contract test per capability
   that asserts a real side-effect — a file, a database row, real output. Mock only a
   paid external API's transport, never the logic.
6. **Native integration.** Apply [red-first](../red-first/SKILL.md): observe a real
   failure and seal the contract before the minimal fix at the owning seam. Keep
   the original test file unchanged. Scope the repository's actual lint,
   type-check, and test commands through [sniper-testing](../sniper-testing/SKILL.md)
   against the pinned `base_sha` and current `candidate_sha`, including owned
   pending paths when applicable. Record unavailable or inapplicable checks
   explicitly; never install an arbitrary checker or invent a passing result.
   Run [clean-code-gauntlet](../clean-code-gauntlet/SKILL.md) inline for quality
   and mutation evidence. A test gap gets a separate supplemental regression,
   never an edit to the sealed original. All work stays within the shared budget.
7. **Verification of the adopted capability.** Exercise the native result against
   the capability map and the user's actual surface. Reuse discovery evidence;
   request a further REA observation only for a specific unresolved comparison,
   within its existing provider and execution boundaries. Select a capable grader
   through the configured rung matrix for a fresh author-disjoint review of the
   exact aggregate candidate; record and exclude the authoring execution. The
   builder never grades its own work. Run the required affected-suite landing pass
   and follow the project's authorized delivery procedure. Static upstream
   evidence or local GREEN alone never establishes native live delivery.
8. **Cite and record.** Write the scaffold credit where the capability now lives:
   author, project, license, what is borrowed (the scaffold) and what is yours (the
   re-engineering). Never invent a credit. Never strip one. Return the capability
   map, exact candidate, check results, attribution, and unresolved gaps to the
   integration owner. If the budget expires or a required gate cannot run,
   checkpoint **STILL-BUILDING** with remaining work and the next action; continue
   useful authorized work that does not spend the exhausted allowance. Never
   manufacture **PROVEN** or silently drop a capability to obtain a pass.

## Hard rules — any one of these fails the skill

- Copying code verbatim instead of re-engineering the capability.
- Building a duplicate without ever searching for prior art.
- Trusting the README or a marketing page over the code.
- Installing a wild dependency instead of re-engineering the technique.
- Copying copyleft or unlicensed code (technique-only, always).
- Dropping a capability without a written refutation.
- Mock theater in a capability test — the test must touch a real side-effect.
- Shipping without the scaffold citation.

## Works well with

- [red-first](../red-first/SKILL.md) — the contract tests that guard each capability.
- [sniper-testing](../sniper-testing/SKILL.md) — real side-effects, no mock theater.
- [blind-tribunal](../blind-tribunal/SKILL.md) — independent non-author grading of the port.
- [decision-bar](../decision-bar/SKILL.md) — license blockers and taste calls go to your human; everything else executes.
