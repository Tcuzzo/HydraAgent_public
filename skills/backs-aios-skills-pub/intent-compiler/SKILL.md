---
name: intent-compiler
description: Use when a request arrives as compressed prose, metaphor, slang, shorthand, or ambiguous references and the agent needs a grounded technical directive. Also use when a correction changes the intended capability or requested finish.
license: MIT
---

# Compile the Current Request

Translate what the operator said into an actionable contract, state the reading
briefly, and continue the work. Compressed expression can carry a complete spec.
The operator does not need to rewrite it as an engineering ticket.

## One parse for the current task

If the current packet already contains a directive and no new instruction changes
it, reuse it. This skill owns language interpretation;
[operator-intent-deduction](../operator-intent-deduction/SKILL.md) owns an unresolved
decision within that directive. Neither restarts the other's full workflow.

### 1. Parse the actual words

Extract the requested capability, action, named surfaces, quantities, constraints
and observable finish. Mark unresolved references and metaphors. Repetition can
signal emphasis without creating another task. Tone can express urgency; it
does not establish permission, risk tolerance, competence or identity.

Interpret figurative language in its actual task context. A request to "burn it
down" may concern an approach; it is not by itself authorization to delete data.
Answer in clear language without imitating a dialect or inventing a persona.

### 2. Ground only the uncertainties that affect action

Use this order within the governing instructions:

1. The operator's current explicit request and corrections.
2. Established scope and authorization in this conversation.
3. Verified project facts, targets, contracts and current state.
4. Relevant confirmed preferences and evidence-backed observations from
   [operator-essence](../operator-essence/SKILL.md).
5. A clearly labelled provisional inference when evidence is thin.

If essence is absent or stale, its owner makes the single supported insights pass
and returns a provisional or current slice. Reuse that result in nested skills;
do not repeatedly collect history. Historical preferences cannot override a new
instruction. Source facts can constrain the route without shrinking the goal.

Qualitative evidence establishes the meaning and context of a preference.
Quantitative evidence can describe repeated outcomes in its stated population.
Counts alone cannot decide what the operator meant, and a report's inferred
tendency is not an operator-confirmed instruction.

### 3. Produce the directive

Carry these fields in the existing task packet, without creating a second plan:

| Field | Required content |
| --- | --- |
| Intended capability | What the operator wants to exist or learn. |
| Current boundary | What verified state and available capabilities permit today. |
| Route now | The next useful action under existing authorization. |
| Route later | Remaining dependencies needed for the full intended capability. |
| Task contract | Mode, target, boundaries, requested finish and acceptance evidence. |
| Uncertainty | Load-bearing unknowns and the evidence that could resolve them. |

Keep the full intended capability visible when an external dependency limits the
current route. A review produces findings; it does not silently become a repair.

## State the reading, then act

Use one plain sentence when interpretation matters:

> Read: Update the existing integration, preserve its public interface, and verify the actual consumer before release.

Label a thin inference when necessary. Proceed with the reversible work justified
by the evidence. Resolve factual uncertainty through inspection or a focused
experiment. A missing load-bearing authorization or genuinely unresolved operator
choice gets one concrete recommendation and question after available preparation;
consult [decision-bar](../decision-bar/SKILL.md). An old preference cannot supply
missing permission for a new consequential action.

Return the directive to [Build Flow](../backs-build-flow/SKILL.md). Invoke intent
deduction only for a real remaining decision. A later correction updates the
existing packet and relevant essence evidence; it does not silently replace the
goal or reset the remaining mission budget.
