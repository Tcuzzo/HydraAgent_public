# Models and providers

Hydra separates chat, tool selection, and typed decisions. Model-family names
are not blocked. Support depends on the interface the selected server or SDK
actually implements.

## Chat and coding

Use Ollama, the Codex CLI, or an OpenAI-compatible chat-completions endpoint.
For custom servers, `hydra setup --mode cloud` accepts an explicit provider name,
endpoint, and model. A key is optional for servers that do not require one.
Keys entered through interactive setup stay in the provider's local env file.

```bash
hydra setup --mode cloud --provider myserver --endpoint http://localhost:8000/v1 --model my-model --non-interactive
hydra ask "Explain the startup flow" --provider myserver --approval-policy deny
```

Custom provider variables follow the provider name: `MYSERVER_ENDPOINT`,
`MYSERVER_MODEL`, and optional `MYSERVER_API_KEY`. A local env file is named
`~/.hydraAgent/workspace/.env.myserver`; exported process variables are also
supported. Files take precedence when both are present. A project `.env` file
is not automatically imported.

`--provider` and `--model` select a particular runtime. Native Anthropic Messages
and other incompatible APIs require an adapter or compatible gateway; removing
a family restriction does not translate those wire protocols.

## Needle: tool selection

The bundled local installation uses `cactus-needle==3.2.1`; `hydraagent[needle]`
also supports installing this capability separately. Upstream weights and the
native runtime are downloaded by `hydra init` or on first use. Hydra calls the SDK's prediction-only `complete`
method and sends the resulting calls through Hydra's approval and tool executor.
It never calls the SDK's tool-executing `run` method.

```bash
hydra ask "Read README.md" --provider needle --model needle3 --approval-policy deny
```

Needle is designed to select tools. It does not supply a conversational final
answer. The CLI can show an empty final answer on a refusal, or a partial-result
notice if the loop reaches its budget; inspect the tool results in `--trace-out`
when evaluating selection. For a complete conversational task, use a chat model.

Pass a tuned `.cact` archive with `--model`, or set `HYDRA_NEEDLE_MODEL` (preferred)
or `NEEDLE_MODEL` (compatibility). The provider and `needle_select` use that same
precedence. Hydra validates the supplied schemas, rejects calls outside them,
checks argument types and required fields, and respects the SDK's ungrounded and
negation failure flags. A valid prediction remains a proposal subject to Hydra's
normal action policy.

Optional model calls run in isolated processes with finite deadlines and output
budgets. Telemetry is disabled; initial model downloads still need network
access. The shared cache defaults to `~/.hydraAgent/models`; override it with
`HYDRA_MODEL_CACHE`. An explicit `HF_HOME` remains respected for Hugging Face
assets. Needle defaults to the process's available CPUs; other prediction
workers default to a 0.15 CPU fraction. `HYDRA_LOCAL_CPU_FRACTION` overrides
either default. See [local runtime measurements](LOCAL-AGNOSTIC-RUNTIME.md).

## Kev: bundled typed decisions

[Jared Palmer's Kev](https://github.com/jaredpalmer/kev) is a distinct open model
with `choice`, `score` and `noul` questions. It is not Jev or Laya, and the PyPI
package named `kev` is an unrelated package. Hydra installs Kev from the official
Git repository pinned to commit
`1f04cfa28e6a708485b28a9585010b9e436b776e`. The SDK requires Python 3.12 or 3.13;
Hydra core remains compatible with Python 3.11. Use the CPU-first
`scripts/install_local.py` installer for the full local environment, or install
the `kev`/`intelligence` extra into a suitable environment with your intended
PyTorch build already selected.

The default checkpoint is
[`jaredpalmer/kev-0.8b@v1.0`](https://huggingface.co/jaredpalmer/kev-0.8b).
The worker loads its actual tokenizer, weights and pointer head through the
official SDK. CPU inference uses fp32. It retains the checkpoint's temperature
and refuses context truncation. Returned question keys, answer types, option
membership, numeric ranges and probability distributions are checked before
Hydra returns the result.

```python
from hydra.decision_models import KevDecisionClient

result = KevDecisionClient(device="cpu").predict(
    {"task": "Read README.md and explain the project"},
    {"route": {"type": "choice", "criteria": {
        "read": "Read existing files", "write": "Change files"
    }}},
    timeout=120,
)
print(result["answers"])
```

```bash
hydra decide --backend kev --state state.json --questions questions.json --device cpu --timeout 120
```

The equivalent module command is `python -m hydra.decision_models --backend kev`.
Set `HYDRA_KEV_MODEL` or `--model` for another checkpoint, and
`HYDRA_KEV_DEVICE` or `--device` for `cpu`, `cuda`, `mps` or an explicitly requested
`auto` choice. An unavailable requested device fails visibly. `kev_decide` is
also available beside the main model in Hydra's ordinary tool catalog.

Kev predicts decisions; it neither supplies a coding conversation nor grants
authorization to execute its answers. Model confidence does not establish that
a proposed action is permitted or correct. The repair sandbox exercised real
CPU inference with the official checkpoint; it did not benchmark general
coding or routing quality.

## Laya: typed decisions

Install `hydraagent[laya]`. This extra declares `laya>=0.4.2,<0.5`; the default
checkpoint is `convaiinnovations/laya`. It is an optional separate decision model,
with `choice`, `score`, and `noul` question types. It is separate from chat roles.

```python
from hydra.decision_models import LayaDecisionClient

client = LayaDecisionClient(device="cpu")
result = client.predict(
    {"task": "Find the model provider configuration"},
    {"area": {
        "type": "choice",
        "instructions": "Which subsystem should handle this task?",
        "criteria": {
            "models": "Provider clients and model configuration",
            "memory": "Persistent memory and recall",
            "tools": "File and shell execution",
        },
    }},
    timeout=120,
)
print(result)
```

Or provide state and question files:

```bash
python -m hydra.decision_models --backend laya --state state.json --questions questions.json --device cpu --timeout 120
```

Use `--model /path/to/checkpoint` or `HYDRA_LAYA_MODEL` for a tuned checkpoint.
Choose `--device cuda` on a compatible PyTorch/CUDA install, or set
`HYDRA_LAYA_DEVICE`. No automatic tool execution happens in the decision API.

## Roles and review

Role configuration uses `agentic.roles` entries with `provider`, `model`, and
`family`. Different roles may share a provider or model. Set
`agentic.require_independent_auditor: true` to retain the stricter independent
provider/family checks when your task needs them. Routing overrides may also
provide `base_url` and an `api_key_env` variable name.

## Training and evaluation

See [TRAINING.md](TRAINING.md) for the built-in bounded Needle/Kev training
runner, source-grounded JSONL export and before/after evaluation receipts.
Training dependencies are separate from inference. The built-in Needle recipe
uses CPU; other device recipes remain available through the upstream SDKs.
Do not select a new checkpoint solely because training completed: compare its
held-out results with the original checkpoint.

## Upstream interfaces and licenses

- [Needle source and Apache-2.0 license](https://github.com/cactus-compute/needle)
- [Needle Python API](https://cactuscompute.com/blog/needle-python-docs)
- [Needle fine-tuning](https://cactuscompute.com/blog/finetuning-needle)
- [Kev source and Apache-2.0 license](https://github.com/jaredpalmer/kev)
- [Kev checkpoint](https://huggingface.co/jaredpalmer/kev-0.8b)
- [Kev labeled-data loader](https://github.com/jaredpalmer/kev/blob/1f04cfa28e6a708485b28a9585010b9e436b776e/kev/data.py)
- [Laya source and Apache-2.0 license](https://github.com/NandhaKishorM/laya)
- [Laya checkpoint](https://huggingface.co/convaiinnovations/laya)
- [Laya fine-tuning](https://github.com/NandhaKishorM/laya/blob/main/docs/finetune.md)
