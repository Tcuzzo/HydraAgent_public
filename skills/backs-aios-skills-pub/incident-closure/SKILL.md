---
name: "incident-closure"
description: "Use when the human reports breakage or says \"fix it\" — especially when the normal control plane (API, CLI, service) is dead and you must reach underneath it. The answer is a full understanding-first close (root cause with evidence, failing test first, green, live proof on the human's own path, commit), never a menu of options back at them. Trigger words: fix it, fix shit, full close, broken, wiped, down, it stopped working, recover, restore."
license: "MIT"
---

# Full Close
**Effort:** free — ordering discipline on a fix you already owe: disk-truth probes and the failing test come first, not extra. Removes: option menus and per-step confirmations lobbed at the human mid-outage.

When the human reports breakage or says "fix it", there is exactly one right answer:
a full, understanding-first close. Root cause with evidence, a failing test first,
green, live proof on the human's own path, then commit. Never a menu of options back
at them, and never a confirmation prompt per step — they already said fix it.

Where sibling skills demand an explicit yes for destructive acts, this rule wins the
reversible half only: the human's "fix it" IS the standing yes for reversible recovery
writes that leave a backup trail; anything irreversible (data destruction, spend,
external sends) still crosses the [decision-bar](../decision-bar/SKILL.md), and the bar wins.

Ask the human for something only when it is provably lost everywhere else and only
they can supply it. Every other input, you go find.

## The method

1. **Probe the normal surface — then stop trusting it.** Call the API or CLI once. If
   it answers normally, this is not an incident-closure situation; hand off. If it returns
   401/403, connection refused, empty results where data should be, or stale data,
   stop treating that surface as authoritative.
2. **Establish ground truth from disk, not from the API.** Never trust a broken
   service to describe its own state. Read the data files, directory listings, and
   modification times yourself, and compare to what the API claims. Divergence is the
   diagnostic signal.
3. **Scan the blast radius.** Search every top-level data directory for files touched
   inside the failure window (e.g. `find /data/volumes -newermt "<start>" ! -newermt
   "<end>"`). Aim for a one-screen answer to "what got touched, what didn't". Narrow
   radius (one volume, one table) is recoverable here. Broad radius (many volumes, the
   whole data dir) is disaster recovery — escalate, don't improvise.
4. **Inventory survivors vs losses.** Classify every affected asset:
   - intact on disk — recover as-is
   - rebuildable from the repo — configs and backups checked into git
   - rebuildable from env or credential files — tokens, passwords
   - permanently lost — encrypted with a missing key, runtime-only state
   Only the last bucket warrants asking the human. Everything else you rebuild.
5. **Root cause with evidence, then a red test.** Name why it broke, with proof from
   disk — not a guess. Where the defect is code, write the failing test that captures
   it before the fix, and make it green. See [red-first](../red-first/SKILL.md) and
   [root-cause-first](../root-cause-first/SKILL.md).
6. **Cascade down through layers — never up to the human.** When the preferred path is
   broken, drop one layer and try again:
   API / SDK → CLI inside the container → direct DB writes → filesystem surgery.
   Do not prompt the human while cascades remain untried. Every rung down is cheaper
   than asking.
7. **Assume dependencies are broken too.** Recovery code uses only your language's
   standard library for HTTP and JSON — third-party clients may be part of what died.
8. **Write idempotently, with backup trails.** Every disk write leaves a timestamped
   `.bak` copy beside the target. Read, sanity-check, copy, write, re-check — never
   blind-overwrite. If you temp-swap a credential to mint a new key, back up the
   original first and restore it before returning: the human's own login survives
   untouched.
9. **Verify with live calls on the human's own path.** Re-run the step-1 probe and
   confirm the numbers match the pre-incident inventory or the repo backups. Green DB
   state is not proof; the surface the human uses working again is proof.
10. **Commit and report.** Commit the fix's own files only. Report: what was probed,
    the blast radius, actions taken in order, counts restored, what is permanently
    lost (empty if nothing), and any step that failed non-fatally.

## Red flags — stop and re-probe

- "Let me ask the human why it broke" — no; find out from disk first.
- "The API says there's nothing here" — a broken API's view of itself is not truth.
- "I'll just reinstall clean" — you are discarding recoverable state.
- "The key is gone so the credentials are useless" — plaintext values often still
  live in env or credential files; recreate the credential.
- "Confirm before each step?" — the human said fix it; run the cascade, report at end.

## Hard rules — any one of these fails the skill

- Options presented back to the human when a clear solve exists.
- A destructive write with no `.bak` trail.
- The human asked for anything before the cascade and the inventory ran dry.
- A retired subsystem "helpfully" restored — a decommissioned service staying down is
  the desired state, and re-enabling it is the human's deliberate call.
- Recovery claimed done off internal state instead of a live probe on their path.
- Fix left uncommitted (unless the human explicitly said no commit).

## Works well with

- [repair-loop](../repair-loop/SKILL.md) — the code-fix loop this close runs when the defect is in code.
- [root-cause-first](../root-cause-first/SKILL.md) · [red-first](../red-first/SKILL.md)
- [decision-bar](../decision-bar/SKILL.md) — what may reach the human, and how.
