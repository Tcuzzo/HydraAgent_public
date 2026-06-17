"""hydra.emergency_fallback — Universal emergency model fallback.

When cloud (Ollama Cloud) fails, instantly switch to the local life-support model:
- Local ollama/qwen2.5-coder:7b (always works on local Ollama)

No hanging, no blocking, no permission errors.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
from pathlib import Path
from typing import Any, Callable


FALLBACK_CHAIN = [
    # Local Ollama — the last-resort fallback when all cloud providers are down.
    {"provider": "ollama", "model": "qwen2.5-coder:7b", "timeout": 30},
]

# Never-expires life-support model. Local Ollama at localhost:11434 is the
# always-available assumption. When a provider fails mid-mission the agent
# switches to this rather than crashing.
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
    """Quick probe to check if model is accessible."""
    try:
        if provider == "ollama":
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        # A failed probe means "not reachable right now" — return False, but
        # don't blanket-swallow programming errors (the old bare ``except:``).
        return False
    return False


def get_emergency_model(preferred_model: str | None = None) -> dict[str, Any]:
    """Get the best available model using emergency fallback chain.
    
    Returns dict with provider, model, and whether fallback was used.
    """
    # If preferred model is local, just use it
    if preferred_model and preferred_model.startswith("ollama/"):
        return {"provider": "ollama", "model": preferred_model.split("/")[-1], "used_fallback": False}
    
    # Try fallback chain
    for i, fallback in enumerate(FALLBACK_CHAIN):
        if probe_model(fallback["provider"], fallback["model"], fallback["timeout"]):
            return {
                "provider": fallback["provider"],
                "model": fallback["model"],
                "used_fallback": i > 0,
                "original_model": preferred_model,
            }
    
    # All probes failed — default to local (always available)
    return {
        "provider": "ollama",
        "model": "qwen2.5-coder:7b",
        "used_fallback": True,
        "original_model": preferred_model,
        "warning": "All cloud probes failed, using local model",
    }


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
    """Build the local life-support client (localhost Ollama).

    Deferred import so callers that never trip the fallback don't pay the
    import cost, and so tests can inject their own factory.
    """
    from hydra.llm import OllamaClient

    return OllamaClient(endpoint=LIFE_SUPPORT_ENDPOINT), LIFE_SUPPORT_MODEL


def engage_life_support_fallback(
    *,
    error: BaseException,
    requested_provider: str,
    mission_id: str,
    repo_root: str | Path,
    checkpoint_state: dict[str, Any],
    local_client_factory: Callable[[], tuple[Any, str]] | None = None,
) -> dict[str, Any]:
    """Switch a failed autonomous mission to the local life-support model.

    Called when a provider chat call raises during an autonomous/local
    mission. It:

      1. Classifies ``error`` (auth vs timeout vs connection vs other).
      2. Builds the local life-support client (ollama/qwen2.5-coder:7b).
      3. Checkpoints ``checkpoint_state`` to
         ``evidence/{mission_id}/s6_pause_checkpoint.json`` so the mission can
         resume from exactly where the provider died.
      4. Returns a structured payload the caller surfaces to the operator —
         the substitution is **never silent** (it reuses the
         ``model_router.last_substitution`` shape: ``requested`` / ``used`` /
         ``downgraded_to_local`` / ``note``).

    No destructive actions: it only writes one JSON checkpoint under
    ``evidence/`` and returns a client. It does not crash the mission.
    """
    error_class = classify_provider_error(error)
    if error_class == "auth":
        ledger_reason = "provider_auth_fallback_engaged"
    else:
        # timeout / connection / other all funnel to the timeout reason on the
        # ledger transition map (the never-expires local model is the cure for
        # any non-auth provider outage).
        ledger_reason = "provider_timeout_fallback_engaged"

    factory = local_client_factory or _default_local_client_factory
    client, used_model = factory()

    root = Path(repo_root).expanduser().resolve()
    checkpoint_dir = root / "evidence" / mission_id
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / S6_CHECKPOINT_NAME

    checkpoint = dict(checkpoint_state)
    checkpoint["mission_id"] = mission_id
    checkpoint["error_class"] = error_class
    checkpoint["error_message"] = str(error)
    checkpoint["requested_provider"] = requested_provider
    checkpoint["life_support_model"] = used_model
    checkpoint["paused_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    checkpoint_path.write_text(
        json.dumps(checkpoint, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    substitution = {
        "requested": requested_provider,
        "used": used_model,
        "downgraded_to_local": True,
        "note": (
            f"provider {requested_provider!r} failed ({error_class}); switched to "
            f"local life-support {used_model!r}"
        ),
    }

    # Plain-English operator notice (§8/§12 — no blobs/paths in the headline).
    operator_message = (
        f"Heads up: the {requested_provider} model is unavailable "
        f"({_plain_cause(error_class)}). I switched to the local "
        f"{used_model} life-support model and paused the mission so it can "
        f"resume cleanly once {requested_provider} is back."
    )

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
        "other": "it returned an error",
    }.get(error_class, "it returned an error")
