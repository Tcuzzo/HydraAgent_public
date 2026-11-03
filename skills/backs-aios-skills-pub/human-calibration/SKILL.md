---
name: human-calibration
description: "Use when a build, design, communication, or consequential UX choice needs awareness of the operator's expressed preferences and working context."
license: MIT
---

# Know Your Human

Apply the operator's current request and corrections. Use evidence about their
working preferences to choose useful explanations, progress updates, and design
tradeoffs. Awareness is revisable context, never a fixed persona or permission.

## One profile pass

[operator-essence](../operator-essence/SKILL.md) owns the private awareness file and
its evidence. Reuse its current slice from [Build Flow](../backs-build-flow/SKILL.md).
If it is missing, run that skill's available insight workflow once, assess the
supplied report/history, and create provisional awareness when evidence is sparse.
Do not restart insight collection from each downstream skill.

An existing operator-designated profile can supply evidence: preserve its source,
date, confirmation status, and uncertainty when reconciling it into essence. Do not
scan unrelated profiles or infer which person owns a file from a display name.
Keep personal information outside public plugin source. A read-only task can pass
an in-memory slice without writing any profile.

## Apply what is known

- Explicit current preferences and corrections take precedence over historical
  observations. Project facts and acceptance checks still need verification.
- Distinguish observed choices from inferred tendencies. A rejected design or
  repeated request for progress updates supports a specific adaptation; it does
  not establish personality, identity, competence, or general risk tolerance.
- Use the smallest relevant slice: preference or observation, supporting evidence,
  proposed adaptation, and uncertainty. Show the operator where it came from.
- Preserve both sides of a real tension, such as context depth and execution speed.
  Resolve it with a concrete readiness condition and evidence, not by reducing the goal.
- An optional question can resolve a material unknown about taste or communication.
  Do useful authorized work first. Do not require a questionnaire or reconfirm
  preferences already provided merely because a new session started.
- Capture corrections and counterexamples when profile writing is authorized.
  A correction updates awareness; it never silently rewrites the task or grants
  authority for unrelated actions.

## Output

Return a concise awareness slice to the existing task owner. The user can inspect
and correct it. Adapt how the agent communicates and gathers evidence; retain the
same factual, quality, and authorization standards for every operator.

## Works well with

- [operator-essence](../operator-essence/SKILL.md) — private evidence and insight bootstrap.
- [human-voice](../human-voice/SKILL.md) — clear communication in the requested register.
- [intent-compiler](../intent-compiler/SKILL.md) — turn ambiguous language into an action contract.
- [operator-intent-deduction](../operator-intent-deduction/SKILL.md) — resolve choices from current intent.
