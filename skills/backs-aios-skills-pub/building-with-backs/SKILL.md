---
name: building-with-backs
description: Use when building or repairing a BACKS project, resolving wrong-folder or checkout ambiguity, or carrying an authorized change from the IDE to its actual runtime.
license: MIT
---

# Building with BACKS

This skill closes a recurring failure: a good patch in the wrong checkout or a
green test that never reaches the operator's app. The destination is the human's
requested product and path. Discover the target before writing.

Join the existing [BACKS Build Flow](../backs-build-flow/SKILL.md) task packet;
direct invocation establishes it once. Keep one integration owner and reuse the
operator essence and context already established. Honor `task_mode` and carry the
mission's deadline and remaining allowances from
[bounded-loops](../bounded-loops/SKILL.md) across workers, models, and handoffs.
Resolving a new path does not restart the budget or authorize a deployment.

1. **Resolve the destination.** Use the exact path named by the human. If none was
   named, inspect the active workspace, recent project references, and repository
   map. Do not substitute a nearby clone merely because it has a matching name.
   Run `python scripts/resolve_target.py --repo <path> --target <file-or-directory>`
   from this skill's directory;
   add `--expected-root <path>` when the human named the checkout. The command
   returns nonzero for a missing target root, a path outside that root, or a
   checkout mismatch or missing Git revision. A new target file may not exist
   yet; its parent must. For a specifically named non-Git project, add
   `--allow-non-git` and record that no revision is available.
2. **Pin the starting truth.** Record the resolved root, branch or detached HEAD,
   `base_sha`, current `candidate_sha`, worktree status, requested target,
   app/service entry point, and checks. Keep the original base pinned as commits
   land; advance the candidate identity explicitly and recheck any pending paths.
   Preserve unrelated edits. If another checkout is newer, compare revisions and
   actual working trees before choosing; "newest" is a measured claim.
3. **Map the route.** Trace user action to entry point, implementation, data,
   outputs, and deployment seam. Name the existing owner of the behavior and
   any dependent consumers. Use `repo-map`, `architecture-engineer`, and
   `root-cause-first` when those seams are uncertain.
4. **Build in the resolved place.** Give parallel readers distinct questions and
   writers non-overlapping files or isolated worktrees. Keep one integration
   owner. Write the smallest change that closes the failure class; apply
   `repair-loop` for a defect inside the current workflow. Follow the full
   `elite-build` play when selected or explicitly requested, reusing valid stages
   rather than booting again. A review or diagnosis returns findings without edits.
5. **Prove the path.** Follow the current sealed RED → minimal fix → sniper
   lint/type/test checks → clean-code-gauntlet loop. Scope checks from the pinned
   aggregate candidate diff; use the repository's real commands and state missing
   or N/A checks honestly. Preserve author-disjoint review and the required full
   affected-suite landing pass. Inspect the actual app/CLI surface and runtime
   logs within the requested scope. Record the exact source revision/hash deployed
   and observed behavior; name anything that could not be exercised.
6. **Promote through the project's own route.** Read its deployment instructions
   and approval rules from that repository. Never transplant a deployment
   command from another project. Re-run the target check before installation
   and verify the running process uses the installed code afterward.

If the destination remains ambiguous, keep read-only investigation moving and
ask for the missing path only after checking available evidence. Never claim a
cross-host installation merely because files were copied; verify discovery in
each host or mark it unverified.

If an allowance expires, checkpoint **STILL-BUILDING** with the verified target,
candidate, unresolved evidence, and exact next action. Preserve the original
mission limits; finish useful authorized work that does not consume the exhausted
allowance. A local check never substitutes for requested delivery evidence.
