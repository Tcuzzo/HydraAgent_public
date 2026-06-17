"""gateways.tui.elite — HydraAgent elite interactive REPL.

Launch: hydra chat  (do not change this)

Layout (per-turn):
  [streaming tool calls + assistant thinking — inline]
  [Final response panel]
  ─── 🐉 tok:~1.2k │ ⚡ iter:3 │ 🛠 7 │ ollama-cloud │ 🔐 yolo ────
  [🐉 qwen2.5:72b] ❯  <input here>
   /help │ /skills │ /models │ /mode │ Ctrl+C stop │ Ctrl+D exit
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Callable

# select.select() works on arbitrary file descriptors on POSIX but only on
# sockets on Windows; guard so the TUI module imports cleanly on all platforms.
try:
    import select as _select_mod
    _HAS_SELECT = True
except ImportError:  # pragma: no cover — shouldn't happen; belt-and-suspenders
    _select_mod = None  # type: ignore[assignment]
    _HAS_SELECT = False

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

_LOG = logging.getLogger(__name__)


def _startup_self_audit() -> None:
    """Run hydra self-audit at TUI startup and log results.

    Log-only: never raises, never blocks. PASS → info, FAIL → warning with
    violation check_ids. This lets the operator see routing health at boot
    without interrupting the TUI session.
    """
    try:
        from hydra.self_audit import run_self_audit
        report = run_self_audit()
        if report.passed:
            _LOG.info(
                "hydra startup self-audit PASSED — %d checks green", len(report.checks)
            )
        else:
            ids = [v.check_id for v in report.violations]
            _LOG.warning(
                "hydra startup self-audit FAILED — %d violation(s): %s",
                len(report.violations),
                ids,
            )
    except Exception as exc:
        _LOG.warning("hydra startup self-audit raised (non-fatal): %s", exc)

# ── Rich ────────────────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
    from rich.markdown import Markdown
    from rich.live import Live
    from rich.spinner import Spinner
    from rich import box

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    Console = None  # type: ignore[misc,assignment]

# ── prompt_toolkit ──────────────────────────────────────────────────────────
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import InMemoryHistory

    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False
    PromptSession = None  # type: ignore[misc,assignment]
    HTML = None           # type: ignore[misc,assignment]
    InMemoryHistory = None  # type: ignore[misc,assignment]

# ── HydraAgent core ─────────────────────────────────────────────────────────
from dataclasses import dataclass  # noqa: E402
from hydra.inter_agent import redact_value  # noqa: E402
from hydra.loop import AgentLoop, LoopStep, LoopResult, Tool  # noqa: E402
from hydra.cli.tool_binding import bind_tools  # noqa: E402
from hydra.operator_auth import OperatorAuth, OperatorAuthError, render_qr_ascii  # noqa: E402
from hydra.skill_spine import build_agent_system_prompt, list_skill_records  # noqa: E402
from hydra.intake import CONVO, classify  # noqa: E402
from hydra.intent_router import classify_intent, route_turn  # noqa: E402
from hydra.turn_profiles import load_turn_profile  # noqa: E402
from hydra.work_executor import (  # noqa: E402
    detect_runaway,
    guard_work_turn,
    is_work_kind,
    resolve_work_model,
)
from hydra.session_memory import (  # noqa: E402
    compact_session,
    create_session,
    add_message,
    get_session_messages,
    rotate_session,
    session_exists,
)

try:
    from hydra.providers import list_providers, resolve, ProviderError
except ImportError:
    list_providers = lambda: []  # noqa: E731
    resolve = None
    ProviderError = Exception

# ── Persona ──────────────────────────────────────────────────────────────────
_ELITE_PERSONA_ADDENDUM = """
═══ ELITE OPERATOR PERSONA — non-negotiable ═══

VOICE
- 8th grade reading level. Direct. Zero filler. Senior engineer who's also a friend.
- Emoji naturally when it adds signal. Never decoration.
- Work tasks → "On it 🔧" then EXECUTE immediately. No preamble.

ANTI-QUIT PROTOCOL — you do NOT bail on a task
- "I can't" is BANNED. There is always a next tool call.
- If a tool call fails: try the alternative. Examples:
    * `find` permission denied → try `grep -r` or `glob`
    * `fs_read` too big → re-call with smaller `max_bytes` or `limit`
    * `http_fetch` timed out → retry once, then try `bash curl`
    * file not found → grep for the symbol, glob for the path pattern
    * unclear path → list_directory the parent, then narrow
- After 3 failed alternate approaches on the SAME sub-goal, do this:
    1. List the 3 exact things you tried (one bullet each, what + why it failed)
    2. State the ONE specific piece of info you need from the operator
    3. Stop and wait — no apology, no "I give up", just the question
- Never end a turn with "I don't know" or "I cannot help". End with results, a
  partial answer + the question that would unblock the next step, or a tool call.
- If a request is ambiguous, ask ONE specific question — do not refuse to start.

WORK COMMITMENT
- Hard problems → spawn parallel subagents (readers, coder, reviewer, validator).
- Long jobs → narrate progress between tool calls ("read 4 of 12 files…").
- Errors → explain what happened + what you're trying next in ONE sentence.
- Confirm completion plainly in 1-2 sentences when done. No celebration spam.
"""

_BRACKETED_PASTE_START = "\x1b[200~"
_BRACKETED_PASTE_END   = "\x1b[201~"

# ── Tool icons ────────────────────────────────────────────────────────────────
_TOOL_ICONS: dict[str, str] = {
    "bash":              "💻",
    "fs_read":           "📄",
    "fs_write":          "✏️",
    "fs_edit":           "✏️",
    "grep":              "🔍",
    "glob":              "🗂️",
    "list_directory":    "📁",
    "http_fetch":        "🌐",
    "browser_navigate":  "🌐",
    "browser_snapshot":  "📸",
    "browser_click":     "🖱️",
    "browser_fill":      "⌨️",
    "skill_list":        "📚",
    "skill_search":      "🔎",
    "skill_show":        "📖",
    "skill_route":       "🗺️",
    "spawn_subagent":    "🤖",
    "spawn_subagents":   "🤖",
    "memory_remember":   "🧠",
    "memory_recall":     "🧠",
    "system_stats":      "📊",
    "todo":              "✅",
    "task_planner_orchestrate": "⚡",
}
_DEFAULT_ICON = "🔧"


@dataclass
class TokenTracker:
    """Track token usage and rough cost in real-time."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    model_name: str = ""

    def update(self, input_tok: int, output_tok: int, model: str) -> None:
        self.input_tokens += input_tok
        self.output_tokens += output_tok
        self.total_tokens = self.input_tokens + self.output_tokens
        self.model_name = model
        if "deepseek" in model.lower():
            self.estimated_cost_usd = (input_tok * 0.14 + output_tok * 0.28) / 1_000_000
        elif "qwen" in model.lower():
            self.estimated_cost_usd = (input_tok * 0.05 + output_tok * 0.15) / 1_000_000
        else:
            self.estimated_cost_usd = 0.0  # local = free

# ── Key hints for bottom toolbar ──────────────────────────────────────────────
_TOOLBAR_HTML = (
    ' <style fg="ansicyan">/help</style>'
    ' <style fg="ansibrightblack"> │ </style>'
    ' <style fg="ansicyan">/skills</style>'
    ' <style fg="ansibrightblack"> │ </style>'
    ' <style fg="ansicyan">/models</style>'
    ' <style fg="ansibrightblack"> │ </style>'
    ' <style fg="ansicyan">/mode</style>'
    ' <style fg="ansibrightblack"> │ </style>'
    ' <style fg="ansicyan">/clear</style>'
    '      '
    ' <style fg="ansibrightblack">Ctrl+C stop</style>'
    ' <style fg="ansibrightblack"> │ </style>'
    ' <style fg="ansibrightblack">Ctrl+D exit</style>'
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _short_model(model: str) -> str:
    parts = model.split("/")
    name = parts[-1] if parts else model
    return name[:20] + "…" if len(name) > 20 else name


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"


def _approx_tokens(char_count: int) -> str:
    """Rough chars-to-tokens conversion (4 chars ≈ 1 token)."""
    if char_count <= 0:
        return ""
    tok = char_count // 4
    if tok >= 1000:
        return f"~{tok / 1000:.1f}k"
    return f"~{tok}"


def _tool_summary(tool_name: str, arguments: dict | None, content: str | None) -> str:
    args = arguments or {}
    if tool_name == "bash":
        cmd = str(args.get("command", ""))
        return cmd[:72] + ("…" if len(cmd) > 72 else "")
    if tool_name in ("fs_read",):
        path = str(args.get("path", ""))
        size_hint = ""
        if content:
            try:
                data = json.loads(content)
                br = data.get("bytes_read") if isinstance(data, dict) else None
                if isinstance(br, int):
                    size_hint = f" ({_format_bytes(br)})"
            except (json.JSONDecodeError, TypeError):
                pass
        return f"{path}{size_hint}"
    if tool_name in ("fs_write", "fs_edit"):
        return str(args.get("path", ""))
    if tool_name == "grep":
        pattern = str(args.get("pattern", ""))
        match_count = ""
        if content:
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    mc = data.get("match_count") or data.get("matches")
                    if isinstance(mc, int):
                        match_count = f" found {mc} matches"
                    elif isinstance(mc, list):
                        match_count = f" found {len(mc)} matches"
            except (json.JSONDecodeError, TypeError):
                pass
        return f"{pattern!r}{match_count}"
    if tool_name == "glob":
        return str(args.get("pattern", ""))
    if tool_name in ("http_fetch", "browser_navigate"):
        return str(args.get("url", ""))
    if tool_name == "browser_snapshot":
        return "page snapshot"
    if tool_name in ("spawn_subagent", "spawn_subagents"):
        tasks = args.get("tasks") or ([args.get("task")] if args.get("task") else [])
        n = len(tasks) if isinstance(tasks, list) else 1
        return f"{n} task(s)"
    if tool_name == "skill_list":
        q = args.get("query")
        return f"query={q!r}" if q else "all skills"
    return str(args.get("path", "") or args.get("name", "") or "")


def _parse_subagent_tasks(arguments: dict | None) -> list[dict]:
    if not arguments:
        return []
    tasks = arguments.get("tasks") or []
    if isinstance(tasks, str):
        tasks = [tasks]
    model = arguments.get("model", "")
    icons   = ["🧠", "💻", "🔍", "🛠️", "📊", "🔬"]
    labels  = ["Planner", "Coder", "Reviewer", "Builder", "Analyst", "Probe"]
    result = []
    for i, task in enumerate(tasks):
        result.append({
            "icon":  icons[i % len(icons)],
            "label": labels[i % len(labels)],
            "task":  str(task)[:60],
            "model": model or "default",
        })
    return result


def _format_envelope_value(value) -> str:
    """Render a JSON-ish value as a single readable line for tables."""
    if isinstance(value, list):
        if not value:
            return "[dim]none[/dim]"
        previews = [str(v) for v in value[:6]]
        more = f" [dim](+{len(value)-6})[/dim]" if len(value) > 6 else ""
        return ", ".join(previews) + more
    if isinstance(value, dict):
        return f"{{{len(value)} keys}}"
    if isinstance(value, bool):
        return "✓" if value else "✗"
    return str(value)


def _coalesce_pasted_lines(first_line: str, extra_lines: list[str]) -> str:
    lines = [first_line, *extra_lines]
    text = "\n".join(line.rstrip("\r\n") for line in lines)
    return (
        text.replace(_BRACKETED_PASTE_START, "")
            .replace(_BRACKETED_PASTE_END, "")
            .strip()
    )


def _drain_ready_stdin_lines(
    stdin, *, idle_timeout: float = 0.05, max_lines: int = 1000
) -> list[str]:
    # select.select() works on arbitrary fds on POSIX; on Windows it only
    # works on sockets, so we skip the drain (bracketed-paste is a terminal
    # feature and doesn't apply on Windows cmd/PowerShell anyway).
    if not _HAS_SELECT or sys.platform == "win32":
        return []
    try:
        fd = stdin.fileno()
    except (AttributeError, OSError):
        return []
    lines: list[str] = []
    while len(lines) < max_lines:
        try:
            ready, _, _ = _select_mod.select([fd], [], [], idle_timeout)
        except (OSError, ValueError):
            break
        if not ready:
            break
        line = stdin.readline()
        if line == "":
            break
        lines.append(line)
        if _BRACKETED_PASTE_END in line:
            break
    return lines


# ── EliteTUI ──────────────────────────────────────────────────────────────────

class EliteTUI:
    """
    HydraAgent elite interactive REPL.
    Launch: hydra chat  (do not change this)
    """

    def __init__(
        self,
        client,
        model: str,
        cfg,
        root: Path,
        system_prompt: str,
        session_id: str | None = None,
        *,
        initial_messages: list[dict] | None = None,
        approval_policy: str = "ask",
        max_iterations: int = 12,
        timeout: float = 120.0,
        tools: list[Tool] | None = None,
        memory_root: Path | str | None = None,
        memory_workspace_root: Path | str | None = None,
        recall_builder: Callable[..., object] | None = None,
        local_memory_chars: int = 12000,
        runtime: dict | None = None,
        trace_out_path: Path | str | None = None,
        command_handler: Callable[["EliteTUI", str], bool] | None = None,
        initial_request: str | None = None,
        notify_telegram: bool = False,
    ):
        self.client = client
        self.model = model
        self.cfg = cfg
        self.root = root
        self.raw_system_prompt = system_prompt
        self.session_id = session_id
        self.model_short = _short_model(model)
        self._max_iterations = max_iterations
        self._timeout = timeout
        self.approval_policy = approval_policy
        self.memory_root = Path(memory_root).expanduser().resolve() if memory_root else None
        self.memory_workspace_root = (
            Path(memory_workspace_root).expanduser().resolve()
            if memory_workspace_root
            else None
        )
        self.recall_builder = recall_builder
        self.local_memory_chars = local_memory_chars
        self.runtime = dict(runtime or {})
        self.trace_out_path = Path(trace_out_path).expanduser().resolve() if trace_out_path else None
        self._trace_turn_index = 0
        self._command_handler = command_handler
        self._initial_request = (initial_request or "").strip() or None
        # Live-operator flag: True only when spawned via the real daemon launcher.
        # Default is False so tests and audits NEVER page the operator's Telegram.
        # Only the live launch path (cmd_chat) sets this True.
        self._notify_telegram: bool = bool(notify_telegram)

        # Provider display name
        provider_name = getattr(cfg, "name", "")
        if not provider_name and hasattr(cfg, "__class__"):
            provider_name = cfg.__class__.__name__.lower().replace("config", "")
        self.provider_name = provider_name or "hydra"

        # Rich console
        self.console: Console = Console(highlight=True)

        # Bind tools + count skills. Thread the live-operator flag through so the
        # fallback bind (no pre-built tools passed) pages the operator on a real
        # approval exactly like the live cmd_chat path. notify_telegram is honored
        # by bind_tools now (operator's bug #7) — default False keeps tests silent.
        self.tools = tools if tools is not None else bind_tools(
            root,
            approval_policy=approval_policy,
            memory_root=self.memory_root,
            notify_telegram=self._notify_telegram,
        )
        self._skill_count = len(list_skill_records())

        # System prompt
        if (
            "Hydra skill spine doctrine" in system_prompt
            and "INTER-AGENT COMMUNICATION PROTOCOL" in system_prompt
        ):
            self.system_prompt = system_prompt.rstrip() + "\n\n" + _ELITE_PERSONA_ADDENDUM
        else:
            self.system_prompt = build_agent_system_prompt(
                system_prompt + "\n" + _ELITE_PERSONA_ADDENDUM
            )

        # Session — auto-rotate on every TUI start so each launch is a fresh session
        if initial_messages is not None:
            self._chat_messages: list[dict] = list(initial_messages)
        else:
            self._chat_messages = []
            if session_id:
                if session_exists(session_id):
                    try:
                        rotate_session(session_id, "TUI restarted — new session")
                    except Exception:
                        pass
                try:
                    create_session(session_id, "HydraAgent elite TUI session")
                except Exception:
                    pass

        # System prompt at head
        if not any(m.get("role") == "system" for m in self._chat_messages):
            self._chat_messages.insert(0, {"role": "system", "content": self.system_prompt})
        else:
            for message in self._chat_messages:
                if message.get("role") == "system":
                    message["content"] = self.system_prompt
                    break

        # AgentLoop
        self._loop = AgentLoop(
            self.client,
            model=self.model,
            system_prompt=self.system_prompt,
        )

        # Per-turn tracking
        self._tool_start_times: dict[str, float] = {}
        self._parallel_agents_this_turn: list[dict] = []
        self._models_used_this_turn: list[str] = [self.model]
        # Work-executor loops cached per (provider, model) so a steering/collab
        # turn runs on the build/most-capable tool-calling executor instead of the
        # chat model. Keyed by the resolved (provider, model) pair. Built
        # lazily and fail-soft: if the executor can't be built we fall back to the
        # chat loop (still guarded against confabulation).
        self._work_loops: dict[tuple[str, str], "AgentLoop"] = {}
        self._turn_had_tools: bool = False
        self._turn_start_time: float = 0.0
        self._current_status: str = "thinking"
        self._live_status: Live | None = None

        # Cumulative stats shown in the stat bar
        self._token_tracker = TokenTracker()
        self._cumulative_chars: int = 0      # chars → approx tokens
        self._cumulative_iterations: int = 0
        self._cumulative_tool_calls: int = 0

        # Auth + session screen transcript
        self._auth = OperatorAuth()
        self._screen_transcript: list[str] = []

        # prompt_toolkit session for readline-quality input
        self._prompt_session = None
        if PROMPT_TOOLKIT_AVAILABLE and sys.stdin.isatty():
            try:
                self._prompt_session = PromptSession(history=InMemoryHistory())
            except Exception:
                pass

        # Self-audit at startup — log-only, non-blocking, never raises.
        _startup_self_audit()

    # ── Public entry ──────────────────────────────────────────────────────────

    def _launch_textual_app(self, HydraApp) -> None:
        """Opt-in Textual full-screen mode. Set HYDRA_TEXTUAL_TUI=1."""
        def _persist(user: str, reply: str) -> None:
            if not self.session_id:
                return
            try:
                add_message(self.session_id, "user", user)
                if reply.strip():
                    add_message(self.session_id, "assistant", reply)
                compact_session(self.session_id)
            except Exception:
                pass

        def _route_command(text: str) -> bool:
            if self._command_handler is not None:
                try:
                    return bool(self._command_handler(self, text))
                except Exception:
                    return False
            return False

        app = HydraApp(
            client=self.client,
            model=self.model,
            cfg=self.cfg,
            system_prompt=self.system_prompt,
            tools=self.tools,
            workspace_root=self.root,
            on_persist=_persist,
            on_command=_route_command,
            max_iterations=self._max_iterations,
            timeout=self._timeout,
            initial_request=self._initial_request,
            initial_messages=self._chat_messages,
            notify_telegram=self._notify_telegram,
        )
        app.run(mouse=False)
        # Terminal is restored at this point — safe to re-exec if /restart was used.
        app.maybe_reexec()

    def run(self) -> None:
        """Launch the interactive TUI.

        Default: the Textual HydraApp (dragon, scrolling runtime, fixed
        chat footer, stat bar). Plain-chat works via the fabric §5 intake
        classifier — convo turns skip tools and just talk back.
        Set ``HYDRA_LEGACY_REPL=1`` to force the linear Rich/prompt_toolkit
        REPL instead (useful when terminal copy/paste needs to be native).
        """
        if os.environ.get("HYDRA_LEGACY_REPL") != "1":
            try:
                from gateways.tui.hydra_app import HydraApp
            except ImportError:
                HydraApp = None  # type: ignore[assignment]
            if HydraApp is not None and sys.stdin.isatty() and sys.stdout.isatty():
                self._launch_textual_app(HydraApp)
                return

        # Fallback — linear REPL (legacy)
        self._print_welcome()

        # Bracketed paste only in fallback (non-prompt_toolkit) mode
        self._set_bracketed_paste(self._prompt_session is None)
        try:
            # If launched with an initial request (e.g., `spawn-hydra "build X"`),
            # consume it as the first turn before going interactive.
            pending_initial = self._initial_request
            self._initial_request = None

            while True:
                if pending_initial:
                    user_input = pending_initial
                    pending_initial = None
                    if RICH_AVAILABLE:
                        self.console.print(
                            f"\n[bold cyan]\\[operator][/bold cyan] [dim]{user_input}[/dim]"
                        )
                else:
                    try:
                        user_input = self._read_operator_input()
                    except (KeyboardInterrupt, EOFError):
                        self.console.print("\n[dim]👋 Hydra signing off.[/dim]")
                        return

                stripped = user_input.strip()
                if not stripped:
                    continue

                # ── Slash commands ──────────────────────────────────────────
                if stripped.lower() in {"/exit", "/quit", "stop"}:
                    self.console.print("[dim]👋 Hydra signing off.[/dim]")
                    return
                if stripped == "/help":
                    self._print_help()
                    continue
                if stripped.startswith("/skills"):
                    query = stripped[len("/skills"):].strip() or None
                    self._cmd_skills(query)
                    continue
                if stripped == "/models":
                    self._cmd_models()
                    continue
                if stripped == "/audit":
                    self._cmd_audit()
                    continue
                if stripped == "/capabilities":
                    self._cmd_capabilities()
                    continue
                if stripped == "/clear":
                    self.console.clear()
                    self._print_header()
                    continue
                if self._handle_auth_command(stripped):
                    continue
                if self._handle_session_command(stripped):
                    continue
                if self._command_handler is not None and self._command_handler(self, stripped):
                    continue

                # ── Agent turn ─────────────────────────────────────────────
                try:
                    reply = self._stream_turn(stripped)
                except KeyboardInterrupt:
                    self.console.print("\n[yellow]⚡ Interrupted.[/yellow]")
                    continue
                except Exception as exc:
                    self.console.print(f"[bold red]Turn error:[/bold red] {exc}")
                    continue

                # Persist
                if self.session_id:
                    try:
                        add_message(self.session_id, "user", stripped)
                        if reply.strip():
                            add_message(self.session_id, "assistant", reply)
                        compact_session(self.session_id)
                    except Exception:
                        pass

                # Multi-model usage line
                if len(self._models_used_this_turn) > 1:
                    models_str = " + ".join(
                        f"{_short_model(m)} ({'chat' if i == 0 else 'tools'})"
                        for i, m in enumerate(self._models_used_this_turn[:2])
                    )
                    self.console.print(f"[dim]💡 {models_str}[/dim]")

        finally:
            self._set_bracketed_paste(False)

    # ── Input ─────────────────────────────────────────────────────────────────

    def _read_operator_input(self) -> str:
        """Print stat bar then read one operator turn."""
        self._print_stat_rule()

        if self._prompt_session is not None:
            try:
                toolbar = HTML(_TOOLBAR_HTML) if HTML is not None else None
                prompt_text = (
                    HTML(
                        f'<style fg="ansicyan">[🐉 {self.model_short}]</style>'
                        f' <style fg="ansiwhite">❯</style> '
                    )
                    if HTML is not None
                    else f"[🐉 {self.model_short}] ❯ "
                )
                return self._prompt_session.prompt(
                    prompt_text,
                    bottom_toolbar=toolbar,
                )
            except KeyboardInterrupt:
                raise
            except EOFError:
                raise

        # Fallback: plain readline
        if RICH_AVAILABLE:
            self.console.print(
                f"[bold cyan]\\[🐉 {self.model_short}][/bold cyan] "
                f"[bright_white]❯[/bright_white] ",
                end="",
            )
        else:
            print(f"[🐉 {self.model_short}] ❯ ", end="", flush=True)

        first_line = sys.stdin.readline()
        if first_line == "":
            raise EOFError
        extra_lines: list[str] = []
        if sys.stdin.isatty():
            extra_lines = _drain_ready_stdin_lines(sys.stdin)
        return _coalesce_pasted_lines(first_line, extra_lines)

    @staticmethod
    def _set_bracketed_paste(enabled: bool) -> None:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return
        sys.stdout.write("\x1b[?2004h" if enabled else "\x1b[?2004l")
        sys.stdout.flush()

    # ── Stat bar (demarcation line before input) ──────────────────────────────

    def _print_stat_rule(self) -> None:
        """Print the ─── stat ─── separator line that marks the input boundary."""
        if not RICH_AVAILABLE:
            return
        tok = _approx_tokens(self._cumulative_chars)
        mode = self._auth.status().mode
        mode_color = {
            "yolo":      "bright_green",
            "iteration": "yellow",
            "operator":  "cyan",
        }.get(mode, "white")

        parts: list[str] = []
        if tok:
            parts.append(f"[bold cyan]🧠[/bold cyan] [white]{tok}[/white]")
        if self._cumulative_iterations > 0:
            parts.append(f"[yellow]⚡[/yellow] [white]{self._cumulative_iterations}[/white]")
        if self._cumulative_tool_calls > 0:
            parts.append(f"[bright_magenta]🛠[/bright_magenta] [white]{self._cumulative_tool_calls}[/white]")
        parts.append(f"[bright_cyan]🐉[/bright_cyan] [dim]{self.model_short}[/dim]")
        parts.append(f"[blue]📡[/blue] [dim]{self.provider_name}[/dim]")
        parts.append(f"[{mode_color}]🔐 {mode}[/{mode_color}]")
        stat_text = "  " + "  [bright_black]·[/bright_black]  ".join(parts) + "  "
        self.console.print(Rule(stat_text, style="bright_black", characters="─"))

    # ── Core turn runner ──────────────────────────────────────────────────────

    def _loop_for_kind(self, kind: str) -> tuple["AgentLoop", str]:
        """Return the (loop, model) to run for this turn's intake kind.

        WORK turns (steering/collab — profiles that declare an ``executor``) run on
        the build / most-capable tool-calling executor resolved from model_routing
        (cloud-planner), NOT the chat model that only narrates. This is the
        fix for the confabulation bug: the chat model would print "I successfully
        connected to the remote server" with ZERO tool calls; the executor actually calls tools.

        Fail-soft: if the work model can't be resolved/built (offline, bad config)
        we fall back to the default chat loop — the no-confabulation guard still
        protects the turn, so a fake "done" never reaches the operator.
        """
        if not is_work_kind(kind):
            return self._loop, self.model
        pair = resolve_work_model(kind)
        if pair is None:
            return self._loop, self.model
        provider, model = pair
        # Same provider+model as the chat brain → reuse the existing loop.
        if model == self.model:
            return self._loop, self.model
        cached = self._work_loops.get(pair)
        if cached is not None:
            return cached, model
        try:
            from hydra.providers import make_client

            client, _cfg = make_client(provider)
            work_loop = AgentLoop(client, model=model, system_prompt=self.system_prompt)
        except Exception:
            # Executor unavailable — keep the chat loop; the guard still applies.
            return self._loop, self.model
        self._work_loops[pair] = work_loop
        return work_loop, model

    # ── Intent client (lazy, cached) ──────────────────────────────────────────

    _intent_client_cache: object | None = None

    def _intent_client(self):
        """Lazily build and cache a client for the 'intent' role.

        Uses make_client with the provider from model_routing 'intent' role.
        On any error the caller's classify_intent() try/except fails safe to task.
        """
        if self._intent_client_cache is not None:
            return self._intent_client_cache
        try:
            from hydra.model_routing import load_routing
            from hydra.providers import make_client
            routing = load_routing()
            entry = routing.role_entry("intent")
            client, _ = make_client(entry.provider)
            self._intent_client_cache = client
            return client
        except Exception:
            # Fail safe: if the intent client can't be built, return None;
            # classify_intent() will get an AttributeError and fail safe to task.
            return None

    # ── Chat draft (side-effect-free) ─────────────────────────────────────────

    def _chat_draft(self, user_input: str, initial_messages: list[dict]) -> str:
        """Run the convo loop and return the text reply. Side-effect-free (tools=[]).

        Injects the CONVO_NO_TOOLS_GUARD. Used as the speculative draft in
        route_turn so that when intent=='chat', the response is already ready.
        """
        from hydra.turn_profiles import load_turn_profile as _ltp
        profile = _ltp("convo")
        msgs = [
            m for m in initial_messages
            if not (
                isinstance(m.get("content"), str)
                and "CONVO_NO_TOOLS_GUARD" in m.get("content", "")
            )
        ]
        msgs.append({
            "role": "system",
            "content": (
                "CONVO_NO_TOOLS_GUARD — THIS IS A CONVERSATIONAL TURN. "
                "No tools are available. Do NOT emit any tool call or function call. "
                "Answer the operator's question directly in plain text. "
                "Do not route to any skill. Just talk."
            ),
        })
        result = self._loop.run(
            user_input,
            tools=[],
            max_iterations=profile.max_iterations,
            max_tokens=profile.max_tokens,
            temperature=profile.temperature,
            timeout=min(self._timeout, 30.0),
            initial_messages=msgs,
            on_step=None,
        )
        # Accumulate stats from the chat draft run
        self._cumulative_iterations += result.iterations
        self._cumulative_tool_calls += result.tool_calls_made
        for m in result.messages:
            content = m.get("content")
            if isinstance(content, str):
                self._cumulative_chars += len(content)
        return result.final_response or ""

    # ── Work turn runner (steering/collab) ────────────────────────────────────

    def _run_work_turn(
        self,
        user_input: str,
        initial_messages: list[dict],
        *,
        intake_kind: str = "steering",
    ) -> str:
        """Run the work executor path for a steering/collab turn.

        Loads the profile, picks the work loop, runs with tools, applies
        detect_runaway + guard_work_turn. Returns the final (non-confabulated) text.
        """
        profile = load_turn_profile(intake_kind)
        active_loop, active_model = self._loop_for_kind(intake_kind)
        if active_model != self.model and active_model not in self._models_used_this_turn:
            self._models_used_this_turn.append(active_model)

        if RICH_AVAILABLE:
            spinner_renderable = self._render_status()
            with Live(
                spinner_renderable,
                console=self.console,
                refresh_per_second=10,
                transient=True,
            ) as live:
                self._live_status = live
                try:
                    result: LoopResult = active_loop.run(
                        user_input,
                        tools=self.tools if profile.tools_enabled else [],
                        max_iterations=profile.max_iterations,
                        max_tokens=profile.max_tokens,
                        temperature=profile.temperature,
                        timeout=self._timeout,
                        initial_messages=initial_messages,
                        on_step=self._on_step,
                    )
                finally:
                    self._live_status = None
        else:
            result = active_loop.run(
                user_input,
                tools=self.tools if profile.tools_enabled else [],
                max_iterations=profile.max_iterations,
                max_tokens=profile.max_tokens,
                temperature=profile.temperature,
                timeout=self._timeout,
                initial_messages=initial_messages,
                on_step=self._on_step,
            )

        self._cumulative_iterations += result.iterations
        self._cumulative_tool_calls += result.tool_calls_made
        for m in result.messages:
            content = m.get("content")
            if isinstance(content, str):
                self._cumulative_chars += len(content)

        final = result.final_response or ""

        # Cap runaway/degenerate output, then block confabulation.
        runaway = detect_runaway(final, max_tokens=profile.max_tokens)
        if runaway is not None:
            final = runaway.capped_text

        def _rerun_forcing_tools(force: bool) -> tuple[str, int]:
            """Force one re-prompt: CALL the tool or admit it wasn't done."""
            forced = list(initial_messages)
            forced.append({
                "role": "system",
                "content": (
                    "NO-CONFABULATION CHECK — your previous reply CLAIMED you "
                    "performed an action but you made ZERO tool calls, so it did "
                    "NOT actually happen. You MUST now either (a) CALL the tool "
                    "to really do it, or (b) state plainly that you have NOT done "
                    "it yet. Do NOT claim success again without calling a tool."
                ),
            })
            r2 = active_loop.run(
                user_input,
                tools=self.tools if profile.tools_enabled else [],
                max_iterations=profile.max_iterations,
                max_tokens=profile.max_tokens,
                temperature=profile.temperature,
                timeout=self._timeout,
                initial_messages=forced,
                on_step=self._on_step,
            )
            self._cumulative_iterations += r2.iterations
            self._cumulative_tool_calls += r2.tool_calls_made
            return (r2.final_response or "", r2.tool_calls_made)

        outcome = guard_work_turn(
            kind=intake_kind,
            final_text=final,
            tool_calls_made=result.tool_calls_made,
            rerun=_rerun_forcing_tools,
        )
        return outcome.text

    def _stream_turn(self, user_input: str, *, source: str = "operator") -> str:
        """Run one AgentLoop turn, streaming output inline. Returns final reply.

        ``source`` is the intake source identifier — "operator" for the local
        operator, "peer:alpha" / "peer:beta" for fabric peers. It is passed
        through to ``classify`` so peer messages correctly route as collab.

        Decision path:
          - classify() still runs to detect peer→collab vs operator→intent check.
          - For operator turns: route_turn() fans out a cloud intent check and a
            speculative convo draft in parallel. chat → emit the ready draft,
            task → discard the draft and run the cloud-qwen work executor.
          - For peer/collab turns: always go straight to the work executor.
        """
        self._parallel_agents_this_turn = []
        self._models_used_this_turn = [self.model]
        self._turn_had_tools = False
        self._tool_start_times = {}
        self._screen_transcript.append(f"operator: {user_input}")
        self._turn_start_time = time.monotonic()
        self._current_status = "thinking"

        # Fabric §5: classify with real source so peer→collab, operator→convo/steering.
        intake = classify(user_input, source=source)
        # Load the declarative turn profile — all behavior comes from here.
        profile = load_turn_profile(intake.kind)
        is_work = is_work_kind(intake.kind)

        initial_messages = list(self._chat_messages)

        # Per-turn, query-aware memory recall.  Gated by profile.memory.enabled
        # so convo turns never call recall_builder (execute-bias fix).
        # The ranked block is appended to this LOCAL copy only (never
        # self._chat_messages) so each turn recalls by its OWN query.
        if profile.memory.enabled and self.recall_builder is not None and self.memory_root is not None:
            try:
                recall = self.recall_builder(
                    user_input,
                    root=self.memory_root,
                    workspace_root=self.memory_workspace_root,
                )
                if getattr(recall, "status", None) == "OK" and getattr(recall, "context", ""):
                    initial_messages.append(
                        {"role": "system", "content": recall.context}
                    )
            except Exception:
                # Recall is best-effort; chat must never break on a memory miss.
                pass

        self.console.print()

        # ── Route the turn ───────────────────────────────────���────────────────
        # For peer/collab turns: skip intent check, always run the work executor.
        # For operator turns: use route_turn() to decide task vs chat in parallel
        # with a speculative chat draft. The chat/work switch is the MODEL's
        # decision (via classify_intent), not the keyword classifier.
        if is_work:
            # Peer collab or keyword-classified work — go straight to executor.
            final = self._run_work_turn(
                user_input, initial_messages, intake_kind=intake.kind
            )
        else:
            # Operator turn: fan out intent check + speculative chat draft.
            # Both run in parallel; chat -> emit draft, task -> run executor.
            _intent_client = self._intent_client()

            def _intent_fn(msg: str):
                return classify_intent(msg, client=_intent_client)

            def _chat_draft_fn(msg: str) -> str:
                return self._chat_draft(msg, initial_messages)

            route = route_turn(user_input, intent_fn=_intent_fn, chat_draft_fn=_chat_draft_fn)

            if route.intent == "chat":
                # Chat path: the draft is already ready (zero added latency).
                final = route.chat_draft or ""
            else:
                # Model says task — discard the draft, run the full work executor.
                # Use "steering" as the kind for operator-directed work turns.
                final = self._run_work_turn(
                    user_input, initial_messages, intake_kind="steering"
                )

        if final:
            self._screen_transcript.append(f"assistant: {final}")

        # Final response rendered as markdown in a styled panel
        if final and RICH_AVAILABLE:
            elapsed = time.monotonic() - self._turn_start_time
            self.console.print()
            self.console.print(
                Panel(
                    self._render_final(final),
                    title=f"[bold bright_cyan]🐉 {self.model_short}[/bold bright_cyan]",
                    title_align="left",
                    subtitle=f"[dim]{elapsed:.1f}s • {self._cumulative_iterations} iter • {self._cumulative_tool_calls} tools[/dim]",
                    subtitle_align="right",
                    border_style="bright_cyan",
                    padding=(0, 1),
                )
            )
        elif final:
            print(final)

        # Update chat history
        self._chat_messages.append({"role": "user", "content": user_input})
        self._chat_messages.append({"role": "assistant", "content": final})

        if self._parallel_agents_this_turn:
            self._print_parallel_summary(None)

        self._write_trace_turn_text(user_input, final)
        return final

    def _render_status(self):
        """Build a live-updating status renderable shown during agent work."""
        elapsed = time.monotonic() - self._turn_start_time
        spinner = Spinner("dots", text=Text(
            f" {self._current_status}…  [{elapsed:.1f}s]",
            style="bright_cyan",
        ))
        return spinner

    def _render_final(self, text: str):
        """Render final assistant response — markdown if it looks like markdown."""
        looks_markdown = any(
            marker in text
            for marker in ("```", "**", "## ", "* ", "- ", "`", "[", "|")
        )
        if looks_markdown:
            try:
                return Markdown(text, code_theme="monokai")
            except Exception:
                pass
        return Text(text)

    def _update_status(self, status: str) -> None:
        self._current_status = status
        if self._live_status is not None:
            try:
                self._live_status.update(self._render_status())
            except Exception:
                pass

    def reconfigure_runtime(
        self, *, client, cfg, model: str, runtime: dict | None = None
    ) -> None:
        """Swap backing client/model after a profile slash command."""
        self.client = client
        self.cfg = cfg
        self.model = model
        self.model_short = _short_model(model)
        self.provider_name = getattr(cfg, "name", None) or self.provider_name
        if runtime is not None:
            self.runtime = dict(runtime)
        self._models_used_this_turn = [self.model]
        self._loop = AgentLoop(
            self.client,
            model=self.model,
            system_prompt=self.system_prompt,
        )

    def _write_trace_turn(self, user_input: str, result: LoopResult) -> None:
        if self.trace_out_path is None:
            return
        self.trace_out_path.parent.mkdir(parents=True, exist_ok=True)
        self._trace_turn_index += 1
        tool_steps = [
            {
                "tool_name": step.tool_name,
                "tool_call_id": step.tool_call_id,
                "duration_ms": step.tool_duration_ms,
                "tool_error": step.tool_error,
                "trace_id": step.trace_id,
            }
            for step in result.steps
            if step.kind == "tool_result"
        ]
        payload = redact_value(
            {
                "schema": "hydra.chat_trace_turn.v1",
                "turn_index": self._trace_turn_index,
                "prompt": user_input,
                "provider": self.provider_name,
                "model": self.model,
                "trace_id": result.trace_id,
                "iterations": result.iterations,
                "tool_calls_made": result.tool_calls_made,
                "halted_reason": result.halted_reason,
                "final_response": result.final_response,
                "tool_steps": tool_steps,
                "approval_policy": self.approval_policy,
            }
        )
        with self.trace_out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")

    # ── Step callback ─────────────────────────────────────────────────────────

    def _on_step(self, step: LoopStep) -> None:
        """Called by AgentLoop for each step. Renders tool calls inline."""
        if step.kind == "assistant":
            content = (step.content or "").strip()
            if content and not step.content.startswith('{"'):
                preview = content[:120] + ("…" if len(content) > 120 else "")
                self._print_above_live(
                    f"  [dim italic blue]│[/dim italic blue] [dim italic]{preview}[/dim italic]"
                )
            self._update_status("thinking")
            return

        if step.kind != "tool_result":
            return

        tool_name = step.tool_name or "unknown"
        icon = _TOOL_ICONS.get(tool_name, _DEFAULT_ICON)
        args = step.tool_arguments or {}
        duration_s = (
            step.tool_duration_ms / 1000.0
            if isinstance(step.tool_duration_ms, int)
            else None
        )
        duration_str = f"[dim] ({duration_s:.1f}s)[/dim]" if duration_s is not None else ""

        # Update live status to current tool
        self._update_status(f"{icon} {tool_name}")

        # ── Spawn panel ─────────────────────────────────────────────────────
        if tool_name in ("spawn_subagents", "spawn_subagent"):
            agents = _parse_subagent_tasks(args)
            self._parallel_agents_this_turn.extend(agents)
            self._render_spawn_panel(agents, duration_str)
            for agent in agents:
                if agent["model"] not in self._models_used_this_turn:
                    self._models_used_this_turn.append(agent["model"])
            self._turn_had_tools = True
            return

        # ── Browser / http_fetch panel ───────────────────────────────────────
        if tool_name in ("http_fetch", "browser_navigate"):
            url = str(args.get("url", ""))
            status = None
            if step.content:
                try:
                    data = json.loads(step.content)
                    status = data.get("status_code") or data.get("status")
                except (json.JSONDecodeError, TypeError):
                    pass
            self._render_browser_bar(url, status, step.tool_error, duration_str)
            self._turn_had_tools = True
            return

        # ── Browser screenshot ───────────────────────────────────────────────
        if tool_name == "browser_snapshot":
            self._print_above_live(
                f"  [blue]│[/blue] [blue]📸[/blue] [bold]snapshot[/bold] [dim]captured[/dim]{duration_str}"
            )
            self._turn_had_tools = True
            return

        # ── Generic tool: error vs success ──────────────────────────────────
        summary = _tool_summary(tool_name, args, step.content)
        self._screen_transcript.append(
            f"{'❌' if step.tool_error else '✅'} {tool_name}: {summary}"
        )
        if step.tool_error:
            self._print_above_live(
                f"  [red]│[/red] [red]✗[/red] [bold]{tool_name}[/bold] [dim]›[/dim] "
                f"[red]{summary or step.tool_error[:60]}[/red]{duration_str}"
            )
        else:
            self._print_above_live(
                f"  [bright_cyan]│[/bright_cyan] [green]✓[/green] {icon} [bold]{tool_name}[/bold] [dim]›[/dim] "
                f"[dim white]{summary}[/dim white]{duration_str}"
            )
        self._turn_had_tools = True

    def _print_above_live(self, msg: str) -> None:
        """Print a line above the live spinner without disturbing it."""
        if self._live_status is not None:
            self._live_status.console.print(msg)
        else:
            self.console.print(msg)

    # ── Browser URL-bar panel ─────────────────────────────────────────────────

    def _render_browser_bar(
        self,
        url: str,
        status: int | None,
        error: str | None,
        duration_str: str,
    ) -> None:
        """Render a browser-style address bar for web fetches."""
        display_url = url if len(url) <= 76 else url[:74] + "…"
        if error:
            status_part = f" [red]✗ {error[:40]}[/red]"
        elif status:
            color = "green" if status < 400 else "yellow" if status < 500 else "red"
            status_part = f" [{color}]{status}[/{color}]"
        else:
            status_part = ""

        self.console.print(
            Panel(
                f"🌐  [bold cyan]{display_url}[/bold cyan]{status_part}"
                f"[dim]{duration_str}[/dim]",
                box=box.HORIZONTALS,
                border_style="blue",
                padding=(0, 1),
            )
        )

    # ── Spawn agent grid ──────────────────────────────────────────────────────

    def _render_spawn_panel(self, agents: list[dict], duration_str: str) -> None:
        """Render a compact parallel-agents grid."""
        if not agents:
            self.console.print(
                f"  🤖 [bold]Spinning up parallel agents...[/bold]{duration_str}"
            )
            return

        grid = Table.grid(padding=(0, 2), expand=False)
        grid.add_column(width=3)   # icon
        grid.add_column(width=12)  # label
        grid.add_column(width=26)  # model
        grid.add_column()           # task snippet

        for agent in agents:
            grid.add_row(
                agent["icon"],
                f"[bold]{agent['label']}[/bold]",
                f"[dim cyan]{_short_model(agent['model'])}[/dim cyan]",
                f"[dim]{agent['task'][:50]}[/dim]",
            )

        self.console.print(
            Panel(
                grid,
                title=f"[bold magenta]🤖 Parallel Agents ({len(agents)}){duration_str}[/bold magenta]",
                border_style="bright_magenta",
                padding=(0, 1),
            )
        )

    # ── Welcome / header ──────────────────────────────────────────────────────

    def _print_header(self) -> None:
        tool_count = len(self.tools)
        status_text = "🟢 online"
        if RICH_AVAILABLE:
            header = Table(
                box=box.DOUBLE_EDGE,
                border_style="bright_cyan",
                show_header=False,
                padding=(0, 1),
            )
            header.add_column(justify="left", style="bold bright_white")
            header.add_column(justify="right", style="dim")
            header.add_row(
                "🐉  H Y D R A  A G E N T",
                f"{self.model_short}  •  {self.provider_name}  •  {status_text}",
            )
            header.add_row(
                f"🛠️  {tool_count} tools  •  📚 {self._skill_count}+ skills  •  🤖 subagents",
                "",
            )
            self.console.print(header)

    def _print_welcome(self) -> None:
        tool_count = len(self.tools)

        if RICH_AVAILABLE:
            # Gradient ASCII dragon banner
            banner_lines = [
                "[bright_cyan]    ▄█▄  [/bright_cyan][bold bright_white]H Y D R A[/bold bright_white]  [bright_magenta]▄█▄[/bright_magenta]",
                "[cyan]   ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀[/cyan]",
            ]
            for line in banner_lines:
                self.console.print(f"  {line}")

            # Status row — pill-style badges
            badges = Table.grid(padding=(0, 1), expand=False)
            badges.add_column()
            badges.add_column()
            badges.add_column()
            badges.add_column()
            badges.add_row(
                f"[on grey15] 🐉 [bold bright_cyan]{self.model_short}[/bold bright_cyan] [/on grey15]",
                f"[on grey15] 📡 [magenta]{self.provider_name}[/magenta] [/on grey15]",
                f"[on grey15] 🛠 [yellow]{tool_count}[/yellow] tools [/on grey15]",
                f"[on grey15] 📚 [green]{self._skill_count}[/green]+ skills [/on grey15]",
            )
            self.console.print()
            self.console.print(badges)
            self.console.print(
                "  [dim]🤖 spawnable agents  •  🌐 browser  •  ⚡ parallel workers  •  🧠 1432+ skills[/dim]"
            )
            self.console.print()
            self.console.print(
                "  [dim italic]Type a task or question  •  [bright_cyan]/help[/bright_cyan] for commands"
                "  •  [bright_black]Ctrl+C[/bright_black] interrupt  •  [bright_black]Ctrl+D[/bright_black] exit[/dim italic]"
            )
            self.console.print()
        else:
            print("╔══════════════════════════════════════════════════════════╗")
            print("║  🐉  H Y D R A  A G E N T                               ║")
            print(f"║  {self.model_short}  •  {self.provider_name}  •  {status_text}")
            print(f"║  🛠️  {tool_count} tools  •  📚 {self._skill_count}+ skills  •  🤖 agents ║")
            print("╚══════════════════════════════════════════════════════════╝")
            print("  Type a task, ask a question, or use /help • Ctrl+C to exit")
            print()

    # ── Slash command handlers ────────────────────────────────────────────────

    def _cmd_skills(self, query: str | None) -> None:
        records = list_skill_records()
        total = len(records)
        if query:
            q_lower = query.lower()
            records = [
                r for r in records
                if q_lower in r.name.lower()
                or q_lower in (r.description or "").lower()
                or q_lower in (r.summary or "").lower()
            ]
        shown = records[:25]

        if RICH_AVAILABLE:
            tbl = Table(
                box=box.SIMPLE_HEAD,
                border_style="dim",
                title=f"Showing {len(shown)} of {total}+ skills"
                + (f" matching {query!r}" if query else ""),
                title_style="bold cyan",
                show_lines=False,
            )
            tbl.add_column("Name", style="bold", max_width=28)
            tbl.add_column("Description", style="dim", max_width=60)
            for r in shown:
                desc = (r.description or r.summary or "")[:80]
                tbl.add_row(r.name, desc)
            self.console.print(tbl)
        else:
            print(f"Skills ({len(shown)} of {total}+):")
            for r in shown:
                print(f"  {r.name:28s}  {(r.description or '')[:60]}")

    def _cmd_models(self) -> None:
        if RICH_AVAILABLE:
            tbl = Table(
                box=box.SIMPLE_HEAD,
                border_style="dim",
                title="Model Matrix",
                title_style="bold cyan",
            )
            tbl.add_column("Role", style="bold")
            tbl.add_column("Model", style="cyan")
            tbl.add_column("Provider", style="dim")
            tbl.add_row("Chat (primary)", self.model_short, self.provider_name)
            tbl.add_row("Tools / workers", "qwen2.5-coder:7b", "ollama-local")
            tbl.add_row("Planner", "deepseek-v3.2", "ollama-cloud")
            self.console.print(tbl)
        else:
            print(f"Chat:    {self.model_short}  ({self.provider_name})")

    def _cmd_audit(self) -> None:
        """Delegate to native Go harness for fast repo audit."""
        from hydra.go_bridge import GoBridge

        self.console.print("[dim]🔍 running Go repo audit…[/dim]")
        result = GoBridge(repo_root=self.root).audit()
        self._render_go_envelope("Repo Audit", result, key_columns=[
            ("language_stack",        "Languages"),
            ("build_system",          "Build"),
            ("test_commands",         "Tests"),
            ("existing_cli_entrypoints", "CLI entry"),
            ("config_file_count",     "Configs"),
            ("docs_count",            "Docs"),
        ])

    def _cmd_capabilities(self) -> None:
        """Delegate to native Go harness for capability discovery."""
        from hydra.go_bridge import GoBridge

        self.console.print("[dim]🔍 discovering capabilities…[/dim]")
        result = GoBridge(repo_root=self.root).capabilities()
        self._render_go_envelope("Capabilities", result, key_columns=[
            ("binaries",         "Binaries"),
            ("local_services",   "Services"),
            ("skill_path_count", "Skills"),
            ("mcp_configs",      "MCP"),
        ])

    def _render_go_envelope(
        self,
        title: str,
        envelope: dict,
        *,
        key_columns: list[tuple[str, str]],
    ) -> None:
        """Render a GoBridge JSON envelope as a rich table."""
        if not RICH_AVAILABLE:
            import json as _json
            print(f"{title}:\n{_json.dumps(envelope, indent=2)[:2000]}")
            return

        status = envelope.get("status", "?")
        if status != "ok":
            reason = envelope.get("reason") or envelope.get("stderr") or "unknown"
            self.console.print(
                Panel(
                    f"[red]Go runtime {status}[/red] [dim]›[/dim] [yellow]{reason}[/yellow]\n"
                    f"[dim]hint: run `make build-go` to compile the binary[/dim]",
                    title=f"[bold red]{title}[/bold red]",
                    border_style="red",
                )
            )
            return

        tbl = Table(
            box=box.SIMPLE_HEAD,
            border_style="dim",
            title=title,
            title_style="bold cyan",
            show_lines=False,
        )
        tbl.add_column("Field", style="bold cyan", no_wrap=True)
        tbl.add_column("Value", style="white")
        for key, label in key_columns:
            value = envelope.get(key)
            if value is None:
                continue
            tbl.add_row(label, _format_envelope_value(value))
        self.console.print(tbl)

    def _print_help(self) -> None:
        commands = [
            ("/skills [query]",                "Browse 1400+ skill library"),
            ("/models",                        "Show current model matrix"),
            ("/audit",                         "Run Go repo audit (fast, JSON-out)"),
            ("/capabilities",                  "Discover binaries, services, skills, MCP"),
            ("/root PATH",                     "Change filesystem scope"),
            ("/session status|new|compact",    "Inspect, rotate, or compact chat history"),
            ("/mode  /mode iteration",         "Inspect or set operator mode"),
            ("/mode yolo CODE  /lock",         "Unlock or leave yolo mode"),
            ("/mfa setup",                     "Print Google Authenticator QR/URI"),
            ("/runtime  /providers",           "Show routing/provider state"),
            ("/cloud  /local",                 "Switch chat profile"),
            ("/skill NAME",                    "Load a trusted skill into context"),
            ("/clear",                         "Clear screen"),
            ("/help",                          "Show this help"),
            ("/exit  /quit  stop",             "Leave the REPL"),
        ]
        if RICH_AVAILABLE:
            tbl = Table(
                box=box.SIMPLE_HEAD,
                border_style="dim",
                show_header=True,
                title="Slash Commands",
                title_style="bold cyan",
            )
            tbl.add_column("Command", style="bold cyan", no_wrap=True)
            tbl.add_column("What it does", style="dim")
            for cmd, desc in commands:
                tbl.add_row(cmd, desc)
            self.console.print(tbl)
        else:
            for cmd, desc in commands:
                print(f"  {cmd:34s}  {desc}")

    # ── Auth handlers ─────────────────────────────────────────────────────────

    def _handle_auth_command(self, stripped: str) -> bool:
        lower = stripped.strip().lower()
        if lower == "/mode":
            self._print_mode_status()
            return True
        if lower in {"/lock", "/mode operator"}:
            status = self._auth.lock()
            self.console.print(f"[green]Mode set to {status.mode}.[/green]")
            return True
        if lower == "/mode iteration":
            status = self._auth.set_mode("iteration")
            self.console.print(f"[green]Mode set to {status.mode}.[/green]")
            return True
        if lower.startswith("/mode yolo ") or lower.startswith("/yolo "):
            code = stripped.split(maxsplit=2)[-1].strip()
            try:
                status = self._auth.unlock_yolo(code)
            except OperatorAuthError as exc:
                self.console.print(f"[red]Yolo unlock failed:[/red] {exc}")
                return True
            minutes = max(1, int((status.expires_in_seconds or 0) / 60))
            self.console.print(
                f"[bold green]Yolo unlocked.[/bold green] "
                f"Local and network authority active for about {minutes} minutes."
            )
            return True
        if lower == "/mfa setup":
            setup = self._auth.setup_totp(force=False)
            qr = render_qr_ascii(setup.provisioning_uri)
            self.console.print("[bold cyan]Google Authenticator setup[/bold cyan]")
            if qr:
                self.console.print(qr)
            else:
                self.console.print("[yellow]QR rendering needs the optional qrcode package.[/yellow]")
            self.console.print(f"[dim]Secret file:[/dim] {setup.secret_path}")
            self.console.print(f"[dim]Manual URI:[/dim] {setup.provisioning_uri}")
            return True
        return False

    def _print_mode_status(self) -> None:
        status = self._auth.status()
        if status.yolo_active:
            minutes = max(1, int((status.expires_in_seconds or 0) / 60))
            self.console.print(
                f"[bold green]Mode:[/bold green] yolo • local+network authority"
                f" • about {minutes} minutes left"
            )
            return
        self.console.print(f"[bold cyan]Mode:[/bold cyan] {status.mode}")

    # ── Session handlers ──────────────────────────────────────────────────────

    def _handle_session_command(self, stripped: str) -> bool:
        normalized = stripped.strip().lower()
        if normalized in {"/new", "/new-session"}:
            normalized = "/session new"
        if normalized in {"/compact", "/compact-session"}:
            normalized = "/session compact"
        if normalized not in {"/session", "/session status", "/session new", "/session compact"}:
            return False
        if not self.session_id:
            self.console.print("[yellow]No persistent session is attached.[/yellow]")
            return True
        if normalized in {"/session", "/session status"}:
            self.console.print(
                f"[dim]Session:[/dim] {self.session_id} | live messages={len(self._chat_messages)}"
            )
            return True
        if normalized == "/session new":
            rotate_session(self.session_id, "HydraAgent new HydraAgent chat session started")
            self._reset_live_chat_history()
            self.console.print("[green]New HydraAgent chat session started.[/green]")
            return True
        if normalized == "/session compact":
            report = compact_session(self.session_id, max_entries=1)
            self._reset_live_chat_history()
            state = "compacted" if report.get("compacted") else "already compact"
            self.console.print(f"[green]Session {state}.[/green]")
            return True
        return False

    def _reset_live_chat_history(self) -> None:
        system_messages = [m for m in self._chat_messages if m.get("role") == "system"]
        if not system_messages:
            system_messages = [{"role": "system", "content": self.system_prompt}]
        self._chat_messages = system_messages

    # ── Parallel summary ──────────────────────────────────────────────────────

    def _print_parallel_summary(self, result: "LoopResult | None") -> None:
        agents = self._parallel_agents_this_turn
        if not agents or not RICH_AVAILABLE:
            return
        n = len(agents)
        self.console.print(f"\n  [dim]🤖 {n} parallel agent(s) completed.[/dim]")

    def _write_trace_turn_text(self, user_input: str, final: str) -> None:
        """Write a simplified trace turn entry when we don't have a LoopResult.

        Used by the refactored _stream_turn which routes via intent_router and
        doesn't expose a single LoopResult object at the top level.
        """
        if self.trace_out_path is None:
            return
        self.trace_out_path.parent.mkdir(parents=True, exist_ok=True)
        self._trace_turn_index += 1
        from hydra.inter_agent import redact_value as _rv
        payload = _rv(
            {
                "schema": "hydra.chat_trace_turn.v1",
                "turn_index": self._trace_turn_index,
                "prompt": user_input,
                "provider": self.provider_name,
                "model": self.model,
                "trace_id": None,
                "iterations": self._cumulative_iterations,
                "tool_calls_made": self._cumulative_tool_calls,
                "halted_reason": None,
                "final_response": final,
                "tool_steps": [],
                "approval_policy": self.approval_policy,
            }
        )
        with self.trace_out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")


# ── Convenience factory ───────────────────────────────────────────────────────

def launch(
    client,
    model: str,
    cfg,
    root: Path | str,
    system_prompt: str,
    session_id: str | None = None,
) -> None:
    """Create and run an EliteTUI session."""
    resolved_root = Path(root).expanduser().resolve() if root else REPO_ROOT
    tui = EliteTUI(
        client=client,
        model=model,
        cfg=cfg,
        root=resolved_root,
        system_prompt=system_prompt,
        session_id=session_id,
    )
    tui.run()
