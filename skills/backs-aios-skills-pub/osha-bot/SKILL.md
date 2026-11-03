---
name: "osha-bot"
description: "Observe harness and watchdog evidence, report findings truthfully, and preserve operator and agent tools and source. Use for OSHA Bot checks in BACKS."
license: "MIT"
---

# OSHA Bot — observe and report

This portable skill is a load-side bridge, not the watchdog implementation.
Inside BACKS, load `skills/osha-bot/SKILL.md` from the current runtime and
follow `backend/services/osha_bot.py::probe`. Read the current `H_Boot.md`, `CLAUDE.md`
and constitution there; repository source owns the exact contract.
Outside BACKS, report that the native runtime is unavailable rather than
claiming that its probes ran.

## Observation preserves capability

CO and OSHA are bystanders. Findings report missing grounding, source drift,
invalid or incomplete integrity evidence, and watchdog failures. GREEN requires
real corresponding evidence. RED/KILL labels are evidence states, never authority
to deny a command, strip tools, stop an agent, or require operator re-attestation.

The existing harness hook delivers actual trusted context and records its hashes
and session identity. Context delivery is separate from skill invocation evidence.
Missing context calls for loading it, without refusing tools.

Health checks observe health and available snapshots. Observation never restores,
snapshots, rewrites source, or silently accepts a changed integrity baseline.
Existing explicit operator kill switches and destructive-action controls remain
separate; this bridge introduces no approval, factor, flag, or default-off path.

## Existing change and recovery paths

An already authorized source change uses the native accepted-change mechanism:
an existing approved code-change proposal binds the replacement bytes, or the
existing verified recovery challenge applies. Caller booleans do not prove
acceptance. Follow the current native contract; this skill grants no authority.

Deliberate snapshot recovery is a separate explicit operation. The native recovery
path validates every locked path and hash before writing source and preserves the
accepted baseline. Health and watchdog probes never invoke recovery implicitly.

## Proof

Invoke the real native probe and retain its findings and errors. Record candidate
tests separately from deployed evidence. Do not claim observation, invocation,
recovery, or closure from a skill name, a receipt-shaped string, or stale memory.
