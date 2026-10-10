# Train local navigation helpers on Hydra source

Hydra can prepare supervised examples for **Needle tool selection** and **Laya subsystem routing** from a public checkout and a private checkout or pinned source snapshot. These are small decision helpers around your chosen coding model. Training them does not replace the model that reasons about or edits code.

The included generator creates grounded starter tasks:

- Needle selects `fs_read` for an explicit repository path, `grep` for a verified Python declaration, or no tool when the requested action is unavailable.
- Laya classifies a file into models, memory, tools, safety, setup, runtime, or documentation using its path and module documentation.

These examples measure navigation and tool routing. They do not establish coding ability, understanding of the entire private repository, or production readiness. Labels are generated from source evidence; they are not human-reviewed solutions to coding tasks.

## Prepare data

From the Hydra checkout, run:

```bash
python -m hydra.training_corpus --public-repo /path/to/HydraAgent_public --private-repo /path/to/HydraAgent --out /path/to/local-training-data
```

The output must be a new or empty directory outside both source trees. The command is local and makes no network calls.

For a Git checkout, the generator inventories tracked files and fingerprints their current working contents. Untracked files are excluded. Modified tracked files are marked in the provenance. For a partial snapshot, provide a `SOURCE_MANIFEST.json` at its root containing a pinned 40-character `revision`, a `repository` name, a `scope` description, and `files` entries with `path`, `type: "blob"`, Git `mode`, and Git blob `sha`. Every selected snapshot file must match its pinned hash.

The generator excludes credential paths, `.env` files, runtime state, tests, binary and oversized files, symlinks/junctions, and detected secret-like content. Secret filtering is conservative and cannot identify every possible secret. Derived private data and adapters remain private; review them before sharing anything.

## Files produced

| File | Purpose |
| --- | --- |
| `needle_train.jsonl`, `needle_eval.jsonl` | Tool selection examples with actual runtime tool schemas |
| `laya_train.jsonl`, `laya_eval.jsonl` | Typed `choice` questions with expected subsystem labels |
| `provenance.jsonl` | Per-row file paths, revisions, content hashes, label method and split group |
| `manifest.json` | Coverage, exclusions, counts, split policy and dataset checksums |

The default evaluation fraction is 20%; use `--eval-fraction 0.25` to change it. All versions of the same relative file path stay in one split. Identical file contents across repositories are deduplicated, and aliases stay in that same group. Tiny corpora receive at least one group in each split. Never tune on the evaluation set and then report it as an untouched test set.

The generator copies literal tool schemas from `hydra/cli/tool_binding.py` without importing repository code. Its JSONL formats match [Needle's fine-tuning format](https://cactuscompute.com/blog/finetuning-needle) and [Laya's supervised training loader](https://github.com/NandhaKishorM/laya/blob/main/laya/train_cli.py):

```json
{"query":"Read 'hydra/loop.py'.","tools":[{"name":"fs_read","description":"Read a file","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}],"answers":[{"name":"fs_read","arguments":{"path":"hydra/loop.py"}}]}
```

```json
{"state":"Hydra workspace file: hydra/loop.py","questions":{"area":{"type":"choice","instructions":"Choose the responsible subsystem.","criteria":{"runtime":"Agent loops and missions","memory":"Persistent memory and recall"}}},"expected":{"area":"runtime"}}
```

## Install and train locally

Use a separate virtual environment. From the Hydra checkout, install the training extra:

```bash
python -m pip install -e ".[training]"
```

The recipes below were exercised with `cactus-needle==3.2.0` and `laya==0.4.2`. Pin those versions to reproduce their APIs. Model weights download on first use; the examples train locally and do not upload data. Use paths outside both source repositories for all data, caches and outputs. On native Windows, the tested Needle JAX backend is CPU; Laya can use CUDA with a compatible PyTorch installation. Start with a small, representative training subset before scheduling a full CPU run.

Save this as `train_helpers.py` in your external training workspace. Replace the three paths, then run `python train_helpers.py needle` or `python train_helpers.py laya`. `NEEDLE_INPUT` can be a documented subset of `needle_train.jsonl`; never sample from `needle_eval.jsonl`.

```python
import argparse
import os
from pathlib import Path
import sys

DATA = Path("/path/to/local-training-data")
OUT = Path("/path/to/local-models")
CACHE = Path("/path/to/local-model-cache")
NEEDLE_INPUT = DATA / "needle_train.jsonl"
OUT.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)
os.environ.update({
    "HF_HOME": str(CACHE / "huggingface"),
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "NEEDLE_TELEMETRY": "0",
    "DO_NOT_TRACK": "1",
    "USE_TF": "0",
})

if sys.argv[1] == "needle":
    from needle.agent import fetch
    from needle.model.finetune import finetune_local, build_main

    # SDK 3.2.0 otherwise puts its native engine cache in the home directory.
    fetch.cache_dir = lambda generation=3: str(
        CACHE / "needle" / f"v{generation}" / fetch.engine_version(generation)
    )
    checkpoint = fetch.fetch_checkpoint(
        "needle3.safetensors", str(CACHE / "needle" / "checkpoints"), generation=3
    )
    target = OUT / "needle-hydra"
    adapter = target / "adapter.safetensors"
    finetune_local(argparse.Namespace(
        checkpoint=checkpoint, jsonl_path=str(NEEDLE_INPUT), generate=0,
        epochs=4, batch_size=4, lr=1e-4, lora_rank=8, lora_alpha=16,
        max_len=1024, val_split=0.1, seed=42,
        checkpoint_dir=str(target), out=str(adapter), score=False,
    ))
    build_main(argparse.Namespace(
        checkpoint=checkpoint, lora=str(adapter),
        out=str(target / "hydra.cact"), layers=20, platform=None, upload=False,
    ))
elif sys.argv[1] == "laya":
    from laya.train import TrainConfig, finetune

    config = TrainConfig(
        epochs=4, micro_batch=2, grad_accum=4, loss="soft-ce",
        freeze_encoder=False, seed=42, shuffle_options=("choice",),
        eval_data=str(DATA / "laya_eval.jsonl"), log_every=5,
        max_len=512, head_max_len=192,
    )
    finetune(
        str(DATA / "laya_train.jsonl"), "convaiinnovations/laya",
        str(OUT / "laya-hydra"), config=config, device="cuda",
    )
else:
    raise SystemExit("Choose needle or laya")
```

Change Laya's `device` to `"cpu"` on a machine without supported CUDA. Its output folder is ready for `LayaDecisionClient` and includes `train_report.json` with base/trained accuracy, loss and calibration metrics. Needle writes a LoRA adapter and exports `hydra.cact` for native inference. `score=False` skips the SDK's slow internal generative scoring; use the separate evaluation below. Local Needle tuning does not train its confidence head, so the exported model reports `confidence: null`.

## Evaluate adapters before enabling them

For Needle, save the following as `evaluate_needle.py` beside `train_helpers.py`. It uses the same cache and paths. Run it before training with `python evaluate_needle.py base`, then after export with `python evaluate_needle.py trained`. It calls prediction only; none of the proposed tools run.

```python
import json
import sys
from pathlib import Path

# Define DATA, OUT and CACHE here using the same external paths as training.
DATA = Path("/path/to/local-training-data")
OUT = Path("/path/to/local-models")
CACHE = Path("/path/to/local-model-cache")
import os
os.environ.update({"NEEDLE_TELEMETRY": "0", "DO_NOT_TRACK": "1"})
import needle
from needle.agent import fetch
fetch.cache_dir = lambda generation=3: str(
    CACHE / "needle" / f"v{generation}" / fetch.engine_version(generation)
)

mode = sys.argv[1]
if mode not in {"base", "trained"}:
    raise SystemExit("Choose base or trained")
rows = [json.loads(s) for s in (DATA / "needle_eval.jsonl").read_text(
    encoding="utf-8").splitlines() if s.strip()]
options = {"auto_date": False}
if mode == "trained":
    options["weights"] = str(OUT / "needle-hydra" / "hydra.cact")
agent = needle.Needle(tools=rows[0]["tools"], **options)
records = []
for index, row in enumerate(rows):
    if row["tools"] != rows[0]["tools"]:
        raise ValueError("This evaluator requires one shared tool schema")
    agent.reset()
    result = agent.complete(row["query"], max_new_tokens=256)
    exact = result.get("success") is True and result.get("function_calls") == row["answers"]
    records.append({"row": index, "exact": exact, "expected": row["answers"], "result": result})
summary = {"rows": len(rows), "exact_calls": sum(r["exact"] for r in records)}
summary["accuracy"] = summary["exact_calls"] / len(rows)
OUT.mkdir(parents=True, exist_ok=True)
(OUT / f"needle-{mode}-evaluation.json").write_text(
    json.dumps({**summary, "predictions": records}, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=2))
```

Laya's training call evaluates the supplied `laya_eval.jsonl` before and after training and saves both results in `laya-hydra/train_report.json`. Inspect per-subsystem errors as well as aggregate accuracy before routing real work. If you use those results to choose among training candidates, call this set validation and reserve another file-group split for a final test.

Run the model SDK's training command on the training file and score the untouched evaluation file separately. Compare the trained adapter with the same base model before training. For Needle, report exact tool name and argument matches, including unsupported-action refusals. For Laya, report accuracy and per-subsystem counts. Preserve SDK/base-model versions, hyperparameters, dataset checksums, training logs and evaluation predictions alongside the adapter.

Keep datasets, checkpoints, adapters and logs outside this public repository. An adapter trained from private source is also a private artifact. The local model dependencies are optional; Hydra's primary reasoning and coding model remains configurable independently.

After review, set `NEEDLE_MODEL` to the absolute `needle-hydra/hydra.cact` path to select it with provider `needle`. Set `HYDRA_LAYA_MODEL` to the absolute `laya-hydra` folder and call `python -m hydra.decision_models --state state.json --questions questions.json --device cuda` for typed decisions. These are separate configuration choices; training does not change Hydra's default chat model or enable an adapter automatically.
