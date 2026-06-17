"""hydra.setup — operator configuration writer.

HydraAgent keeps provider secrets outside the repo. This module writes
small `.env.<provider>` files into the operator env directory so local,
cloud/API, and Codex OAuth based setups all use the same shape.
"""
from __future__ import annotations

import getpass
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from hydra.providers import DEFAULT_ENV_DIR, FORBIDDEN_PROVIDER_NAMES


class SetupError(Exception):
    """Setup input was incomplete or unsafe."""


@dataclass(frozen=True)
class SetupResult:
    provider: str
    path: Path
    keys_written: tuple[str, ...]


@dataclass(frozen=True)
class CommissioningReport:
    env_dir: Path
    has_local_gpu: bool | None
    automation_policy: str
    sudo_policy: str
    download_consent: bool
    launch_tui: bool
    results: tuple[SetupResult, ...]
    skipped: tuple[str, ...]
    links: tuple[str, ...]
    evidence_path: Path
    tui_command: str | None = None


AUTOMATION_POLICIES = frozenset({"ask", "auto", "yolo"})
SUDO_POLICIES = frozenset({"ask", "never", "yolo"})
# SSOT: hydra/model_routing.yaml (unified 2026-06-11). The ollama-cloud host is
# https://api.ollama.cloud everywhere (was spelled ollama.com here, which 403s).
SETUP_LINKS = {
    "codex": "https://platform.openai.com/docs",
    "ollama": "https://api.ollama.cloud",
    "ollama_cloud": "https://api.ollama.cloud",
    "openrouter": "https://openrouter.ai",
}


def env_prefix(provider: str) -> str:
    if not provider or not isinstance(provider, str):
        raise SetupError(f"provider must be a non-empty string, got {provider!r}")
    if provider.lower() in FORBIDDEN_PROVIDER_NAMES:
        raise SetupError(f"provider {provider!r} is forbidden")
    prefix = re.sub(r"[^A-Za-z0-9]", "_", provider).upper().strip("_")
    if not prefix:
        raise SetupError(f"provider {provider!r} has no usable env prefix")
    return prefix


def write_env_file(
    provider: str,
    values: dict[str, str],
    *,
    env_dir: str | Path | None = None,
) -> SetupResult:
    prefix = env_prefix(provider)
    root = Path(env_dir) if env_dir else DEFAULT_ENV_DIR
    root.mkdir(parents=True, exist_ok=True)
    path = root / f".env.{provider}"
    lines = [
        "# HydraAgent provider config. Keep this file outside the repo.",
    ]
    written: list[str] = []
    for key, value in values.items():
        if value is None or value == "":
            continue
        if not key.startswith(prefix + "_") and not key.startswith("CODEX_"):
            raise SetupError(f"key {key!r} does not match provider prefix {prefix!r}")
        lines.append(f"{key}={value}")
        written.append(key)
    if len(lines) == 1:
        raise SetupError(f"no values supplied for provider {provider!r}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return SetupResult(provider=provider, path=path, keys_written=tuple(written))


def _env_root(env_dir: str | Path | None = None) -> Path:
    return Path(env_dir) if env_dir else DEFAULT_ENV_DIR


def _yes_no(
    input_fn: Callable[[str], str],
    prompt: str,
    *,
    default: bool,
) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        raw = input_fn(prompt + suffix).strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Please answer y or n.")


def _choice(
    input_fn: Callable[[str], str],
    prompt: str,
    allowed: frozenset[str],
    *,
    default: str,
) -> str:
    allowed_text = "/".join(sorted(allowed))
    while True:
        raw = input_fn(f"{prompt} [{default}] ({allowed_text}) ").strip().lower()
        value = raw or default
        if value in allowed:
            return value
        print(f"Please choose one of: {allowed_text}.")


def _split_provider_names(raw: str) -> list[str]:
    names = [p.strip() for p in raw.split(",") if p.strip()]
    return list(dict.fromkeys(names))


def write_setup_evidence(report: CommissioningReport) -> Path:
    report.evidence_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "env_dir": str(report.env_dir),
        "has_local_gpu": report.has_local_gpu,
        "automation_policy": report.automation_policy,
        "sudo_policy": report.sudo_policy,
        "download_consent": report.download_consent,
        "launch_tui": report.launch_tui,
        "tui_command": report.tui_command,
        "configured": [
            {
                "provider": r.provider,
                "path": str(r.path),
                "keys_written": list(r.keys_written),
            }
            for r in report.results
        ],
        "skipped": list(report.skipped),
        "links": list(report.links),
        "secret_policy": (
            "Secrets are stored in provider env files outside the repo. "
            "Hydra records key names and paths only; it does not record key values."
        ),
    }
    text = json.dumps(payload, indent=2, sort_keys=True)
    report.evidence_path.write_text(text + "\n", encoding="utf-8")
    try:
        os.chmod(report.evidence_path, 0o600)
    except OSError:
        pass
    return report.evidence_path


def setup_local_ollama(
    *,
    endpoint: str = "http://localhost:11434",
    model: str = "qwen3:8b",
    api_key: str | None = None,
    env_dir: str | Path | None = None,
) -> SetupResult:
    values = {
        "OLLAMA_ENDPOINT": endpoint,
        "OLLAMA_MODEL": model,
    }
    if api_key:
        values["OLLAMA_API_KEY"] = api_key
    return write_env_file("ollama", values, env_dir=env_dir)


def setup_cloud_provider(
    provider: str,
    *,
    endpoint: str,
    model: str,
    api_key: str,
    env_dir: str | Path | None = None,
) -> SetupResult:
    prefix = env_prefix(provider)
    if not endpoint:
        raise SetupError("cloud endpoint is required")
    if not model:
        raise SetupError("cloud model is required")
    if not api_key:
        raise SetupError("cloud api key is required")
    return write_env_file(
        provider,
        {
            f"{prefix}_ENDPOINT": endpoint,
            f"{prefix}_MODEL": model,
            f"{prefix}_API_KEY": api_key,
        },
        env_dir=env_dir,
    )


def setup_codex_oauth(
    *,
    oauth_path: str,
    env_dir: str | Path | None = None,
) -> SetupResult:
    if not oauth_path:
        raise SetupError("Codex OAuth path is required")
    return write_env_file(
        "codex",
        {
            "CODEX_OAUTH_PATH": oauth_path,
            "CODEX_PROVIDER_FAMILY": "openai-codex",
        },
        env_dir=env_dir,
    )


def setup_operator_policy(
    *,
    has_local_gpu: bool | None,
    automation_policy: str,
    sudo_policy: str,
    download_consent: bool,
    launch_tui: bool,
    env_dir: str | Path | None = None,
) -> SetupResult:
    if automation_policy not in AUTOMATION_POLICIES:
        raise SetupError(f"unknown automation policy {automation_policy!r}")
    if sudo_policy not in SUDO_POLICIES:
        raise SetupError(f"unknown sudo policy {sudo_policy!r}")
    gpu_value = "unknown" if has_local_gpu is None else ("yes" if has_local_gpu else "no")
    return write_env_file(
        "hydra",
        {
            "HYDRA_LOCAL_GPU": gpu_value,
            "HYDRA_AUTOMATION_POLICY": automation_policy,
            "HYDRA_SUDO_POLICY": sudo_policy,
            "HYDRA_DOWNLOAD_CONSENT": "yes" if download_consent else "no",
            "HYDRA_LAUNCH_TUI": "yes" if launch_tui else "no",
        },
        env_dir=env_dir,
    )


def prompt_setup(
    *,
    input_fn: Callable[[str], str] = input,
    secret_fn: Callable[[str], str] = getpass.getpass,
    env_dir: str | Path | None = None,
) -> CommissioningReport:
    """Interactive first-run operator commissioning.

    The wizard records operator choices and writes provider pointers
    outside the repo. It does not install packages, pull models, run sudo,
    or reveal secrets without an explicit operator action outside this
    function.
    """
    root = _env_root(env_dir)
    evidence_path = root / "setup-evidence.json"
    results: list[SetupResult] = []
    skipped: list[str] = []
    links: list[str] = []

    print("HydraAgent first-run setup")
    print("Secrets stay outside this repo. Answer y/n for each capability.")

    gpu_known = _yes_no(input_fn, "Does this machine have a local GPU?", default=False)
    has_local_gpu: bool | None = gpu_known

    download_consent = _yes_no(
        input_fn,
        "Download/pull basic dependencies or models when a later step asks?",
        default=False,
    )
    automation_policy = _choice(
        input_fn,
        "Automation policy: ask, auto, or yolo",
        AUTOMATION_POLICIES,
        default="ask",
    )
    sudo_policy = _choice(
        input_fn,
        "Sudo policy: ask, never, or yolo",
        SUDO_POLICIES,
        default="ask",
    )
    if automation_policy == "yolo" or sudo_policy == "yolo":
        print("WARNING: YOLO mode is operator-selected. Keep `stop` ready in the TUI.")

    results.append(
        setup_operator_policy(
            has_local_gpu=has_local_gpu,
            automation_policy=automation_policy,
            sudo_policy=sudo_policy,
            download_consent=download_consent,
            launch_tui=False,
            env_dir=env_dir,
        )
    )

    local = _yes_no(input_fn, "Configure local Ollama?", default=True)
    if local:
        links.append(SETUP_LINKS["ollama"])
        endpoint = input_fn("Ollama endpoint [http://localhost:11434] ").strip()
        model = input_fn("Ollama model [qwen3:8b] ").strip()
        results.append(
            setup_local_ollama(
                endpoint=endpoint or "http://localhost:11434",
                model=model or "qwen3:8b",
                env_dir=env_dir,
            )
        )
    else:
        skipped.append("local_ollama")

    ollama_cloud = _yes_no(input_fn, "Configure Ollama Cloud/OpenAI-compatible endpoint?", default=False)
    if ollama_cloud:
        links.append(SETUP_LINKS["ollama_cloud"])
        endpoint = input_fn("Ollama Cloud endpoint ").strip()
        model = input_fn("Ollama Cloud model ").strip()
        api_key = secret_fn("Ollama Cloud API key (hidden) ").strip()
        results.append(
            setup_cloud_provider(
                "ollama_cloud",
                endpoint=endpoint,
                model=model,
                api_key=api_key,
                env_dir=env_dir,
            )
        )
    else:
        skipped.append("ollama_cloud")

    cloud = _yes_no(input_fn, "Configure other API providers?", default=False)
    if cloud:
        print("Known provider examples: openrouter. Claude/Anthropic is forbidden.")
        provider_names = _split_provider_names(
            input_fn("Provider names, comma-separated (example: openrouter) ").strip()
        )
        if not provider_names:
            raise SetupError("at least one provider name is required")
        for provider in provider_names:
            link = SETUP_LINKS.get(provider)
            if link:
                links.append(link)
            print(f"Configuring provider {provider}.")
            endpoint = input_fn(f"{provider} endpoint ").strip()
            model = input_fn(f"{provider} model ").strip()
            api_key = secret_fn(f"{provider} API key (hidden) ").strip()
            results.append(
                setup_cloud_provider(
                    provider,
                    endpoint=endpoint,
                    model=model,
                    api_key=api_key,
                    env_dir=env_dir,
                )
            )
    else:
        skipped.append("api_providers")

    codex = _yes_no(input_fn, "Configure Codex evaluator OAuth path?", default=False)
    if codex:
        links.append(SETUP_LINKS["codex"])
        print("Codex setup link:", SETUP_LINKS["codex"])
        print("Use your Codex login flow, then paste the local OAuth path.")
        oauth_path = input_fn("Codex OAuth path ").strip()
        results.append(setup_codex_oauth(oauth_path=oauth_path, env_dir=env_dir))
    else:
        skipped.append("codex_oauth")

    launch_tui = _yes_no(input_fn, "Launch the interactive TUI after setup?", default=True)
    tui_command = "python3 -m hydra chat --setup-if-needed" if launch_tui else None
    results[0] = setup_operator_policy(
        has_local_gpu=has_local_gpu,
        automation_policy=automation_policy,
        sudo_policy=sudo_policy,
        download_consent=download_consent,
        launch_tui=launch_tui,
        env_dir=env_dir,
    )

    if not download_consent:
        skipped.append("downloads")
    if not has_local_gpu:
        skipped.append("local_gpu_acceleration")
    if not results:
        skipped.append("provider_configuration")

    report = CommissioningReport(
        env_dir=root,
        has_local_gpu=has_local_gpu,
        automation_policy=automation_policy,
        sudo_policy=sudo_policy,
        download_consent=download_consent,
        launch_tui=launch_tui,
        results=tuple(results),
        skipped=tuple(dict.fromkeys(skipped)),
        links=tuple(dict.fromkeys(links)),
        evidence_path=evidence_path,
        tui_command=tui_command,
    )
    write_setup_evidence(report)
    return report


def prompt_legacy_provider_setup(
    *,
    input_fn: Callable[[str], str] = input,
    secret_fn: Callable[[str], str] = getpass.getpass,
    env_dir: str | Path | None = None,
) -> list[SetupResult]:
    """Old three-question provider setup kept for tests and small callers."""
    results: list[SetupResult] = []
    local = input_fn("Configure local Ollama? [Y/n] ").strip().lower()
    if local in {"", "y", "yes"}:
        endpoint = input_fn("Ollama endpoint [http://localhost:11434] ").strip()
        model = input_fn("Ollama model [qwen3:8b] ").strip()
        results.append(
            setup_local_ollama(
                endpoint=endpoint or "http://localhost:11434",
                model=model or "qwen3:8b",
                env_dir=env_dir,
            )
        )

    cloud = input_fn("Configure cloud/OpenAI-compatible API provider? [y/N] ").strip().lower()
    if cloud in {"y", "yes"}:
        provider = input_fn("Provider name (example: openrouter) ").strip()
        endpoint = input_fn("Provider endpoint ").strip()
        model = input_fn("Provider model ").strip()
        api_key = secret_fn("Provider API key (hidden) ").strip()
        results.append(
            setup_cloud_provider(
                provider,
                endpoint=endpoint,
                model=model,
                api_key=api_key,
                env_dir=env_dir,
            )
        )

    codex = input_fn("Configure Codex evaluator OAuth path? [y/N] ").strip().lower()
    if codex in {"y", "yes"}:
        oauth_path = input_fn("Codex OAuth path ").strip()
        results.append(setup_codex_oauth(oauth_path=oauth_path, env_dir=env_dir))
    return results
