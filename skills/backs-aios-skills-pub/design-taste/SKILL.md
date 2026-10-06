---
name: "design-taste"
description: "Use before building anything visual (a site, app, dashboard, console, or deck) so it ships with real taste instead of generic AI defaults. Trigger words: design, UI, taste, design tokens, design system, accessibility, WCAG, screenshot critique, dark mode, restyle."
license: "MIT"
---

# Design Taste — Tokens First, Eyes On, Accessibility Hard
**Effort:** light — a token file before any component, plus a screenshot → vision-critic pass per rendered surface. Removes: reshipping generic AI defaults — the restyle rework and the post-ship accessibility retrofit.

Generic UI is a workflow bug, not a model bug. Fix it structurally: read the brief as
a spec, set exact design tokens before any component, forbid the defaults by name,
give the builder eyes with a screenshot loop, and gate on accessibility — hard.

## When to run

- Any "build me a / design me a …" request that renders pixels.
- Before scaffolding a frontend or a customer-facing deliverable.
- When an existing surface looks generic and needs a specific, defensible direction.

## Steps

1. **Read the brief as spec.** A metaphor, a cadence, a named era, artist, or place in
   the human's words is a concrete design constraint, not decoration. Full
   read-the-brief discipline: [intent-compiler](../intent-compiler/SKILL.md).
2. **Pick a grounded direction.** Choose a *lead* reference (a real design system or
   library that sets the structural baseline) and an *accent* reference (one that
   stamps its signature on top). Both must be real and current, with a verifiable
   taste signature. An invented vibe fails the gate closed.
3. **Emit tokens FIRST.** Before any component, write a machine-readable, three-tier
   design-token file (primitive → semantic → component; W3C token format, `$value` +
   `$type`). Fix up front: a perceptually even color ramp (Oklch — a color space
   where equal steps look equal), a real type scale on a non-default typeface, one
   spacing increment (4px base → 4/8/12/16/24/32/48/64), a radius scale, an elevation
   scale, and named motion tokens (duration + easing per enter / scroll / state
   change; honor `prefers-reduced-motion`). Dark and light are first-class and both
   resolve from the SAME semantic tokens.
4. **Forbid the generic defaults by name.** Prohibitions beat adjectives: no
   default-reflex font (Inter/Roboto), no purple gradients, no centered hero, no
   three-equal-card row, no gray-on-white slab. Add your own banned list per project.
5. **Build under constraint.** Components consume tokens only. A raw hex, px, or font
   family hardcoded inside a component is a defect.
6. **Close the screenshot → vision-critic loop.** For anything rendered: render it in
   a headless browser at mobile and desktop widths, screenshot, and have a vision
   model score it — then fix, in separate passes (critique → structural fix → audit →
   polish), never one-shot. The critic is a grader: use a model from a different
   family than the builder, scoring named axes, never one holistic score. Resolve the
   critic model from config at call time — a pinned model id retires someday and
   takes the whole loop down with it.
7. **Score the 8-axis taste rubric.** 0–3 per axis, and every axis must score ≥ 2:
   token-adherence · layout/hierarchy · typography · color/contrast · motion ·
   dark-light parity · accessibility · designed-vs-mean gut check ("does this look
   designed, or like the average of everything?"). One axis under 2 = not done.
8. **Enforce the accessibility HARD gate (WCAG 2.2).** Pointer targets ≥ 24×24 CSS px.
   Visible focus indicator ≥ 2px perimeter at ≥ 3:1 contrast. Text contrast ≥ 4.5:1
   normal, ≥ 3:1 large text and UI components. Fully keyboard-navigable. Contrast
   verified in BOTH themes. This is a gate, not a suggestion: fail = do not ship.
9. **Test the code behind the pixels.** Token resolvers, theme switches, contrast
   calculators, and state reducers get real tests on real rendered DOM — a flipped
   comparison in a contrast check ships a beautiful screen that is silently
   inaccessible. Tests judge the code; the rubric and the WCAG gate judge the taste.

## Hard rules — any one of these fails the skill

- A component written before the token file exists.
- A raw hex / px / font family inside a component.
- Any item from the banned-defaults list appearing in the output.
- Skipping the screenshot → critic loop for anything rendered.
- The builder grading its own visuals, or a single holistic score instead of axes.
- Any rubric axis below 2, or any WCAG 2.2 check failing, at ship time.
- A taste direction that cannot be grounded in a real, verifiable reference.

## Works well with

- [intent-compiler](../intent-compiler/SKILL.md) — the full read-the-brief discipline.
- [blind-eval](../blind-eval/SKILL.md) — keep-or-revert when taste is the question.
- [clean-code-gauntlet](../clean-code-gauntlet/SKILL.md) — hardening the code behind the pixels.
- [blind-tribunal](../blind-tribunal/SKILL.md) — cross-family grading before landing.

> Scaffold credit: W3C Design Tokens Community Group (token format); WCAG 2.2, W3C
> (accessibility gate); UICrit, UIST 2024 (axis-scored UI critique); AI Jason, &
> JackJack. (2025). superdesign: AI design agent [Computer software]. GitHub.
> https://github.com/superdesigndev/superdesign (AGPL-3.0; dual-licensed with a
> commercial enterprise license) — forbid-the-defaults. The composition and hard
> rules here are BACKS AIOS.
