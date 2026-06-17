"""hydra.providers — LLM provider registry, env-loaded.

The factory loads API keys from the operator's env files outside the
repo (default `~/.hydraAgent/workspace/.env.*`), then hands back a
configured `OllamaClient` (the generic OpenAI-compat client from
§10.20/§10.23) pointed at the right endpoint.

Built-in providers:
  - `ollama` — local, no auth, `http://localhost:11434`. Loads
    `OLLAMA_API_KEY` from `.env.ollama` if present (hosted Ollama).
  - `ollama-cloud` — cloud Ollama endpoint, `OLLAMA_CLOUD_API_KEY` from `.env.ollama-cloud`.
  - any user-named entry whose env file declares `<NAME>_API_KEY`
    and `<NAME>_ENDPOINT` (plus optional `<NAME>_MODEL`).

Forbidden providers: `anthropic` and `claude_api`. The §2 prohibition
stays in force at the factory boundary even if a user were to drop a
`.env.anthropic` file — the factory refuses to construct it.
This is the runtime mirror of the verifier's `forbidden_dependencies`
import check.

Maturity: SCAFFOLDED. Promoted by §10.24.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from hydra.llm import OllamaClient


DEFAULT_ENV_DIR = Path(os.path.expanduser("~/.hydraAgent/workspace"))


# Provider names whose construction is refused at the factory regardless
# of operator env files. Mirrors the §2 forbidden_dependencies list.
FORBIDDEN_PROVIDER_NAMES = frozenset({"anthropic", "claude_api", "claude"})


# Built-in provider defaults. `env_file` is read from `env_dir` at
# resolve time; values missing there are taken from the provider's
# defaults below. A provider that requires a key but doesn't get one
# raises `ProviderError` — fail-closed.
@dataclass(frozen=True)
class _ProviderSpec:
    name: str
    env_file: str
    endpoint_default: str
    model_default: str
    requires_key: bool


_BUILTINS: dict[str, _ProviderSpec] = {
    "ollama": _ProviderSpec(
        name="ollama",
        env_file=".env.ollama",
        endpoint_default="http://localhost:11434",
        model_default="qwen3:8b",
        requires_key=False,
    ),
    "ollama-cloud": _ProviderSpec(
        name="ollama-cloud",
        env_file=".env.ollama-cloud",
        endpoint_default="https://api.ollama.cloud",
        model_default="qwen2.5:72b",  # SSOT: hydra/model_routing.yaml cloud-planner
        requires_key=True,
    ),
    # minimax: OpenAI-compatible big-token cloud workhorse.
    # Key loaded from MINIMAX_API_KEY env var or workspace .env file.
    "minimax": _ProviderSpec(
        name="minimax",
        env_file=".env.minimax",
        endpoint_default="https://api.minimax.io",  # client appends /v1/chat/completions
        model_default="MiniMax-Text-01",
        requires_key=True,
    ),
}

_PROVIDER_ALIASES: dict[str, str] = {
    "ollama_cloud": "ollama-cloud",
}

_ENV_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "OLLAMA_ENDPOINT": ("OLLAMA_BASE_URL",),
    "OLLAMA_CLOUD_ENDPOINT": ("OLLAMA_CLOUD_BASE_URL",),
}


class ProviderError(Exception):
    """A provider could not be resolved or constructed."""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    endpoint: str
    model: str
    api_key: str | None


def _parse_env_file(path: Path) -> dict[str, str]:
    """Tiny dotenv reader — `KEY=value` lines, comments stripped, quotes
    optional. Same shape as the operator env files this is built for."""
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def _env_prefix(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", name).upper().strip("_")


def _canonical_provider_name(name: str) -> str:
    return _PROVIDER_ALIASES.get(name, name)


def _lookup_env_value(env: dict[str, str], key: str) -> str | None:
    for candidate in (key, *_ENV_KEY_ALIASES.get(key, ())):
        if candidate in env and env[candidate]:
            return env[candidate]
        if candidate in os.environ and os.environ[candidate]:
            return os.environ[candidate]
    return None


def list_providers() -> list[str]:
    """Names every built-in provider; the factory accepts these (or any
    operator-configured name with a matching env file)."""
    return sorted(_BUILTINS)


def resolve(
    name: str, *, env_dir: str | Path | None = None
) -> ProviderConfig:
    """Resolve a provider name into a ProviderConfig. Refuses
    forbidden names; raises `ProviderError` on missing required keys
    or unknown unconfigured names."""
    if not name or not isinstance(name, str):
        raise ProviderError(f"provider name must be a non-empty string, got {name!r}")
    name = _canonical_provider_name(name)
    if name.lower() in FORBIDDEN_PROVIDER_NAMES:
        raise ProviderError(
            f"provider {name!r} is on the §2 forbidden list — "
            f"`anthropic` / `claude_api` cannot be wired up"
        )
    env_root = Path(env_dir) if env_dir else DEFAULT_ENV_DIR

    spec = _BUILTINS.get(name)
    if spec is None:
        # Operator-named provider: look for a `.env.<name>` file declaring
        # at least `<NAME>_ENDPOINT` and `<NAME>_API_KEY`.
        env_path = env_root / f".env.{name}"
        env = _parse_env_file(env_path)
        upper = _env_prefix(name)
        endpoint = env.get(f"{upper}_ENDPOINT")
        api_key = env.get(f"{upper}_API_KEY")
        model = env.get(f"{upper}_MODEL") or ""
        if not endpoint or not api_key:
            raise ProviderError(
                f"unknown provider {name!r}: expected "
                f"{env_path} with {upper}_ENDPOINT + {upper}_API_KEY"
            )
        return ProviderConfig(
            name=name, endpoint=endpoint, model=model, api_key=api_key
        )

    env = _parse_env_file(env_root / spec.env_file)
    for alias, canonical in _PROVIDER_ALIASES.items():
        if canonical == spec.name:
            env = {**_parse_env_file(env_root / f".env.{alias}"), **env}
    upper = _env_prefix(spec.name)
    endpoint = _lookup_env_value(env, f"{upper}_ENDPOINT") or spec.endpoint_default
    model = _lookup_env_value(env, f"{upper}_MODEL") or spec.model_default
    api_key = _lookup_env_value(env, f"{upper}_API_KEY") or None

    if spec.requires_key and not api_key:
        raise ProviderError(
            f"provider {spec.name!r} requires a key — set "
            f"{upper}_API_KEY in {env_root / spec.env_file}"
        )
    return ProviderConfig(
        name=spec.name, endpoint=endpoint, model=model, api_key=api_key
    )


def make_client(
    name: str, *, env_dir: str | Path | None = None
):
    """Build a configured client for `name` and return it along with the
    resolved `ProviderConfig` (so callers know which model/endpoint they got).

    Returns an `OllamaClient` (OpenAI-compat HTTP client) for all HTTP providers.
    """
    cfg = resolve(name, env_dir=env_dir)
    client = OllamaClient(endpoint=cfg.endpoint, api_key=cfg.api_key)
    return client, cfg
