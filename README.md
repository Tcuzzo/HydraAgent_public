# Hydra

**A powerful, no-frills autonomous coding & ops agent you run on your own machine.**

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

## Quickstart

```bash
python -m hydra ask "summarize what this repo does"     # one-shot
python -m hydra chat                                    # interactive
python -m hydra tools                                   # list the tool set
python -m hydra providers                               # show configured models
python -m hydra setup                                   # guided provider setup
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

## License

**MIT** — see [LICENSE.md](LICENSE.md). Free for any use, including commercial.
See [NOTICE.md](NOTICE.md) for third-party attributions and
[PROVENANCE.md](PROVENANCE.md) for derivation.
