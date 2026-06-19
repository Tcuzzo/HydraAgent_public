# Design — one surface: `hydra` launches the chat, with working setup

2026-06-19

## Problem
A public user faces friction: bare `hydra` errors (subcommand required), the chat
TUI is hidden behind `hydra chat`, and the provider story is incomplete — only
Ollama/cloud-key work, and the "Sign in with ChatGPT" path is half-stripped (dead
`codex` setup mode). It should be: install → `hydra` → chat.

## Decisions (locked)
- Bare `hydra` on a TTY → opens the chat TUI (the existing `EliteTUI`). Bare `hydra`
  with no TTY (piped) → prints the command list. All subcommands unchanged.
- First run with no provider → the TUI opens (not an error) and shows an in-surface
  `/setup`. The chat surface is the only surface.
- `/setup` offers three **working** paths: Local (Ollama) · Cloud (API key) ·
  Sign in with ChatGPT (official browser login via the Codex CLI). No auto-detect.

## Components

### 1. Default launch — `hydra/__main__.py`
- Make the subparsers optional (`required=False`). Add a pure
  `resolve_default_action(has_subcommand: bool, is_tty: bool) -> "chat"|"help"`.
- In `main()`: if no subcommand → resolve; `"chat"` dispatches `cmd_chat` with chat
  defaults + `setup_if_needed=True`; `"help"` prints the parser help. Subcommands
  dispatch exactly as today.

### 2. TUI opens without a provider — `hydra/cli/cmd_chat.py` + `gateways/tui/elite.py`
- Today `cmd_chat` raises when the provider config is missing. Change the
  bare-launch path: if no provider resolves, construct `EliteTUI` in an
  **unconfigured** state instead of raising.
- `EliteTUI` gains an `unconfigured` mode: it renders a one-line banner —
  "No AI model connected yet. Type `/setup` to connect one." — and any normal
  message is answered with the same nudge until a provider is set.

### 3. In-surface setup — `/setup` slash command
- Add `/setup` to the TUI's slash-command handling. It runs a small picker:
  `1) Local Ollama  2) Cloud API key  3) Sign in with ChatGPT`.
  - Local → write the ollama provider (endpoint default localhost:11434).
  - Cloud → prompt endpoint + key, write a generic OpenAI-compatible provider via
    the existing `setup_cloud_provider`.
  - ChatGPT → run the Codex browser login (below), wire the `codex` provider.
- After a successful path, re-resolve the provider and the TUI flips to configured;
  the user is chatting. Parsing/dispatch of `/setup` is a pure, tested function.

### 4. Sign in with ChatGPT (restore, sanitized) — `hydra/codex_client.py` + provider
- Re-add a **generic, sanitized** Codex provider (no operator paths/identity): it
  shells out to the official Codex CLI (`codex`), which performs the browser
  "Sign in with ChatGPT" OAuth and runs the model on the user's subscription —
  no API key, OpenAI-sanctioned (same flow VS Code/Codex use).
- `/setup` ChatGPT path: detect `codex` on PATH (via `shutil.which`); if missing,
  print the one-line install hint; run `codex login` (browser) if not logged in;
  register `codex` in the provider `_BUILTINS` so `ask`/`chat` route through it.
- Resolve binary via `shutil.which("codex")` + `HYDRA_CODEX_BIN` override — never a
  hardcoded home path.

## Testing (TDD)
- `resolve_default_action(has_subcommand, is_tty)` — pure, table-tested.
- `is_provider_configured(env_dir)` — pure detection (any provider env present / a
  reachable local Ollama is out of scope; config-file based).
- `/setup` choice parsing (`parse_setup_choice("1"|"local"|"cloud"|"chatgpt"...)`).
- Codex provider: `codex_command(prompt, model, ...)` builder (pure) + binary
  resolution (`which` + env override) — tested without invoking the CLI.
- Smoke: bare `hydra` non-TTY prints help (exit 0); `EliteTUI` constructs in
  unconfigured mode without raising. (TUI rendering can't be driven headless.)

## Out of scope (YAGNI)
Native/replicated ChatGPT OAuth (use the official Codex CLI login). Auto-installing
the Codex CLI. A graphical settings screen. Multi-account switching.
