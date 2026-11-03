---
name: task_planner
description: Build working-memory bundles, bounded delegate slices, and reusable Skill Node contracts. Planning engine that breaks large tasks into skill-dispatched slices. Use when a large task must be broken into bounded, skill-dispatched slices before work starts.
license: MIT
allowed-tools: fs_read list_directory grep skill_list skill_show skill_route spawn_subagent shell
metadata:
  version: "1.0"
---
# Task Planner

The planning skill for the Hydra agentic runtime. Manages working-memory context, delegates bounded slices to skill-routing, and verifies results.
