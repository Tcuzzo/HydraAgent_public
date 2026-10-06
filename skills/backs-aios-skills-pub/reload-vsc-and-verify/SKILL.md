---
name: "reload-vsc-and-verify"
description: "Use when a new BACKS AIOS skill has been added to the plugin pack or the BACKS source and the operator wants the live VS Code window to pick it up without manual steps. Detects the local install topology (single-symlink, per-file symlinks, or missing install), bridges any plugin-pack skill that has no entry in ~/.claude/skills/, then issues the VS Code reload command and prints the slash commands to verify the load. Trigger words: reload VS Code, force reload, IDE reload, skills not loaded, plugin pack reload, claude code reload, refresh VS Code, code --reload-window."
license: "MIT"
---

# reload-vsc-and-verify

Operator-facing surface for refreshing the VS Code Claude Code
extension after the AIOS plugin pack gains new skills. Closes the
class of "added a skill but VS Code doesn't see it" by detecting the
local install topology and bridging per-file symlinks before
reloading.

## When this skill is the right organ

When a BACKS AIOS skill was just added (or just mirrored via
`backs-pack-sync`) and the operator wants it visible in the live
VS Code chat without manually creating symlinks or running
`Developer: Reload Window` from the palette.

## How to invoke

In any shell with the plugin pack repo checked out:

```bash
bash ~/backs-aios-skills/bin/reload-vsc-and-verify
```

The script runs three steps:

1. **Topology check** — labels ~/.claude/skills/ as A (single
   symlink to plugin pack), B (per-file symlinks, the common
   install mode), or C (missing — instructs the operator to run
   `install.sh --target claude`).
2. **Bridge new skills** — for topology B, walks the plugin pack
   and creates any missing `~/.claude/skills/<name>` symlink so VS
   Code's Claude Code extension discovers it on the next scan.
   Idempotent — re-running reports "no missing links" once clean.
3. **Reload VS Code** — issues `code --command
   workbench.action.reloadWindow`. If `code` is not on the terminal's
   PATH (the common case from VS Code's integrated terminal), the
   script prints the palette command instead.

Then prints the slash commands (`/co`, `/osha`, `/co-health`, …) to
run in chat to prove the load succeeded.

## What this skill is, and what it isn't

- **Is** — the addressable, IDE-discoverable surface for the reload
  script in BOTH the public plugin pack and the BACKS private
  runtime. Same body, same trigger words, same backing script.
- **Isn't** — the script itself. That lives at
  `bin/reload-vsc-and-verify` in the plugin pack repo.

When this session is inside the plugin pack, the runtime resolves
the canonical script from `bin/`. When this session is inside
BACKS, the same skill loads with the same backing script via the
BACKS mirror.
