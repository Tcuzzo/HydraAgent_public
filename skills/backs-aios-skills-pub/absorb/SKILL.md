---
name: "absorb"
description: "Use when you need a capability an open-source project already provides — adopt it and re-engineer it as a native skill instead of inventing a duplicate. Trigger words: absorb, adopt, port, re-engineer, ingest a repo, prior art, capability port, make this native."
license: "MIT"
---

# Absorb — Adopt Prior Art, Don't Reinvent It
**Effort:** light — a repo ingest (metadata probe + shallow clone) and a cross-family grade of the port. Removes: reinventing a capability prior art already solved — the from-scratch duplicate you'd owe every bug for.

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

1. **Hunt prior art first.** Search before you build. A duplicate you invent is worse
   than a scaffold you adopt: you inherit zero field testing and you owe all the bugs.
2. **Ingest beyond the README.** Pull the project metadata (license, activity,
   language) from the platform API. Shallow-clone into a scratch directory. Read the
   code and the tests. The README is marketing; the code is the truth.
3. **Run the trust gates.**
   - *License:* permissive (MIT / Apache / BSD / MPL) = safe to re-engineer.
     Copyleft (GPL / AGPL) = technique-only — re-engineer the idea, never copy the
     code. No license = treat as all-rights-reserved, technique-only.
     Non-commercial terms = a blocker; take it to your human.
   - *Shady scan:* grep for cloak / spam / fake-review / scam patterns. Flag loudly.
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
6. **Reconstruct red-first.** Commit the failing tests, then build until green across
   the whole seam. A model from a different family than the builder grades the result
   — the builder never grades its own work.
7. **Cite and record.** Write the scaffold credit where the capability now lives:
   author, project, license, what is borrowed (the scaffold) and what is yours (the
   re-engineering). Never invent a credit. Never strip one.

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
- [blind-tribunal](../blind-tribunal/SKILL.md) — cross-family grading of the port.
- [decision-bar](../decision-bar/SKILL.md) — license blockers and taste calls go to your human; everything else executes.
