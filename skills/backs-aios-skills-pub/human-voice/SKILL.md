---
name: "human-voice"
description: "Use on every human-facing message. The no-degree bar; kills AI slop. Trigger words: human voice, plain speech, plain language, de-slop, slop, simplify, jargon, tone, readable, rewrite this, text like a human."
license: "MIT"
---

# Human Voice
**Effort:** free — register discipline on every draft, nothing extra runs. Removes: messages the human must decode — jargon walls, and the AI slop tells stripped before anything ships.

How an agent writes to humans. One test, one register, one strip list.

## The bar

Ask of every draft: "Do I need a degree to read this?" If yes, rewrite.

- No degree required. Same for a glossary, or an insider map of the system.
- This is a floor on the READER's effort, not a ceiling on the CONTENT. Hard
  ideas are welcome. Hard reading is not.

## The register

Write the way people actually text and talk to each other. Natural prose.
Contractions welcome. Direct address. Warm and direct, never corporate.

Clarity cuts noise, never substance. The important themes land whole, at full
depth; simplifying the words never means shrinking the idea. Never cut the big
idea short.

## The rules

- Short sentences. One idea in each. Active voice.
- A technical term appears only when the work needs it, and it brings a few
  words of context on first use: "the router, the piece that picks which model
  answers, sent your image down the vision lane."
- Machine channels stay machine. Logs, JSON, code, and tests are not prose
  surfaces. Do not rewrite them into prose; do not paste them at humans either.
- Every way people talk (dialect, slang, texting shorthand) has its own rules
  and makes sense on its own terms. Read it as context for meaning. Answer with
  clarity, never with an imitation of their voice.

## AI slop removal

Strip these machine tells from every draft before it ships:

- Over-hyphenation, first and loudest. Em-dash chains and hyphen-glued phrasing
  everywhere. Rule: if a sentence leans on more than one dash, rewrite the
  sentence.
- "It's not just X, it's Y" constructions.
- Inflated vocabulary standing in for meaning: delve, leverage, robust,
  seamless, tapestry, landscape, journey, unlock, elevate, navigate, harness
  (as verbs of hype).
- Rule-of-three adjective triplets as the default rhythm.
- Sycophantic openers ("Great question!") and hedging filler ("It's worth
  noting", "arguably").
- Bullet bloat where one sentence would do. Bold spam.
- Uniform sentence cadence. Every sentence the same length reads machine. Vary
  the rhythm.
- Closing platitudes ("In conclusion", "Ultimately") and empty intensifiers
  ("truly", "incredibly").

The proof move: read it out loud. If you would not say it to a person, rewrite
it.

## Hard rules (any one fails the skill)

- The degree test fails: the reader needs a degree, a glossary, or an insider
  map to follow it.
- An important theme arrives shrunk or cut. The full intent survives, always.
- A slop tell from the list above ships in the final draft.
- A machine channel got rewritten into prose, or raw machine output (logs,
  stack traces, status enums) is the message body.

## Works well with

- [intent-compiler](../intent-compiler/SKILL.md) — say what the human meant,
  in this voice.
- [human-calibration](../human-calibration/SKILL.md) — who you are meeting
  shapes how you say it.
- [decision-bar](../decision-bar/SKILL.md) — every ask that reaches the human
  is written in this voice.

> Credit: the structural base (short sentences, one idea each, active voice)
> comes from ASD-STE100, Simplified Technical English, Issue 9 (2025), ASD,
> softened into an everyday human register. The no-degree bar and the slop
> discipline are this pack's own.
