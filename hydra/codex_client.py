"""hydra.codex_client — Codex CLI provider ("Sign in with ChatGPT").

`CodexClient` is an LLM provider that shells out to OpenAI's official `codex`
command-line tool instead of speaking OpenAI-compatible HTTP. The `codex` CLI
performs the browser "Sign in with ChatGPT" OAuth and runs the model on the
user's own ChatGPT subscription — so this provider needs **no API key**. The
user installs the Codex CLI and runs `codex login` (browser) once; Hydra then
routes turns through it.

It duck-types the same surface the rest of Hydra depends on (the
`OllamaClient` shape: `chat(...) -> ChatResponse`, `list_models() -> [...]`),
so `model_router`, `roles`, and the CLI reach it through `make_client` with no
special-casing.

The verified invocation shape is:

    printf '%s' "$PROMPT" | codex exec --skip-git-repo-check -C <cwd> -o <tmp> -

The flattened prompt is fed on STDIN. `codex exec` prints noisy progress/log
lines to stdout; the `-o <FILE>` flag writes ONLY the agent's final message to a
file, so we read a clean answer from that file instead of scraping stdout.

Binary resolution is generic: `HYDRA_CODEX_BIN` env override first, then
`shutil.which("codex")` on PATH. If neither resolves, we raise a clear error
telling the user to install the Codex CLI and run `codex login`. No personal or
machine-specific paths are baked in.

`tools` are accepted on `chat()` (the agent loop passes OpenAI tool schemas)
but NOT forwarded — `codex exec` drives its own tools. Documented here, not
silently dropped.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from threading import Event
from typing import Callable, Iterable

from hydra.llm import ChatMessage, ChatResponse, LlmError


# Default model label. The actual model is governed by the user's `codex login`
# / Codex CLI configuration; this is just the catalog name Hydra reports and can
# be overridden per call or at construction.
_DEFAULT_MODEL = "codex"

# Substrings that pin the cause of a FAILED turn (no valid final answer). Only
# consulted when there is NO clean answer — a non-empty `-o` answer with exit 0
# is success even if one of these appears as a recovered-from log warning.
_OUTAGE_MARKERS: tuple[str, ...] = (
    "exceeded retry limit",
    "403 forbidden",
    "429 too many requests",
    "401 unauthorized",
    "stream error",
)

# Shown when the CLI can't be found — tells the user exactly what to do.
_INSTALL_HINT = (
    "could not find the `codex` CLI on PATH — install the Codex CLI "
    "(e.g. `npm install -g @openai/codex`), run `codex login` to sign in with "
    "ChatGPT, or set HYDRA_CODEX_BIN to the codex binary path"
)


def configure_native_scope(client, *, root: str | Path, approval_policy: str) -> None:
    """Bind native tools to the same selected scope and write policy as Hydra."""
    if isinstance(client, CodexClient):
        client.cd = str(Path(root).expanduser().resolve())
        # Native exec cannot call Hydra's interactive approval callback.
        client.sandbox = "workspace-write" if approval_policy == "allow" else "read-only"


def resolve_codex_bin() -> str:
    """Resolve the `codex` binary path generically.

    Order: `HYDRA_CODEX_BIN` env override (if set and non-empty), then
    `shutil.which("codex")` on the inherited PATH. Raises `LlmError` with a
    clear install/login hint if neither resolves.
    """
    override = os.environ.get("HYDRA_CODEX_BIN", "").strip()
    if override:
        return override
    found = shutil.which("codex")
    if found:
        return found
    raise LlmError(_INSTALL_HINT)


def codex_command(
    prompt: str,
    *,
    cwd: str,
    output_file: str,
    model: str | None = None,
    codex_bin: str = "codex",
) -> list[str]:
    """Build the exact `codex exec` argv list.

    The prompt is fed on STDIN (the trailing ``-``), so it is not part of argv.
    Shape:

        [codex_bin, "exec", "--skip-git-repo-check",
         (optional) "-m", model,
         "-C", cwd, "-o", output_file, "-"]
    """
    argv = [codex_bin, "exec", "--skip-git-repo-check"]
    if model:
        argv.extend(["-m", model])
    argv.extend(["-C", cwd, "-o", output_file, "-"])
    return argv


def _native_launcher_argv(argv: list[str]) -> list[str]:
    """Honor an explicit Python launcher on systems without shebang execution.

    The shebang must be one complete, existing absolute interpreter path. No
    shell or command-string splitting is involved, including when paths contain
    spaces. Other executable formats retain their normal OS handling.
    """
    executable = Path(argv[0])
    if not executable.is_absolute() and not os.path.dirname(argv[0]):
        return argv
    try:
        if not executable.is_file():
            return argv
        with executable.open("rb") as source:
            header = source.readline(4097)
        if len(header) > 4096 or not header.startswith(b"#!"):
            return argv
        interpreter = Path(header[2:].decode("utf-8").strip())
        if (not interpreter.is_absolute() or not interpreter.is_file()
                or re.fullmatch(r"pythonw?(?:\d+(?:\.\d+)*)?(?:\.exe)?",
                                interpreter.name, re.IGNORECASE) is None):
            return argv
        return [str(interpreter), *argv]
    except (OSError, UnicodeError, ValueError):
        return argv


def _default_runner(argv, *, input=None, capture_output=True, text=True, timeout=None,
                    cancel_event: Event | None = None):
    """Run a native provider in an owned group with cancellation and a deadline.

    Injected so unit tests can pass a fake and never touch the CLI/account.
    """
    from hydra.proc import kill_owned_group, popen_portable

    if cancel_event is not None and cancel_event.is_set():
        raise LlmError("codex exec cancelled before launch")
    deadline = time.monotonic() + timeout if timeout is not None else None
    process = popen_portable(
        _native_launcher_argv(argv),
        stdin=subprocess.PIPE if input is not None else None,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=text,
        encoding="utf-8" if text else None,
    )
    pending_input = input
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise LlmError("codex exec cancelled")
            remaining = deadline - time.monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            try:
                stdout, stderr = process.communicate(
                    input=pending_input,
                    timeout=min(0.1, remaining) if remaining is not None else 0.1,
                )
                return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                # communicate() retains buffered output and any unwritten stdin;
                # subsequent polls must not submit the same input a second time.
                pending_input = None
    finally:
        # The group is owned by popen_portable. POSIX group ownership survives
        # the leader; Windows uses the existing bounded taskkill /T tree cleanup.
        kill_owned_group(process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


class CodexClient:
    """LLM client that runs the `codex` CLI ("Sign in with ChatGPT").

    Shells `printf prompt | codex exec --skip-git-repo-check -C <cwd> -o <tmp> -`
    and reads the agent's clean final message from the `-o` file. Requires the
    Codex CLI on PATH (or `HYDRA_CODEX_BIN`) and the user having run
    `codex login` — no API key. Duck-types the consumer-facing slice of
    `OllamaClient` (`chat(...)`, `list_models()`).
    """

    def __init__(
        self,
        *,
        codex_bin: str | None = None,
        cd: str | None = None,
        model: str = _DEFAULT_MODEL,
        runner: Callable[..., object] | None = None,
        sandbox: str | None = None,
    ) -> None:
        self.codex_bin = codex_bin or resolve_codex_bin()
        # Default working dir is the process cwd — generic, no hardcoded path.
        self.cd = cd or os.getcwd()
        self.model = model or _DEFAULT_MODEL
        self._runner = runner or _default_runner
        self.sandbox = sandbox  # e.g. "read-only" to enforce read-only CLI sandbox
        self.cancel_event: Event | None = None

    def set_cancellation_event(self, event: Event | None) -> None:
        """Attach the active turn's cancellation token to owned native work."""
        self.cancel_event = event

    # --- public surface (mirrors OllamaClient) ---------------------------

    def chat(
        self,
        messages: Iterable[ChatMessage] | list[dict],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        timeout: float = 180.0,
        tools: list[dict] | None = None,
    ) -> ChatResponse:
        """Run one Codex turn.

        `messages` are flattened into a single prompt fed to `codex exec` on
        STDIN; the agent's final message is read from the `-o` file and returned
        as `ChatResponse.content`. `tools` is accepted but NOT forwarded —
        `codex exec` drives its own tools.
        """
        chosen_model = model or self.model
        prompt = self._flatten(messages)
        if not prompt.strip():
            raise LlmError("codex chat: messages flattened to an empty prompt")

        # Temp file for the clean final message. Random name under the OS temp
        # dir so concurrent calls never collide; deleted after we read it.
        out_file = os.path.join(
            tempfile.gettempdir(), f"hydra-codex-{uuid.uuid4().hex}.txt"
        )

        # Only pass `-m` when the model differs from the generic default label —
        # otherwise let the user's `codex login` config pick the model.
        model_arg = chosen_model if chosen_model and chosen_model != _DEFAULT_MODEL else None
        argv = codex_command(
            prompt,
            cwd=self.cd,
            output_file=out_file,
            model=model_arg,
            codex_bin=self.codex_bin,
        )
        # --sandbox enforces an OS-level restriction (e.g. "read-only"). Only
        # inserted when explicitly set; omitting it keeps default behavior.
        if self.sandbox is not None:
            # insert right after `exec` so it precedes -C/-o/-.
            argv[2:2] = ["--sandbox", self.sandbox]

        try:
            runner_options = dict(input=prompt, capture_output=True, text=True, timeout=timeout)
            # Preserve the existing injected-runner contract. Real native work
            # alone owns processes and accepts the turn cancellation token.
            if self._runner is _default_runner:
                runner_options["cancel_event"] = self.cancel_event
            proc = self._runner(argv, **runner_options)
        except Exception as exc:  # TimeoutExpired, OSError, ValueError, etc.
            if type(exc).__name__ == "TimeoutExpired":
                raise LlmError(f"codex exec timed out after {timeout}s") from exc
            raise LlmError(f"could not run codex exec: {exc}") from exc

        stdout = getattr(proc, "stdout", "") or ""
        stderr = getattr(proc, "stderr", "") or ""
        returncode = getattr(proc, "returncode", 0) or 0
        combined = f"{stdout}\n{stderr}"

        # The clean final message the runner wrote to `-o` is the authoritative
        # success signal: a non-empty answer with a clean exit is success even
        # when a transient warning is present in the logs (never a fake red).
        content = self._read_last_message(out_file, stdout).strip()
        if returncode == 0 and content:
            return ChatResponse(
                content=content,
                model=chosen_model,
                finish_reason="stop",
                prompt_tokens=0,
                completion_tokens=0,
                raw={"stdout": stdout, "stderr": stderr, "returncode": returncode},
                tool_calls=[],
            )

        # No valid final answer — diagnose the failure (surfaced, never swallowed).
        lowered = combined.lower()
        for marker in _OUTAGE_MARKERS:
            if marker in lowered:
                raise LlmError(
                    f"codex outage: matched {marker!r} "
                    f"({stderr.strip()[:300] or stdout.strip()[:300]})"
                )

        if returncode != 0:
            raise LlmError(
                f"codex exec exited {returncode}: "
                f"{stderr.strip()[:300] or stdout.strip()[:300] or 'no output'}"
            )

        raise LlmError(
            "codex exec produced no final message (empty answer) — "
            "treated as an outage, not silent success"
        )

    def list_models(self, *, timeout: float = 10.0) -> list[str]:
        """Codex exposes the user-configured model as a single catalog entry."""
        return [self.model]

    # --- internals -------------------------------------------------------

    @staticmethod
    def _flatten(messages: Iterable[ChatMessage] | list[dict]) -> str:
        """Flatten chat messages into one prompt string for `codex exec`.

        codex exec takes a single instruction; role labels are kept so the model
        still sees system vs user framing. Accepts `ChatMessage` or raw dicts.
        """
        parts: list[str] = []
        for m in messages:
            if isinstance(m, ChatMessage):
                role, content = m.role, m.content
            elif isinstance(m, dict):
                role = m.get("role", "user")
                content = m.get("content") or ""
            else:
                raise LlmError(
                    f"codex chat: message must be ChatMessage or dict, got "
                    f"{type(m).__name__}"
                )
            if not isinstance(content, str):
                content = str(content)
            if not content.strip():
                continue
            if role in ("user", "") or role is None:
                parts.append(content)
            else:
                parts.append(f"[{role}]\n{content}")
        return "\n\n".join(parts)

    def _read_last_message(self, out_file: str, stdout: str) -> str:
        """Read the `-o` last-message file, then delete it.

        If the file yields nothing, fall back to stdout (best effort); the caller
        treats an empty answer as an outage.
        """
        try:
            if os.path.isfile(out_file):
                with open(out_file, "r", encoding="utf-8") as fh:
                    data = fh.read()
                try:
                    os.remove(out_file)
                except OSError:
                    pass
                if data.strip():
                    return data
        except OSError:
            pass
        return stdout
