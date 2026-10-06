---
name: "conscientious-objector"
description: "Use when an IDE agent (Cursor, Codex, Claude Code) is operating inside BACKS / BACKSAIOS and a human-quality watchdog is needed to verify the harness is being used truthfully. The Conscientious Objector flips the build RED and strips tools from any agent that bypasses the harness (missing skill invocation, mock-theater tests, claim-vs-code drift, silent fallbacks, hidden restrictions). It NEVER sides with an agent going outside the runtime. Sentry-locked: any shape change is RED-FLAGged; the operator can disarm via dual-factor (authenticator TOTP + Telegram approval button) within a 90-second window. Trigger words: CO, conscientious objector, harness watchdog, harness truthfulness, harness bypass, going outside the runtime, harness veto, RED flip, tool strip, RED FLAG, dual-factor kill switch, sentry lock, BACKSAIOS IDE watchdog."
license: "MIT"
---

# Conscientious Objector — the BACKS harness watchdog

This is the AIOS-pack load-side surface. The full implementation lives in the
BACKS runtime at `skills/conscientious-objector/SKILL.md` and
`backend/services/conscientious_objector.py` — load it from there when this
session is running inside the BACKS repo.

## When the CO is the right organ

Any agent building with BACKSAIOS in an IDE — Cursor, Codex, or Claude Code —
needs a watchdog that:

- Refuses to side with the agent when the harness is bypassed.
- Detects harness bypass in real time (real probes, not hardcoded rules).
- Flips the build RED and strips tools from the watched agent on a real finding.
- Locks its own shape against silent change (sentry).

That watchdog is the Conscientious Objector. Attach it; do not bypass it.

## The four kill switches the operator can fire (on-site, no Telegram required)

1. **Surface file flip** — write `{"override": true}` to
   `backend/state/co/operator_override.json`.
2. **IDE / chat SHA code** — type `CO-OVERRIDE: <64-hex-sha>` in the chat.
3. **Local env token** — set `CO_OPERATOR_LOCAL_TOKEN` in `.env` to the
   operator-issued token (60-second window).
4. **Telegram dual-factor** — operator authenticator TOTP plus the Approve
   button on the CO's Telegram card.

All four must work. The CO is forbidden to lock out the operator.

## What this pack skill is, and what it isn't

- **Is** — the AIOS-portable load-side surface. Lets the IDE discover the CO
  via this pack and route the operator's `/co` invocation to the BACKS
  implementation.
- **Isn't** — the watchdog itself. The watchdog is the BACKS-side
  `ConscientiousObjector` class. This skill is the address; that class is the
  body.

When this session is inside the BACKS repo, the runtime resolves the CO from
the BACKS source. When this session is outside BACKS, this skill is the
trailer that says "use BACKS to get the CO."
