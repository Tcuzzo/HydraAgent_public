# How Hydra works (plain English)

Hydra is a standalone assistant you run in a terminal. Give it a project folder and a goal such as “explain how login works” or “fix the failing test.” Hydra connects to the model you configure, offers tools that can read or change the project, and applies your approval policy before an action runs.

Hydra does not contain a conversational model. You supply one through local Ollama, a direct cloud provider, or your signed-in Codex CLI. The public package does not discover or call a private routing service.

## What you need

| Requirement | Purpose | Check |
|---|---|---|
| Python 3.11 or newer | Runs Hydra | `python3 --version` |
| One configured model source | Produces answers and tool requests | `hydra setup` |
| A project directory | Defines the file tools' workspace | Run inside it or pass `--root` |

```bash
pipx install git+https://github.com/Tcuzzo/HydraAgent_public.git
hydra setup
hydra ask "Explain this project's entry point" --approval-policy deny
```

The [VM installation guide](VM-INSTALL.md) covers an Ubuntu installation with Python's `venv` and `pip`. The [quickstart](../QUICKSTART.md) covers provider settings and optional capabilities.

## Choose a model source

| Source | How it connects | What you configure |
|---|---|---|
| Local Ollama | Directly to your Ollama endpoint | `OLLAMA_ENDPOINT` and `OLLAMA_MODEL`, or local setup |
| Cloud provider | Directly to the selected provider's compatible API | Provider endpoint, model and your own key |
| Codex CLI | Through the installed, signed-in `codex` process | Codex setup and login; an explicit model is optional |

For a local model:

```bash
hydra setup --mode local --model '<your-model>' --non-interactive
hydra models --provider ollama
hydra ask "Read README.md and summarize it" --provider ollama --approval-policy deny
```

`<your-model>` is a placeholder: replace it with a model available on your server. A default endpoint does not prove that the server or model is installed. If no local model is configured, Hydra can inspect that endpoint's loaded and installed model lists. It does not ask another service to choose the model.

For a direct cloud provider, setup writes provider-specific settings under `~/.hydraAgent/workspace/` by default. `HYDRA_ENV_DIR` selects another directory. Keep credentials outside the repository. Process environment settings override saved provider settings.

`--provider` selects a provider for one command. `HYDRA_PROVIDER` selects a configured default. These explicit choices take precedence over profile discovery. A named custom provider is supported when its endpoint and model are configured; a native proprietary API may require a compatible adapter.

## A request, step by step

1. **Choose the workspace and policy.** Hydra resolves the project root and the requested approval mode.
2. **Resolve the provider and model.** Explicit command options, provider settings and optional role configuration determine the route.
3. **Send the task and allowed tool descriptions.** The model returns text or requests tools.
4. **Execute permitted tools.** Hydra checks policy and workspace constraints, runs the selected tool, and sends its result back to the model.
5. **Return the answer and execution evidence.** The trace records actual calls and results. A model's claim alone is not proof that a file changed or a check passed.

For Codex, the native process performs its own tool work. Hydra records its launch workspace, sandbox mode and return code separately from Hydra-local tool counts. Process success alone does not certify edits.

## Roles and fallbacks

A **role** is a kind of work: planning, coding, or auditing. The packaged `hydra/model_routing.yaml` names role entries and their fallback order. It contains no credentials or deployment-specific model IDs.

The operator configures each provider. `HYDRA_<RUNG>_PROVIDER` can choose a configured provider for a routing entry: `HYDRA_PLAN_PROVIDER`, for example, selects the `plan` entry. An optional role file can explicitly assign provider, model and family. Copy [the example](../.hydraAgent/hydra.example.yaml) and replace its placeholders.

Role configuration is selected from an explicit path, `$HYDRA_CONFIG`, the requested workspace, then `~/.hydra/hydra.yaml`. When no role file is selected, Hydra uses the packaged mapping. An invalid selected file is an error.

Fallback depends on the caller and configured route. Role and decision ladders can try another configured option after a failure. Hydra does not supply a remote model, key or private service as an emergency fallback. If no eligible model answers, it reports a failure.

Common provider failures include refused credentials, exhausted credit, an unavailable model, rate limits and server errors. The error and trace should be used to identify which configured route failed. A successful fallback does not mean that the original provider recovered.

## Optional capabilities

| Capability | What it adds | Configuration |
|---|---|---|
| Needle | Local tool selection; Hydra still executes tools | `hydraagent[needle]` and a configured model |
| Kev, text and vision decisions | Typed decisions through configured local models | The matching `HYDRA_*_MODEL` setting and Ollama endpoint |
| Laya | Optional local typed-decision SDK | `hydraagent[laya]`, `HYDRA_LAYA_MODEL` |
| Direct Jev | Typed decisions through a direct cloud API | `OPENROUTER_API_KEY`, `HYDRA_JEV_MODEL` |
| Bubblewrap | Confined worker verification on supported Linux hosts | Install `bubblewrap`; the host must permit the required namespaces |
| MCP | Tools shared with explicitly configured servers | `hydraagent[mcp]` and a server configuration |
| Memory embeddings | Recall by meaning alongside keyword recall | Optional memory extra and configured embedding backend |
| Telegram | A separate chat surface | Your bot token and authorized operator chat |

Typed decisions and conversational chat are separate paths. The decision ladder tries configured local models and direct Jev. `hydra doctor --rungs` reports which settings are present without placing a paid call. Question validation and model-specific checks do not constitute a universal guarantee that every returned answer matches a schema; callers must use the decision contract's validation rules.

Downloaded local models can run offline. Explicit network tools, remote endpoints and configured cloud fallbacks may still use the network. “Local model” does not make every enabled tool offline.

## Approval, verification and undo

Use `--approval-policy deny` for a read-only task. Mutating tools require the applicable approval policy; scripted work must explicitly allow them. File tools stay inside the requested workspace, and shell confinement depends on the platform and available sandbox.

Hydra's file mutation tools create checkpoints for supported edits. `hydra undo` restores the latest checkpoint for the workspace. Check the actual file and command result after an edit or undo. External tools and native processes may have their own change history; a Hydra-local checkpoint cannot represent an action it did not perform.

## Where settings live

| Location | Contents |
|---|---|
| Your project folder or `--root` | The workspace visible to file tools |
| `.hydraAgent/hydra.yaml` | Optional workspace role settings |
| `~/.hydraAgent/workspace/` | Default provider settings; `HYDRA_ENV_DIR` can override it |
| `$HYDRA_CONFIG` or `~/.hydra/hydra.yaml` | Optional selected role settings |
| Packaged `hydra/model_routing.yaml` | Role mapping and fallback order |

Placeholders such as `<provider>`, `<your-model>`, `<base-url>` and `<key-env>` stand for your own configuration. `<key-env>` means the name of an environment variable containing a key, not the key itself. Do not publish real credentials or private infrastructure details.

More detail: [Quickstart](../QUICKSTART.md) · [Models](MODELS.md) · [Command reference](CLI-REFERENCE.md) · [VM installation](VM-INSTALL.md)
