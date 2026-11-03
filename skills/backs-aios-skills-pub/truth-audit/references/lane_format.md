# Lane file format

One lane writes one file, `lanes/<LANE>_<NAME>.md`, and nobody else writes it. The
coordinator reads it, spot-checks it, and promotes it into the ledger with
`scripts/ledger.py import-lane`. The format is fixed so that a tool can promote it
and a stranger can re-run it.

## File header (top of the file, before any finding)

```
# Lane <LANE> · <concern> · <date>

Auditor: lane <LANE>. Read-only on the product.
Isolation: <what was copied, which sandbox home/port/keyring, which env vars were unset>.
Source under test: <path> at <commit or "working tree, N dirty paths">; imported from <interpreter answer>.
Lenses applied: <skills or rules used to judge, e.g. sniper-testing for test findings>.
```

The header says exactly what was examined. A finding about "the code" is worthless
if nobody knows which copy of the code.

## One finding block

```
### <LANE>-NN · P0|P1|P2|P3 · <title: the defect in one plain sentence>
- status: OPEN · confidence: high|med|low
- surface: <where a human meets it: window, page, route, job, report, command>
- evidence: <commands + observed output, file:line; enough for a stranger to re-run>
- root cause: <why it happens, not what happens>
- fix seam: <files and symbols where the fix belongs>
- proof of done: <a native observed check that passes with no builder present>
```

- **ID.** Lane prefix in capitals, a dash, a two-digit number: `SVC-03`, `DATA-11`.
  Findings a builder discovers later use `N-<ball>-NN` (for example `N-1A-01`).
  IDs are never reused, even when a finding is withdrawn; withdraw by adjudication.
- **Title.** States the defect, not the area. "Worker retries forever on a rejected
  credential", not "Worker issues".
- **Evidence.** Commands and what they printed, with timestamps where time matters.
  Sub-bullets are fine. Quote the exact log line, query row, or `file:line`. Counts
  carry their denominator ("602 of 602 loop errors today").
- **Root cause.** It may have qualifiers, and you may give more than one:
  `- root cause (theater): ...` and `- root cause (product, not confirmed): ...`.
  Mark what you did not confirm.
- **Fix seam.** The shared primitive where the fix closes the class, not the line
  where the symptom shows.
- **Proof of done.** Observed on the real surface by something that is not the
  builder: a scheduled watchdog row, a real click on the real page, a real test-account
  or sandbox response, a reboot. "Tests pass" is never proof of done on its own.

## Severity

| sev | meaning |
|---|---|
| P0 | wrong product, data loss, an authority path wrong (spend, delete, publish), or a safety fence down |
| P1 | a capability is broken on the human's own surface |
| P2 | misleading or degraded: works, but says something false or loses detail |
| P3 | polish |

## Confidence

| confidence | meaning |
|---|---|
| high | re-run and observed by this lane |
| med | read in source or config, not executed |
| low | inferred from indirect signals; says what would confirm it |

## Positive findings

When a lane checks something and it turns out true and healthy, it still writes a
block. That stops the next auditor from re-checking it and records what was ruled out.

```
### DATA-07 · P3 · Synced records are real (match the source system row for row)
- status: ADJUDICATED-NOT-A-BUG · confidence: high
- evidence: 20 random rows from a copy of `records.db` equal the source API's `GET /records/<id>` responses field for field
```

`import-lane` records it and immediately marks it `ADJUDICATED-NOT-A-BUG` with that
evidence. Fix seam and proof may be omitted only on adjudicated blocks.

## Tolerated variants

Lanes are written by different agents, so the importer accepts these and the
coordinator must not refuse a block for them:

- `- evidence (read-only, service log):` (a qualifier in parentheses, kept in the value)
- `- surface and evidence:` (one merged field, counted as both)
- `* **Evidence:**`, `- **fix-seam**:`, `- Proof:` (bullet, bold, case, dash variants)
- `### SVC-02 | P2 | title`, `### SVC-02 — P2 — title`, `### SVC-02: P2: title`
  (pipe, dash, or colon separators instead of `·`)

A `### <ID> ...` heading that still cannot be parsed is refused with its line number
and the import exits 1; it is never skipped silently. Re-importing a lane whose
evidence, fix seam, or proof changed is refused too: record the change with
`ledger.py amend <ID> --evidence ...`, then re-import.

A block that is really missing evidence, fix seam, or proof is refused and named on
stderr. Fix the block in the lane file and re-import; the ledger skips IDs it already has.

## Required closing sections

```
## Side effects
<every effect this lane had on the world, or "none": a window that took focus, a file
written outside scratch, an outbound request that carried personal data, a paid model
call, a process started or killed, a cache warmed on a shared host>

## Not checked
<what was in scope but not examined, and why (budget, access, risk)>
```

Some lanes also produce a spec section the plan cites by name (for example
"Native watchdog spec" or "Sniper test list"). Put those after the findings under a
`## ` heading so the importer stops each finding block before them.
