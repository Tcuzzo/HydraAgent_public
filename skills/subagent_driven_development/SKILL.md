---
name: subagent-driven-development
description: Split independent implementation slices across subagents and review their outputs. Fan-out parallel work, collect results, reconcile. Use when a task splits into independent slices that can run in parallel.
license: MIT
allowed-tools: fs_read list_directory grep spawn_subagent
metadata:
  version: "1.0"
---
# Subagent-Driven Development

Decompose a task into independent slices. Spawn one subagent per slice. Reconcile results.
