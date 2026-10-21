# Train and evaluate local navigation helpers

Hydra exports source-grounded supervised examples for **Needle tool selection**,
**Kev typed decisions**, and the optional **Laya** model. Its local training
runner fits a candidate, evaluates the original and candidate on the same held-out
inputs, and preserves a receipt. Training never changes the active checkpoint.
These helpers cover navigation and routing around the main coding model. They do
not establish coding competence or broad intelligence.

## Prepare a source-grounded corpus

The public checkout alone is sufficient:

```bash
python -m hydra.training_corpus --public-repo /path/to/HydraAgent_public --out /path/to/local-training-data
```

Add `--private-repo /path/to/HydraAgent` only when you intend to include private
source. The output must be a new or empty directory outside the source trees.
This command makes no network calls. Tracked working-tree files are inventoried
and hashed; untracked files are excluded and modified tracked files are recorded.
For a partial snapshot, `SOURCE_MANIFEST.json` must pin a 40-character revision
and each selected Git blob hash. This is a source-derived starter corpus, not a
collection of human-reviewed coding solutions.

The generator excludes credential paths, environment files, runtime state, tests,
symlinks/junctions, binary and oversized files, and detected secret-like content.
Filtering cannot detect every secret. Private-derived datasets, adapters and
prediction logs remain private artifacts; review them before sharing.

| File | Purpose |
| --- | --- |
| `needle_train.jsonl`, `needle_eval.jsonl` | Exact tool name/argument labels and unsupported-action refusals |
| `kev_train.jsonl`, `kev_eval.jsonl` | Typed questions with a `label` inside each question, matching the official Kev loader |
| `laya_train.jsonl`, `laya_eval.jsonl` | Laya questions with the separate `expected` mapping |
| `provenance.jsonl` | Per-row source paths, revisions, hashes, label methods and split groups |
| `manifest.json` | Coverage, exclusions, split policy and dataset checksums |

Needle receives `fs_read` path tasks, `grep` tasks for verified Python declarations,
and requests that none of the supplied tools can fulfill. Tool schemas are
extracted from the public runtime without importing its code. Kev and Laya learn
source subsystem labels derived from file paths and module documentation.

The default evaluation fraction is 20%, configurable with `--eval-fraction`.
All versions of one relative path remain in a single split. Identical content
is deduplicated and aliases retain the same split group. Small corpora receive
at least one independent group in each split. This grouping reduces direct file
leakage; it is not a guarantee against semantically similar tasks.

Kev's official labeled-request shape is:

```json
{"state":"Hydra workspace file: hydra/loop.py","questions":{"area":{"type":"choice","instructions":"Choose the responsible subsystem.","criteria":{"runtime":"Agent loops and missions","memory":"Persistent memory and recall"},"label":"runtime"}}}
```

Labels are option keys for `choice`, booleans for `noul`, and integer level indices
for `score`. A Laya `expected` field is not a Kev label.

## Install the local training dependencies

Use the complete local installer described in [MODELS.md](MODELS.md), with Python
3.12 or 3.13 and the intended PyTorch build. In that isolated environment, add
Needle's training extra:

```bash
python -m pip install "cactus-needle[train]==3.2.1"
```

The actual CPU smoke used `torch==2.8.0+cpu`, `cactus-needle==3.2.1`,
`jax==0.12.0`, `flax==0.12.10`, `optax==0.2.8`, `transformers==5.20.2`,
`peft==0.21.2`, and the official Kev SDK at commit
`1f04cfa28e6a708485b28a9585010b9e436b776e`. These identify the exercised environment;
other platforms need their own device checks. Do not install the unrelated
PyPI `kev` package.

PyPI Needle 3.2.1 exports `build_main` from `needle.model.finetune`. Current
upstream source moved it into `needle.model.build` while retaining the same
package version. Hydra's runner supports both locations explicitly. A missing
transitive dependency is still an error.

Model and tokenizer downloads need network access on the first run. The training
data stays in the local process. The runner disables Needle/Hugging Face telemetry
and synthetic-data API calls. It uses the same `HYDRA_MODEL_CACHE` as initialization
and prediction, defaulting to `~/.hydraAgent/models`, and respects explicit
`HF_HOME`. Needle's training tokenizer may also populate its SDK-managed tokenizer
cache. No remote fine-tuning API is involved.

## Inspect a concrete plan, then train

`--plan-only` validates datasets and output boundaries without loading an ML SDK
or creating the output directory:

```bash
hydra train --backend needle --train /path/to/local-training-data/needle_train.jsonl --eval /path/to/local-training-data/needle_eval.jsonl --out /path/to/local-models/needle-run-01 --epochs 1 --timeout 1800 --plan-only
```

The module entry `python -m hydra.local_training` accepts the same arguments.
Remove `--plan-only` to fit and evaluate the candidate:

```bash
hydra train --backend needle --train /path/to/local-training-data/needle_train.jsonl --eval /path/to/local-training-data/needle_eval.jsonl --out /path/to/local-models/needle-run-01 --epochs 1 --timeout 1800
```

For Kev, choose its labeled files and a fresh output directory:

```bash
hydra train --backend kev --train /path/to/local-training-data/kev_train.jsonl --eval /path/to/local-training-data/kev_eval.jsonl --out /path/to/local-models/kev-run-01 --epochs 1 --timeout 3600 --device cpu
```

Kev defaults to `jaredpalmer/kev-0.8b@v1.0`; `--model` selects another compatible
LoRA checkpoint. It warm-starts the existing adapter and pointer head through the
official trainer with a learning rate of `2e-5`, batch size 1, accumulation 8 and
seed 42. Device selection is explicit. The bounded wrapper is wired to the audited
upstream APIs; actual Kev inference was exercised in the repair sandbox, while a
full Kev training run was not completed as part of that repair.

The Needle recipe uses official Needle 3 on CPU: one example per batch, learning
rate `1e-4`, rank 8, alpha 16, seed 42, and a 512-token training ceiling. It refuses
an example that would lose its target through the SDK's truncation. Its evaluation
split is never passed to the optimizer. Local training does not fit Needle's
confidence head, so the exported candidate reports `confidence: null`. The run
receipt records this warning and `needle_select` exposes `confidence_available`
alongside the raw value; missing confidence is never replaced with a success
score. Other
architectures, longer sequences, or accelerator training recipes can use the
upstream SDK directly.

## Boundaries and receipts

The runner requires distinct nonempty UTF-8 JSONL files, at most 10,000 rows and
32 MiB each, valid typed labels, a new output directory outside source checkouts
and the installed runtime, 1–20 epochs, and a finite timeout of at most one day.
It rejects duplicate source states or queries across training and evaluation.
When the corpus manifest is present, it verifies its checksums and disjoint source
file groups. Without that manifest, `split_validation` says `exact_inputs`; it
cannot claim file-group separation for arbitrary user data.

Inputs are copied into the run directory with their SHA-256 fingerprints, along
with the corpus manifest and provenance when supplied. The worker has an outer
process-group deadline and a 32 MiB log ceiling. Timeout, failure or cancellation
terminates the worker tree; partial artifacts and a failed receipt remain for
inspection. CPU affinity is reported and explicit `HYDRA_LOCAL_CPU_FRACTION`
settings are honored. These bounds do not promise a particular RAM or electrical
power budget.

A completed run contains:

| Artifact | Contents |
| --- | --- |
| `plan.json` | Input hashes, configuration, split checks and provenance fingerprints |
| `train.jsonl`, `evaluation.jsonl` | Sealed run inputs |
| `manifest.json`, `provenance.jsonl` | Archived source evidence when present |
| `training.log` | Local SDK diagnostics and optimization progress |
| `baseline.json` | Original checkpoint predictions and accuracy |
| `candidate-evaluation.json` | Candidate predictions and accuracy; Kev also reports Brier loss |
| `report.json` | Actual package versions, result status, accuracy delta and `promoted: false` |
| `adapter.safetensors`, `candidate.cact` | Needle's local adapter and native inference archive |
| `candidate/` | Kev's trained checkpoint directory |

The Needle repair smoke actually completed four optimizer steps on four synthetic
training rows and evaluated two distinct held-out queries. Both original and
candidate returned two exact matches out of two: accuracy delta **0**,
`improvement_observed: false`, `promoted: false`. The exported native archive
loaded successfully. This proves the local train/export/evaluate mechanism; six
simple examples cannot establish a quality improvement.

## Decide whether a candidate should be used

Inspect per-task errors and refusals, not only aggregate accuracy. Generated path
labels measure navigation; broader routing and coding tasks need separately
labeled tests. If evaluation results guide repeated training choices, that split
has become a validation set: reserve another untouched source-group test set for
the final comparison. Neither this runner nor its confidence values certify that
a model is safe to execute actions automatically.

After reviewing measured results, choose a checkpoint explicitly:

```bash
export HYDRA_NEEDLE_MODEL=/path/to/local-models/needle-run-01/candidate.cact
export HYDRA_KEV_MODEL=/path/to/local-models/kev-run-01/candidate
```

This changes those helper models only. It does not change the main coding model
or bypass the action policy. Keep private-trained artifacts outside public source
control. Laya remains available through its own upstream training interface; its
corpus format and checkpoints are separate from Kev's.

## Primary implementation sources

- [Needle source](https://github.com/cactus-compute/needle)
- [Needle fine-tuning documentation](https://cactuscompute.com/blog/finetuning-needle)
- [Pinned Kev labeled-data loader](https://github.com/jaredpalmer/kev/blob/1f04cfa28e6a708485b28a9585010b9e436b776e/kev/data.py)
- [Pinned Kev trainer](https://github.com/jaredpalmer/kev/blob/1f04cfa28e6a708485b28a9585010b9e436b776e/kev/train.py)
- [Laya training interface](https://github.com/NandhaKishorM/laya/blob/main/docs/finetune.md)
