"""hydra.llm — local LLM client.

Mirrors OpenMono's `IProvider` / `ILlmClient` pattern (see
`src/OpenMono.Cli/Llm/ProviderRegistry.cs`) but in Python, stdlib-only.
This is the keystone of "Hydra": every higher-level reasoning surface
(agent loop, planner, builder iteration) depends on `chat()` returning
real LLM output from a configured local or cloud model. No provider SDK is
needed for OpenAI-compatible HTTP servers.

Default backend: Ollama's OpenAI-compatible endpoint at
`http://localhost:11434/v1/chat/completions`. Drop-in replaceable via
`OllamaClient(endpoint=...)` for a different host, or by writing a
sibling client class that implements the same shape.

Maturity: SCAFFOLDED. Promoted to PROVEN by §10.20.
"""
from __future__ import annotations

import json
import hashlib
import logging
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from typing import Iterable, Protocol

_LOG = logging.getLogger(__name__)

# --- cloud model-catalog cache (inv_16: never hammer the provider) ----------
# A live GET /v1/models is reused for this many seconds per endpoint, so a
# flurry of provider_available checks makes at most ONE upstream call per TTL
# window. Module-level (process-wide) because provider keys/endpoints are
# stable per process and the cost of one extra call is the bug we prevent.
_CLOUD_MODEL_TTL_SECONDS: float = 60.0
_cloud_model_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}


@dataclass
class ChatMessage:
    """One turn in a conversation. Mirrors the OpenAI chat-completions
    message schema (the protocol both Ollama and OpenMono speak)."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


@dataclass
class ToolCall:
    """One tool the model wants invoked, parsed from the OpenAI-style
    `tool_calls[].function` shape. `arguments_raw` is the raw JSON
    string the model emitted; `arguments` is the parsed dict (or {} if
    invalid arguments raise LlmError before they can reach a tool)."""

    id: str
    name: str
    arguments_raw: str
    arguments: dict


@dataclass
class ChatResponse:
    """Parsed result of one chat call."""

    content: str
    model: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    raw: dict = field(repr=False)
    tool_calls: list[ToolCall] = field(default_factory=list)


class LlmError(Exception):
    """Any failure of the LLM client — connection refused, timeout,
    unknown model, malformed response. The message is operator-facing
    plain English (§4 voice contract). Structured fields survive routing failures.
    """

    def __init__(self, message: str, *, kind: str = "unknown", status_code: int | None = None,
                 error_code: str | None = None, retry_after: float | None = None,
                 refresh_needed: bool = False, details: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.error_code = error_code
        self.retry_after = retry_after
        self.refresh_needed = refresh_needed
        self.details = details or {}

    def to_dict(self) -> dict:
        from hydra.provider_health import safe_message
        return {"kind": self.kind, "message": safe_message(str(self)),
                "status_code": self.status_code, "error_code": safe_message(self.error_code) if self.error_code else None,
                "retry_after": self.retry_after, "refresh_needed": self.refresh_needed,
                **self.details}


class LlmClient(Protocol):
    """Structural protocol every backend implements. Lets the §10.22
    agent loop accept either OllamaClient or a future LlamaServerClient
    without knowing the difference."""

    def chat(
        self,
        messages: Iterable[ChatMessage],
        *,
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        timeout: float = 60.0,
    ) -> ChatResponse:
        ...

    def list_models(self, *, timeout: float = 10.0, force_refresh: bool = False) -> list[str]:
        ...


# --- Ollama backend ------------------------------------------------------


def _parse_tool_calls(raw: list[dict]) -> list[ToolCall]:
    """Parse calls without turning malformed arguments into executable defaults."""
    if not isinstance(raw, list):
        raise LlmError("chat: tool_calls must be a list")
    out: list[ToolCall] = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("function"), dict):
            raise LlmError("chat: each tool call must contain a function object")
        fn = entry["function"]
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            raise LlmError("chat: tool call name must be a non-empty string")
        args_raw = fn.get("arguments", "")
        if isinstance(args_raw, dict):
            # Some servers pre-parse arguments to a dict.
            args_dict = args_raw
            args_raw = json.dumps(args_raw)
        elif isinstance(args_raw, str):
            try:
                args_dict = json.loads(args_raw) if args_raw else {}
                if not isinstance(args_dict, dict):
                    raise LlmError(f"chat: arguments for {name!r} must be an object")
            except json.JSONDecodeError as exc:
                raise LlmError(f"chat: invalid JSON arguments for {name!r}") from exc
        else:
            raise LlmError(f"chat: arguments for {name!r} must be an object or JSON string")
        out.append(
            ToolCall(
                id=str(entry.get("id") or ""),
                name=name,
                arguments_raw=args_raw,
                arguments=args_dict,
            )
        )
    return out


class OllamaClient:
    """OpenAI-compatible HTTP client.

    Despite the name, this class is provider-agnostic — it speaks the
    OpenAI chat-completions wire shape, which is what Ollama,
    llama-server, OpenAI itself, and most local LLM runtimes serve. The
    historical name is kept so existing imports don't break; for cloud
    hosts pass `api_key=` and a non-localhost `endpoint=`.

    When `api_key` is set, every request includes `Authorization: Bearer
    <api_key>`. When unset, no header is sent (local Ollama doesn't
    require one). Model names are passed through unchanged for every family.

    Catalog discovery supports Ollama `/api/tags` and compatible `/v1/models`.
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:11434",
        *,
        api_key: str | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key

    @property
    def api_base(self) -> str:
        """Accept either a server root or a versioned OpenAI base URL."""
        return self.endpoint if urlsplit(self.endpoint).path.rstrip("/") else self.endpoint + "/v1"

    # --- public ----------------------------------------------------------

    def chat(
        self,
        messages: Iterable[ChatMessage] | list[dict],
        *,
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        timeout: float = 60.0,
        tools: list[dict] | None = None,
    ) -> ChatResponse:
        """Call `model` with `messages`. If `tools` is given (OpenAI
        function-calling tool schemas), they're passed through and any
        `tool_calls` the model emits are parsed into `ChatResponse.tool_calls`.

        `messages` accepts either `ChatMessage` instances or raw dicts —
        the agent loop appends raw dicts (assistant messages with
        `tool_calls`, tool-response messages) which would lose fields if
        forced through `ChatMessage`.
        """
        if not isinstance(model, str) or not model.strip():
            raise LlmError("chat: model must be a non-empty string")
        msgs: list[dict] = []
        for m in messages:
            if isinstance(m, ChatMessage):
                msgs.append(m.to_dict())
            elif isinstance(m, dict):
                msgs.append(m)
            else:
                raise LlmError(
                    f"chat: message must be ChatMessage or dict, got "
                    f"{type(m).__name__}"
                )
        if not msgs:
            raise LlmError("chat: messages must be non-empty")
        body: dict = {
            "model": model,
            "messages": msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
        payload = self._post_json(
            f"{self.api_base}/chat/completions", body, timeout=timeout
        )
        if not isinstance(payload, dict):
            raise LlmError("chat: response must be a JSON object")
        choices = payload.get("choices") or []
        if not isinstance(choices, list) or not choices:
            raise LlmError(
                f"chat: response has no choices — model {model!r} may not "
                f"exist or the server returned an error: "
                f"{payload.get('error') or payload}"
            )
        first = choices[0]
        if not isinstance(first, dict) or not isinstance(first.get("message"), dict):
            raise LlmError("chat: choices[0].message must be an object")
        msg = first["message"]
        content = msg.get("content")
        # When the model emits only tool_calls, some servers return
        # content as null. Coerce to empty string for the dataclass
        # contract; the caller distinguishes the two via `tool_calls`.
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise LlmError(
                f"chat: choices[0].message.content is not a string or null: {content!r}"
            )
        tool_calls = _parse_tool_calls(msg.get("tool_calls") or [])
        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            raise LlmError("chat: usage must be an object")
        try:
            prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise LlmError("chat: token usage must contain numbers") from exc
        return ChatResponse(
            content=content,
            model=payload.get("model", model),
            finish_reason=first.get("finish_reason", ""),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            raw=payload,
            tool_calls=tool_calls,
        )

    def list_models(self, *, timeout: float = 10.0, force_refresh: bool = False) -> list[str]:
        """List available models from the provider.

        For local Ollama: hits GET {endpoint}/api/tags (the Ollama-specific
        catalog endpoint).
        For cloud providers: hits GET {endpoint}/models (the OpenAI-compatible
        catalog endpoint) and returns the LIVE list, backed by a short TTL
        cache so a flurry of availability checks never hammers the provider
        (inv_16). On failure the call RAISES `LlmError` — loudly, with no
        silent fallback to a stale hardcoded list — so `provider_available`
        can fail-open (admit the model) instead of falsely rejecting valid
        newer models off a frozen catalog.

        The old behavior returned hardcoded stale catalogs for "openai.com" /
        "anthropic.com" endpoints, which `provider_available` then used as a
        DENYLIST to gate off valid models (gpt-4.1, o4-mini, ...). That was
        the bug; the live query + raise-on-failure closes the class at the
        shared seam.
        """
        # Local Ollama speaks /api/tags, not the OpenAI /models shape.
        parsed = urlsplit(self.endpoint)
        is_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"} and not parsed.path.rstrip("/")
        if is_local:
            try:
                payload = self._get_json(f"{self.endpoint}/api/tags", timeout=timeout)
                names: list[str] = []
                for entry in payload.get("models") or []:
                    n = entry.get("name") or entry.get("model")
                    if isinstance(n, str):
                        names.append(n)
                return names
            except LlmError:
                # Local endpoint unavailable — surface LOUDLY, consistent with the
                # cloud branch and the docstring ("On failure RAISES LlmError").
                # Callers (provider_available, cmd_models) already wrap this in
                # try/except and fail-open / print a clear error. Returning []
                # silently (the old behavior) hid the failure and broke the
                # documented contract.
                pass  # llama.cpp/vLLM also run locally; try the standard catalog.
            except Exception as e:  # noqa: BLE001
                # Wrap any non-transport failure (e.g. malformed payload shape)
                # so the contract "raises LlmError on failure" holds for ALL
                # failure modes — never leak a raw AttributeError to callers.
                raise LlmError(
                    f"failed to list local models from {self.endpoint}/api/tags: {e}"
                ) from e

        # Cloud provider: query GET {endpoint}/models live, with a TTL cache
        # so repeated availability checks make at most ONE upstream call per
        # window. No hardcoded fallback — a stale list used as a denylist is
        # the exact bug this closes.
        now = time.monotonic()
        cache_key = (self.api_base, hashlib.sha256((self.api_key or "").encode()).hexdigest())
        cached = _cloud_model_cache.get(cache_key)
        if cached is not None and not force_refresh:
            expires_at, cached_names = cached
            if now < expires_at:
                return list(cached_names)

        payload = self._get_json(f"{self.api_base}/models", timeout=timeout)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise LlmError("model catalog response must contain a data list")
        # OpenAI-compatible shape: {"data": [{"id": "..."}, ...]}.
        names: list[str] = []
        for entry in payload.get("data") or []:
            mid = entry.get("id") if isinstance(entry, dict) else None
            if isinstance(mid, str) and mid:
                names.append(mid)
        _cloud_model_cache[cache_key] = (now + _CLOUD_MODEL_TTL_SECONDS, names)
        _LOG.debug(
            "live catalog for %s: %d models (cached %.0fs)",
            self.endpoint,
            len(names),
            _CLOUD_MODEL_TTL_SECONDS,
        )
        return list(names)

    # --- embeddings ------------------------------------------------------

    def _post_embeddings(self, model: str, text: str) -> list[float]:
        """POST to Ollama's /api/embeddings and return the float vector.

        Raises `LlmError` (via `_post_json` → `_read_json`) on any HTTP,
        network, or JSON failure — same error contract as `chat()`.
        """
        data = self._post_json(
            f"{self.endpoint}/api/embeddings",
            {"model": model, "prompt": text},
            timeout=30.0,
        )
        return list(data.get("embedding") or [])

    def embed(
        self, texts: list[str], *, model: str = "nomic-embed-text"
    ) -> list[list[float]]:
        """Embed a list of strings via `model` (default: nomic-embed-text).

        Returns one float vector per input string, in the same order.
        Each call to the underlying `_post_embeddings` is sequential —
        Ollama's embeddings endpoint is synchronous and single-request.
        """
        return [self._post_embeddings(model, t) for t in texts]

    # --- transport -------------------------------------------------------

    _USER_AGENT = "HydraAgent/1.0 (+local-llm; stdlib-urllib)"

    def _default_headers(self) -> dict[str, str]:
        """Headers every request carries.

        `User-Agent: HydraAgent/...` is honest and matters operationally:
        Cloudflare-fronted hosts return HTTP 403 / Cloudflare
        error 1010 against the default `Python-urllib/X.Y` UA, blocking
        the request before it ever reaches the upstream LLM. We surface
        the client identity instead of impersonating a browser.
        """
        h: dict[str, str] = {"User-Agent": self._USER_AGENT}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _get_json(self, url: str, *, timeout: float) -> dict:
        req = urllib.request.Request(
            url, method="GET", headers=self._default_headers()
        )
        return self._read_json(req, timeout=timeout)

    def _post_json(self, url: str, body: dict, *, timeout: float) -> dict:
        data = json.dumps(body).encode("utf-8")
        headers = {**self._default_headers(), "Content-Type": "application/json"}
        req = urllib.request.Request(
            url, data=data, method="POST", headers=headers
        )
        return self._read_json(req, timeout=timeout)

    def _read_json(self, req: urllib.request.Request, *, timeout: float) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read(16384).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
            from hydra.provider_health import http_failure
            raise http_failure(e.code, body or str(e.reason), e.headers.get("Retry-After") if e.headers else None) from e
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            raise LlmError(
                f"could not reach provider: {reason}", kind="timeout" if isinstance(reason, (socket.timeout, TimeoutError)) else "connection"
            ) from e
        except socket.timeout as e:
            raise LlmError(
                f"timed out talking to provider after {timeout}s", kind="timeout"
            ) from e
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise LlmError(f"JSON response from {req.full_url} must be an object")
            return payload
        except json.JSONDecodeError as e:
            raise LlmError(
                f"non-JSON response from {req.full_url}: {raw[:200]!r}"
            ) from e
