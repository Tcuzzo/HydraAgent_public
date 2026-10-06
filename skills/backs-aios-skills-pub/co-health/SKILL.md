---
name: "co-health"
description: "Use when the Conscientious Objector (or any watchdog) needs a health check on the operator's own cadence — proven live, on demand, never silently. CO Health is the autonomic self-healer: tests first, clears failures (auto-revert), then names itself. Self-test battery covers locked-shape integrity, watchdog probe, dual-factor seam, local override path, auto-revert safety net, public release, and skill registry. Bounded — every check has a small fixed budget, never silently retries, never pages the operator on micro-issues. Operator kill switches always work; the Health Doctor never locks out the operator. Trigger words: CO health, co-health, health doctor, autonomic self-healer, watchdog health, watchdog self-test, CO self-test, watchdog alive, watchdog up, is the CO running."
license: "MIT"
---

# CO Health — the autonomic watchdog doctor

This is the AIOS-pack load-side surface. The full implementation lives in
the BACKS runtime at `skills/co-health/SKILL.md` and
`backend/services/co_health.py`. The public CLI companion is
`backend/services/co_doctor.py`.

## What the Health Doctor covers

The self-test battery is seven probes, each with a small fixed budget:

1. **Locked shape** — the watchdog's source files hash to the locked value.
2. **Watchdog probe** — the CO actually responds to a probe and returns a
   verdict.
3. **Dual-factor seam** — the TOTP + Telegram path resolves (without firing a
   real challenge).
4. **Local override path** — the four operator kill switches resolve.
5. **Auto-revert safety net** — the snapshot+revert path restores FILE
   CONTENTS (not just hash records).
6. **Public release** — the public CLI is wired and answers `co doctor status`.
7. **Skill registry** — the CO family is registered on the chat surface and
   answerable.

## When to invoke

- On demand — `co doctor status` from any surface that exposes the CLI.
- On cadence — the Health Doctor runs itself on a schedule the operator sets.
- After a watchdog change — the next health check proves the change did not
  break the locked shape.

The Health Doctor is the answer to "is the watchdog actually up?" — proven
live, not narrated.
