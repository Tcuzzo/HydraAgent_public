---
name: "scrub-plugin-pack-literals"
description: "Use when a tracked skill file in the BACKS AIOS plugin pack may carry a pre-publish literal — real operator paths (/opt, /mnt, /home), private IPs, workstation hostnames, or operator first names. Closes the public-safety leak class at the primitive so it never enters the GitHub mirror. Wraps bin/scrub-plugin-pack-literals with three modes: --dry-run lists would-scrub files, --apply rewrites in place, --hook exits 1 if any leak remains (used by .githooks/pre-commit and .githooks/pre-push). Trigger words: plugin pack hygiene, public safety scrub, pre-publish leak, /opt literal, /mnt literal, private IP scrub, Co-Authored-By trailer, inv_21 hygiene, BACKS AIOS public hygiene."
license: "MIT"
---

# scrub-plugin-pack-literals

Public-safety scrubber for the BACKS AIOS plugin pack. Wraps the
`bin/scrub-plugin-pack-literals` bash script that lives alongside
this skill in the plugin pack repo.

## When this skill is the right organ

When a tracked file in the plugin pack may carry a pre-publish literal:

- Real operator paths (`/opt/<word>/`, `/mnt/<word>/`, `/home/<word>/`)
- Private IP literals (RFC1918 ranges)
- Operator workstation or host references (the scrubber replaces them with
  `operator-host` / `operator-workstation`)
- Operator first names in prose (the scrubber folds them to `operator`)
- Model-credit trailers on commit messages (the pre-push hook catches
  `Co-Authored-By: <model>` before it lands)

These classes are caught by `tests/test_public_hygiene.py` per BACKS
invariant 26. The scrubber exists so a leak fails loud BEFORE it
enters history — run the hook to see the exact classes it scans for.

## How to invoke

In any shell with the plugin pack repo checked out:

```bash
# Dry-run (default) — list files that would change
bash bin/scrub-plugin-pack-literals

# Rewrite in place — for one-shot cleanups before commit
bash bin/scrub-plugin-pack-literals --apply

# Hook mode — exit 1 if any leak remains; never rewrites
bash bin/scrub-plugin-pack-literals --hook
```

Both `.githooks/pre-commit` and `.githooks/pre-push` wrap the
`--hook` mode so the leak class is closed at the gate.

## What this skill is, and what it isn't

- **Is** — the addressable, IDE-discoverable surface for the scrubber
  in BOTH the public plugin pack and the BACKS private runtime.
  Same body, same trigger words, same backing script.
- **Isn't** — the scrubber script itself. That lives at
  `bin/scrub-plugin-pack-literals` in the plugin pack repo.

When this session is inside the plugin pack, the runtime resolves
the canonical script from `bin/`. When this session is inside BACKS,
the same skill loads with the same backing script via the BACKS
mirror of the plugin pack.
