# Hydra

**A powerful, no-frills autonomous coding & ops agent you run on your own machine.**

**Version 1.0.0** · MIT · Linux / macOS / Windows · check yours with `hydra --version`

Hydra reads and writes code, runs shell commands, searches your repo, fetches the
web, remembers what it learns, and routes work across local or cloud models — all
from your terminal, with a clear safety model and an optional Telegram remote.

This is the **public edition**: the full coding-agent core, sanitized for open use.
It carries none of the original project's private methodology, media pipelines,
multi-machine swarm, or operator data. See [PROVENANCE.md](PROVENANCE.md).

---

## What it can do

- **Edit code safely** — bounded file read/write/edit with path-escape protection.
- **Run a shell** — execute commands cross-platform, behind an approval gate.
- **Search & analyze** — grep, glob, repo audit, git diff.
- **Reason with any model** — local (Ollama) or cloud, routed by task complexity
  across fast / reasoning / judge tiers.
- **Remember** — a single-file hybrid memory: vector similarity + keyword (FTS5)
  search fused together, so recall feels human, not literal.
- **Pluggable skills** — drop a `SKILL.md` in and the agent can route to it.
- **Browse** (optional) — headless browser tools when Playwright is installed.
- **Drive it remotely** (optional) — a Telegram bot to chat, approve risky actions,
  and unlock unattended mode with a 2FA code.
- **Stay secure** — `hydra doctor` checks every dependency for updates and known
  vulnerabilities (PyPI + OSV) and upgrades them with `--fix`.

## Requirements

- **Python 3.11+** on Linux, macOS, or Windows.
- A model provider: a local [Ollama](https://ollama.com) install (free) and/or a
  cloud provider API key. You bring your own keys — none ship with this repo.
- Optional: `sqlite-vec` (full vector memory), `playwright` (browser tools).

## Install

**Quickest — install the `hydra` CLI straight from GitHub (one command):**

```bash
pipx install git+https://github.com/Tcuzzo/HydraAgent_public.git
# ...or into your current environment:
pip install git+https://github.com/Tcuzzo/HydraAgent_public.git
```

**Or from a clone (for development):**

```bash
git clone https://github.com/Tcuzzo/HydraAgent_public.git
cd HydraAgent_public
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e .
```

**Optional capabilities:**

```bash
pip install sqlite-vec        # full vector memory (otherwise keyword-only recall)
pip install playwright && playwright install chromium   # browser tools
```

Then run `hydra setup` to configure a model provider (or drop keys in
`~/.hydraAgent/workspace/.env.<provider>`) and you're ready: `hydra ask "..."`.

**Cross-platform notes**

- The shell tool runs through one per-OS helper (`hydra/proc.py`): POSIX shell on
  Linux/macOS, `cmd.exe` (or Git-Bash if present) on Windows.
- The bundled vector extension (`vec0.so`) is Linux x86-64 only. On macOS/Windows,
  `pip install sqlite-vec` for full vector memory; without it, recall gracefully
  falls back to keyword search.
- GPU-busy detection (`hydra/model_router.py`) reads a POSIX advisory lock
  (`fcntl.flock`) to tell whether a local GPU job already holds the card. Native
  Windows has no `fcntl`, so **on Windows a busy GPU cannot be detected**: the
  router logs a loud warning and routes as if the GPU were free. Linux/macOS are
  unaffected. The tests covering that seam skip on Windows for the same reason.

## Quickstart

```bash
python -m hydra ask "summarize what this repo does"     # one-shot
python -m hydra chat                                    # interactive
python -m hydra tools                                   # list the tool set
python -m hydra providers                               # show configured models
python -m hydra setup                                   # guided provider setup
python -m hydra doctor                                  # check deps for updates + CVEs
```

By default the agent's filesystem scope is the **current directory** and risky
tools require approval (see below).

## Watch — recurring & triggered runs

Run a task automatically on a timer, when files change, or both — no daemon, no
cron required. **Read-only by default** (the agent can analyze but not change
anything); add `--yolo` to let it act.

```bash
# every 10 minutes (read-only):
python -m hydra watch --every 10m "audit the repo for new TODOs and summarize them"

# when code or tests change, re-run the suite and fix failures (allowed to act):
python -m hydra watch --watch ./src --watch ./tests --yolo "run the tests; if any fail, fix them"

# read the task fresh each cycle from a file, stop after 5 runs:
python -m hydra watch --task-file task.md --every 1h --max-cycles 5
```

Triggers (use either or both): `--every <30s|10m|2h>` and/or `--watch <path>`
(repeatable). Controls: `--poll`, `--debounce`, `--max-cycles`, `--stop-file`,
`--yolo` (or `--approval-policy`). Stop with `Ctrl-C` (or by creating the
`--stop-file`). It's a plain CLI — for OS-level scheduling, point `cron` / a
`systemd` timer / Windows Task Scheduler at `hydra ask` or `hydra watch`.

## Command reference

Run any command as `hydra <cmd>` (installed) or `python -m hydra <cmd>`, and add
`-h` to any command for its full flags. Flags common to the agent commands:
`--provider`, `--model`, `--root <dir>` (filesystem scope), `--timeout`,
`--max-iterations`, `--approval-policy {ask,allow,deny}`.

**Run the agent**

| Command | What it does |
|---|---|
| `ask "<prompt>"` | One-shot — work the prompt to completion. |
| `chat` | Interactive multi-turn session with persistent history + memory. |
| `watch ...` | Run on a timer and/or on file change — see [Watch](#watch--recurring--triggered-runs). |
| `execute "<mission>"` | Planner → doer → auditor loop for larger missions. |

`ask` is the workhorse. Key flags: `--profile {auto,cloud,local}` ·
`--provider`/`--model` override · `--root <dir>` scope (default: current dir) ·
`--approval-policy {ask,allow,deny}` (default `ask`) · `--with-context` /
`--truth-context` inject memory · `--auto-route` pick the model by task type ·
`--trace-out <file>` write a JSON trace · `--runtime-only` show the resolved
model/route without calling the model.

**Set up & discover**

| Command | What it does |
|---|---|
| `setup` | Guided provider setup (local Ollama or a cloud key). |
| `providers` | List configured providers. |
| `models --provider <name>` | List a provider's models. |
| `roles` | Show planner/doer/auditor model routing. |
| `tools` | List the agent's tool set. |

**Skills & memory**

| Command | What it does |
|---|---|
| `skills list \| show <name> \| route "<prompt>" \| search "<q>"` | Inspect & route the skill library. |
| `remember "<lesson>" --source <path>` | Save a sourced lesson to durable memory. |
| `local-memory [--query "<q>"]` | Show or query durable memory. |

**Inspect (read-only — no model, no mutation)**

| Command | What it does |
|---|---|
| `audit <dir>` | Deterministic repo audit: evidence, hot files, hints. |
| `locate "<name>"` | Find files/dirs by name under a root. |
| `status` | Repo verification verdict. |
| `code <file>` | Run a source file with syntax highlighting. |
| `undo [--list]` | Restore the most recent file-edit snapshot(s). |
| `ops recall "<q>"` | Keyword recall over saved lessons & evidence. |

**Health, security & control**

| Command | What it does |
|---|---|
| `update` | Pull the latest Hydra from GitHub in one command (see [below](#updating)). |
| `doctor [--fix]` | Check deps for updates + known CVEs (see [below](#keeping-your-install-secure-hydra-doctor)). |
| `self-audit` | Run the agent's own classify→route→execute invariant checks. |
| `telegram health \| listen \| send-proof` | Drive & approve from Telegram (see [below](#telegram-remote-optional)). |

**Advanced** — `mission`, `continuation`, `declarative`, `capabilities`,
`source`, `wiki`, `capability-score`, `competitive-score`, `task-eval`,
`domain-pack`, `trace-bundle`, `aci`, `autonomy`: mission orchestration,
capability scoring, and deeper introspection. Run `hydra <cmd> -h` for each.

## Keeping your install secure (`hydra doctor`)

`hydra doctor` checks every dependency (and `pip` itself) against PyPI for newer
releases and against the [OSV](https://osv.dev) advisory database for known
vulnerabilities — so you can keep your install current and safe.

```bash
hydra doctor              # report installed vs latest + any known CVEs
hydra doctor --fix        # upgrade outdated / vulnerable packages to the latest
hydra doctor --format json
```

Read-only unless you pass `--fix`. It exits non-zero when a known vulnerability is
found (useful in CI). Hydra ships current, vulnerability-free dependency floors;
`doctor --fix` keeps them that way over time.

## Updating

Get the latest Hydra in **one command**:

```bash
hydra update            # pull + reinstall the newest version from GitHub
hydra update --check    # show the update command without running it
```

`hydra update` reinstalls from the public repo's latest commit (force-reinstall,
since the version pin is stable). Prefer pipx? `pipx install --force
git+https://github.com/Tcuzzo/HydraAgent_public.git` does the same. After updating,
run `hydra doctor` to confirm your dependencies are current and safe.

## Configuration

Copy `.env.example` to `.env` and fill in what you use. Everything is environment-
driven; nothing is hardcoded. Common variables:

| Variable | Purpose |
|---|---|
| `HYDRA_OPERATOR_NAME` | How the agent refers to you (default: "the operator") |
| `HYDRA_CONFIG` | Path to your model-routing config |
| `HYDRA_VEC0_PATH` | Path to a `sqlite-vec` extension if not pip-installed |
| `HYDRA_TELEGRAM_BOT_TOKEN` | Telegram bot token (from @BotFather) |
| `HYDRA_OPERATOR_DM_CHAT_ID` | Your Telegram chat ID (where approvals go) |
| `HYDRA_OPERATOR_USERNAME` | Your Telegram @username (trusted operator) |
| `HYDRA_OPERATOR_AUTH_DIR` | Where the TOTP secret for yolo mode is stored |

## Trust & safety model

Hydra is honest about what it can do: by design it can run a shell on your machine.
Control that with the approval policy (`--approval-policy`):

- **`ask`** (default) — risky tools (`bash`, `fs_write`, `fs_edit`) prompt you on an
  interactive terminal; when run non-interactively (scripts/CI) they are **blocked**,
  never auto-run. Safe, read-mostly tools run freely.
- **`allow`** — run everything unattended. Only choose this when you trust the task
  and scope. This is the "yolo" posture.
- **`deny`** — refuse risky tools entirely.

**Yolo (unattended) mode, gated by 2FA.** Over Telegram you can unlock `allow`
behavior with a time-limited code from any TOTP authenticator app (e.g. Google
Authenticator): run `/mfa setup`, scan the QR, then `/mode yolo <6-digit-code>`. The
unlock expires after an hour and can be extended. There is no "always on" backdoor.

## Telegram remote (optional)

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Set `HYDRA_TELEGRAM_BOT_TOKEN`, `HYDRA_OPERATOR_DM_CHAT_ID`,
   `HYDRA_OPERATOR_USERNAME` in `.env`.
3. Run `python -m hydra telegram listen`.

You can then chat with the agent, get plain-language approval prompts for risky
actions, and unlock yolo mode — all from your phone. Untrusted senders can never
trigger an action without your approval.

## Extending Hydra

Hydra is built to grow without you needing its internals:

- **Bring your own model/provider** — add an entry to the provider registry; it
  speaks the OpenAI-compatible chat + tool-call protocol.
- **Swap the embedding model** behind the memory kernel.
- **Add tools/skills** — drop a `SKILL.md`; the skill spine auto-discovers and
  routes to it. No core changes needed.
- **Build a UI** — the CLI is scriptable; wrap it in a web or desktop front-end.
- **Add multi-agent coordination** with any off-the-shelf framework — the loop is a
  clean building block.

## Architecture (one breath)

`python -m hydra ask` → the agent loop (`hydra/loop.py`) calls your model, parses
tool calls, runs them through the approval gate, feeds results back, and iterates
until done — with the skill spine choosing context, the model router choosing the
model, and the memory kernel remembering across runs.

```mermaid
flowchart TD
    Prompt[Your Prompt] --> Ask[Hydra Ask]
    Ask --> Runtime[Resolve Runtime Model And Optional Route]
    Runtime --> PromptBuild[Build System Prompt And Skill Context]
    Ask -. With Context Or Truth Context .-> MemoryContext[Optional Memory Context]
    MemoryContext -.-> PromptBuild
    PromptBuild --> Tools[Bind Tools]
    Tools -. Available If Called .-> MemoryTools[Memory Recall And Remember Tools]
    Tools --> Loop[Agent Loop]
    Loop --> Model[Model Call]
    Model --> Parser[Tool Call Parser]
    Parser -- Tool Call --> Risk{Risky Tool?}
    Parser -- No Tool Call --> Exit[Answer Delivered]
    Risk -- No --> Runner[Tool Runner]
    Risk -- Yes --> Gate[Approval Gate]
    Gate -- Ask --> Runner
    Gate -- Allow --> Runner
    Gate -- Deny --> Feedback[Result Feedback]
    Runner --> Feedback
    Feedback --> Loop
```

## License

**MIT** — see [LICENSE.md](LICENSE.md). Free for any use, including commercial.
See [NOTICE.md](NOTICE.md) for third-party attributions and
[PROVENANCE.md](PROVENANCE.md) for derivation.

## Part of a family

Hydra is one of a family of local-first, operator-owned, bring-your-own-model agents: [OpenMontage](https://github.com/Tcuzzo/OpenMontage), an agentic video-production studio, and [bucks](https://github.com/Tcuzzo/bucks), a local-first trading agent that is paper-first and keeps you holding the keys.
The shared philosophy is your machine, your keys, your models, a clear safety model, and a Telegram remote.
