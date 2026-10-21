---
name: "intent-compiler"
description: "Use when a human's ask arrives as natural prose (metaphor, slang, poetry, compressed shorthand, heat, or \"you know what I mean\") instead of a ticket. Translates the language into a stated technical directive, states the reading in one line, then executes. Trigger words: prose is the spec, read the prose, translate the ask, ambiguous prompt, unclear ask, what did they mean, deduce intent, metaphor, slang, vernacular, vibe, phrasing."
license: "MIT"
---

# Prose Is the Spec
**Effort:** free — reading discipline before any build, nothing extra runs. Removes: whole builds lost to a literal misread — the stated reading makes a wrong guess cost one word, not a rebuild.

People do not write tickets. They talk — fast, with rhythm, metaphor, and heat,
leaving out what they assume you already know. Most agents treat that as a
low-quality prompt and fail in one of two ways: they run the words literally,
or they park a question and wait.

Both are failures. The prose is not a rough draft of a spec. **The prose IS the
spec.** It carries more than a ticket does — priority, risk tolerance, taste,
and the reason. Compressed expression is not incomplete thinking. An agent that
cannot read it is throwing away the richest part of the input.

## The three forbidden failures

- **Literalism** — running a metaphor as an instruction. "Burn it down" is not
  a delete. "Kill it" is not a destroy. "Make it sing" is not audio. This is
  hallucination by dictionary, and it is a destructive-action risk.
- **Caricature** — mirroring the slang back, performing the dialect, reaching
  for stereotype to sound relatable. Read the culture; do not cosplay it. An
  agent busy performing is an agent not listening, and it misreads.
- **Invention** — filling a gap with something that sounds right. When the
  anchor is thin, say it is thin. Never fabricate meaning.

## Step 1 — Parse: split carrier from payload

Strip the input down to its mechanics.

- **Carrier** = cadence, repetition, volume, profanity, heat. Carrier marks
  priority and emotional weight. It is real signal. It is not content.
- **Payload** = the nouns, verbs, named surfaces, constraints, and quantities.
  This is the instruction.
- **Repetition is emphasis, not a second request.** "Fix it, fix it now" is one
  urgent fix, not two fixes queued.
- **Mark every metaphor and every double meaning.** A word can do two jobs at
  once — that is the point of the form, not an accident.
- **Compression is not vagueness.** Missing detail is usually detail the human
  assumed you had. Go find it before you call it missing.

Output: the ask rewritten as *priority* + *literal payload* + *a list of the figures that still need grounding*.

## Step 2 — Ground: anchor every reading in evidence

Strict priority — higher beats lower, always:

1. **The human's own record** — their past decisions, corrections, saved
   preferences, and profile (see [human-calibration](../human-calibration/SKILL.md)).
2. **The project's source truth** — the actual files, symbols, configs, docs.
3. **The lived vernacular** — the phrase's real meaning and history in its culture,
   read as context. A dialect is a valid grammar with its own internal logic.
4. **Model priors** — dead last, and never on their own.

A reading that only reaches rung 4 is a guess. Label it thin and keep going.

## Step 3 — Deduce: produce the four-part directive

State four separate things. The split exists to stop the number-one
misalignment risk — shrinking a big vision into something easier to build:

1. **Intended capability** — what the human actually wants to exist.
2. **Current boundary** — what the system can do today.
3. **The route available now.**
4. **The route required later.**

**Never lower the goal because the near route is short.** Build route 3, name route 4, keep capability 1 intact.

## Output protocol — state the reading, then build

Open with one plain line, then execute:

> **Read:** <the deduced directive, in one sentence>

- Grounded on rungs 1–3 → `Read:`
- Thin anchor, mostly inference → `Read (thin):` — and **build anyway**.

Ambiguity is resolved by deciding and saying so — never by parking a question.
The stated reading is the receipt: if it is wrong, the human's correction costs
one word instead of a whole build. A question goes back only when the call is
genuinely theirs (taste, vision, or destructive/data-loss risk; see
[decision-bar](../decision-bar/SKILL.md)), and then as a plain summary with
choices, never a paragraph of hedging.

## Fluency, not costume

Speaking the language is comprehension and register: understanding what the
words mean, and answering in plain, warm, modern speech (see
[human-voice](../human-voice/SKILL.md)). Cosplaying the language is
performance. An agent that actually speaks the language does not need to perform
it. Fluency shows up as getting the read right — not as an accent.

## Example reads

| They said | Literal misread (wrong) | Anchored reading |
|---|---|---|
| "burn it down" | delete the files | The approach is wrong at the root — redesign it. High heat = top priority. Destructive action still needs an explicit yes. |
| "make it sing" | audio | The surface should feel alive — motion, transitions, responsiveness. |
| "don't build toys" | avoid a games folder | It must produce a real outcome, not a demo. |
| "fix it, fix it now" | two tickets | One fix, urgent. |

## Red flags — you are about to misread

- "This prompt is too vague to act on." → It is compressed. Ground it first.
- "Let me ask what they mean." → State the reading and build.
- "I'll match their energy in the reply." → Caricature. Read, don't perform.
- "I'll build the small version that's clearly possible." → Never shrink the
  intended capability — name route-now and route-later instead.
- "The vibe words aren't real requirements." → The vibe IS a spec. Route
  aesthetic reads to [design-taste](../design-taste/SKILL.md).
- "I'll fill the gap with what usually makes sense." → That is priors alone.
  Label it thin, or go find the anchor.

## Works well with

- [understanding-gates](../understanding-gates/SKILL.md) — translate before
  scoring; a stage gate graded on raw poetic prose marks faithful work wrong.
- [human-calibration](../human-calibration/SKILL.md) — the record this skill
  grounds in.
- [decision-bar](../decision-bar/SKILL.md) — the only bar a question may cross.
- [human-voice](../human-voice/SKILL.md) — the register for the way back.
