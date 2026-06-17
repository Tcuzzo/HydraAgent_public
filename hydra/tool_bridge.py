"""hydra.tool_bridge — recover text-emitted tool calls from local models.

Hydra already supports native OpenAI-style `tool_calls`. This module covers
the common failure mode from Pi Heads where a model emits a tool request as
plain text instead of structured metadata.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable


SUPPORTED_TOOLS = frozenset(
    {
        "read",
        "write",
        "append",
        "mkdir",
        "bash",
        "exec",
        "shell",
        "hydra-runtime",
        "apply_patch",
        "delete",
        "printtree",
        "chmod",
        "modify",
        "edit",
        "count_lines",
        "run",
        "sh",
        "rename",
        "copy",
        "touch",
        "list",
        "symlink",
        "sleep",
    }
)

_FUNCTION_PREFIXED_RE = re.compile(r"^function:\s*(?P<name>[\w.-]+)\s*(?P<args>.*)$", re.IGNORECASE)
_PAIR_RE = re.compile(r"""([A-Za-z_][\w-]*)=("([^"\\]|\\.)*"|'([^'\\]|\\.)*'|\S+)""")
_BROKEN_JSON_FRAGMENT_RE = re.compile(r"^\s*[\{\}\[\],:\"`\s]+\s*$")
_FENCED_JSON_RE = re.compile(r"```json\s*([\s\S]*?)```", re.IGNORECASE)


@dataclass(frozen=True)
class BridgedToolCall:
    name: str
    arguments: dict[str, Any]
    arguments_raw: str


ToolCall = BridgedToolCall


def is_supported_tool(name: str | None) -> bool:
    return str(name or "").strip().lower() in SUPPORTED_TOOLS


def parse_function_prefixed_tool_call(text: str) -> BridgedToolCall | None:
    match = _FUNCTION_PREFIXED_RE.match(str(text or "").strip())
    if not match:
        return None
    name = match.group("name")
    args: dict[str, Any] = {}
    args_part = match.group("args")
    for key, raw_value, _, _ in _PAIR_RE.findall(args_part):
        value = raw_value
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = _unescape_quoted_tool_value(value[1:-1])
        args[key] = value
    return BridgedToolCall(name=name, arguments=args, arguments_raw=json.dumps(args, sort_keys=True))


def looks_like_bare_json_output(output: str) -> bool:
    text = str(output or "").strip()
    if not text:
        return False
    try:
        _parse_json_candidate(text)
        return True
    except json.JSONDecodeError:
        return False


def extract_bridged_tool_call(output: str) -> BridgedToolCall | None:
    function_call = parse_function_prefixed_tool_call(output)
    if function_call is not None:
        return function_call if is_supported_tool(function_call.name) else None

    text = str(output or "").strip()
    if not text:
        return None
    return parse_tool_json(text)


def parse_tool_json(text: str) -> BridgedToolCall | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        payload = _parse_json_candidate(raw)
    except json.JSONDecodeError:
        return None

    for candidate in _iter_tool_candidates(payload):
        function_value = candidate.get("function")
        if isinstance(function_value, dict):
            entries = [
                (key, value)
                for key, value in function_value.items()
                if isinstance(value, dict)
            ]
            if len(entries) == 1:
                name, arguments = entries[0]
                if is_supported_tool(name):
                    return BridgedToolCall(
                        name=str(name),
                        arguments=dict(arguments),
                        arguments_raw=json.dumps(arguments, sort_keys=True),
                    )

        name = candidate.get("name") or candidate.get("tool")
        arguments = candidate.get("arguments", {})
        if not name and isinstance(function_value, str):
            name = function_value
        if not is_supported_tool(name):
            continue
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if isinstance(arguments, list) and len(arguments) == 1 and isinstance(arguments[0], dict):
            arguments = arguments[0]
        if not isinstance(arguments, dict):
            arguments = {}
        for key in ("path", "command", "content", "patch"):
            if key in candidate and key not in arguments:
                arguments[key] = candidate[key]
        return BridgedToolCall(
            name=str(name),
            arguments=arguments,
            arguments_raw=json.dumps(arguments, sort_keys=True),
        )
    return None


def extract_narrated_tool_calls(output: str) -> list[BridgedToolCall]:
    text = str(output or "")
    calls: list[BridgedToolCall] = []
    seen: set[tuple[str, str]] = set()
    indexed_calls: list[tuple[int, BridgedToolCall | None]] = []

    for match in re.finditer(r"(?m)^[ \t]*\{.*\}[ \t]*$", text):
        indexed_calls.append((match.start(), parse_tool_json(match.group(0).strip())))

    for match in re.finditer(r"(?mi)^[ \t]*function:\s*[\w-]+.*$", text):
        indexed_calls.append((match.start(), parse_function_prefixed_tool_call(match.group(0).strip())))

    for match in _FENCED_JSON_RE.finditer(text):
        indexed_calls.append((match.start(), parse_tool_json(match.group(1).strip())))

    for start, block in _extract_bare_json_blocks(text):
        indexed_calls.append((start, parse_tool_json(block)))

    for _, call in sorted(indexed_calls, key=lambda item: item[0]):
        _append_call(calls, seen, call)

    return calls


def should_bridge_output(output: str, *, has_logs: bool = False) -> bool:
    return should_run_qwen_tool_bridge({"output": output, "logs": [] if not has_logs else ["tool:pending"]})


def should_run_qwen_tool_bridge(head: dict[str, Any] | None = None) -> bool:
    head = head or {}
    if head.get("noTools"):
        return False
    text = str(head.get("output") or "").strip()
    if not text:
        return False
    if text.startswith("HYDRA_SUPPRESSED"):
        return False
    if text.startswith("qwen-tool-bridge:"):
        return False
    logs = _coerce_logs(head.get("logs"))
    if any(re.search(r"\btool:start\b", line, re.IGNORECASE) for line in logs):
        return True
    direct = extract_bridged_tool_call(text)
    if direct is not None:
        return True
    if any(is_supported_tool(call.name) for call in extract_narrated_tool_calls(text)):
        return True
    if not logs and looks_like_narrated_tool_call(text):
        return True
    return False


def looks_like_narrated_tool_call(output: str) -> bool:
    text = str(output or "")
    prefixed = parse_function_prefixed_tool_call(text.strip())
    if prefixed is not None and is_supported_tool(prefixed.name):
        return True
    tool_match = re.search(r"\btool:\s*([A-Za-z_][\w-]*)", text, re.IGNORECASE)
    if tool_match and is_supported_tool(tool_match.group(1)):
        return True
    return bool(re.search(r'"(?:name|tool)"\s*:\s*"(?:read|write|edit|bash|exec|apply_patch|hydra-runtime)"', text))


def scrub_public_fake_output_preview(output: str) -> str:
    text = str(output or "")
    if extract_bridged_tool_call(text) is not None:
        return text
    if extract_narrated_tool_calls(text) or looks_like_narrated_tool_call(text) or looks_like_broken_json_fragment(text):
        return "HYDRA_SUPPRESSED_PENDING_TOOL_JSON"
    return text


def looks_like_broken_json_fragment(output: str) -> bool:
    text = str(output or "").strip()
    if not (0 < len(text) <= 80):
        return False
    if _BROKEN_JSON_FRAGMENT_RE.match(text):
        return True
    return any(ch in text for ch in ('{', '}', '[', ']', ':', '"', '`')) and text.count("{") != text.count("}")


def _parse_json_candidate(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```json"):
        stripped = stripped[7:]
    if stripped.endswith("```"):
        stripped = stripped[:-3]
    return json.loads(stripped.strip())


def _iter_tool_candidates(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _append_call(
    calls: list[BridgedToolCall],
    seen: set[tuple[str, str]],
    call: BridgedToolCall | None,
) -> None:
    if call is None:
        return
    key = (call.name.lower(), call.arguments_raw)
    if key in seen:
        return
    seen.add(key)
    calls.append(call)


def _coerce_logs(raw_logs: Any) -> list[str]:
    if isinstance(raw_logs, str):
        return [raw_logs]
    if isinstance(raw_logs, Iterable):
        return [str(item) for item in raw_logs]
    return []


def _extract_bare_json_blocks(text: str) -> list[tuple[int, str]]:
    blocks: list[tuple[int, str]] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False

    for idx, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = idx
            depth += 1
            continue
        if char == "}":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start >= 0:
                blocks.append((start, text[start : idx + 1].strip()))
                start = -1

    return blocks


def _unescape_quoted_tool_value(value: str) -> str:
    return (
        str(value)
        .replace(r"\n", "\n")
        .replace(r"\r", "\r")
        .replace(r"\t", "\t")
        .replace(r"\'", "'")
        .replace(r"\"", '"')
        .replace(r"\\", "\\")
    )
