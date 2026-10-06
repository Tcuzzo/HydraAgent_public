---
name: "decision-bar"
description: "Use when you are about to ask your human a question, wait for approval, or park a decision during autonomous work. Filters every decision through one bar — only taste, vision, or destructive risk reach the human; everything else executes. Trigger words: ask-me bar, ask me, approval, permission, should I, decision, escalate, human in the loop, blocked on you."
license: "MIT"
---

# The Ask-Me Bar
**Effort:** free — one bar test at the moment you'd ask; cuts cost outright by killing interrupt round-trips. Removes: questions the standing rules already answer, and real decisions parked where the human never looks.

Agents fail their humans two ways: they interrupt with questions the rules already
answer, or they "surface" a real decision somewhere no one will ever see it. This
skill closes both.

## The bar

A decision reaches the human ONLY when it is genuinely theirs:

- **Taste** — style, wording, look, feel; the call has no objectively right answer.
- **Vision** — direction, scope, product intent; getting it wrong bends the mission.
- **Destructive risk** — data loss, irreversible action, real money, real people.

Everything below that bar EXECUTES — resolved from the standing rules, the project's
own truth, the human's known intent, and sensible defaults. Zero added friction.

## Steps

1. Catch the moment. You are about to ask, wait, or defer. Stop and run the bar.
2. Test it: is this taste, vision, or destructive risk? If none — it is not an ask.
3. Below the bar: look before asking. Re-read the standing rules and the code.
   The answer is almost always already written. Resolve it, execute, and note the
   call in your work log so the human can audit it later.
4. At the bar: DELIVER the ask. One plain-language summary of the situation, then
   the choices as a short list (as buttons if the human's channel supports them)
   on the channel the human actually watches. Then continue any work that does not
   depend on the answer.
5. Never park. A decision left in a doc, a commit message, a ledger row, or a long
   paragraph does not exist to the human. A parked decision is a hidden gate.

## Hard rules (any one fails the skill)

- Asking anything answerable from standing rules, the code, or sensible defaults.
- Inventing new approval machinery (a flag, a queue, a sign-off step) for
  below-bar work. Verification may be added; gates may not.
- Manufacturing an approval for a decision the human's standing rules already made.
- Parking a real decision anywhere the human does not actively look.
- Reporting "done" or "green" from a proxy probe instead of the human's surface —
  the proof law lives in [invariant-floor](../invariant-floor/SKILL.md).

## Works well with

- [wayfinder](../wayfinder/SKILL.md) — chart the route through unknowns below the bar instead of asking.
- [human-voice](../human-voice/SKILL.md) — the register every delivered ask is written in.
- [invariant-floor](../invariant-floor/SKILL.md) — the standing rules to re-read before any question goes up.
