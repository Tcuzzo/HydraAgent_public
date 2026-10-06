---
name: "human-calibration"
description: "Use when a build, design, or consequential UX decision starts and you must first meet the human it serves. Loads or builds a session profile of how this human thinks, decides, and wants to be spoken to, then steers the whole build through it. Trigger words: yoke, know your human, human profile, session profile, grounding ladder, interaction model, intent."
license: "MIT"
---

# Know Your Human
**Effort:** light — one profile pass: load the saved profile, or build it from at most 7 casual questions. Removes: re-interrogating a human who already answered, and rework from builds that misread their taste.

A build that misreads its human is wrong before the first line is written.
This skill replaces guessing with a working model of the human it serves:
thinking pattern, taste, register, and where their word is trusted outright.
Meet the human where they are — never make them rise to the system's level.

## When to run

At the start of any build, design, uplift, or consequential UX decision. Not chat decoration.

## The flow: profile or interrogate

1. **Identify the human.** Check `.agent/profiles/<human>.md` in the project,
   then the agent's home config dir (e.g. `~/.claude/profiles/<human>.md`) for
   an all-projects profile. If a validated profile exists there, load it and
   apply it. Never re-interrogate a human who already has one.
2. **No profile? Run the question protocol** (below). Up to 7 casual questions,
   plus at most 3 follow-ups where an answer opens a thread. Always optional —
   a human who shrugs one off gets profiled from observed behavior instead.
   Never a gate on the work.
3. **Synthesize a session profile** (template below). Every field carries a
   `source` and a `status`. A section with no evidence stays empty: empty is
   honest, guessed is a hidden inference.
4. **Reconcile the goal.** Restate the build's intent through the profile, in
   the human's own register — one plain paragraph, not a spec. They confirm or
   correct. Their correction is final.
5. **Reprompt yourself.** Before executing, rewrite your working prompt through
   the profile: what they meant, which statements to trust, which need one
   subtle check, what will feel alive to them and what will feel disrespectful.
6. **Build with the profile as the guiding hand** — design, engineering, UX,
   and taste decisions all steer through it.
7. **Learn.** Observed choices, rejections, and corrections update the profile —
   saved back to `.agent/profiles/<human>.md` (or the home config dir for an
   all-projects profile). Correction wins, instantly.

## The grounding ladder (priority order, absolute)

```
HUMAN CORRECTION
  > OBSERVED REPEATED BEHAVIOR
  > DECLARED ARCHETYPE   (what they say they are)
  > CULTURAL PATTERN     (what that declared archetype typically implies)
  > MODEL GUESS
```

No lower rung ever overrides a higher one. Archetypes and cultural patterns are
steering context, never a box — observed behavior and correction outrank them.

## The question protocol

Design rules: no degree needed to answer. Casual true/false and either/or. One at a time,
sprinkled through the goal conversation — never fired as a list, never scored,
never repeated. Capture the human's own phrasing; it matters as much as the answer.

The 7 core questions (each reads two or more axes at once):
1. New gadget: read how it works first, or just start pressing buttons? → processing style, risk comfort.
2. True/false: ugly bugs you more than slow. → taste priority (aesthetic vs mechanical).
3. Late friend: quick text, or a call with the whole story? → register (compressed vs narrative).
4. Building a treehouse: picture the finished thing, or the first board? → whole-picture vs step thinking.
5. True/false: rules that make no sense should still be followed. → frame acceptance vs challenge.
6. Three good options, or one strong recommendation you can veto?
   → authority preference — directly sets how you present decisions.
7. Their work gets criticized: defend, fix, or ask what they'd do instead?
   → correction style — sets how you deliver hard findings.

Follow-ups (max 3, only where a core answer opens a thread): gut trusted
everywhere or only where they're great (trust map); "good enough is good
enough?" (shipping bias); change-it-later freedom vs works-today certainty
(reversibility taste); still theirs after someone else edits it (ownership);
"what do people get wrong about how you work?" (identity anchor, their words).

## The trust rule

The profile maps where this human's judgment is strong and where it is weak.
- **Strong area + confident statement → trust it.** No re-deriving, no
  second-guessing, no explaining basics back to them.
- **Weak area + vague statement → one subtle check.** Ask one casual question
  that resolves the ambiguity, or offer your interpretation for a one-word
  confirm. Never challenge them to their face; never silently substitute your
  own plan.
- **Never use the profile to cap what the human may attempt.** It tunes HOW you
  listen, never WHETHER you obey.

## Session profile template (compact)

```markdown
# SESSION PROFILE — <human>
## Identity anchors   # value + source (declared|observed|cultural|guess) + status (confirmed|working|needs-validation|rejected)
## Working pattern    # one paragraph: how the anchors combine for THIS human
## Steering traits    # "likely to: <behavior>" → "so I: <concrete agent rule>"
## Trust map          # strong areas (trust outright) / weak areas (one subtle check)
## Core tension       # both/and needs that look contradictory but are requirements
## Misalignment risk  # the most likely misread, stated as a prohibition
## Ledger             # date, ladder rung, change, evidence
```

A session profile is session-scoped: in a new session it is data, not truth,
until the human re-confirms it or behavior re-earns it. The profile is the
human's property: show it on request, correct it the moment they say it is
wrong, and never act on an inference they cannot see — that is a hidden gate.

## Hard rules (any one of these fails the skill)

- Re-interrogating a human who already has a validated profile.
- Making the questions feel like a test, or making them mandatory.
- A guessed field dressed as a confirmed one.
- A lower ladder rung overriding a higher one.
- Using the profile to limit what the human is allowed to attempt.
- Lowering the goal because one route is incomplete. Separate: intended
  capability → current boundary → route available now → route needed later.

## Works well with

- [human-voice](../human-voice/SKILL.md) — the register to answer in once the profile says how they listen.
- [decision-bar](../decision-bar/SKILL.md) — which decisions reach the human at all; the profile shapes how they arrive.
- [intent-compiler](../intent-compiler/SKILL.md) — the human's prompt is the spec; the profile tells you what they meant.
- [model-fusion](../model-fusion/SKILL.md) — panel-then-compress synthesis of the profile.
