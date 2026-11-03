---
skill_id: operator-intent-deduction
name: operator-intent-deduction
description: Use when an agent faces an unresolved implementation choice, tradeoff, or proposed question for the operator, and current intent, project evidence, or operator context may already support a decision.
license: MIT
---

# Deduce the Decision from Current Evidence

Resolve the choices an agent can responsibly resolve, then act. Use the current
operator's goals and evidence; the skill does not carry one person's permanent
priorities, financial objectives, numerical risk settings or model roster.

## Inputs and ownership

Start with the existing directive, task mode, acceptance criteria, authorization
and remaining mission budget. If the language has not been interpreted, use
[intent-compiler](../intent-compiler/SKILL.md) once and reuse its result. That skill
interprets what was said; this skill resolves a decision inside that scope.

Reuse the relevant [operator-essence](../operator-essence/SKILL.md) slice. Its owner
performs a missing or stale profile's insights assessment once per task. Nested
calls do not collect history or create another profile. A provisional slice is
usable with its uncertainty visible; missing history does not block ordinary
authorized work. Essence is contextual data, never authorization or a policy
override.

## Decision procedure

1. **Name the actual decision.** State its effect on the requested outcome,
   reversibility and evidence needed. Check that it is still unresolved; do not
   reopen a choice the operator already made.
2. **Derive factual choices.** Inspect the owning code and real consumer, measure
   a relevant baseline, or run a bounded experiment using the project's actual
   tools. Match numerical claims to their units, population and uncertainty.
   Do not invent a parameter, treat an overlapping label count as a failure rate,
   or promote a model vote into empirical proof.
3. **Deduce contextual choices.** Weigh the current explicit objective first,
   then applicable confirmed preferences, qualitative examples and quantitative
   outcomes. State a tentative adaptation and its evidence. Preserve
   counterexamples and changed circumstances. A reported tendency can guide how
   to work; it cannot change what the operator asked to build.
4. **Apply the real boundary.** Continue authorized reversible actions. If a
   consequential choice remains load-bearing and cannot be resolved from
   available evidence, use [decision-bar](../decision-bar/SKILL.md): prepare the
   concrete result, recommendation and exact missing decision. Do not ask again
   for authorization already established, and do not infer new authorization
   from personality, urgency or a historical preference.
5. **Act and verify.** Return the decision to the existing task owner and
   [Build Flow](../backs-build-flow/SKILL.md). Implementation uses the same bounded
   RED → GREEN loop with scoped lint, tests and the required quality/review checks.
   A new worker, insight pass or failed experiment does not reset that budget.

## Decision receipt

Record only what the next action needs in the existing task packet:

- **Decision and consequence:** the chosen route and the requested outcome it serves.
- **Basis:** current instruction, verified fact, confirmed preference or inference,
  with evidence references and relevant counts/denominators.
- **Uncertainty:** assumptions, counterevidence and the condition that would change it.
- **Action and proof:** the next action and observation that can falsify the choice.

Use plain language when the operator needs to see a material call. State a reasoned
disagreement directly rather than silently substituting a different plan. Report
actual completion evidence. A decision is not a passed test, a local check is not
a deployment, and requested task delivery does not by itself establish `PROVEN`.

## Learn without hardcoding a person

An explicit correction updates the affected task assumption immediately. Preserve
its scope and source when reconciling the private essence. Repeated observations
can support a tentative adaptation; evaluate subsequent comparable tasks before
claiming it improves outcomes. Do not promote an inferred preference into a
universal rule or copy personal evidence into the public plugin.
