---
name: sniper-testing
description: "Use during a build or repair loop, or before trusting a green result, when verification must cover the actual candidate without redundant whole-suite runs."
license: MIT
---

# Sniper Testing

Verify the changed behavior through its real consumer. Use the complete task diff
to choose checks, then rerun only checks affected by the next change. Inherit the
same mission allowance from [bounded-loops](../bounded-loops/SKILL.md).

## 1. Pin the real scope

Preserve the verified pre-task `base_sha` and current committed `candidate_sha` in
the [Build Flow](../backs-build-flow/SKILL.md) packet. A clean working tree after
committing RED and the fix still has a task diff. `git diff HEAD` alone loses it.

Use the bundled read-only scope helper from this skill's resolved directory:

```bash
python scripts/changed_files.py --repo <verified-repo> --base <base-sha> --candidate <candidate-sha>
```

During iteration add `--include-worktree` only when the candidate is HEAD. It
reports committed, staged, unstaged, and untracked paths separately, preserving
unusual filenames. The aggregate scope includes deletions and both sides of a
rename. Mark unrelated pending work as preserved; do not claim every returned path
as yours. A missing base or invalid revision is an error to resolve, never an empty
scope that justifies skipping checks. The helper does not fetch or change history.

## 2. Choose and execute real checks

Map changed files and affected callers/consumers to behavior tests, then read the
repository's actual scripts, CI, and configuration for applicable commands.

| Check | Scope and evidence |
| --- | --- |
| Lint and format-check | Changed supported files using existing config; use check mode before deciding to reformat. |
| Typecheck or compiler | Affected package/project boundary; type dependencies may require a wider target. |
| Behavior tests | Real changed path and a regression that fails when the capability breaks. |
| Contract/integration tests | Consumers, aliases, packaging, settings preservation, or runtime boundaries touched by this change. |
| Required landing gates | Repository-mandated suites once the candidate settles. |

State each command, why it covers the risk, revision/pending state, executed result,
and remaining failures. An absent linter/typechecker is **unavailable** or **not
applicable**, with a reason; do not invent a pass. Do not install a new tool just
because an example names it. A green lint run does not substitute for behavior tests.

During iteration choose the narrowest meaningful target. A previously passing
check need not rerun when no exercised code, inputs, or relevant environment changed.
A concrete unresolved coupling or a required project gate justifies broader testing;
record why. At landing, run the required aggregate checks once for the final candidate.
Changes after that result invalidate only affected evidence and required commit gates.

## 3. Make tests detect real breakage

Assert an observable effect: an artifact exists and is valid, data reads back from
a real local database, the host receives actual context bytes, or a UI element
renders. Exercise local files, databases, and sockets rather than mocking them.
A metered external transport leaf may be replaced by a deterministic fixture while
request construction, routing, parsing, and the local consumer remain real. Never
claim that fixture proves the external service ran.

Keep the sealed RED file from [red-first](../red-first/SKILL.md) byte-identical.
For a new gap, add a separate supplemental regression file using that skill's
evidence and sealing rules. Never weaken, rename, replace, or edit the original
sealed file.

## Exit

Return candidate scope, executed lint/type/test results, real side-effect evidence,
and unresolved gates to the same owner. [clean-code-gauntlet](../clean-code-gauntlet/SKILL.md)
uses this evidence as its first stage, not as a reason to repeat unchanged checks.
