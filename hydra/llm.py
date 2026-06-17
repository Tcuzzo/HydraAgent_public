"""hydra.llm — local LLM client.

Mirrors OpenMono's `IProvider` / `ILlmClient` pattern (see
`src/OpenMono.Cli/Llm/ProviderRegistry.cs`) but in Python, stdlib-only.
This is the keystone of "Hydra": every higher-level reasoning surface
(agent loop, planner, builder iteration) depends on `chat()` returning
real LLM output from a local model. No cloud API, no `anthropic`
import, no `claude_api` import — §2 of PRINCIPLES.md stays in force.

Default backend: Ollama's OpenAI-compatible endpoint at
`http://localhost:11434/v1/chat/completions`. Drop-in replaceable via
`OllamaClient(endpoint=...)` for a different host, or by writing a
sibling client class that implements the same shape.

Maturity: SCAFFOLDED. Promoted to PROVEN by §10.20.
"""
from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Iterable, Protocol

_LOG = logging.getLogger(__name__)


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
    the model emitted garbage — caller decides how to handle)."""

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
    plain English (§4 voice contract)."""


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

    def list_models(self, *, timeout: float = 10.0) -> list[str]:
        ...


# --- Ollama backend ------------------------------------------------------


def _parse_tool_calls(raw: list[dict]) -> list[ToolCall]:
    """Parse OpenAI-shaped `tool_calls` into our dataclass. Malformed
    entries are skipped rather than raised — different Ollama models
    serialize edge cases differently, and the agent loop's tool
    dispatcher will surface real failures via its own error path."""
    out: list[ToolCall] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        fn = entry.get("function") or {}
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            continue
        args_raw = fn.get("arguments", "")
        if isinstance(args_raw, dict):
            # Some servers pre-parse arguments to a dict.
            args_dict = args_raw
            args_raw = json.dumps(args_raw)
        elif isinstance(args_raw, str):
            try:
                args_dict = json.loads(args_raw) if args_raw else {}
                if not isinstance(args_dict, dict):
                    args_dict = {}
            except json.JSONDecodeError:
                args_dict = {}
        else:
            args_raw, args_dict = "", {}
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
    require one). The §2 doctrine forbids the `anthropic` / `claude_api`
    Python packages, not OpenAI-compatible HTTP — so this client is
    free to talk to OpenAI-compatible HTTP hosts, as long as the key
    comes from operator-configured env outside the repo.

    `/api/tags` is Ollama-specific; for hosts that don't expose it,
    `list_models()` will raise `LlmError` and the caller should fall
    back to the provider's own catalog or skip discovery.
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:11434",
        *,
        api_key: str | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key

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
        if not model:
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
            f"{self.endpoint}/v1/chat/completions", body, timeout=timeout
        )
        choices = payload.get("choices") or []
        if not choices:
            raise LlmError(
                f"chat: response has no choices — model {model!r} may not "
                f"exist or the server returned an error: "
                f"{payload.get('error') or payload}"
            )
        first = choices[0]
        msg = first.get("message") or {}
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
        return ChatResponse(
            content=content,
            model=payload.get("model", model),
            finish_reason=first.get("finish_reason", ""),
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            raw=payload,
            tool_calls=tool_calls,
        )

    def list_models(self, *, timeout: float = 10.0) -> list[str]:
        """List available models from the provider.
        
        For Ollama: hits /api/tags endpoint
        For cloud providers: returns cached model list (no /api/tags endpoint)
        """
        # Check if this is a cloud provider by endpoint URL
        is_cloud = not self.endpoint.startswith("http://localhost") and not self.endpoint.startswith("http://127.0.0.1")
        
        if is_cloud:
            # Cloud providers don't have /api/tags - return cached model lists.
            # Cloud providers may not have /api/tags; return known model lists for common endpoints.
            if "openai.com" in self.endpoint:
                return ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"]
            elif "anthropic.com" in self.endpoint:
                return ["claude-sonnet-4-20250514", "claude-opus-4-20250514"]
            # Unknown cloud provider - return empty list instead of crashing
            return []
        
        # Local Ollama - use /api/tags endpoint
        try:
            payload = self._get_json(f"{self.endpoint}/api/tags", timeout=timeout)
            names: list[str] = []
            for entry in payload.get("models") or []:
                n = entry.get("name") or entry.get("model")
                if isinstance(n, str):
                    names.append(n)
            return names
        except Exception as e:
            # Local endpoint unavailable - return empty list
            _LOG.warning(f"Failed to fetch local models from {self.endpoint}: {e}")
            return []

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
                body = e.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
            raise LlmError(
                f"HTTP {e.code} from {req.full_url}: "
                f"{body[:300] or e.reason}"
            ) from e
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            raise LlmError(
                f"could not reach {req.full_url}: {reason}"
            ) from e
        except socket.timeout as e:
            raise LlmError(
                f"timed out talking to {req.full_url} after {timeout}s"
            ) from e
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise LlmError(
                f"non-JSON response from {req.full_url}: {raw[:200]!r}"
            ) from e
