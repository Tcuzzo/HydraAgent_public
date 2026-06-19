"""In-surface "connect a model" setup panel — built for non-programmers.

Rendered on the chat surface with Rich; reuses the tested ``hydra.setup`` writers.
The console, the input/secret prompts, and ``which`` are all injected, so the whole
flow is unit-testable without a real terminal. ``run_setup_panel`` returns the
provider name it configured (``"ollama"`` / ``"cloud"`` / ``"codex"``) or ``None``
if the person skipped.
"""
from __future__ import annotations

import shutil

from hydra.setup import setup_cloud_provider, setup_codex_oauth, setup_local_ollama

try:  # rich ships as a dependency; degrade to plain text if ever absent
    from rich.panel import Panel
    from rich.table import Table
    from rich import box

    _RICH = True
except Exception:  # pragma: no cover
    _RICH = False


_CHOICES = {
    "local": {"1", "local", "ollama", "free", "l"},
    "cloud": {"2", "cloud", "key", "api", "c"},
    "chatgpt": {"3", "chatgpt", "gpt", "chat"},
}
_CANCEL = {"q", "quit", "cancel", "exit", "skip", "back", ""}

# Common cloud services: (label, endpoint, sensible default model).
_CLOUD_SERVICES = {
    "1": ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
    "2": ("Groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "3": ("MiniMax", "https://api.minimax.io", "MiniMax-Text-01"),
}


def parse_panel_choice(raw: str | None) -> str | None:
    """Map a typed choice to 'local' / 'cloud' / 'chatgpt' / 'cancel' (or None)."""
    s = (raw or "").strip().lower()
    if s in _CANCEL:
        return "cancel"
    for key, aliases in _CHOICES.items():
        if s in aliases:
            return key
    return None


def _render_menu(console) -> None:
    if _RICH:
        t = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
        t.add_column(justify="right", style="bold cyan", no_wrap=True)
        t.add_column()
        t.add_row("1", "[bold]Local (Ollama)[/bold]   [dim]Free. Runs on your computer. No account, no key.[/dim]")
        t.add_row("2", "[bold]Cloud API key[/bold]    [dim]Paste a key from OpenAI, Groq, MiniMax, etc.[/dim]")
        t.add_row("3", "[bold]Sign in with ChatGPT[/bold]   [dim]Use your ChatGPT subscription. No API key.[/dim]")
        console.print(Panel(t, title="[bold]Connect an AI model[/bold]",
                            subtitle="[dim]pick one to start chatting[/dim]", border_style="cyan"))
    else:  # pragma: no cover
        console.print("Connect an AI model:")
        console.print("  1) Local (Ollama) — free, no key")
        console.print("  2) Cloud API key — OpenAI / Groq / MiniMax / ...")
        console.print("  3) Sign in with ChatGPT — your subscription, no key")


def _ok(console, provider: str, detail: str) -> None:
    msg = f"Connected — {detail}. Just start typing."
    console.print(Panel(f"[green]✓[/green] {msg}", border_style="green") if _RICH else f"✓ {msg}")


def _do_local(console, env_dir) -> str:
    setup_local_ollama(env_dir=env_dir)
    _ok(console, "ollama", "using local Ollama (qwen3:8b at localhost:11434)")
    return "ollama"


def _do_cloud(console, ask, secret_ask, env_dir) -> str | None:
    console.print("Which cloud service?  1) OpenAI   2) Groq   3) MiniMax   4) Other")
    pick = (ask("  Service (1-4): ") or "").strip()
    if pick in _CLOUD_SERVICES:
        label, endpoint, model = _CLOUD_SERVICES[pick]
    else:
        label = "custom"
        endpoint = (ask("  API endpoint (e.g. https://host/v1): ") or "").strip()
        model = (ask("  Model name: ") or "").strip()
    key = (secret_ask(f"  Paste your {label} API key (hidden): ") or "").strip()
    try:
        setup_cloud_provider("cloud", endpoint=endpoint, model=model, api_key=key, env_dir=env_dir)
    except Exception as exc:  # missing field — guide, don't crash
        console.print(f"  Couldn't save that: {exc}")
        return None
    _ok(console, "cloud", f"using {label} ({model})")
    return "cloud"


def _do_chatgpt(console, ask, env_dir, which) -> str | None:
    if which("codex") is None:
        console.print(Panel(
            "To use your ChatGPT login you need OpenAI's free Codex CLI:\n"
            "  1. Install it:  npm install -g @openai/codex\n"
            "  2. Sign in:     codex login   (opens your browser)\n"
            "Then run hydra again and pick this option.",
            title="[bold]Sign in with ChatGPT[/bold]", border_style="yellow",
        ) if _RICH else
        "To use your ChatGPT login, install OpenAI's Codex CLI (npm install -g @openai/codex), "
        "run `codex login`, then run hydra again.")
        return None
    console.print("If you haven't yet, run `codex login` in another window (it opens your browser).")
    ask("  Press Enter once you're signed in to ChatGPT… ")
    setup_codex_oauth(env_dir=env_dir)
    _ok(console, "codex", "signed in with ChatGPT (no API key)")
    return "codex"


def run_setup_panel(console, *, ask, secret_ask, env_dir=None, which=shutil.which) -> str | None:
    """Render the panel and walk the person through connecting one model.

    Returns the configured provider name, or None if they skipped.
    """
    _render_menu(console)
    while True:
        choice = parse_panel_choice(ask("  Connect a model — type 1, 2, 3 (or q to skip): "))
        if choice == "cancel":
            console.print("  Skipped — you can connect a model anytime with /setup.")
            return None
        if choice is None:
            console.print("  Please type 1, 2, 3, or q.")
            continue
        if choice == "local":
            return _do_local(console, env_dir)
        if choice == "cloud":
            result = _do_cloud(console, ask, secret_ask, env_dir)
            if result is None:
                continue
            return result
        if choice == "chatgpt":
            return _do_chatgpt(console, ask, env_dir, which)
