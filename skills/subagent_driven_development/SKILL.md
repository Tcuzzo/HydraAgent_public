---
name: subagent-driven-development
description: Split independent implementation slices across subagents and review their outputs. Fan-out parallel work, collect results, reconcile.
license: MIT
version: "1.0"
allowed-tools:
  - fs_read
  - list_directory
  - grep
  - spawn_subagent
---
# Subagent-Driven Development

Decompose a task into independent slices. Spawn one subagent per slice. Reconcile results.
