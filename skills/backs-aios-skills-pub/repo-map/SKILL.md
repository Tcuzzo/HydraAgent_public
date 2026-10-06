---
name: "repo-map"
description: "Use on the first session in a cold repo with no index, and whenever the map goes stale. Walks the tree once, writes one CODE_MAP.md at the repo root, and makes every later session read the map first — map first, walk raw only when the map has no answer. Trigger words: repo map, code map, map first, map-first, index the repo, cold repo, stale map, refresh the map."
license: "MIT"
---

# Repo Map
**Effort:** light — one walk on first run, near-free after. Removes: agents re-deriving the repo's shape every session — the largest latency and token tax on an unindexed repo.

An indexed codebase answers "where does X live" for free. Most repos have no
index, so every session pays the same tax: walk the tree, rediscover the layout,
forget it all when the session ends. This skill pays that tax once. Walk the
tree one time, write what you learned into one map file, and make every later
question read the map before it walks.

## When to run

- The first session on a cold repo — one with no map and no index.
- Whenever the map goes stale (see the staleness rule below).

## The steps

1. **Walk the tree once.** One pass over the real structure: directories, entry
   points, where things live. This should be the only full walk the repo ever
   needs.
2. **Write one `CODE_MAP.md` at the repo root.** It carries:
   - the entry points — where execution starts;
   - the sections and seams, each with a one-line purpose;
   - where the tests live;
   - the build, run, and test commands;
   - the hot paths — seed from history (`git log --name-only` frequency), or
     leave empty and let later sessions fill it in.
3. **Keep it lean.** A map, not documentation. One line per fact. If an entry
   grows into a paragraph, it is drifting into a doc — cut it back to a pointer.
4. **Record the tree's shape.** Store one cheap fingerprint in the map,
   `git ls-files | sha256sum` (catches adds, moves, and renames), so a later
   session can tell whether the shape changed.

## The map-first law

Research, wayfinding, and plays read the map BEFORE walking the tree. A raw walk
is the fallback for when the map has no answer — and whatever the walk learns
gets written INTO the map before the session moves on. The map absorbs every
walk. Re-derivation is paid once, never per session.

## The staleness rule

Refresh the map only when the tree's shape changed — files added, moved, or
renamed since the map's recorded state. Compare the stored fingerprint
(`git ls-files | sha256sum`) against the live tree. Never refresh on a timer.
Never refresh every session. A map rebuilt on schedule is just the per-session
tax wearing a new name.

## Hard rules

- **Facts and locations, never opinions.** "Auth lives in `src/auth/`" belongs
  in the map; "the auth code is messy" does not.
- **A dead pointer dies the moment it is found.** A path that no longer resolves
  gets fixed or cut on sight. A map that lies is worse than no map.
- **The map never carries secrets.** No keys, no tokens, no credentials, no
  private hostnames. It is a tracked file; treat it like one.

## Works well with

- [live-research](../live-research/SKILL.md) — the researcher reads the map first, then the source.
- [wayfinder](../wayfinder/SKILL.md) — charting starts from the map, not from a cold walk.
- [session-handoff](../session-handoff/SKILL.md) — the map is the piece of a handoff every session shares.
