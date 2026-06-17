---
name: task_planner
description: Build working-memory bundles, bounded delegate slices, and reusable Skill Node contracts. Planning engine that breaks large tasks into skill-dispatched slices.
license: MIT
version: "1.0"
allowed-tools:
  - fs_read
  - list_directory
  - grep
  - skill_list
  - skill_show
  - skill_route
  - spawn_subagent
  - shell
---
# Task Planner

The planning skill for the Hydra agentic runtime. Manages working-memory context, delegates bounded slices to skill-routing, and verifies results.
