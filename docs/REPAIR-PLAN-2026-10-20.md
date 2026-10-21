# Hydra local intelligence and bot-team repair

Created for Cuzzo (Tcuzzo), using BACKS AIOS. Tool used: OpenAI coding assistant.

## Intent

Repair the public Hydra agent in an isolated checkout. Ship Needle and the real
Kev decision model through a reproducible local installation, connect durable
memory and knowledge learning to actual work, and provide the Grok, Seam and
Hydra bots with their essence, libraries and execution contracts. Report initial
failures, repair the shared causes, and verify the public commands and built
package. The coding/reasoning provider remains interchangeable.

## Source baseline

- Hydra: `b1b8535c61e1c7212060a4c39b0e55db25e7c8a2`.
- BACKS skills: `18eb3a0f3ad564f10ac5547fa860268e2672c367`.
- Public repository: https://github.com/Tcuzzo/HydraAgent_public.
- BACKS repository: https://github.com/Tcuzzo/backs-aios-skills.
- Kev is https://github.com/jaredpalmer/kev, distinct from Jev and Laya.
  The unrelated PyPI package named `kev` must never be installed by this workflow.

The initial environment is an isolated Git branch, separate per-author
worktrees, and a disposable Python virtual environment. No production checkout,
operator credentials, model selection or existing memory is replaced.

## Verified weaknesses and repair decisions

1. **Distribution and selectors.** Core install omits the optional local model
   dependencies. Kev has no adapter. Needle and Laya need stronger result
   validation. Add an explicit complete local bundle, pin the official Kev
   source, unify model-path configuration, enforce typed response contracts,
   reject unsupported/negated Needle calls, and expose diagnostics and a real
   initialization command. Preserve the small core installation for existing
   environments; recommend the complete bundle for this product configuration.
2. **Memory continuity.** `remember_lesson` writes Markdown that the persistent
   recall index does not automatically ingest. Default ask, chat and team paths
   differ. Synchronize the existing source corpus incrementally, restrict
   retrieval to current workspace sources, keep a relevant lexical path when
   embeddings are unavailable, and enforce the actual context budget.
3. **Grounded learning.** Reuse existing SQLite/FTS/vector storage and the memory
   kernel. Record real task outcomes with verification evidence; retain failures
   as failures. Build related-evidence links and reusable lessons from supported
   outcomes. Keep retrieval/consolidation separate from optional neural training.
   Export source-linked supervised examples and evaluate untouched holdouts
   before promoting any trained artifact. Do not call an unmeasured change
   "smarter" merely because training or a test suite completed.
4. **Bot identity and libraries.** Preserve the seven current profiles. Add
   Grok Bot, Seam Bot and Hydra Bot as provider-independent public profiles with
   bounded, packaged BACKS libraries, source identities and content hashes. Give
   the normal engineering team a coherent discover/repair/verify/review flow.
   Inject the same relevant memory into workers and record library provenance.
5. **Completion and review.** A required independent review must fail loudly
   when unavailable. A writer's natural-language completion is insufficient
   evidence of task execution. Require real tool activity and validate declared
   artifact/verification requirements. Preserve bounded iterations, subprocess
   deadlines, approval enforcement and ordered writers.

## Execution order

1. Capture the original public CLI and initial regression results.
2. Commit reproducing contract tests before implementation in each worktree.
3. Implement model, memory and team repairs in separate worktrees; integrate
   their shared CLI/configuration contracts sequentially.
4. Exercise actual SQLite/filesystem behavior, hostile model outputs, stale
   memory, missing dependencies, failed review, and interrupted/failed workers.
5. Run official Needle and Kev inference on CPU where this environment permits;
   retain the distinction between adapter tests and actual model inference.
6. Build and install the wheel into a fresh environment, run public commands,
   and execute the affected regression suite. Have non-authoring reviewers
   inspect the changes and resolve every reported defect on the changed paths.
7. Publish the tested branch and reviewable change through GitHub. Record exact
   commits, test results, any environment-dependent limitation and rollback.

## Acceptance evidence

- A complete install exposes both local selectors without fetching the wrong
  package or depending on a hosted paid service.
- A saved lesson is retrieved in a fresh process, relevant to the query and
  workspace; edited/deleted source does not return as current truth.
- `ask`, chat and team workers use the same bounded recall machinery.
- Verified outcomes can influence later retrieval with source/evidence links;
  failed/unsupported claims cannot become verified positive training examples.
- Bot profiles, essence and libraries survive wheel installation and actually
  reach the model request.
- A fake writer completion or missing required review causes nonzero failure.
- Tests and CLI receipts state exactly what was exercised. Model quality,
  deployment on the operator's machine and neural-training improvement require
  their own evidence and are never inferred from the unit-test count.

## BACKS skills applied

`intent-compiler`, `invariant-floor`, `human-calibration`,
`understanding-gates`, `root-cause-first`, `seam-engineering`, `repair-loop`,
`red-first`, `sniper-testing`, and the applicable model/memory/team playbooks.

These rules guide implementation and verification. The user's current request
authorizes the repairs and shipping work; it does not require another approval
round for ordinary reversible engineering steps.
