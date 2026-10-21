# Hydra

**Turn a task into working code, with the models you choose.**

Hydra is a coding and operations agent that runs from your terminal. Give it a repository and a goal: it can explore files, make edits, run commands, check its work, and keep useful context between sessions. Use a local model, connect a cloud service, or mix providers for different tasks.

**Created by Cuzzo (Tcuzzo) with BACKS AIOS.** Models and coding assistants are tools used within AIOS. [Project and skill credits](PROVENANCE.md#project-and-tool-credit).

Python 3.11+ for the core CLI · Python 3.12 or 3.13 for the complete local bundle · [MIT](LICENSE.md)

[Get started](#get-started) · [Bot team](#bot-team) · [Needle and Kev](#needle-and-kev) · [Memory and learning](#memory-and-learning) · [Training](docs/TRAINING.md)

**Local runtime guide:** [SDK account links, interchangeable models, CPU selectors,
iGPU embeddings, source indexing and blind review](docs/LOCAL-AGNOSTIC-RUNTIME.md).

## What you can do

- **Understand a codebase:** find the entry point, trace a feature, or explain a failure.
- **Build and repair:** ask for a change, review the edits, and let Hydra run the checks you approve.
- **Repeat useful work:** watch files or run a bounded task on a schedule.
- **Keep context:** save project lessons and recall them in later sessions.
- **Choose where inference runs:** use local compute, cloud APIs, or both.
- **Add capabilities:** load local skill playbooks, enable browser tools, or connect Telegram.
- **Coordinate specialists:** run Hydra Bot, Grok Bot and Seam Bot with separate testing and review, plus the original vision, design, engineering, Kaizen and Lean Six Sigma profiles.
- **Use local helpers:** install Needle for structured tool proposals and Kev for typed decisions alongside the main chat model.
- **Connect your apps:** run the official Zapier SDK locally, with account links and explicit action receipts. Hosted Zapier MCP is not required.
- **Find current source faster:** share an incremental symbol index and verify content keys before reading.
- **Keep failures literal:** honor provider resets, pause exhausted accounts for eight hours, and use only configured fallbacks.

Try a concrete request: “Find why the tests fail, make the smallest fix, and run the relevant tests.” Hydra's usefulness depends on the selected model and the tools available on your machine.

## Get started

Install the complete local bundle from a clone:

```bash
git clone https://github.com/Tcuzzo/HydraAgent_public.git
cd HydraAgent_public
python3.12 scripts/install_local.py
source "$HOME/.hydraAgent/local-runtime/venv/bin/activate"
```

The installer creates its own virtual environment, installs CPU PyTorch, Needle, the pinned official Kev SDK and vector-memory support, then runs real local initialization. First initialization downloads the helper model weights. It prints the installed Hydra command and records its installation state. Use `--directory` to choose another installation location outside the checkout; `--skip-download` installs dependencies and reports model readiness as unverified.

On Windows, use `py -3.12 scripts/install_local.py`, then activate the printed environment's `Scripts\Activate.ps1`. The [quickstart](QUICKSTART.md) includes both command forms. For the smaller core CLI without the local helper dependencies, `pipx install git+https://github.com/Tcuzzo/HydraAgent_public.git` remains available.

Open a terminal in the project you want Hydra to work on, activate the installed environment, then run:

```bash
hydra
```

On first launch, choose a local chat model, a cloud API key, or the Codex CLI sign-in path. Needle and Kev perform narrow helper jobs; the main chat and coding provider still needs configuration. `hydra setup` configures that provider from the command line. Provider settings live outside your project in `~/.hydraAgent/workspace/`.

For a local first task, install [Ollama](https://ollama.com), start it, then:

```bash
ollama pull qwen3:8b
hydra setup --mode local --model qwen3:8b --non-interactive
hydra ask "Explain this project's entry point" --provider ollama --model qwen3:8b --approval-policy deny
```

That model is an example; choose one that fits your hardware and supports the tasks you need. Run `hydra models --provider ollama` to inspect installed models. See [QUICKSTART.md](QUICKSTART.md) for development installation and first-run checks.

## Bot team

The default engineering team follows five stages: **Hydra Bot frames the task, Grok Bot gathers repository evidence, Seam Bot repairs the shared cause, the test engineer checks the result, and the code reviewer inspects it.** Each worker receives its own canon, essence and selected BACKS playbooks, with source URLs and checked content hashes.

```bash
hydra team list
hydra team show seam_bot
hydra team plan "Find and repair the failing tests, then inspect the result" --provider ollama --model qwen3:8b --output team.json
hydra team run team.json --root . --approval-policy allow
```

Inspect the plan before running it. `allow` authorizes its writer roles to edit files and execute commands. Tasks have explicit dependencies, concurrency and iteration limits, and subprocess deadlines. Readers can run together; workspace writers are ordered to avoid conflicting edits. Each task can select its own provider, model and declared family.

The example uses the local model configured above. Ten public profiles are available; the original seven remain selectable. Grok Bot is a role name and uses the provider you configure. It does not install or silently select an xAI model.

Work and review roles must produce actual observations or satisfy declared artifact conditions. Final prose alone is not evidence. You can require specific files, hashes and changes in the plan. When `require_independent_review` is enabled, missing independence produces a RED report and a nonzero exit. Reports retain `verified: false`: measured operations and file conditions have a narrower scope than overall software quality.

From the source checkout in the activated environment, optional MCP and Zapier connections use the existing orchestration path:

```bash
python -m pip install ".[mcp]"
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
| Needle | Local structured tool proposals through `needle_select` |
| Kev | Local typed decisions through `kev_decide` or `hydra decide` |
| Laya | Optional separately installed typed-decision backend |

For a custom compatible server:

```bash
hydra setup --mode cloud --provider localserver --endpoint http://localhost:8000/v1 --model YOUR_MODEL --non-interactive
hydra ask "Describe this repository" --provider localserver --approval-policy deny
```

The key can be omitted for an unauthenticated server. For a hosted service, use interactive setup to enter its key. The HTTP adapter expects OpenAI-compatible chat completions; a provider with a different native API needs a compatible gateway or its own adapter. Model IDs and endpoints are yours to configure.

Planner, worker, and auditor roles can share a provider. Where independent review is required, configure `agentic.require_independent_auditor: true` in your role configuration. See the [model guide](docs/MODELS.md).

## Needle and Kev

The complete bundle includes two local helpers with separate responsibilities:

- [Cactus Compute Needle](https://github.com/cactus-compute/needle) proposes a tool and structured arguments from a supplied tool schema.
- [Jared Palmer's Kev](https://github.com/jaredpalmer/kev) answers typed questions about supplied state. Hydra installs the official GitHub source at a pinned revision; the unrelated PyPI package with the same name is not the model SDK.

Initialize an installed bundle or inspect its dependency state:

```bash
hydra init
hydra init --check
```

`hydra init` loads both helpers and runs bounded local inference probes. `--check` only inspects dependencies and never claims inference readiness. Helper weights use the shared `HYDRA_MODEL_CACHE` location, defaulting to `~/.hydraAgent/models`.

For typed questions saved as JSON and state saved as text:

```bash
hydra decide --backend kev --state state.txt --questions questions.json
```

The `needle_select` and `kev_decide` tools return proposals. They do not automatically execute the proposed operation or replace the chat model. Actual work still goes through Hydra's tool policy and execution path. Laya remains a distinct optional backend; see [model configuration](docs/MODELS.md) for its installation and API.

## Memory and learning

`ask`, `chat` and team workers use the same durable memory root by default: `HYDRA_MEMORY_ROOT`, or `~/.hydra-memory` when unset. Execution episodes are associated with their workspace so one project's recorded actions do not become another project's task history. The runtime records successful and failed tool observations, bounded redacted arguments, result hashes and links to referenced artifacts. Later tasks retrieve relevant observations and recheck current source.

Inspect the memory for the project you are in:

```bash
hydra local-memory --workspace-root . --query "failing tests"
hydra local-memory --workspace-root . --query "failing tests" --graph
```

The graph exposes recorded task, tool and artifact relationships. It does not invent causal facts. Model final answers are excluded from learned facts, and execution records remain `quality_verified: false`. Retrieval falls back visibly to bounded lexical search when semantic retrieval is unavailable. `--no-local-memory` disables automatic memory on `ask` and `chat`; a per-command `--memory-root` can choose another store.

This experience loop improves access to previous work without retraining neural weights. The separate [local training workflow](docs/TRAINING.md) prepares provenance-backed datasets and supported fine-tuning runs, with held-out evaluation before checkpoint promotion.

The public package contains training tools, not private repository contents or private-trained weights. Keep datasets and checkpoints outside the public checkout. Preparing a corpus is not training, and a completed training run is not proof of better coding performance: compare against a held-out evaluation before selecting a checkpoint for normal use.

## Stay in control

Filesystem tools are scoped to your working directory or explicit `--root`. The default `ask` policy requests approval for shell commands and file changes; noninteractive runs refuse those actions. Use `deny` for inspection or `allow` when you deliberately want unattended execution.

```bash
hydra ask "Review the error handling" --root ./my-project --approval-policy deny
hydra watch --help
hydra undo --list
```

A shell command can affect more than the workspace; approval is not an operating system sandbox. If you enable container execution, Hydra must find a working engine rather than silently run the command on the host. `undo` restores saved file edits; it cannot reverse external side effects.

Worker verification that requests a sandbox requires Linux with working Bubblewrap
(`sudo apt-get install bubblewrap` on Ubuntu). It returns `sandbox unavailable`
without running the command when confinement is unavailable, including on native
Windows and macOS. Ordinary local CLI tools remain available on all three systems;
use a Linux environment for confined worker verification.

`hydra ask` exits nonzero when it fails or exhausts its iteration budget, while printing the available partial result. `--max-iterations` limits tool-loop iterations. A final no-tool synthesis request and recovery requests may add model calls; runtime results report these separately as `provider_calls`. `--timeout` limits each model call. Tool execution and approval waits have their own limits. Optional Telegram and browser setup lives in the [command reference](docs/CLI-REFERENCE.md).

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
