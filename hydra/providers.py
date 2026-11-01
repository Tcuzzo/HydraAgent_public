"""hydra.providers — LLM provider registry, env-loaded.

The factory loads API keys from the operator's env files outside the
repo (default `~/.hydraAgent/workspace/.env.*`), then hands back a
configured `OllamaClient` (the generic OpenAI-compat client from
§10.20/§10.23) pointed at the right endpoint.

Chat routing does not pick providers here: hydra/model_routing.yaml names BACKS
rungs, and hydra/rung_resolver.py resolves them through BACKS at call time (the
`backs` gateway transport, or `local` for this host's GPU). The transports below
carry no model name; a model always comes from a resolved rung or the operator's
env file.

Built-in transports:
  - the keyless local endpoint (`local`), `http://localhost:11434`.
  - `backs` — the BACKS rung gateway; location from HYDRA_BACKS_URL or `.env.backs`.
  - `needle` — optional local tool-selection SDK; Hydra executes tool calls.
  - the hosted and CLI transports listed in `_BUILTINS`.
  - any named OpenAI-compatible endpoint whose env file declares `<NAME>_ENDPOINT`
    (plus an optional key and `<NAME>_MODEL`).

Provider names and model families are unrestricted. A named provider still
needs the matching transport: native proprietary APIs need a compatible proxy.

Maturity: SCAFFOLDED. Promoted by §10.24.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from hydra.llm import LlmError, OllamaClient


DEFAULT_ENV_DIR = Path(os.path.expanduser("~/.hydraAgent/workspace"))


# Compatibility export; model families are selected by the user.
FORBIDDEN_PROVIDER_NAMES: frozenset[str] = frozenset()


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
    "needle": _ProviderSpec(
        name="needle", env_file=".env.needle", endpoint_default="",
        model_default="needle3", requires_key=False,
    ),
    "ollama": _ProviderSpec(
        name="ollama",
        env_file=".env.ollama",
        endpoint_default="http://localhost:11434",
        model_default="",
        requires_key=False,
    ),
    "ollama-cloud": _ProviderSpec(
        name="ollama-cloud",
        env_file=".env.ollama-cloud",
        endpoint_default="https://api.ollama.cloud",
        model_default="",  # the model comes from a resolved BACKS rung
        requires_key=True,
    ),
    # Hosted OpenAI-compatible transport; key and model from the operator's env file.
    "minimax": _ProviderSpec(
        name="minimax",
        env_file=".env.minimax",
        endpoint_default="https://api.minimax.io",  # client appends /v1/chat/completions
        model_default="",
        requires_key=True,
    ),
    # codex: the official `codex` CLI ("Sign in with ChatGPT"). NOT an HTTP
    # provider — it shells the local Codex CLI, which runs on the user's ChatGPT
    # subscription via browser OAuth (run `codex login` once). No endpoint and no
    # api_key (requires_key=False; the OAuth lives in the CLI, never copied).
    # `make_client` returns a CodexClient for it instead of the HTTP OllamaClient.
    "codex": _ProviderSpec(
        name="codex",
        env_file=".env.codex",
        endpoint_default="",
        model_default="",
        requires_key=False,
    ),
    # The BACKS rung gateway: Kev/Needle on the BACKS host pick the model per
    # request and hold the keys, so Hydra sends none.
    "backs": _ProviderSpec(
        name="backs",
        env_file=".env.backs",
        endpoint_default="",
        model_default="",
        requires_key=False,
    ),
}

_PROVIDER_ALIASES: dict[str, str] = {
    "local": "ollama",
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


def is_local_provider(name: str) -> bool:
    """True for this host's GPU transport (under any of its names)."""
    return _canonical_provider_name(str(name or "")) == "ollama"


#: The one transport that shells the user's own signed-in vendor CLI (registry entry
#: above) instead of an HTTP endpoint. Other modules ask the registry; they never
#: spell the name.
CLI_TRANSPORT = "codex"
#: The family a CLI-transport setup records for itself.
CLI_TRANSPORT_FAMILY = "openai-codex"
#: Families the CLI transport's own vendor would be grading itself on.
CLI_VENDOR_FAMILIES: frozenset[str] = frozenset({"openai", CLI_TRANSPORT_FAMILY, CLI_TRANSPORT})
#: What setup tells a person about the CLI transport (label, how to get it, sign-in).
CLI_TRANSPORT_LABEL = "Sign in with ChatGPT"
CLI_TRANSPORT_LOGIN = "codex login"
CLI_TRANSPORT_INSTALL = (
    "To use your ChatGPT login you need OpenAI's free Codex CLI:\n"
    "  1. Install it:  npm install -g @openai/codex\n"
    "  2. Sign in:     codex login   (opens your browser)\n"
    "Then run hydra again and pick this option."
)


def is_cli_transport(name: str) -> bool:
    """True for the signed-in vendor CLI transport (under any of its names)."""
    return _canonical_provider_name(str(name or "")) == CLI_TRANSPORT


def cli_transport_status() -> tuple[bool, str]:
    """Whether the CLI transport's binary resolves here (PATH or its env override)."""
    try:
        from hydra.codex_client import resolve_codex_bin

        bin_path = resolve_codex_bin()
    except Exception as e:  # noqa: BLE001
        return False, f"EVALUATOR_UNAVAILABLE: {e}"
    return True, f"available (CLI transport: {bin_path})"


CLI_TRANSPORT_BIN_ENV = f"HYDRA_{CLI_TRANSPORT.upper()}_BIN"


def cli_transport_installed(which) -> bool:
    """True when the binary override is set or ``which`` finds the CLI transport."""
    if os.environ.get(CLI_TRANSPORT_BIN_ENV, "").strip():
        return True
    return which(CLI_TRANSPORT) is not None


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


#: Transports whose default model is a resolved BACKS rung (local GPU / gateway).
_RUNG_TRANSPORTS = {"ollama": True, "backs": False}  # name -> is the local rung


def _rung_default_model(transport: str) -> str:
    """The model a bare transport should use: whatever its rung resolves to now."""
    from hydra.model_routing import load_routing

    local = _RUNG_TRANSPORTS[transport]
    try:
        for entry in load_routing().roster:
            if entry.local == local:
                return entry.model
    except Exception:  # noqa: BLE001 - no rung => caller decides
        return ""
    return ""


def resolve(
    name: str, *, env_dir: str | Path | None = None, rung_model: bool = True
) -> ProviderConfig:
    """Resolve a provider name into a ProviderConfig. Raises `ProviderError` on missing required keys
    or unknown unconfigured names."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        raise ProviderError(f"provider name must use letters, digits, underscores, or hyphens, got {name!r}")
    name = _canonical_provider_name(name)
    env_root = Path(env_dir) if env_dir else DEFAULT_ENV_DIR

    spec = _BUILTINS.get(name)
    if spec is None:
        # Operator-named provider: look for a `.env.<name>` file declaring
        # at least `<NAME>_ENDPOINT` and `<NAME>_API_KEY`.
        env_path = env_root / f".env.{name}"
        env = _parse_env_file(env_path)
        upper = _env_prefix(name)
        endpoint = _lookup_env_value(env, f"{upper}_ENDPOINT") or _lookup_env_value(env, f"{upper}_BASE_URL")
        api_key = _lookup_env_value(env, f"{upper}_API_KEY")
        model = _lookup_env_value(env, f"{upper}_MODEL") or ""
        if not endpoint:
            raise ProviderError(
                f"unknown provider {name!r}: expected "
                f"{upper}_ENDPOINT in the process environment or {env_path}"
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
    # rung_model=False: the rung resolver only wants the endpoint (asking the rung
    # for its model from inside rung resolution would recurse).
    if not model and rung_model and spec.name in _RUNG_TRANSPORTS:
        model = _rung_default_model(spec.name)
    api_key = _lookup_env_value(env, f"{upper}_API_KEY") or None

    if spec.requires_key and not api_key:
        raise ProviderError(
            f"provider {spec.name!r} requires a key — set "
            f"{upper}_API_KEY in {env_root / spec.env_file}"
        )
    return ProviderConfig(
        name=spec.name, endpoint=endpoint, model=model, api_key=api_key
    )


def _make_transport_client(
    name: str, *, env_dir: str | Path | None = None
):
    """Build a configured client for `name` and return it along with the
    resolved `ProviderConfig` (so callers know which model/endpoint they got).

    For every HTTP provider this is an `OllamaClient` (OpenAI-compat). For the
    `codex` provider it is a `CodexClient` instead — the official `codex` CLI
    ("Sign in with ChatGPT"), which runs on the user's ChatGPT subscription with
    no API key. Same duck-typed `.chat()` / `.list_models()` surface, so
    model_router, roles, and the CLI reach it unchanged.

    The codex path raises `LlmError` with a clear install/login hint when the
    `codex` binary is not found (PATH or `HYDRA_CODEX_BIN`) — not an import
    crash.
    """
    cfg = resolve(name, env_dir=env_dir)
    if cfg.name == "needle":
        from hydra.needle_client import NeedleClient

        return NeedleClient(model=cfg.model), cfg
    if cfg.name == "codex":
        # Imported lazily so the HTTP path never pays for the codex module and
        # there is no import cycle.
        from hydra.codex_client import CodexClient

        kwargs: dict = {}
        if cfg.model:
            kwargs["model"] = cfg.model
        # CodexClient.__init__ resolves the binary (HYDRA_CODEX_BIN or
        # shutil.which), raising the clear install/login error if absent.
        client = CodexClient(**kwargs)
        return client, cfg
    if cfg.name == "backs":
        from hydra.rung_resolver import RungGatewayClient, backs_url

        endpoint = cfg.endpoint or backs_url(env_dir)
        return RungGatewayClient(endpoint=endpoint), replace(cfg, endpoint=endpoint)
    num_ctx = None if cfg.api_key else local_num_ctx(cfg.name, env_dir=env_dir)
    client = OllamaClient(endpoint=cfg.endpoint, api_key=cfg.api_key, num_ctx=num_ctx)
    return client, cfg


def local_num_ctx(name: str, *, env_dir: str | Path | None = None) -> int:
    """Context window a keyless provider asks its server for: ``<NAME>_NUM_CTX``
    from the provider's env file (or the process env). 0 = server default."""
    name = _canonical_provider_name(name)
    spec = _BUILTINS.get(name)
    env_root = Path(env_dir) if env_dir else DEFAULT_ENV_DIR
    env = _parse_env_file(env_root / (spec.env_file if spec else f".env.{name}"))
    raw = _lookup_env_value(env, f"{_env_prefix(name)}_NUM_CTX") or ""
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return 0


def make_client(name: str, *, env_dir: str | Path | None = None, health_path: str | Path | None = None):
    """Build the requested transport with durable health checks on every chat."""
    from hydra.provider_health import attach_health
    client, cfg = _make_transport_client(name, env_dir=env_dir)
    return attach_health(client, cfg, health_path=health_path), cfg


def make_runtime_client(name: str, *, model: str | None = None, family: str = '',
                        env_dir: str | Path | None = None, health_path: str | Path | None = None,
                        fallbacks=()):
    """Explicit bounded fallback, preserving the actual provider/model/family."""
    from hydra.provider_health import RoutedClient
    def factory(provider):
        return make_client(provider, env_dir=env_dir, health_path=health_path)
    try:
        client, cfg = factory(name)
    except (ProviderError, LlmError):
        if not fallbacks:
            raise
        client, cfg = None, ProviderConfig(name=name, endpoint='', model=model or '', api_key=None)
    return RoutedClient(client, cfg, model=model or cfg.model, family=family,
                        fallbacks=fallbacks, factory=factory, health_path=health_path), cfg
