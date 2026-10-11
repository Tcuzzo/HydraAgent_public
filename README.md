# Hydra

**Turn a task into working code, with the models you choose.**

Hydra is a coding and operations agent that runs from your terminal. Give it a repository and a goal: it can explore files, make edits, run commands, check its work, and keep useful context between sessions. Use a local model, connect a cloud service, or mix providers for different tasks.

**Created by Cuzzo (Tcuzzo) with BACKS AIOS.** Models and coding assistants are tools used within AIOS. [Project and skill credits](PROVENANCE.md#project-and-tool-credit).

Python 3.11+ · Linux, macOS, Windows · [MIT](LICENSE.md)

[Get started](#get-started) · [Choose your models](#choose-your-models) · [Needle and Laya](#needle-and-laya) · [Training](docs/TRAINING.md) · [Command reference](docs/CLI-REFERENCE.md)

**Local runtime guide:** [SDK account links, interchangeable models, CPU selectors,
iGPU embeddings, source indexing and blind review](docs/LOCAL-AGNOSTIC-RUNTIME.md).

## What you can do

- **Understand a codebase:** find the entry point, trace a feature, or explain a failure.
- **Build and repair:** ask for a change, review the edits, and let Hydra run the checks you approve.
- **Repeat useful work:** watch files or run a bounded task on a schedule.
- **Keep context:** save project lessons and recall them in later sessions.
- **Choose where inference runs:** use local compute, cloud APIs, or both.
- **Add capabilities:** load local skill playbooks, enable browser tools, or connect Telegram.
- **Coordinate specialists:** run a bounded team for vision, design, engineering, testing, review, Kaizen and Lean Six Sigma.
- **Connect your apps:** run the official Zapier SDK locally, with account links and explicit action receipts. Hosted Zapier MCP is not required.
- **Find current source faster:** share an incremental symbol index and verify content keys before reading.
- **Keep failures literal:** honor provider resets, pause exhausted accounts for eight hours, and use only configured fallbacks.

Try a concrete request: “Find why the tests fail, make the smallest fix, and run the relevant tests.” Hydra's usefulness depends on the selected model and the tools available on your machine.

## Get started

Install the CLI with [pipx](https://pipx.pypa.io/):

```bash
pipx install git+https://github.com/Tcuzzo/HydraAgent_public.git
```

Open a terminal in the project you want to work on, then run:

```bash
hydra
```

On first launch, choose a local model, a cloud API key, or the Codex CLI sign-in path. `hydra setup` also configures providers from the command line. Provider settings live outside your project in `~/.hydraAgent/workspace/`.

For a local first task, install [Ollama](https://ollama.com), start it, then:

```bash
ollama pull qwen3:8b
hydra setup --mode local --model qwen3:8b --non-interactive
hydra ask "Explain this project's entry point" --provider ollama --model qwen3:8b --approval-policy deny
```

That model is an example; choose one that fits your hardware and supports the tasks you need. Run `hydra models --provider ollama` to inspect installed models. See [QUICKSTART.md](QUICKSTART.md) for development installation and first-run checks.

## Personal orchestration preview

Hydra now includes an experimental specialist-team runtime and optional MCP connections. It is a working foundation for a personal orchestrator; it does not yet promise unattended operation across arbitrary apps or verified quality from every model.

```bash
python -m pip install -e ".[mcp]"
hydra team list
hydra team plan "Improve this project and review the changes" --provider ollama --model YOUR_MODEL --output team.json
hydra team run team.json --root . --approval-policy allow
```

Inspect the plan before running it. `allow` authorizes its writer roles to edit files and execute commands. Tasks have explicit dependencies, concurrency and iteration limits, and subprocess deadlines. Readers can run together; workspace writers are ordered to avoid conflicting edits. Each task can select its own provider, model and declared family.

The seven public profiles include canon and essence instructions used by the worker, not just role labels. Local private overlays stay outside the source repository. Image evidence can be attached to tasks using a compatible multimodal HTTP provider. A completed run is recorded as execution success, **not a quality certification**.

```bash
hydra mcp serve --root .
hydra zapier setup --directory ./my-zapier-harness
```

Hydra's MCP server is read-only by default. The Zapier command prepares pinned official SDK dependencies; installation and account login follow separately. Local models and local MCP servers do not require a Hydra subscription. Zapier's SDK beta, hosted MCP and individual connected services have their own terms and usage limits; unlimited free app access is not promised.

See the [personal orchestration guide](docs/PERSONAL-ORCHESTRATION.md) for team plans, MCP configuration, Zapier setup, private profiles, public export and current limitations.

## Choose your models

Hydra selects models by configuration and protocol, without a model-family denylist. A family name alone does not determine API compatibility.

| Connection | Use it for |
|---|---|
| Ollama | Local chat and coding models |
| OpenAI-compatible HTTP endpoint | Cloud services and local servers exposing chat completions |
| Codex CLI | An installed, authenticated Codex command-line client |
| Needle | Local structured tool selection |
| Laya | Local typed decisions: choices, scores, and numbers |

For a custom compatible server:

```bash
hydra setup --mode cloud --provider localserver --endpoint http://localhost:8000/v1 --model YOUR_MODEL --non-interactive
hydra ask "Describe this repository" --provider localserver --approval-policy deny
```

The key can be omitted for an unauthenticated server. For a hosted service, use interactive setup to enter its key. The HTTP adapter expects OpenAI-compatible chat completions; a provider with a different native API needs a compatible gateway or its own adapter. Model IDs and endpoints are yours to configure.

Planner, worker, and auditor roles can share a provider. Where independent review is required, configure `agentic.require_independent_auditor: true` in your role configuration. See the [model guide](docs/MODELS.md).

## Needle and Laya

These open models are installable dependencies with distinct jobs:

- [Cactus Compute Needle](https://github.com/cactus-compute/needle) predicts structured tool calls. Hydra executes those calls through its own tool and approval path. Needle is a tool selector, so use a chat model when you need explanations or long-form coding assistance.
- [Laya](https://github.com/NandhaKishorM/laya), the open Jev-style decision model from Convai Innovations, answers typed questions about supplied state. Hydra exposes it through a decision API and command-line entry point.

Install both alongside Hydra:

```bash
pipx install --force "hydraagent[needle,laya] @ git+https://github.com/Tcuzzo/HydraAgent_public.git"
```

Or, from a clone in your virtual environment:

```bash
python -m pip install -e ".[needle,laya]"
```

Model weights are separate downloads. These extras are loaded when used, so the core CLI can run without the local model libraries. Configure a tuned Needle archive or Laya checkpoint by path; see [docs/MODELS.md](docs/MODELS.md).

## Teach it your Hydra

The [local training workflow](docs/TRAINING.md) builds labeled examples from explicit public and private source roots, records provenance, and separates training from evaluation. Start with repository navigation and typed routing; these tasks match Needle and Laya's interfaces.

The public package contains training tools, not private repository contents or private-trained weights. Keep datasets and checkpoints outside the public checkout. Preparing a corpus is not training, and a completed training run is not proof of better coding performance: compare against a held-out evaluation before selecting a checkpoint for normal use.

## Stay in control

Filesystem tools are scoped to your working directory or explicit `--root`. The default `ask` policy requests approval for shell commands and file changes; noninteractive runs refuse those actions. Use `deny` for inspection or `allow` when you deliberately want unattended execution.

```bash
hydra ask "Review the error handling" --root ./my-project --approval-policy deny
hydra watch --help
hydra undo --list
```

A shell command can affect more than the workspace; approval is not an operating system sandbox. If you enable container execution, Hydra must find a working engine rather than silently run the command on the host. `undo` restores saved file edits; it cannot reverse external side effects.

`hydra ask` exits nonzero when it fails or exhausts its iteration budget, while printing the available partial result. `--max-iterations` limits model turns and `--timeout` limits each model call. Tool execution and approval waits have their own limits. Optional Telegram and browser setup lives in the [command reference](docs/CLI-REFERENCE.md).

## Find your next step

| Need | Start here |
|---|---|
| Check installed commands | `hydra --help` and `hydra tools` |
| Inspect the selected model without calling it | `hydra ask "hello" --runtime-only` |
| Check dependency advisories | `hydra doctor` |
| Configure and train local models | [Models](docs/MODELS.md) · [Training](docs/TRAINING.md) |
| Build and review a full-stack application | [Full-stack workflow for Hydra and BACKS](docs/FULLSTACK-WORKFLOW.md) |
| Understand platform differences | [Platform notes](docs/PLATFORM-NOTES.md) |
| Develop and run tests | [Quickstart](QUICKSTART.md) |
| Report a vulnerability | [Security policy](SECURITY.md) |

Hydra's public edition includes the coding-agent runtime and public skill library. See [PROVENANCE.md](PROVENANCE.md) and [NOTICE.md](NOTICE.md) for origins and third-party notices.
