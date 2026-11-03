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
hydra ask "Read README.md" --provider needle --model '<your-needle-model>' --approval-policy deny
```

`<your-needle-model>` is a placeholder. `hydra/needle_client.py` does not read an
environment variable. The `needle` provider passes `NEEDLE_MODEL` from the
process environment or `~/.hydraAgent/workspace/.env.needle`. If that variable is
unset, the provider uses the fallback name compiled in `hydra/providers.py`.

Needle is designed to select tools. It does not supply a conversational final
answer. The CLI can show an empty final answer on a refusal, or a partial-result
notice if the loop reaches its budget; inspect the tool results in `--trace-out`
when evaluating selection. For a complete conversational task, use a chat model.

Set `NEEDLE_MODEL` to a tuned `.cact` archive, or pass its path with `--model`.
Hydra rejects tool names outside the provided schemas and explicitly ungrounded
arguments. Optional model calls run in a subprocess with a timeout. Telemetry is
disabled for this worker; initial model downloads still need network access.

## Laya: typed decisions

Install `hydraagent[laya]`. This extra declares `laya>=0.4.2,<0.5`. It is an open
Jev-style decision model, with `choice`, `score`, and `noul` question types. It
is separate from chat roles. Pass `--model <your-checkpoint>` or set
`HYDRA_LAYA_MODEL`. If both are unset, `hydra/decision_models.py` uses the
checkpoint id compiled in that file. The upstream checkpoint page is linked below;
it is a starting point you can replace. Direct Jev calls and the decision
service are separate rungs. See
[Jev typed decisions](../QUICKSTART.md#jev-typed-decisions-optional).

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

That command is the direct Laya entry point. The decision ladder in
`hydra/decision_ladder.py` does not use the compiled checkpoint id. It calls
Laya only when `HYDRA_LAYA_MODEL` is set.

## Direct Jev and the three decision tiers

`hydra/model_routing.yaml` names `llm_decision`, `vision_decision`, and
`text_decision`. No model id is stored there. At call time the ladder in
[Quickstart](../QUICKSTART.md#jev-typed-decisions-optional) tries a decision
service, then a local model, then direct Jev.

Direct Jev needs both `OPENROUTER_API_KEY` and `HYDRA_JEV_MODEL`. Create the
key at [openrouter.ai/keys](https://openrouter.ai/keys). The HTTP shape is the
[OpenRouter quickstart](https://openrouter.ai/docs/quickstart). Set
`HYDRA_JEV_MODEL` to an id from your account. `HYDRA_JEV_BASE_URL` overrides
the default origin `https://openrouter.ai/api/v1`. Every request includes
`instructions`. HTTP 401, 402, and 403 step to the next configured rung.
`hydra doctor --rungs` reports the configuration and does not place a paid call.

Tools the agent can call:

- `live_research` searches and fetches pages when facts may be stale.
- `vision_decide` asks the vision rung whether an image shows a claim (`yes_no` or `score`).
- `text_decide` asks the text rung to `classify` or `validate` structured output.

Set `HYDRA_REFLEX=1` to run the llm tier before another big-model loop.

## Local decision models

Set the env var to an id you pulled or downloaded. The value is not compiled
into Hydra. Sizes below are the parameter counts published on the model page,
times two bytes for a rough FP16 weight footprint. Quantized builds are smaller;
read the tag size on the page before you pull.

### Text and llm decisions

[Laya](https://huggingface.co/convaiinnovations/laya) is an Apache-2.0 typed
decision checkpoint (about 421 million parameters, text classification). The
[source and license](https://github.com/NandhaKishorM/laya) and the
[license file](https://github.com/NandhaKishorM/laya/blob/main/LICENSE) match.
FP16 weights are under 1 GB. The ladder uses it when `HYDRA_LAYA_MODEL` is set.
`HYDRA_LAYA_DEVICE` selects `cpu` or `cuda`.

For an Ollama or Hugging Face instruct model on the llm tier, set
`HYDRA_KEV_MODEL` or `HYDRA_LLM_DECISION_MODEL`. For the text tier, set
`HYDRA_TEXT_DECISION_MODEL`. A small starting point is
[Qwen2.5-0.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct)
(Apache-2.0, about 494 million parameters, roughly 1 GB at FP16). The same
family is on the [Ollama Qwen2.5 library](https://ollama.com/library/qwen2.5),
including the [0.5b tag](https://ollama.com/library/qwen2.5:0.5b). Put the tag
you pulled in the env var.

```bash
ollama pull '<your-decision-model>'
export HYDRA_LLM_DECISION_MODEL='<your-decision-model>'
export HYDRA_TEXT_DECISION_MODEL='<your-decision-model>'
```

### Vision decisions

Set `HYDRA_VISION_MODEL`. The rung asks a yes/no or score question and sends
the claim plus an image reference.

[moondream2](https://huggingface.co/vikhyatk/moondream2) is Apache-2.0,
image-to-text, about 1.9 billion parameters. Full-precision weights are about
4 GB. The [Ollama moondream library](https://ollama.com/library/moondream) lists
the size of each tag.

A smaller Hugging Face checkpoint is
[SmolVLM-256M-Instruct](https://huggingface.co/HuggingFaceTB/SmolVLM-256M-Instruct)
(Apache-2.0, about 256 million parameters, roughly 0.5 GB at FP16).

```bash
ollama pull '<your-vision-model>'
export HYDRA_VISION_MODEL='<your-vision-model>'
```

These three tiers are also the deterministic control layer for a future
robotics use: the llm tier picks the next action, the vision tier checks that
the camera matches the claim, and the text tier validates a structured command
before anything is sent to hardware. Hydra does not include that driver.

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
