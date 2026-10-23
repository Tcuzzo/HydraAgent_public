"""Tool calls a model printed as text instead of sending them as structured tool calls.

Some local models answer a tools-enabled request with the call written out in the reply text, for example a bare JSON
object ``{"name": ..., "arguments": {...}}``, the same object in a fenced code block, a list of them, or
``<tool_call>{...}</tool_call>`` tags. Left alone, the agent loop sees no tool call, treats the printed JSON as the
final answer and stops: nothing was fetched, nothing was cited.

``promote_text_tool_calls`` turns such a reply into real tool calls so the normal tool loop runs them. It is strict on
purpose, so ordinary answers that merely show JSON are never hijacked:

* only names of tools bound to this request are accepted;
* tagged calls are accepted wherever they appear; untagged JSON is accepted only when it is the whole reply;
* arguments must be a JSON object (or a JSON string holding one).

Model-agnostic: nothing here names a model or provider.
"""
from __future__ import annotations

import dataclasses
import json
import re
from typing import Any, Callable, Iterable

from hydra.llm import ToolCall

_TAGGED = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S | re.I)
_FENCE = re.compile(r"^```[a-zA-Z_]*\s*\n?(.*?)\n?```$", re.S)


def bound_tool_names(tools: Iterable[Any] | None) -> set[str]:
    names: set[str] = set()
    for tool in tools or ():
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = fn.get("name")
        if isinstance(name, str) and name:
            names.add(name)
    return names


def _items(blob: str) -> list[dict] | None:
    try:
        doc = json.loads(blob)
    except (TypeError, ValueError):
        return None
    if isinstance(doc, dict) and isinstance(doc.get("tool_calls"), list):
        doc = doc["tool_calls"]
    items = doc if isinstance(doc, list) else [doc]
    return items if items and all(isinstance(i, dict) for i in items) else None


def _call(item: dict, allowed: set[str], idx: int) -> ToolCall | None:
    fn = item.get("function") if isinstance(item.get("function"), dict) else item
    name = fn.get("name")
    args: Any = fn.get("arguments", fn.get("parameters", {}))
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError:
            return None
    if not isinstance(name, str) or name not in allowed or not isinstance(args, dict):
        return None
    return ToolCall(id=f"text_call_{idx}", name=name, arguments_raw=json.dumps(args), arguments=args)


def parse_text_tool_calls(text: str, allowed: set[str]) -> list[ToolCall]:
    """Tool calls written in ``text``, limited to ``allowed`` names. Empty when the text is a normal answer."""
    if not text or not allowed:
        return []
    blobs = [m.group(1) for m in _TAGGED.finditer(text)]
    if not blobs:
        whole = text.strip()
        fenced = _FENCE.match(whole)
        blobs = [fenced.group(1).strip() if fenced else whole]
        if not blobs[0].startswith(("{", "[")):
            return []
    calls: list[ToolCall] = []
    for blob in blobs:
        items = _items(blob)
        if items is None:
            return []
        for item in items:
            call = _call(item, allowed, len(calls))
            if call is None:
                return []  # one unknown or malformed call: leave the whole reply as text, never half-run it
            calls.append(call)
    return calls


def promote_text_tool_calls(resp: Any, tools: Iterable[Any] | None,
                            log: Callable[[str], None] | None = None, rung: str = "") -> Any:
    """Return ``resp`` with tool calls printed as text moved into real ``tool_calls`` (unchanged otherwise)."""
    if resp is None or getattr(resp, "tool_calls", None):
        return resp
    calls = parse_text_tool_calls(str(getattr(resp, "content", "") or ""), bound_tool_names(tools))
    if not calls:
        return resp
    if log is not None:
        log(f"hydra rung {rung or '?'} printed {len(calls)} tool call(s) as text "
            f"({', '.join(c.name for c in calls)}); running them through the tool loop")
    changes = {"tool_calls": calls, "content": "", "finish_reason": "tool_calls"}
    if dataclasses.is_dataclass(resp) and not isinstance(resp, type):
        fields = {f.name for f in dataclasses.fields(resp)}
        return dataclasses.replace(resp, **{k: v for k, v in changes.items() if k in fields})
    for key, value in changes.items():
        setattr(resp, key, value)
    return resp
