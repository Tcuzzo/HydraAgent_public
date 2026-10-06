---
skill_id: context-engineer
name: context-engineer
description: Assemble a precise execution context from repository state, traces, constraints, and user intent when work must be handed off or scoped before implementation.
license: Apache-2.0
preferred_mode: direct
tags:
- backs-native
- context-engineer
allowed-tools:
- sys.read_file
- sys.bash
- sys.write_file
metadata:
  backs_tier: native
  upstream_package: backs-aios
  upstream_version: 0.8.5+codex.20260908213642
  upstream_sources:
    skills/context-engineer/SKILL.md: 769329c1f32f74c6e7d018f0aa17d93d7cacf859b4fdc5ec8e0561a2008687dc
  adaptation: Selective native reconstruction; current CLAUDE.md and operator scope override portable runtime restrictions; reviewed relative links bind native files.
---

## Native execution binding

Load the current [canonical law](../../CLAUDE.md) through H_Boot
and apply [Yoke](../human-calibration/SKILL.md). Current operator instructions and that law
control scope. This method adds no hook, rearm, approval, tool restriction, host
allowlist, write gate, sandbox default, or LAN change. Owner configuration is a
decision, not an audit finding. Act on already authorized work; surface any
unrequested runtime or hardware change before taking it.

Use the registered `sys.read_file`, `sys.bash`, and `sys.write_file` tools for the
file and command steps below. Tool names describe usable bindings, not a reduced
runtime tool catalog. Resolve model roles and transports from
the configured fleet via
`python scripts/fleet.py ladder --json` and `python scripts/fleet.py build --role
<configured-role> --spec <complete-spec> --repo <owned-worktree> --timeout <budget>`.
Record actual model, provider, transport, host, fallback, exit status, and evidence;
never invent a route. Run only the operations authorized for the current task.
A loaded method is not proof that its tools ran or that the capability was delivered.

# Context Engineer

Build a compact packet that lets another engineer act without rediscovering the problem or inheriting unsupported conclusions.

## Assemble from evidence

- Extract the user's objective, explicit exclusions, priority order, and definition of done.
- Inspect the current revision, dirty state, relevant runtime path, tests, logs, and governing project instructions.
- Name exact symbols and paths only after verifying them.
- Separate confirmed facts, strong inferences, unresolved questions, and stale historical context.
- Resolve gaps from available evidence when practical. Mark only gaps that materially block or change execution.

Preserve concurrent work and scope boundaries in the packet. Do not turn a handoff into authorization for unrelated changes, deployment, or external actions.

## Context packet

Keep the result short and structured around:

- Objective and definition of done
- Current fixed point and workspace state
- Hard constraints and explicit non-goals
- Verified execution path and affected seams
- Existing work and evidence to preserve
- Evidence gaps or risks
- Recommended work split, only when parallel ownership is useful
- Verification and landing signals

Include exact commands or artifact paths only when they are current, safe, and necessary for continuation. End with the next concrete action, not a generic planning summary.

## Reconstruction provenance

Source: BACKS AIOS package 0.8.5+codex.20260908213642. Paths and SHA-256 values are recorded
in frontmatter. The full operative method above is adapted from the audited
package, not loaded from a user cache. Canonical-law adaptation removes obsolete
host restriction, hook/rearm, forced-route, or same-family landing defaults where
present. Existing native IDs and files are preserved.
