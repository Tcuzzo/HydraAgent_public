# Quickstart

Install the local bundle, connect a chat model, and run a task with inspectable evidence.

## 1. Install the complete local bundle

Use Python 3.12 or 3.13 and Git. From a terminal on Linux or macOS:

```bash
git clone https://github.com/Tcuzzo/HydraAgent_public.git
cd HydraAgent_public
python3.12 scripts/install_local.py
source "$HOME/.hydraAgent/local-runtime/venv/bin/activate"
```

On Windows PowerShell:

```powershell
git clone https://github.com/Tcuzzo/HydraAgent_public.git
cd HydraAgent_public
py -3.12 scripts/install_local.py
& "$HOME\.hydraAgent\local-runtime\venv\Scripts\Activate.ps1"
```

The installer creates an owned virtual environment outside the checkout, installs CPU PyTorch, Needle, the pinned official Kev SDK and vector-memory dependencies, then downloads the helper weights and runs real initialization probes. No API keys ship with Hydra. The installer prints the Hydra executable path; you can use that path directly instead of activating the environment.

Use `--directory /path/to/local-runtime` to choose another location outside the source checkout. Re-running the same command can resume an incomplete owned installation. `--skip-download` installs dependencies only and explicitly leaves model readiness unverified. To finish initialization later:

```bash
hydra init
```

`hydra init --check` checks dependency availability and the official Kev source revision without downloading or running the models. It does not establish inference readiness. Model weights use `HYDRA_MODEL_CACHE`, defaulting to `~/.hydraAgent/models`.

For only the smaller core CLI, Python 3.11+ and `pipx install git+https://github.com/Tcuzzo/HydraAgent_public.git` remain supported. That installation does not include the complete local helper dependencies.

## 2. Connect the main chat model

```bash
hydra
```

On a fresh install, the chat surface shows the **connect a model** panel. `hydra setup` exposes the same choices from the command line. Needle selects structured tool proposals and Kev answers typed questions; they are helpers for the agent and do not replace its chat or coding model.

Pick one path:

- Local: install [Ollama](https://ollama.com), pull a model, and let Hydra use it.
- Cloud: enter your own provider key when prompted.
- Codex CLI: use an installed, authenticated Codex client with an eligible account.

For a concrete local example, start Ollama and run:

```bash
ollama pull qwen3:8b
hydra setup --mode local --model qwen3:8b --non-interactive
hydra models --provider ollama
```

Choose a model appropriate for your hardware and workload; this example is not a quality benchmark. Provider configuration is stored outside the project in `~/.hydraAgent/workspace/`.

## 3. Ask Hydra to do something

Open a terminal in the project you want Hydra to inspect and activate the installed environment. For the local example above:

```bash
hydra ask "Explain this project's entry point" --provider ollama --model qwen3:8b --approval-policy deny
```

`deny` permits inspection while refusing file writes and command execution. Use the default approval policy for interactive approval, or deliberately pass `--approval-policy allow` for unattended work that may edit files and run commands. A shell command runs with the process's operating-system permissions unless you separately configure confinement; workspace-scoped file tools do not make host shell execution a sandbox.

## 4. Inspect and run the bot team

```bash
hydra team list
hydra team show seam_bot
hydra team plan "Find and repair the failing tests, then inspect the result" --provider ollama --model qwen3:8b --output team.json
```

Review `team.json`, then run it from the target project:

```bash
hydra team run team.json --root . --approval-policy allow
```

The default plan runs **Hydra Bot → Grok Bot → Seam Bot → test engineer → code reviewer**. All ten public profiles include a canon, essence and selected BACKS playbooks with provenance hashes. Grok Bot is a repository-understanding role; it uses your configured model and does not install xAI Grok.

Writer and review tasks need actual observations or declared file evidence to complete. Reports contain tool outcomes and artifact conditions, and retain `verified: false` because execution success does not establish overall quality. If you enable `require_independent_review`, the reviewer must use a different effective model identity and declared family from every writer. The [orchestration guide](docs/PERSONAL-ORCHESTRATION.md) includes a complete plan with file hashes and independent routing.

## 5. Inspect memory and local decisions

`ask`, `chat` and team workers recall and record observed tool evidence by default. They share `HYDRA_MEMORY_ROOT`, defaulting to `~/.hydra-memory`, and scope execution history to its workspace. Inspect the current project's history:

```bash
hydra local-memory --workspace-root . --query "failing tests"
hydra local-memory --workspace-root . --query "failing tests" --graph
```

The graph connects recorded tasks, tools and referenced artifacts. Failed attempts are retained; model final answers are excluded from learned facts. Semantic retrieval reports its fallback when only lexical search is available. `ask` and `chat` accept `--no-local-memory`; `ask`, `chat` and `team run` accept `--memory-root` to choose another store. This records experience for retrieval, without changing model weights.

To try Kev directly, save this text as `state.txt`:

```text
The service state is ready. The health check passed.
```

Save the questions as `questions.json`:

```json
{"ready":{"type":"noul","instructions":"Is the service state ready?"}}
```

Then run:

```bash
hydra decide --backend kev --state state.txt --questions questions.json
```

Kev answers from the supplied text; this example does not run a health check. The agent's `needle_select` and `kev_decide` tools likewise return local proposals. Any resulting operation still goes through normal tool execution and approval policy.

## Develop from source

For an editable core development environment, use a separate virtual environment in your clone:

```bash
python3.12 -m venv .venv
```

Activate it with `source .venv/bin/activate` on Linux/macOS or
`.venv\Scripts\Activate.ps1` in Windows PowerShell. Then:

```bash
python -m pip install -e ".[test]" -c constraints.txt
python -m hydra --help
python -m hydra tools
python -m pytest -q
```

The test command runs the repository's test suite; it is not an attestation that every environment or optional provider has passed. The complete bundle installer above owns its separate runtime. For editable helper development, install a suitable CPU PyTorch build first, then `python -m pip install -e ".[intelligence]" -c constraints.txt` using Python 3.12 or 3.13. Laya remains a separate optional backend. Install training dependencies in a separate environment; see [model configuration](docs/MODELS.md) and [training](docs/TRAINING.md).

## More

See the [orchestration guide](docs/PERSONAL-ORCHESTRATION.md) for bot acceptance contracts, MCP and private profiles, and the [command reference](docs/CLI-REFERENCE.md) for the broader CLI.
