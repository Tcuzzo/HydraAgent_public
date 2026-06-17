"""hydra.go_bridge — single Python surface to the native Go runtime.

Replaces the per-subcommand subprocess sprawl. Locates the compiled
binary (preferred) or falls back to `go run` (dev convenience), invokes
subcommands uniformly, parses JSON, applies secret redaction, returns
typed envelopes.

Binary discovery (first hit wins):
  1. $HYDRA_GO_BINARY    — explicit override
  2. ./bin/hydra-harness — `make build-go` output
  3. `go run ./cmd/hydra-harness` — slow but always-on for devs

Every public method returns a dict with at minimum:
    {"schema": "...", "status": "ok"|"failed"|"unavailable", ...payload}
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SCHEMA = "hydra.go_bridge.v1"
REPO_ROOT = Path(__file__).resolve().parents[1]
COMPILED_BINARY = REPO_ROOT / "bin" / "hydra-harness"

Runner = Callable[..., subprocess.CompletedProcess[str]]

_SECRET_VALUE = re.compile(
    r"(?i)(sk-(?:ant-|proj-)?[a-z0-9_-]{8,}|xox[baprs]-[a-z0-9-]{8,}|gh[pousr]_[a-z0-9_]{8,}|[a-z0-9_]*token[a-z0-9_]*\s*[:=]\s*\S+)"
)
_SECRET_KEY = re.compile(r"(?i)(api[_-]?key|token|secret|cookie|password|credential)")


@dataclass(frozen=True)
class GoInvocation:
    """Resolved command + working directory for one Go subcommand call."""
    command: list[str]
    cwd: str


class GoBridge:
    """One Python surface to every native Go subcommand."""

    def __init__(
        self,
        *,
        repo_root: str | Path | None = None,
        binary_path: str | Path | None = None,
        runner: Runner = subprocess.run,
        timeout: float = 60.0,
    ) -> None:
        self.repo_root = Path(repo_root or REPO_ROOT).expanduser().resolve()
        self._runner = runner
        self._timeout = timeout
        self._binary_override = Path(binary_path).expanduser().resolve() if binary_path else None

    # ── Subcommand wrappers ───────────────────────────────────────────────

    def audit(self) -> dict[str, Any]:
        """Run repo audit. Returns languages, build system, agent surfaces, risks."""
        return self._invoke(["audit"])

    def capabilities(self) -> dict[str, Any]:
        """Discover binaries, services, skills, MCP configs on the host."""
        return self._invoke(["capabilities"])

    def models(self) -> dict[str, Any]:
        """Build provider matrix with health checks."""
        return self._invoke(["models"], timeout=max(self._timeout, 30.0))

    def input(
        self,
        text: str,
        *,
        source: str = "python",
        context: str | None = None,
    ) -> dict[str, Any]:
        """Route one operator message through the Go runtime."""
        args = ["input", "--source", source]
        if context and context.strip():
            args.extend(["--context", context.strip()])
        args.append(text)
        return self._invoke(args, timeout=max(self._timeout, 180.0))

    def eval(self) -> dict[str, Any]:
        """Completion gate check."""
        return self._invoke(["eval"])

    def review(self) -> dict[str, Any]:
        """Adversarial reviewer pass."""
        return self._invoke(["review"])

    # ── Plumbing ─────────────────────────────────────────────────────────

    def resolve_invocation(self, args: list[str]) -> GoInvocation:
        """Build the command line that would be executed for these args.

        Public so callers/tests can introspect without actually running Go.
        """
        binary = self._resolve_binary()
        if binary is not None:
            cmd = [str(binary), *args]
        else:
            cmd = ["go", "run", "./cmd/hydra-harness", *args]
        return GoInvocation(command=cmd, cwd=str(self.repo_root))

    def _resolve_binary(self) -> Path | None:
        if self._binary_override is not None:
            return self._binary_override if self._binary_override.exists() else None
        env_override = os.environ.get("HYDRA_GO_BINARY", "").strip()
        if env_override:
            path = Path(env_override).expanduser()
            return path if path.exists() else None
        compiled = self.repo_root / "bin" / "hydra-harness"
        return compiled if compiled.exists() else None

    def _invoke(
        self,
        args: list[str],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        invocation = self.resolve_invocation(args)
        effective_timeout = timeout if timeout is not None else self._timeout
        try:
            completed = self._runner(
                invocation.command,
                cwd=invocation.cwd,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
                env=_subprocess_env(),
            )
        except FileNotFoundError:
            return {
                "schema": SCHEMA,
                "status": "unavailable",
                "subcommand": args[0] if args else "",
                "reason": "go runtime not found (need either bin/hydra-harness or `go` on PATH)",
            }
        except subprocess.TimeoutExpired:
            return {
                "schema": SCHEMA,
                "status": "failed",
                "subcommand": args[0] if args else "",
                "reason": f"timeout after {effective_timeout}s",
            }

        if completed.returncode != 0:
            return {
                "schema": SCHEMA,
                "status": "failed",
                "subcommand": args[0] if args else "",
                "returncode": completed.returncode,
                "stdout": _redact_text(completed.stdout or "")[-1200:],
                "stderr": _redact_text(completed.stderr or "")[-1200:],
            }
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return {
                "schema": SCHEMA,
                "status": "failed",
                "subcommand": args[0] if args else "",
                "reason": "go runtime returned non-json output",
                "stdout": _redact_text(completed.stdout or "")[-1200:],
            }
        if isinstance(payload, dict) and "status" not in payload:
            payload = {"status": "ok", **payload}
        return _redact(payload)


# ── Redaction (shared with loop_runtime.py) ──────────────────────────────


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)):
                out[key] = "[REDACTED]"
            else:
                out[key] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(value: str) -> str:
    return _SECRET_VALUE.sub("[REDACTED]", value)


def _subprocess_env() -> dict[str, str]:
    """Carry workspace env files (.env.ollama-cloud, etc.) into the child subprocess."""
    env = dict(os.environ)
    for path in _operator_env_files():
        env.update(_parse_env_file(path))
    _apply_provider_aliases(env)
    return env


def _operator_env_files() -> list[Path]:
    configured = os.environ.get("HYDRA_ENV_DIR", "").strip()
    raw_dir = Path(configured).expanduser() if configured else Path.home() / ".hydraAgent" / "workspace"
    if not raw_dir.exists() or not raw_dir.is_dir():
        return []
    return sorted(raw_dir.glob(".env*"))


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            values[key] = value
    return values


def _apply_provider_aliases(env: dict[str, str]) -> None:
    aliases = {
        "OLLAMA_CLOUD_API_KEY": "OLLAMA_API_KEY",
        "OLLAMA_API_KEY": "OLLAMA_CLOUD_API_KEY",
        "OLLAMA_CLOUD_BASE_URL": "OLLAMA_CLOUD_ENDPOINT",
        "OLLAMA_CLOUD_ENDPOINT": "OLLAMA_CLOUD_BASE_URL",
    }
    for canonical, alias in aliases.items():
        if env.get(canonical) or not env.get(alias):
            continue
        env[canonical] = env[alias]
