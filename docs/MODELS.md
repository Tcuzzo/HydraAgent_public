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

Install `hydraagent[needle]` from the source revision containing this integration.
This extra declares `cactus-needle>=3.2.0,<4`. Upstream weights and native runtime
are downloaded on first use. Hydra calls the SDK's prediction-only `complete`
method and sends the resulting calls through Hydra's approval and tool executor.
It never calls the SDK's tool-executing `run` method.

```bash
hydra ask "Read README.md" --provider needle --model needle3 --approval-policy deny
```

Needle is designed to select tools. It does not supply a conversational final
answer. The CLI can show an empty final answer on a refusal, or a partial-result
notice if the loop reaches its budget; inspect the tool results in `--trace-out`
when evaluating selection. For a complete conversational task, use a chat model.

Set `NEEDLE_MODEL` to a tuned `.cact` archive, or pass its path with `--model`.
Hydra rejects tool names outside the provided schemas and explicitly ungrounded
arguments. Optional model calls run in a subprocess with a timeout. Telemetry is
disabled for this worker; initial model downloads still need network access.

## Laya: typed decisions

Install `hydraagent[laya]`. This extra declares `laya>=0.4.2,<0.5`; the default
checkpoint is `convaiinnovations/laya`. It is an open Jev-style decision model,
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
python -m hydra.decision_models --state state.json --questions questions.json --device cpu --timeout 120
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

See [TRAINING.md](TRAINING.md). Training dependencies are separate from inference:
install `hydraagent[training]` in an isolated environment. Native Windows JAX
uses CPU; Needle CUDA training requires a supported Linux or WSL environment.
Laya's PyTorch backend can use CUDA where its installed build supports it.
Do not select a new checkpoint solely because training completed: compare its
held-out results with the original checkpoint.

## Upstream interfaces and licenses

- [Needle source and Apache-2.0 license](https://github.com/cactus-compute/needle)
- [Needle Python API](https://cactuscompute.com/blog/needle-python-docs)
- [Needle fine-tuning](https://cactuscompute.com/blog/finetuning-needle)
- [Laya source and Apache-2.0 license](https://github.com/NandhaKishorM/laya)
- [Laya checkpoint](https://huggingface.co/convaiinnovations/laya)
- [Laya fine-tuning](https://github.com/NandhaKishorM/laya/blob/main/docs/finetune.md)
