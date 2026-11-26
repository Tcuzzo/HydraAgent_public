"""Resolve public Hydra roles from operator-configured standalone providers.

Configuration discovery is local. Only an unconfigured local model may query
its own configured Ollama endpoint; no external routing service participates.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import urllib.request
from collections.abc import Callable
from typing import Any

from hydra.llm import LlmError

LOCAL = "local"


class RungResolutionError(LlmError):
    """No configured standalone provider can serve this routing role."""


def decision_timeout(explicit: float | None = None) -> float:
    """Seconds to wait on the configured decision provider. An explicit value wins; otherwise
    ``HYDRA_DECISION_TIMEOUT`` or the cold-start default."""
    if explicit is not None:
        return explicit
    raw = os.environ.get("HYDRA_DECISION_TIMEOUT", "").strip()
    if not raw:
        return 30.0
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    if value <= 0:
        return 30.0
    return value


def _get(url: str, timeout: float) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.load(response)
    except (OSError, ValueError) as exc:
        raise RungResolutionError(f"local model discovery failed: {exc}") from exc


def _provider_files(root: Path) -> list[str]:
    return [path.name[5:] for path in sorted(root.glob(".env.*")) if path.is_file()]


def _provider_names(rung: str, env_dir: str | Path | None) -> list[str]:
    from hydra.providers import default_env_dir, list_providers

    root = Path(env_dir) if env_dir is not None else default_env_dir()
    role_key = "HYDRA_" + re.sub(r"[^A-Za-z0-9]", "_", rung).upper() + "_PROVIDER"
    preferred = [os.environ.get(role_key, ""), os.environ.get("HYDRA_PROVIDER", "")]
    files = _provider_files(root)
    return list(dict.fromkeys(name for name in [*preferred, *files, *list_providers()] if name))


def _configured_model(config, env_dir: str | Path | None) -> str:
    from hydra.providers import default_env_dir, is_cli_transport

    if config.model:
        return config.model
    root = Path(env_dir) if env_dir is not None else default_env_dir()
    if is_cli_transport(config.name) and (root / f".env.{config.name}").is_file():
        return config.name  # The CLI owns its model default; this is a transport label.
    return ""


def _cloud_pair(rung: str, env_dir: str | Path | None) -> tuple[str, str] | None:
    from hydra.providers import ProviderError, is_local_provider, resolve as resolve_provider

    for name in _provider_names(rung, env_dir):
        try:
            config = resolve_provider(name, env_dir=env_dir, rung_model=False)
        except ProviderError:
            continue
        model = _configured_model(config, env_dir)
        if model and not is_local_provider(config.name):
            return config.name, model
    return None


def configured_pair(rung: str = "", *, local: bool = False,
                    env_dir: str | Path | None = None) -> tuple[str, str]:
    """Read the configured route without contacting any provider."""
    from hydra.providers import resolve as resolve_provider

    if not local:
        cloud = _cloud_pair(rung, env_dir)
        if cloud is not None:
            return cloud
    config = resolve_provider(LOCAL, env_dir=env_dir, rung_model=False)
    return LOCAL, config.model


def _row_model(row: dict) -> str:
    return str(row.get("model") or row.get("name") or "")


def _model_name(rows: list[dict]) -> str | None:
    for row in rows:
        name = _row_model(row)
        if name and "embed" not in name.lower():
            return name
    return None


def _served_model(payload: dict | None) -> str | None:
    return _model_name((payload or {}).get("models") or [])


def _local_served_model(env_dir: str | Path | None, fetch: Callable | None, timeout: float) -> str | None:
    from hydra.providers import resolve as resolve_provider

    base = resolve_provider(LOCAL, env_dir=env_dir, rung_model=False).endpoint.rstrip("/")
    get = fetch or _get
    for path in ("/api/ps", "/api/tags"):
        try:
            model = _served_model(get(f"{base}{path}", timeout))
        except RungResolutionError:
            continue
        if model:
            return model
    return None


def resolve(rung: str, *, local: bool = False, task: str | None = None,
            env_dir: str | Path | None = None, fetch: Callable | None = None,
            timeout: float = 5.0) -> tuple[str, str]:
    """Resolve a role to its configured provider/model, discovering local models only."""
    provider, model = configured_pair(rung, local=local, env_dir=env_dir)
    if not model and provider == LOCAL:
        model = _local_served_model(env_dir, fetch, min(timeout, 5.0))
    if not model:
        raise RungResolutionError("no model configured; run hydra setup or configure a provider model")
    return provider, model
