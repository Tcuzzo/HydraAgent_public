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

## Implementation record — 2026-10-21

The release candidate was published for review in
[PR #45](https://github.com/Tcuzzo/HydraAgent_public/pull/45). That PR records
the current publication state, independent review and Linux, macOS and Windows
CI results. This dated implementation record does not establish deployment on
an operator's machine. Results identify the tested revision; results from an
earlier snapshot do not approve later repairs.

| Area | Implemented behavior |
| --- | --- |
| Complete installation | `scripts/install_local.py` creates an owned environment outside the checkout, installs CPU PyTorch, Needle 3.2.1, sqlite-vec and official Kev pinned to `1f04cfa28e6a708485b28a9585010b9e436b776e`, then initializes the installed package. Python 3.12 or 3.13 is required for this bundle. An existing unowned destination is refused. |
| Local helper models | `hydra init` runs actual loading and inference probes; `hydra init --check` reports dependencies only. Needle proposals and Kev typed answers pass schema checks and remain subject to the action policy. Neither helper replaces the configured conversational model. |
| Memory and knowledge | Ask, both chat surfaces and team workers share bounded recall. Current source membership and workspace identity constrain retrieval. Actual tool observations produce episodes, result hashes and artifact links; model narratives do not become verified facts. SQLite/FTS retrieval remains available when embeddings are unavailable. |
| Bot team | Ten profiles include Hydra Bot, Grok Bot and Seam Bot alongside the original seven. Eight BACKS playbooks are packaged with their source revision and content hashes, canon and essence. The default sequence covers framing, investigation, repair, testing and review. Grok Bot is a configured role, not a bundled xAI model. |
| Training | Source-linked exports separate training and evaluation groups. The local runner seals inputs, bounds the worker and logs, evaluates the original and candidate, and preserves failure receipts. It never automatically promotes a checkpoint. |

See [Models](MODELS.md), [Memory and learning](MEMORY-LEARNING.md),
[Training](TRAINING.md), and the [team guide](PERSONAL-ORCHESTRATION.md) for
configuration and receipt formats.

### Workflow defects exposed by independent review

Each reproduced defect received a failing regression committed before its
repair. The corrected paths are covered by `hydra/test_review_*.py`; those
contracts remain unchanged during implementation.

| Observed weakness | Repair and observable contract |
| --- | --- |
| A successful provider or root command could leave the live app using old runtime state or old truth context. Native execution could use a different directory or policy from the displayed scope. | Rebind the live client, loop, tools and native scope together; clear cached work loops and replace workspace context. Reject scope changes while a turn is active. Exclude linked wiki paths that escape the workspace. |
| A second prompt could be paired with the first response; provider fallback could lose already executed tools. | Admit only one active turn, retain its accepted prompt, and carry partial transcripts, observations and remaining iteration budget into fallback. |
| File-specific search could report no match for valid generated training labels. Status commands could fail instead of reporting the selected runtime. | Match literal files correctly within the workspace boundary; report runtime, root and policy with verification explicitly unperformed. Preserve exact packaged library bytes on Windows checkouts. |
| Legacy work could exceed the requested iteration cap, and a slow HTTP response could outlive its nominal timeout. | Apply the requested cap across retries and enforce an outer deadline on the HTTP worker, including slow streaming. |
| An interrupted file writer could leave an overwritten file without its prior contents. | Checkpoint exact preimages before `fs_write` and `fs_edit` mutate files. A real timed-out team worker can leave a recoverable checkpoint even without a completed worker report. |
| A failed process could show a success icon; requested traces and session-save errors could disappear. | Preserve explicit tool failure status, write actual per-turn trace IDs/counts/steps/halt reasons on both chat surfaces, and show session persistence errors while retaining the visible answer. Discarded speculative drafts cannot replace the selected work receipt. |
| Closing the UI could allow a pending model proposal to write afterward. | Propagate cancellation into the loop and check it before further tool dispatch. Preserve completed observations. Actual native subprocess tests confirm that cancellation and timeout terminate owned descendants before delayed writes. Explicit Python launchers also support interpreter paths containing spaces. Windows behavior remains subject to the CI result. |
| A successful repair could remain blocked by its original failing check. | Keep the original failed observation and link it to a later successful invocation of the same tool with the same canonical arguments. Unresolved failures and unmet artifact acceptance conditions still block the team; an unrelated successful command cannot erase a failure. |

### Evidence obtained so far

| Exercise | Observed result and limit |
| --- | --- |
| Original public regression baseline | 807 passed, 1 failed, 6 skipped and 7 deselected. The failure was a DNS-resolution diagnostic reported as a private-address rejection; that diagnostic was repaired. |
| Earlier integrated snapshot | Revision `59fee0ce4e455ed785ffe223df853d59acdc0610` completed 898 tests with 10 skips. This predates the independent-review repairs and is not the final candidate's result. |
| Corrected runtime regression suite | Revision `67991150014e9294186a26ff33c9d1bcf86d0baa` completed 940 tests with 9 skips in 74.61 seconds on Linux/Python 3.12. The skips require the missing local configuration, usable nested Bubblewrap confinement, or a live embedding service. The GitHub matrix separately exercises supported operating systems and positive Linux confinement. |
| Complete installer and installed wheel | A fresh environment loaded the installed package outside the checkout. `hydra init` reported both helpers ready after real inference, the exact official Kev revision, and sqlite-vec 0.1.9. |
| Actual helper inference | Needle produced a grounded file-read proposal. Kev loaded the official CPU checkpoint and produced typed choice and noul answers. These prove loading and protocol operation, not general task quality. |
| Actual Needle training | Four synthetic training rows, four optimizer steps and two distinct evaluation queries. Training loss fell from 0.8461 to 0.6282. Original and candidate both matched 2/2 evaluation answers: accuracy delta 0, `improvement_observed: false`, `promoted: false`. The exported native archive loaded successfully. |
| Filesystem and workflow exercises | Real SQLite transactions, filesystem mutations, shell exits, process deadlines, installed workers and Textual handlers were exercised. Tests that script model responses prove the surrounding runtime behavior; they are not evidence of a live chat model's reasoning quality. |

The sandbox retains `wheel-initialization.json`, the regression logs, and
`tiny-needle-run2/report.json` with its sealed inputs and training log. These are
development evidence, not assets installed into the runtime package. PR #45
tracks the corrected candidate's CI results, fresh review verdicts and
installed-runtime verification alongside this dated evidence.

## Verification and recovery on the target machine

After installing the complete bundle, run these commands from outside its source
checkout, in the project to be inspected:

```bash
hydra init --check
hydra init
hydra team list
hydra team show seam_bot
hydra local-memory --workspace-root . --query "failing tests" --graph
```

Configure the conversational provider through `hydra setup`, then run a bounded
inspection before authorizing a writer:

```bash
hydra ask "Read this project's entry point and explain what it does" --root . --approval-policy deny --max-iterations 4 --trace-out inspection.json
```

For source-level verification in a separate development environment:

```bash
python -m pip install -e ".[test,mcp]"
python -m pytest -q
```

Inspect skipped tests and their requirements. Confined worker verification needs
a working Linux Bubblewrap environment; unavailable confinement must report a
failure without executing the requested verification command. Live model and
embedding checks require the corresponding service or installed weights.

Hydra's shared file writers save preimages for recovery:

```bash
hydra undo --root . --list
hydra undo --root . -n 1
```

Review the checkpoint stack before restoring. This recovers checkpointed Hydra
file edits, including exact original bytes. It does not undo arbitrary shell
commands, native-provider file changes, database writes or external actions.
Training leaves the selected checkpoint unchanged, so an unsuccessful candidate
requires no automatic model rollback.

## Remaining scope and explicit limits

No production host or live conversational-provider credentials were supplied for
this repair. The installed local helpers and tiny Needle training run were
exercised; deployment with the operator's chosen chat model remains a target
machine check. Kev's training adapter and plan were checked, but a full Kev
weight-training run was not performed. No live Ollama embedding-relevance
benchmark was completed; the exercised fallback and persistence contracts do
not certify retrieval quality.

BACKS permits an independent reviewer in a fresh session even when its model
family matches the authoring model, provided author context is excluded and
that isolation is recorded. Hydra's existing independent-review runtime still
uses stricter declared provider/model/family separation. This is a documented
policy mismatch, not an implementation of BACKS session-isolation enforcement.
Required independence remains fail-closed rather than silently bypassing that
runtime gate. The independent review of this repair records fresh review
sessions separately from those shipped runtime checks.
