---
name: "backs-pack-sync"
description: "Use when the BACKS AIOS plugin pack has drifted from the BACKS source skills/ — a new BACKS skill exists that the IDE cannot discover because the plugin pack has no entry for it. Closes the drift class at the primitive so a missing skill stops being a manual add-it-by-hand cycle. Wraps bin/backs-pack-sync: dry-run shows the drift, --bridge <name> mirrors one skill, --bridge-all-missing mirrors every BACKS-only skill (currently 131). Each bridged file is a thin AIOS-portable SKILL.md that points to the canonical BACKS implementation. Trigger words: plugin pack drift, BACKS skill missing, sync plugin pack, mirror skill to AIOS, bridge skill, deploy tree root vs plugin pack."
license: "MIT"
---

# backs-pack-sync

Sync organ for the BACKS AIOS plugin pack. Diff the BACKS source
`skills/` against the plugin pack `skills/` and propose bridges for
any skill the operator wants mirrored. Closes the drift class so a
missing skill stops being a manual add-it-by-hand cycle.

## When this skill is the right organ

When the BACKS source has a skill the IDE doesn't see — usually
because the plugin pack was last synced before that skill landed.
Default dry-run shows the drift; one `--bridge` call adds the missing
skill and re-runs the scrubber so the new file passes hygiene.

## How to invoke

In any shell with the plugin pack repo checked out (default
`BACKS_SOURCE` is `[candidate-tree-root]/JarvisAI`, override with env):

```bash
# Dry-run — show drift
bash bin/backs-pack-sync

# Mirror one skill
bash bin/backs-pack-sync --bridge <name>

# Mirror every BACKS-only skill
bash bin/backs-pack-sync --bridge-all-missing
```

Each bridged file is a thin AIOS-portable `SKILL.md` with the
BACKS skill's description (verbatim if it fits the 1-1024
trigger-word budget) and a body that points to the canonical BACKS
implementation. The bridge run also runs the scrubber so the new
file passes hygiene before any commit.

## What this skill is, and what it isn't

- **Is** — the addressable, IDE-discoverable surface for the sync
  organ in BOTH the public plugin pack and the BACKS private
  runtime. Same body, same trigger words, same backing script.
- **Isn't** — the sync script itself. That lives at
  `bin/backs-pack-sync` in the plugin pack repo.

When this session is inside the plugin pack, the runtime resolves
the canonical script from `bin/`. When this session is inside
BACKS, the same skill loads with the same backing script via the
BACKS mirror.
