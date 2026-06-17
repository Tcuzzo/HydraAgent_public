"""gateways.tui.hydra_app — Textual TUI for HydraAgent.

Three-panel layout (Claude Code / arcade-edition style):

  ┌──────────────────────────────────────────────────────────────────┐
  │   ▄█▄  H Y D R A  ▄█▄                                            │
  │                  agent                                            │
  ├─ SCROLLING RUNTIME LIVE STREAM ─────────────── ▲ UP / DOWN ▼ ────┤
  │  [🐉 qwen2.5:72b] ▶ thinking…                                      │
  │  ✓ bash › git status (0.0s)                                       │
  │  ✓ glob › **/*.zig (3.1s)                                         │
  │  ✓ fs_read › /src/main.zig (0.1s)                                 │
  │  …                                                  (auto-scroll) │
  ├─ FIXED CHAT & INPUT (15 LINES) ───────────────────────────────────┤
  │  [User] » previous question                                       │
  │  [HYDRA] » previous answer                                        │
  │  …                                                                │
  │  HYDRA [User] » █                                          [SEND] │
  └──────────────────────────────────────────────────────────────────┘

The scrolling runtime never blocks the input — AgentLoop runs in a Textual
Worker (background thread) and posts events via the message pump.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pyfiglet
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.css.query import NoMatches
# textual 8.2.7 removed MouseWheel; use MouseScrollUp/Down (direction-encoded events).
# Guarded import keeps the module loadable on older textual that still has MouseWheel.
try:
    from textual.events import MouseScrollDown, MouseScrollUp
except ImportError:  # older textual (<= pre-8.x)
    MouseScrollDown = MouseScrollUp = None  # type: ignore[assignment,misc]
from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Footer, Input, RichLog, Static, TextArea

from hydra.intake import COLLAB, CONVO, STEERING, classify
from hydra.loop import AgentLoop, LoopStep, LoopResult, Tool
from hydra.operator_auth import OperatorAuth, OperatorAuthError, render_qr_ascii
from hydra.work_executor import (
    detect_runaway,
    guard_work_turn,
    is_work_kind,
    resolve_work_model,
)
from gateways.tui.auth_commands import parse_auth_command


class ChatInput(TextArea):
    """The operator's multiline chat box.

    Enter SUBMITS the whole buffer (posts ``ChatInput.Submitted``); Shift+Enter inserts a
    NEWLINE without submitting. Ctrl+J is a newline fallback for terminals that don't send
    a distinct shift+enter (no Kitty keyboard protocol). All other keys keep normal
    TextArea editing.
    """

    class Submitted(Message):
        """Posted on Enter. Carries the full (possibly multiline) text."""

        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    @property
    def value(self) -> str:
        """Alias for ``self.text`` — lets tests set widget content via ``widget.value = ...``."""
        return self.text

    @value.setter
    def value(self, text: str) -> None:
        self.text = text

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text))
            return
        if event.key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)


# ── Re-exec helper ────────────────────────────────────────────────────────
# Kept at module level so tests can monkeypatch gateways.tui.hydra_app._reexec_hydra
# without affecting the real os.execv.


def _reexec_hydra() -> None:
    """Re-exec the current process with the same executable and argv.

    This re-runs the exact command that launched Hydra — works for both
    ``python -m hydra ...`` and direct-script launches.

    IMPORTANT: only call this AFTER Textual's ``app.run()`` returns
    (terminal restored).  Never call from inside the running Textual loop.
    """
    import os
    import sys as _sys
    os.execv(_sys.executable, [_sys.executable] + _sys.argv)


# ── Visual constants ──────────────────────────────────────────────────────

CSS = """
Screen {
    background: #0a0612;
    color: #e8eaf6;
    layout: vertical;
    overflow: hidden;  /* Prevent page-level scrolling */
}

#header-band {
    height: 10;
    background: #0a0612;
    border-bottom: heavy #ff2d95;
    padding: 0 1;
}

/* Chat fills all available space between header and input */
#chat-stream {
    height: 1fr;
    background: #0a0612;
    color: #f0eaff;
    overflow-y: auto;  /* Only vertical scroll, contained within chat area */
    scrollbar-gutter: stable;
    scrollbar-color: #ff2d95 #1a0f2e;
    scrollbar-color-hover: #00e5ff #1a0f2e;
    scrollbar-color-active: #00e5ff #1a0f2e;
    border: none;
    padding: 1 2;
}

#input-row {
    height: 3;
    background: #110a26;
    padding: 0 1;
    border-top: solid #ff2d95;
}

#input-prompt {
    width: 19;
    color: #00e5ff;
    text-style: bold;
    background: #110a26;
    padding: 1 0 0 1;
}

#operator-input {
    background: #1a0f2e;
    color: #ffffff;
    border: tall #ff2d95;
    height: 3;
    margin: 0 1 0 0;       /* gap before SEND → a clear right boundary */
    scrollbar-size: 1 1;
}

#operator-input:focus {
    border: tall #00e5ff;
}

/* Masked 6-digit code field — hidden until YOLO code entry swaps it in. */
#code-input {
    display: none;
    background: #1a0f2e;
    color: #00e5ff;
    border: tall #ff2d95;
    height: 3;
    margin: 0 1 0 0;
}

#send-hint {
    width: 10;
    color: #00e5ff;
    text-style: bold;
    padding: 1 1 0 0;
    content-align: right middle;
}

#stat-bar {
    height: 2;
    background: #110a26;
    color: #b8a4d0;
    padding: 0 1;
    border-top: solid #4a3a6e;
}
"""


# ── Banner ────────────────────────────────────────────────────────────────


# 5-headed dragon ASCII art template. {E1}…{E5} = eye glyphs (animated per
# frame); {W} = water ripple character (animated). Each row is the same
# width so the side-by-side composition with the HYDRA banner stays aligned.
_DRAGON_TEMPLATE = [
    r"    /\    /\    /\    /\    /\    ",
    r"   /{E1}{E1}\  /{E2}{E2}\  /{E3}{E3}\  /{E4}{E4}\  /{E5}{E5}\   ",
    r"   \__/  \__/  \__/  \__/  \__/   ",
    r"     \     \    |    /     /      ",
    r"      \_____\___|___/_____/       ",
    r"             █▓███▓█              ",
    r"              ▀▀▀▀▀               ",
    r"           {W}{W}{W}{W}{W}{W}{W}{W}{W}            ",
]

# Animation frames — eyes cycle through a glow/blink loop, water ripples
# alternate. Frame timing comes from set_interval below.
_DRAGON_FRAMES = [
    # (e1, e2, e3, e4, e5, water)
    ("◉", "◉", "◉", "◉", "◉", "≈"),  # all eyes open
    ("◉", "◉", "●", "◉", "◉", "~"),  # center dimming
    ("●", "◉", "─", "◉", "●", "≈"),  # center closed, edges dim
    ("◉", "●", "─", "●", "◉", "~"),  # center closed, mid closing
    ("◉", "◉", "●", "◉", "◉", "≈"),  # center reopening
    ("◉", "◉", "◉", "◉", "◉", "~"),  # all glow
    ("⊙", "◉", "◉", "◉", "⊙", "≈"),  # edges narrow
    ("◉", "◉", "◉", "◉", "◉", "~"),  # back to baseline
]

# Eye glyphs that should glow red (anything in this set styled bright red).
_GLOWING_EYES = {"◉", "●", "⊙", "○"}


def _render_dragon(frame_index: int) -> list[Text]:
    """Render one animation frame of the dragon as a list of styled Text rows."""
    e1, e2, e3, e4, e5, water = _DRAGON_FRAMES[frame_index % len(_DRAGON_FRAMES)]
    rows: list[Text] = []
    for template_row in _DRAGON_TEMPLATE:
        line = template_row.format(E1=e1, E2=e2, E3=e3, E4=e4, E5=e5, W=water)
        text = Text()
        for ch in line:
            if ch in _GLOWING_EYES:
                text.append(ch, style="bold #ff1744")  # glowing red eye
            elif ch == "─":
                text.append(ch, style="bold #6e0028")  # closed eye, dim red
            elif ch in "█▓":
                text.append(ch, style="bold #d04090")  # body, neon pink
            elif ch == "▀":
                text.append(ch, style="bold #9d2d70")
            elif ch == "≈" or ch == "~":
                text.append(ch, style="bold #00b8d4")  # water, cyan
            elif ch in r"/\_|":
                text.append(ch, style="#ff45a0")        # outline, hot pink
            else:
                text.append(ch)
        rows.append(text)
    return rows


def _build_neon_banner(
    model_label: str, status_label: str, *, dragon_frame: int = 0
) -> Text:
    """Header band: animated dragon + HYDRA banner + agent badge + status."""
    ascii_art = pyfiglet.figlet_format("HYDRA", font="ansi_shadow").rstrip("\n")
    hydra_lines = ascii_art.splitlines()
    gradient = ["#ff2d95", "#ff45a0", "#cc4fb8", "#9d59ce", "#6e63e3", "#3f6df9"]
    dragon_rows = _render_dragon(dragon_frame)
    dragon_width = len(_DRAGON_TEMPLATE[0])  # all template rows same width

    text = Text()
    rows = max(len(dragon_rows), len(hydra_lines))
    for i in range(rows):
        # left column: animated dragon (already styled)
        if i < len(dragon_rows):
            text.append_text(dragon_rows[i])
        else:
            text.append(" " * dragon_width)
        text.append("  ")  # spacer
        # right column: HYDRA banner (gradient)
        if i < len(hydra_lines):
            color = gradient[min(i, len(gradient) - 1)]
            text.append(hydra_lines[i], style=color)
        text.append("\n")
    # Sub-label
    text.append(" " * (dragon_width + 2), style="")
    text.append("agent", style="bold #00e5ff")
    text.append("  ·  ", style="#5a5070")
    text.append(model_label, style="bold #ff2d95")
    text.append("  ·  ", style="#5a5070")
    text.append(status_label, style="bold #00ff66")
    return text


def _build_stat_bar(
    *,
    tokens: str,
    iterations: int,
    tools: int,
    model: str,
    provider: str,
    mode: str,
    live: bool,
) -> Text:
    """Render the single-line stat / key-hint bar shown above the input row."""
    text = Text()

    # Key hints (left)
    text.append(" /help", style="bold #00e5ff")
    text.append("  /clear", style="bold #00e5ff")
    text.append("  /audit", style="bold #00e5ff")
    text.append("  /capabilities", style="bold #00e5ff")
    text.append("  /skills", style="bold #00e5ff")
    text.append("  /restart", style="bold #00e5ff")
    text.append("   │   ", style="dim #5a5070")

    # Runtime stats
    if tokens:
        text.append("🧠 ", style="")
        text.append(tokens, style="bold #ffffff")
        text.append("  ", style="")
    text.append("⚡ ", style="")
    text.append(f"iter:{iterations}", style="bold #ffffff")
    text.append("  ", style="")
    text.append("🛠 ", style="")
    text.append(str(tools), style="bold #ffffff")
    text.append("   │   ", style="dim #5a5070")

    # Identity (right side feel)
    text.append("🐉 ", style="")
    text.append(model, style="bold #ff2d95")
    text.append("  📡 ", style="")
    text.append(provider, style="bold #00e5ff")
    text.append("  🔐 ", style="")
    text.append(mode, style="bold #ffd966")
    text.append("   │   ", style="dim #5a5070")
    if live:
        text.append("● LIVE", style="bold #00ff66 blink")
    else:
        text.append("○ idle", style="dim #6e63e3")
    return text


# ── Messages from worker → UI ─────────────────────────────────────────────


# (Worker-thread → UI handoff uses self.call_from_thread directly.
# Earlier we routed through Message subclasses but Textual's handler
# discovery doesn't reliably match `on__foo` for `_Foo` private classes,
# which silently swallowed tool-step events. Direct call_from_thread is
# both simpler and never drops a step.)


# ── Tool-call presentation ────────────────────────────────────────────────

_TOOL_ICONS: dict[str, str] = {
    "bash": "💻", "fs_read": "📄", "fs_write": "✏️", "fs_edit": "✏️",
    "grep": "🔍", "glob": "🗂️", "list_directory": "📁",
    "http_fetch": "🌐", "browser_navigate": "🌐", "browser_snapshot": "📸",
    "skill_list": "📚", "skill_search": "🔎", "skill_show": "📖",
    "spawn_subagent": "🤖", "spawn_subagents": "🤖",
    "memory_remember": "🧠", "memory_recall": "🧠",
    "system_stats": "📊", "todo": "✅",
}


def _tool_summary(tool_name: str, args: dict | None, content: str | None) -> str:
    a = args or {}
    if tool_name == "bash":
        cmd = str(a.get("command", ""))
        return cmd[:64] + ("…" if len(cmd) > 64 else "")
    if tool_name in {"fs_read", "fs_write", "fs_edit"}:
        return str(a.get("path", ""))
    if tool_name == "grep":
        return str(a.get("pattern", ""))
    if tool_name == "glob":
        return str(a.get("pattern", ""))
    if tool_name in {"http_fetch", "browser_navigate"}:
        return str(a.get("url", ""))
    if tool_name in {"spawn_subagent", "spawn_subagents"}:
        tasks = a.get("tasks") or ([a.get("task")] if a.get("task") else [])
        return f"{len(tasks)} task(s)"
    return str(a.get("path", "") or a.get("name", "") or "")


_VIBE_PATH = Path(__file__).resolve().parents[2] / "VIBE.md"


def _load_vibe() -> str:
    """Read VIBE.md (the agent's voice/discipline). Empty string if missing.

    VIBE.md is the operator-editable persona file. Other agents may also
    edit it but only via the Telegram gate per VIBE.md §Gate.
    """
    try:
        return _VIBE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _augment_system_prompt(
    base: str, *, workspace_root: Path, tool_names: list[str]
) -> str:
    """Compose: VIBE.md + dynamic runtime context + base system prompt.

    VIBE.md owns the voice, tool-use discipline, and gate rules (edit
    that file to change agent behavior). Only the dynamic bits (cwd,
    tool inventory) are stitched in here.
    """
    vibe = _load_vibe()
    tool_inventory = ", ".join(sorted(tool_names)) if tool_names else "(none)"
    runtime_block = (
        "\n\n═══ RUNTIME CONTEXT (system, dynamic per launch) ═══\n"
        f"workspace: {workspace_root}\n"
        f"tools ({len(tool_names)}): {tool_inventory}\n"
        "═══════════════════════════════════════════════════════\n\n"
    )
    return (vibe + runtime_block + base) if vibe else (runtime_block + base)


# ── HydraApp ──────────────────────────────────────────────────────────────


class HydraApp(App[int]):
    """Textual app for HydraAgent — header + scrolling runtime + fixed chat."""

    CSS = CSS

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("ctrl+d", "quit", "Quit", show=False),
        Binding("ctrl+l", "clear_runtime", "Clear runtime", show=True),
        Binding("pageup", "scroll_runtime_up", "Scroll up", show=True),
        Binding("pagedown", "scroll_runtime_down", "Scroll down", show=True),
        Binding("up", "scroll_up", "Scroll up", show=False),
        Binding("down", "scroll_down", "Scroll down", show=False),
    ]

    operator_label: reactive[str] = reactive("User")

    def __init__(
        self,
        *,
        client,
        model: str,
        cfg,
        system_prompt: str,
        tools: list[Tool],
        workspace_root: Path | str | None = None,
        on_persist: Callable[[str, str], None] | None = None,
        on_command: Callable[[str], bool] | None = None,
        max_iterations: int = 200,  # UNLOCKED: iterate like Claude Code/Codex (operator: no iteration gates)
        timeout: float = 120.0,
        initial_request: str | None = None,
        initial_messages: list[dict] | None = None,
        notify_telegram: bool = False,
    ) -> None:
        super().__init__()
        self.client = client
        self.model = model
        self.cfg = cfg
        self.tools = tools
        self.workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root else Path.cwd()
        self._on_persist = on_persist
        self._on_command = on_command
        self._max_iterations = max_iterations
        self._timeout = timeout
        self._initial_request = (initial_request or "").strip() or None
        # Live-operator flag: True only when spawned via the real daemon launcher
        # (spawn-hydra / cmd_chat). Default is False so tests and audits NEVER
        # page the operator's Telegram. Only the live launch path sets this True.
        self._notify_telegram: bool = bool(notify_telegram)

        # Inject workspace + toolkit context into the system prompt so the
        # agent always knows where it is and what it can do. Without this
        # the model asks 'what's the repo root?' every turn.
        self.system_prompt = _augment_system_prompt(
            system_prompt,
            workspace_root=self.workspace_root,
            tool_names=[t.name for t in tools],
        )

        self._chat_messages: list[dict] = list(initial_messages or [])
        if not any(m.get("role") == "system" for m in self._chat_messages):
            self._chat_messages.insert(0, {"role": "system", "content": self.system_prompt})
        else:
            for m in self._chat_messages:
                if m.get("role") == "system":
                    m["content"] = self.system_prompt
                    break
        self._agent_loop = AgentLoop(client, model=model, system_prompt=self.system_prompt)
        # Operator authority (TOTP yolo). The same OperatorAuth the policy
        # layer reads via is_yolo_active — unlocking here flips real authority.
        self._auth = OperatorAuth()
        self._awaiting_yolo_code = False
        self._turn_in_flight = False
        self._provider_name = getattr(cfg, "name", "") or "hydra"
        self._chat_lines: list[tuple[str, str]] = []  # (role, content) history
        self._turn_start = 0.0
        # Running stats for the bar
        self._stat_tokens_chars = 0
        self._stat_iterations = 0
        self._stat_tools = 0
        self._stat_live = False
        self._tools_used_this_turn = 0
        # Dragon animation frame counter (tick'd by set_interval in on_mount)
        self._dragon_frame = 0
        # Set by /restart handler; checked by maybe_reexec() after run() returns.
        self._restart_requested = False
        # Tracks the current turn's intake class so on_turn_done can suppress
        # runtime chrome for plain conversation turns.
        self._active_intake: str = STEERING
        # Active model for the CURRENT turn — updated by _run_agent_loop_sync
        # from _route_for_kind so the stat bar shows the executor actually
        # running (cloud-planner for work turns, chat model for convo turns).
        self._active_model: str = self.model
        # Active provider for the CURRENT turn — mirrors _active_model so the
        # banner/stat-bar can show the right provider during a work turn.
        self._active_provider: str = self._provider_name
        # Cache of work-executor AgentLoops keyed by (provider, model) pair so
        # we build them once and reuse — mirrors elite.py:764 / _work_loops.
        self._work_loops: dict[tuple[str, str], AgentLoop] = {}
        # Work executor pair resolved ONCE at init so the header can always
        # display BOTH the chat model and the work model (even at rest, before
        # any work turn runs). Fail-soft: None if routing is offline/misconfigured.
        _work_pair = resolve_work_model("steering")
        if _work_pair is not None:
            self._work_provider: str | None = _work_pair[0]
            self._work_model: str | None = _work_pair[1]
        else:
            self._work_provider = None
            self._work_model = None

    # ── Layout ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        # Header banner (neon) — dragon animation drives by frame counter
        header = Static(self._render_banner(), id="header-band")
        yield header

        # ONE chat scrollback — fills everything between header and input.
        # User turns, agent replies, and tool calls all stream here as a
        # single conversation. Auto-scroll keeps the latest in view.
        # ONE chat scrollback — fills everything between header and input.
        # User turns, agent replies, and tool calls all stream here as a
        # single conversation. Auto-scroll keeps the latest in view.
        chat = RichLog(
            id="chat-stream",
            highlight=False,
            markup=True,
            wrap=True,
            auto_scroll=True,
            max_lines=4000,
        )
        chat.can_focus = False  # Don't steal focus from input, but still allow scroll
        yield chat

        # Input row — locked into the lower band. The compact stat strip stays below it.
        with Horizontal(id="input-row"):
            yield Static(
                f"HYDRA \\[{self.operator_label}] »",
                id="input-prompt",
            )
            yield ChatInput(
                id="operator-input",
                soft_wrap=True,
                show_line_numbers=False,
                tab_behavior="focus",
            )
            # Masked single-line field, shown only for 6-digit YOLO code entry.
            yield Input(password=True, id="code-input")
            yield Static("⮕ SEND", id="send-hint")

        # Stat bar — compact notes under the input; never a large spacer.
        yield Static(self._render_stat_bar(), id="stat-bar")

    async def on_mount(self) -> None:
        chat = self.query_one("#chat-stream", RichLog)
        # Build the model line — always show BOTH chat and work models so the
        # operator can see at a glance the active chat model and work executor.
        _chat_part = f"chat:{self.model}@{self._provider_name}"
        if self._work_model and self._work_model != self.model:
            _work_part = f"  ·  work:{self._work_model}@{self._work_provider}"
        else:
            _work_part = ""
        chat.write(
            "[bright_magenta]🐉 HYDRA[/bright_magenta]  "
            f"[dim]{_chat_part}{_work_part}  ·  tools={len(self.tools)}[/dim]"
        )
        chat.write(
            "[dim italic]  hey — just talk to me, or drop a task. "
            "shift+enter = new line, enter = send. ctrl+c to quit.[/dim italic]"
        )
        chat.write("")
        self.query_one("#operator-input", ChatInput).focus()

        # Animate the dragon — eye blink / glow cycle every 350ms
        self.set_interval(0.35, self._tick_dragon)

        # Seeded initial request becomes the first turn
        if self._initial_request:
            req = self._initial_request
            self._initial_request = None
            self._post_chat_line("user", req)
            self._submit_turn(req)

    # ── Input handling ───────────────────────────────────────────────────

    async def on_chat_input_submitted(self, event: "ChatInput.Submitted") -> None:
        """Operator pressed Enter in the multiline chat box."""
        text = event.value.strip()
        try:
            self.query_one("#operator-input", ChatInput).text = ""
        except NoMatches:
            pass
        if text:
            await self._process_submitted(text)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Only the masked #code-input fires this now (6-digit YOLO code entry)."""
        text = (event.value or "").strip()
        try:
            self.query_one("#code-input", Input).value = ""
        except NoMatches:
            pass
        if text:
            await self._process_submitted(text)

    async def _process_submitted(self, text: str) -> None:
        """Route one submitted line (from chat or the code field): auth, slash, or turn."""
        # Operator auth first — including the "waiting for your code" state, so
        # the 6-digit code typed at the prompt is consumed here, not sent to
        # the model. (Bug fix: previously /yolo etc. fell through to chat.)
        auth_cmd = parse_auth_command(text, awaiting_code=self._awaiting_yolo_code)
        if auth_cmd.kind != "none":
            self._handle_auth(auth_cmd)
            return
        # Slash commands handled inline
        if text.lower() in {"/exit", "/quit", "stop"}:
            self.exit(0)
            return
        if text == "/help":
            self._show_help()
            return
        if text == "/clear":
            self.action_clear_runtime()
            return
        if text == "/audit":
            self._show_go("audit")
            return
        if text == "/capabilities":
            self._show_go("capabilities")
            return
        if text == "/restart":
            self._do_restart()
            return
        if self._on_command is not None and self._on_command(text):
            return
        self._post_chat_line("user", text)
        # Fabric §5 step 1: classify before we route.
        intake = classify(text, source="operator")
        self._submit_turn(text, intake.kind)

    def _submit_turn(self, user_text: str, intake_kind: str = STEERING) -> None:
        if self._turn_in_flight:
            self._post_log(
                "[yellow]⚠ a turn is already running — wait for it to finish[/yellow]"
            )
            return
        self._turn_in_flight = True
        self._stat_live = True
        self._update_stat_bar()
        self._turn_start = time.monotonic()
        self._active_intake = intake_kind
        self._tools_used_this_turn = 0  # we'll reveal chrome only if tools fire

        self.run_worker(
            self._run_agent_loop_async(user_text, intake_kind),
            exclusive=True,
            thread=True,
            name="agent-turn",
        )

    async def _run_agent_loop_async(self, user_text: str, intake_kind: str) -> None:
        """Run AgentLoop in a worker thread, then marshal completion to UI."""
        loop = asyncio.get_event_loop()
        try:
            result: LoopResult = await loop.run_in_executor(
                None, self._run_agent_loop_sync, user_text, intake_kind
            )
            self._handle_turn_done(result.final_response or "")
        except Exception as exc:
            self._handle_turn_error(str(exc))

    # ── Work-turn routing (ported from elite.py:742-776) ────────────────

    def _route_for_kind(self, kind: str) -> tuple[AgentLoop, str]:
        """Return the (loop, model) to run for this turn's intake kind.

        WORK turns (steering/collab — profiles that declare an ``executor``) run
        on the build / most-capable tool-calling executor resolved from
        model_routing (cloud-planner), NOT the chat model that only narrates.
        This is the confabulation fix: the chat model would print
        "I successfully connected to the remote server" with ZERO tool calls; the executor
        actually calls tools.

        Fail-soft: if the work model can't be resolved/built (offline, bad
        config) we fall back to the default chat loop — the no-confabulation
        guard still protects the turn so a fake "done" never reaches the
        operator.

        Ported from elite.py _loop_for_kind (lines 742-776) — same logic,
        same cache, same fail-soft contract.
        """
        if not is_work_kind(kind):
            return self._agent_loop, self.model
        pair = resolve_work_model(kind)
        if pair is None:
            return self._agent_loop, self.model
        provider, model = pair
        # Same provider+model as the chat brain → reuse the existing loop.
        if model == self.model:
            return self._agent_loop, self.model
        cached = self._work_loops.get(pair)
        if cached is not None:
            return cached, model
        try:
            from hydra.providers import make_client

            client, _cfg = make_client(provider)
            work_loop = AgentLoop(client, model=model, system_prompt=self.system_prompt)
        except Exception:
            # Executor unavailable — keep the chat loop; the guard still applies.
            return self._agent_loop, self.model
        self._work_loops[pair] = work_loop
        return work_loop, model

    def _run_agent_loop_sync(self, user_text: str, intake_kind: str) -> LoopResult:
        """Route to the correct loop for this intake kind, then run it.

        WORK turns (steering/collab) execute on the tool-capable work executor
        (cloud-planner) resolved by _route_for_kind.  CONVO turns stay on the
        chat loop.  Fail-soft: if the work executor is unavailable,
        _route_for_kind returns the chat loop so the turn still runs (guarded
        against confabulation by work_executor.guard_work_turn).

        The work loop receives the same tools list as the chat loop — the model
        decides whether to call them; we never restrict the executor's toolkit.
        """
        agent_loop, active_model = self._route_for_kind(intake_kind)
        # Update _active_model so the stat bar shows the ACTUAL executor for
        # this turn (cloud-planner for work turns, chat model default otherwise).
        self._active_model = active_model
        # Also track _active_provider so the banner/stat-bar can show the right
        # provider during a work turn.  Re-resolve is cheap (pure dict lookup).
        if active_model != self.model:
            _pair = resolve_work_model(intake_kind)
            self._active_provider = _pair[0] if _pair else self._provider_name
        else:
            self._active_provider = self._provider_name
        initial = list(self._chat_messages)
        result = agent_loop.run(
            user_text,
            tools=self.tools,
            max_iterations=self._max_iterations,
            max_tokens=2048,
            temperature=0.0 if intake_kind != CONVO else 0.5,
            timeout=self._timeout,
            initial_messages=initial,
            on_step=self._on_loop_step,
        )

        # NO-CONFABULATION GUARD (ported from elite.py:917-957). This was MISSING
        # on the live TUI path — a work turn could narrate "I connected to the server
        # and ran the audit" with ZERO tool calls and the lie reached the operator.
        # Now: cap runaway output, then on a WORK turn that CLAIMED an action with
        # no tool call, force ONE re-prompt to actually call the tool or admit it
        # wasn't done. Convo turns pass straight through (guard exempts non-work).
        final = result.final_response or ""
        runaway = detect_runaway(final, max_tokens=2048)
        if runaway is not None:
            final = runaway.capped_text

        def _rerun_forcing_tools(force: bool) -> tuple[str, int]:
            forced = list(initial)
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
            r2 = agent_loop.run(
                user_text,
                tools=self.tools,
                max_iterations=self._max_iterations,
                max_tokens=2048,
                temperature=0.0 if intake_kind != CONVO else 0.5,
                timeout=self._timeout,
                initial_messages=forced,
                on_step=self._on_loop_step,
            )
            return (r2.final_response or "", r2.tool_calls_made)

        outcome = guard_work_turn(
            kind=intake_kind,
            final_text=final,
            tool_calls_made=result.tool_calls_made,
            rerun=_rerun_forcing_tools,
        )
        result.final_response = outcome.text
        return result

    # ── Loop step callback (runs in worker thread) ───────────────────────

    def _on_loop_step(self, step: LoopStep) -> None:
        """AgentLoop step callback. Runs in worker thread — must marshal
        all UI writes to the main thread via call_from_thread."""
        if step.kind == "assistant":
            content = (step.content or "").strip()
            if content and not content.startswith('{"'):
                preview = content[:120] + ("…" if len(content) > 120 else "")
                self.call_from_thread(
                    self._post_log,
                    f"  [bright_cyan]│[/bright_cyan] [dim italic]{preview}[/dim italic]",
                )
            return

        if step.kind != "tool_result":
            return

        self._stat_tools += 1
        self._tools_used_this_turn += 1
        self.call_from_thread(self._update_stat_bar)

        tool_name = step.tool_name or "unknown"
        icon = _TOOL_ICONS.get(tool_name, "🔧")
        duration_str = ""
        if isinstance(step.tool_duration_ms, int):
            duration_str = f" [dim]({step.tool_duration_ms / 1000:.1f}s)[/dim]"
        summary = _tool_summary(tool_name, step.tool_arguments, step.content)
        if step.tool_error:
            markup = (
                f"  [red]✗[/red] {icon} [bold]{tool_name}[/bold] "
                f"[dim]›[/dim] [red]{step.tool_error[:60]}[/red]{duration_str}"
            )
        else:
            markup = (
                f"  [green]✓[/green] {icon} [bold]{tool_name}[/bold] "
                f"[dim]›[/dim] [dim]{summary}[/dim]{duration_str}"
            )
        self.call_from_thread(self._post_log, markup)

    # ── Message handlers (main thread) ───────────────────────────────────

    def _handle_turn_done(self, final_response: str) -> None:
        """Runs on the UI event loop after AgentLoop returns."""
        self._turn_in_flight = False
        self._stat_live = False
        self._stat_iterations += 1
        final = final_response.strip()
        if final:
            self._stat_tokens_chars += len(final)
        self._update_stat_bar()
        elapsed = time.monotonic() - self._turn_start
        # Only show turn-complete chrome when the agent actually worked
        # (i.e. fired at least one tool). Pure chat turns stay clean.
        if self._tools_used_this_turn > 0:
            self._post_log(
                f"  [bright_magenta]●[/bright_magenta] [dim]turn complete in {elapsed:.1f}s "
                f"· {self._tools_used_this_turn} tool calls[/dim]"
            )
        if final:
            self._post_chat_line("hydra", final)
            self._chat_messages.append({"role": "user", "content": self._last_user_text()})
            self._chat_messages.append({"role": "assistant", "content": final})
            if self._on_persist is not None:
                try:
                    self._on_persist(self._last_user_text(), final)
                except Exception:
                    pass

    def _handle_turn_error(self, detail: str) -> None:
        self._turn_in_flight = False
        self._stat_live = False
        self._update_stat_bar()
        self._post_log(f"[red]✗ turn error:[/red] {detail}")
        self._post_chat_line("system", f"⚠ {detail[:160]}")

    # ── Utilities ────────────────────────────────────────────────────────

    def _post_log(self, markup: str) -> None:
        """Write a runtime/tool/system line into the chat scrollback.

        Tool calls and agent-loop chrome now share the chat stream so the
        operator sees one conversation thread, not a separate panel.
        """
        try:
            self.query_one("#chat-stream", RichLog).write(markup)
        except NoMatches:
            pass

    def _render_banner(self) -> Text:
        """Render the header banner for the current dragon frame.

        Always shows BOTH the chat model and the work model so the operator
        can never look up and see only the chat model at rest.

        Format (idle):     chat:llama-3.3-70b  ·  work:qwen2.5:72b
        Format (work active):  chat:llama-3.3-70b  ·  ⚙ work:qwen2.5:72b
        Fail-soft (no work):   llama-3.3-70b
        """
        if self._work_model and self._work_model != self.model:
            work_active = (self._active_model == self._work_model)
            work_part = (
                f"⚙ work:{self._work_model}"
                if work_active
                else f"work:{self._work_model}"
            )
            model_label = f"chat:{self.model}  ·  {work_part}"
        else:
            # Fail-soft: no work model resolved, or work model same as chat.
            model_label = self._active_model
        return _build_neon_banner(
            model_label=model_label,
            status_label=f"📡 {self._provider_name}  ·  🟢 online",
            dragon_frame=self._dragon_frame,
        )

    def _tick_dragon(self) -> None:
        """Advance dragon animation by one frame and repaint the header."""
        self._dragon_frame = (self._dragon_frame + 1) % len(_DRAGON_FRAMES)
        try:
            self.query_one("#header-band", Static).update(self._render_banner())
        except NoMatches:
            pass

    def _render_stat_bar(self) -> Text:
        """Build the renderable for the bottom stat/key bar."""
        tok_approx = ""
        if self._stat_tokens_chars > 0:
            t = self._stat_tokens_chars // 4
            tok_approx = f"~{t/1000:.1f}k" if t >= 1000 else f"~{t}"
        # Show the model ACTUALLY running this turn.  For work turns
        # (steering/collab) _active_model is the work executor; for chat turns it
        # equals self.model.  A "⚙ work" prefix makes the switch obvious.
        is_work_turn = self._active_model != self.model
        display_model = (
            f"⚙ work:{self._active_model}" if is_work_turn else self._active_model
        )
        return _build_stat_bar(
            tokens=tok_approx,
            iterations=self._stat_iterations,
            tools=self._stat_tools,
            model=display_model,
            provider=self._provider_name,
            mode=self._operator_mode(),
            live=self._stat_live,
        )

    def _update_stat_bar(self) -> None:
        try:
            self.query_one("#stat-bar", Static).update(self._render_stat_bar())
        except NoMatches:
            pass

    def _post_chat_line(self, role: str, content: str) -> None:
        """Write a user/hydra/system turn into the single chat scrollback."""
        tag = {
            "user":   "[bold #00e5ff]\\[User][/bold #00e5ff]",
            "hydra":  "[bold #ff45a0]\\[HYDRA][/bold #ff45a0]",
            "system": "[bold #ffd966]\\[SYS][/bold #ffd966]",
        }.get(role, f"\\[{role}]")
        # Preserve multi-line content — chat-stream wraps natively.
        line = f"{tag} » {content}"
        self._chat_lines.append((role, content))
        try:
            self.query_one("#chat-stream", RichLog).write(line)
        except NoMatches:
            pass

    def _last_user_text(self) -> str:
        for role, content in reversed(self._chat_lines):
            if role == "user":
                return content
        return ""

    def _show_help(self) -> None:
        self._post_log(
            "\n[bold cyan]Slash commands[/bold cyan]\n"
            "  [bright_cyan]/help[/bright_cyan]          show this help\n"
            "  [bright_cyan]/clear[/bright_cyan]         clear the runtime stream\n"
            "  [bright_cyan]/audit[/bright_cyan]         run Go repo audit\n"
            "  [bright_cyan]/capabilities[/bright_cyan]  discover host capabilities\n"
            "  [bright_cyan]/restart[/bright_cyan]       relaunch HYDRA (picks up new code)\n"
            "  [bright_cyan]/yolo[/bright_cyan]          unlock full authority (prompts for your code)\n"
            "  [bright_cyan]/mfa setup[/bright_cyan]     print Google Authenticator QR/URI\n"
            "  [bright_cyan]/lock[/bright_cyan]          leave yolo · [bright_cyan]/mode[/bright_cyan] show mode\n"
            "  [bright_cyan]/exit[/bright_cyan] · [bright_cyan]/quit[/bright_cyan] · [bright_cyan]stop[/bright_cyan]   leave\n"
            "  [dim]Ctrl+C[/dim] quit · [dim]Ctrl+L[/dim] clear runtime"
        )

    def _show_go(self, op: str) -> None:
        from hydra.go_bridge import GoBridge
        self._post_log(f"[dim]🔍 calling Go: {op}…[/dim]")

        def _go_call() -> dict:
            return getattr(GoBridge(), op)()

        async def _drive() -> None:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _go_call)
            preview = json.dumps(result, indent=2)
            if len(preview) > 1800:
                preview = preview[:1800] + "\n…(truncated)…"
            self._post_log(f"[bold cyan]Go {op}:[/bold cyan]\n[dim]{preview}[/dim]")

        self.run_worker(_drive(), exclusive=False, name=f"go-{op}")

    def _do_restart(self) -> None:
        """Handle the /restart slash command.

        Writes a brief confirmation line, sets ``_restart_requested``, then
        calls ``self.exit()`` so Textual cleanly restores the terminal.
        The CALLER (launcher) must call ``app.maybe_reexec()`` after ``run()``
        returns — never exec from inside the live Textual loop.
        """
        self._post_log(
            "[bold #00ff66]↻ restarting HYDRA…[/bold #00ff66]  "
            "[dim](picking up new code)[/dim]"
        )
        self._restart_requested = True
        self.exit(0)

    def maybe_reexec(self) -> None:
        """Re-exec the process if /restart was requested.

        Call this immediately AFTER ``app.run()`` returns (terminal is
        restored at that point).  No-op when ``_restart_requested`` is False.
        """
        if self._restart_requested:
            _reexec_hydra()

    # ── Operator auth (TOTP yolo) ─────────────────────────────────────────

    def _operator_mode(self) -> str:
        """Real auth mode for the stat bar (operator | iteration | yolo)."""
        try:
            return self._auth.status().mode
        except OperatorAuthError:
            return "operator"

    def _set_code_prompt(self, on: bool) -> None:
        """Toggle the input into masked 'enter your code' mode."""
        self._awaiting_yolo_code = on
        try:
            chat = self.query_one("#operator-input", ChatInput)
            code = self.query_one("#code-input", Input)
            chat.display = not on
            code.display = on
            code.value = ""
            (code if on else chat).focus()
        except NoMatches:
            pass
        try:
            label = "HYDRA \\[code] »" if on else f"HYDRA \\[{self.operator_label}] »"
            self.query_one("#input-prompt", Static).update(label)
        except NoMatches:
            pass

    def _handle_auth(self, cmd) -> None:
        if cmd.kind == "status":
            status = self._auth.status()
            if status.yolo_active:
                mins = max(1, int((status.expires_in_seconds or 0) / 60))
                self._post_log(
                    f"[bold green]🔓 Mode: yolo[/bold green] [dim]· local+network authority · ~{mins} min left[/dim]"
                )
            else:
                self._post_log(f"[bold cyan]Mode:[/bold cyan] {status.mode}")
            return
        if cmd.kind == "lock":
            self._auth.lock()
            self._set_code_prompt(False)
            self._post_log("[green]🔒 Mode set to operator.[/green]")
            self._update_stat_bar()
            return
        if cmd.kind == "iteration":
            self._auth.set_mode("iteration")
            self._post_log("[green]Mode set to iteration.[/green]")
            self._update_stat_bar()
            return
        if cmd.kind == "setup":
            self._do_mfa_setup(cmd.force)
            return
        if cmd.kind == "prompt_code":
            self._set_code_prompt(True)
            self._post_log(
                "[bright_cyan]🔐 Enter your Google Authenticator code[/bright_cyan] "
                "[dim](6 digits, or /cancel)[/dim]"
            )
            return
        if cmd.kind == "cancel":
            self._set_code_prompt(False)
            self._post_log("[dim]yolo unlock cancelled[/dim]")
            return
        if cmd.kind == "invalid_code":
            # Stay in the prompt — don't consume garbage as a failed code.
            self._post_log("[yellow]Need a 6-digit code.[/yellow] [dim]Try again or /cancel[/dim]")
            return
        if cmd.kind in {"unlock", "consume_code"}:
            self._set_code_prompt(False)
            try:
                status = self._auth.unlock_yolo(cmd.code or "")
            except OperatorAuthError as exc:
                self._post_log(f"[red]✗ Yolo unlock failed:[/red] {exc}")
                return
            mins = max(1, int((status.expires_in_seconds or 0) / 60))
            self._post_log(
                f"[bold green]🔓 Yolo unlocked[/bold green] "
                f"[dim]· local + network authority for ~{mins} min[/dim]"
            )
            self._update_stat_bar()
            return

    def _do_mfa_setup(self, force: bool) -> None:
        try:
            setup = self._auth.setup_totp(force=force)
        except OperatorAuthError as exc:
            self._post_log(f"[red]✗ MFA setup failed:[/red] {exc}")
            return
        self._post_log("[bold cyan]Google Authenticator setup[/bold cyan]")
        qr = render_qr_ascii(setup.provisioning_uri)
        if qr:
            self._post_log(f"[dim]{qr}[/dim]")
        else:
            self._post_log("[yellow]Install the optional 'qrcode' package to render a scannable QR.[/yellow]")
        self._post_log(f"[dim]Secret file:[/dim] {setup.secret_path}")
        self._post_log(f"[dim]Manual URI:[/dim] {setup.provisioning_uri}")

    # ── Actions ──────────────────────────────────────────────────────────

    def action_clear_runtime(self) -> None:
        try:
            self.query_one("#chat-stream", RichLog).clear()
            self._chat_lines.clear()
            self._post_log("[dim]— cleared —[/dim]")
        except NoMatches:
            pass

    def action_scroll_runtime_up(self) -> None:
        try:
            self.query_one("#chat-stream", RichLog).scroll_page_up()
        except NoMatches:
            pass

    def action_scroll_runtime_down(self) -> None:
        try:
            self.query_one("#chat-stream", RichLog).scroll_page_down()
        except NoMatches:
            pass

    def action_scroll_up(self) -> None:
        try:
            chat = self.query_one("#chat-stream", RichLog)
            # Scroll up by 5 lines
            chat.scroll_relative(y=-5)
        except NoMatches:
            pass

    def action_scroll_down(self) -> None:
        try:
            chat = self.query_one("#chat-stream", RichLog)
            # Scroll down by 5 lines
            chat.scroll_relative(y=5)
        except NoMatches:
            pass

    async def on_mouse_scroll_down(self, event) -> None:  # noqa: ANN001
        """Handle mouse wheel scroll-down in chat area (textual 8.2.7+)."""
        try:
            chat = self.query_one("#chat-stream", RichLog)
            chat.scroll_relative(y=3)
        except NoMatches:
            pass

    async def on_mouse_scroll_up(self, event) -> None:  # noqa: ANN001
        """Handle mouse wheel scroll-up in chat area (textual 8.2.7+)."""
        try:
            chat = self.query_one("#chat-stream", RichLog)
            chat.scroll_relative(y=-3)
        except NoMatches:
            pass
