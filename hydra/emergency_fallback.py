"""Checkpoint a failed mission and optionally select an explicit recovery route.

Client construction and catalog reachability never prove a model can run.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
from pathlib import Path
from typing import Any, Callable


FALLBACK_CHAIN = []  # Legacy embedding hook; no implicit model/provider guesses.

# Legacy identifiers retained for injected-client callers, not default routing.
LIFE_SUPPORT_PROVIDER = "ollama"
LIFE_SUPPORT_MODEL = "qwen2.5-coder:7b"
LIFE_SUPPORT_ENDPOINT = "http://localhost:11434"

# S6 checkpoint filename, relative to evidence/{mission_id}/.
S6_CHECKPOINT_NAME = "s6_pause_checkpoint.json"


class EmergencyFallbackError(Exception):
    """All fallback models failed."""


def classify_provider_error(error: BaseException) -> str:
    """Classify a provider/LLM failure into one of:

      - ``"auth"``       — credentials rejected (401/403, "Unauthorized",
                            "API key invalid"). A local life-support switch
                            fixes this; retrying cloud will not.
      - ``"timeout"``    — the request timed out (slow/overloaded host).
      - ``"connection"`` — the host was unreachable (refused/DNS/URLError).
      - ``"other"``      — anything else (500, malformed body, unknown model).

    This is the S6 fix for ``probe_model``'s old bare ``except:`` that could
    not tell an expired key from a slow network. Classification keys off the
    operator-facing ``LlmError`` message signatures emitted by ``hydra.llm``
    (HTTP code + reason), so it works without a live network.
    """
    kind = getattr(error, 'kind', 'unknown')
    if kind != 'unknown':
        return 'auth' if kind in {'authentication', 'permission'} else kind
    msg = str(error).lower()
    # Auth: explicit 401/403 or a key-rejection phrase.
    if "401" in msg or "403" in msg or "unauthorized" in msg or "forbidden" in msg:
        return "auth"
    if "api key" in msg and ("invalid" in msg or "missing" in msg or "expired" in msg):
        return "auth"
    if "authentication" in msg:
        return "auth"
    # Timeout vs connection are distinct: a timeout means the host answered
    # slowly; connection means it never answered at all.
    if "timed out" in msg or "timeout" in msg:
        return "timeout"
    if "could not reach" in msg or "connection refused" in msg or "name or service" in msg:
        return "connection"
    return "other"


def probe_model(provider: str, model: str, timeout: float = 5.0) -> bool:
    """Quick liveness probe: is the provider's catalog endpoint reachable?

    Probes the ACTUAL provider the client is configured for — local Ollama
    (``/api/tags``), cloud OpenAI-compatible endpoints (``/models`` with the
    bearer key), or the Codex CLI (binary present) — not just ``ollama``.
    ``model`` is accepted for call-site compatibility; the probe is a catalog
    reachability check, not a per-model lookup. Returns True when the endpoint
    answers HTTP 200 / the Codex binary is present; False on any failure
    (unconfigured, unreachable, auth, timeout) — never raises, never
    blanket-swallows a programming error (the old bare ``except:``).
    """
    import shutil
    import urllib.request

    try:
        from hydra.providers import ProviderError, resolve

        cfg = resolve(provider)
    except (ProviderError, ValueError):
        # Unknown / unconfigured provider (e.g. a cloud provider whose key
        # is missing) is "not available right now" — not a programming error.
        return False
    # Codex is a local CLI provider, not an HTTP endpoint — probe the binary.
    if not cfg.endpoint:
        binary = os.environ.get("HYDRA_CODEX_BIN") or shutil.which("codex")
        return bool(binary)
    headers: dict[str, str] = {"User-Agent": "HydraAgent/probe"}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    is_local = cfg.endpoint.startswith("http://localhost") or cfg.endpoint.startswith(
        "http://127.0.0.1"
    )
    # Local Ollama speaks /api/tags; cloud OpenAI-compatible hosts speak /models.
    catalog_url = f"{cfg.endpoint}/api/tags" if is_local else f"{cfg.endpoint}/models"
    req = urllib.request.Request(catalog_url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (catalog probe)
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        # Unreachable / timed out / malformed URL — "not reachable right now".
        return False


def get_emergency_model(preferred_model: str | None = None) -> dict[str, Any]:
    """Select only an explicit provider/model pair; no availability claim."""
    if preferred_model and '/' in preferred_model:
        provider, model = preferred_model.split('/', 1)
        if provider and model:
            return {'provider': provider, 'model': model, 'used_fallback': False, 'availability': 'unverified'}
    provider = os.environ.get('HYDRA_EMERGENCY_PROVIDER', '').strip()
    model = os.environ.get('HYDRA_EMERGENCY_MODEL', '').strip()
    if provider and model:
        return {'provider': provider, 'model': model, 'used_fallback': True, 'availability': 'unverified'}
    raise EmergencyFallbackError('No emergency route configured; set HYDRA_EMERGENCY_PROVIDER and HYDRA_EMERGENCY_MODEL or choose a configured role fallback')


def with_emergency_fallback(func: Callable, preferred_model: str | None = None):
    """Decorator to wrap function with emergency fallback.
    
    If preferred_model fails, automatically retry with fallback chain.
    """
    def wrapper(*args, **kwargs):
        model_config = get_emergency_model(preferred_model)
        kwargs["model"] = model_config["model"]
        kwargs["provider"] = model_config["provider"]
        
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if model_config["used_fallback"]:
                # Already using fallback, re-raise
                raise
            # Try with fallback chain
            fallback_config = get_emergency_model(None)
            kwargs["model"] = fallback_config["model"]
            kwargs["provider"] = fallback_config["provider"]
            return func(*args, **kwargs)

    return wrapper


def _default_local_client_factory() -> tuple[Any, str]:
    """Build only the explicitly configured recovery route, with health guards."""
    from hydra.providers import make_runtime_client
    route = get_emergency_model()
    client, _cfg = make_runtime_client(route['provider'], model=route['model'],
                                     family=os.environ.get('HYDRA_EMERGENCY_FAMILY', ''))
    return client, route['model']


def engage_life_support_fallback(
    *,
    error: BaseException,
    requested_provider: str,
    mission_id: str,
    repo_root: str | Path,
    checkpoint_state: dict[str, Any],
    local_client_factory: Callable[[], tuple[Any, str]] | None = None,
) -> dict[str, Any]:
    """Checkpoint a failure and select an explicitly configured recovery route.

    The returned route has not completed a call and is not certified available.
    If selection fails, return client=None and preserve the paused checkpoint.
    """
    error_class = classify_provider_error(error)
    if error_class == "auth":
        ledger_reason = "provider_auth_fallback_engaged"
    else:
        # Preserve the existing ledger reason vocabulary for paused missions.
        ledger_reason = "provider_timeout_fallback_engaged"

    from hydra.atomic_write import atomic_write_bytes
    from hydra.provider_health import safe_message
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', mission_id):
        raise ValueError('mission ID must be a safe identifier')
    factory = local_client_factory or _default_local_client_factory
    selection_error = None
    try:
        client, used_model = factory()
    except Exception as exc:
        client, used_model = None, None
        selection_error = safe_message(str(exc))

    root = Path(repo_root).expanduser().resolve()
    checkpoint_dir = root / "evidence" / mission_id
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / S6_CHECKPOINT_NAME

    checkpoint = dict(checkpoint_state)
    checkpoint["mission_id"] = mission_id
    checkpoint["error_class"] = error_class
    checkpoint["error_message"] = safe_message(str(error))
    checkpoint["failure"] = error.to_dict() if hasattr(error, "to_dict") else {"kind": error_class, "message": safe_message(str(error))}
    checkpoint["recovery_route_error"] = selection_error
    checkpoint["requested_provider"] = requested_provider
    checkpoint["life_support_model"] = used_model
    checkpoint["paused_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    atomic_write_bytes(checkpoint_path, (json.dumps(checkpoint, indent=2, sort_keys=True) + "\n").encode())

    substitution = {
        "requested": requested_provider,
        "used": used_model,
        "downgraded_to_local": client is not None and (getattr(client, "requested_route", {}) or {}).get("provider", "ollama") == "ollama",
        "status": "selected_unverified" if client is not None else "unavailable",
        "note": (
            f"provider {requested_provider!r} failed ({error_class}); switched to "
            f"local life-support {used_model!r}"
        ),
    }

    if client is None:
        operator_message = f"The {requested_provider} model failed ({_plain_cause(error_class)}). The mission is checkpointed and paused. {selection_error}"
        substitution['note'] = 'No recovery route selected; mission paused'
    else:
        operator_message = f"The {requested_provider} model failed ({_plain_cause(error_class)}). Recovery route {used_model!r} is configured but has not completed a call. The mission is checkpointed and paused."
        substitution['note'] = 'Explicit recovery route selected; availability unverified'

    return {
        "error_class": error_class,
        "ledger_reason": ledger_reason,
        "client": client,
        "used_model": used_model,
        "checkpoint_path": str(checkpoint_path),
        "checkpoint": checkpoint,
        "substitution": substitution,
        "operator_message": operator_message,
    }


def _plain_cause(error_class: str) -> str:
    return {
        "auth": "its credentials were rejected",
        "timeout": "it timed out",
        "connection": "it could not be reached",
        "budget": "its account has no available budget",
        "rate_limit": "the provider rate limit was reached",
        "retired": "the provider explicitly retired it",
        "model_unavailable": "model availability needs catalog refresh",
        "other": "it returned an error",
    }.get(error_class, "it returned an error")
