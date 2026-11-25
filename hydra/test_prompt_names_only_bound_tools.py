#!/usr/bin/env python3
"""Contract: the chat system prompt may only name tools the binder actually exposes.

The chat system prompt is built in ``hydra/cli/cmd_chat.py`` (``DEFAULT_SYSTEM_PROMPT``)
and the chat path hands the model its tool list from ``hydra/cli/tool_binding.bind_tools``.
If the prompt names a tool the binder does not expose, the model is instructed on every
turn to call a tool that does not exist — an instruction pointing at a removed seam
(``spawn_subagent`` / ``spawn_subagents`` were cut from the binder, but the chat prompt
still told the model to use them).

Both sides of the contract are built through the REAL code path: the real prompt
constant, the real system-prompt builder, and the real binder over the real
repository ``.hydraAgent`` catalog. Nothing is mocked — not the filesystem, not the
database, not a socket.

Target before running (sniper discipline):
  pytest hydra/test_prompt_names_only_bound_tools.py -q
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hydra.cli.cmd_chat import DEFAULT_SYSTEM_PROMPT
from hydra.cli.tool_binding import bind_tools
from hydra.skill_spine import build_agent_system_prompt

REPO = Path(__file__).resolve().parents[1]

#: A snake_case identifier — the shape every Hydra tool name has.
_SNAKE = r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+"

#: Every snake_case identifier in the text, whatever it is doing there.
_ALL_SNAKE_RE = re.compile(r"(?<![A-Za-z0-9_])(?P<name>" + _SNAKE + r")(?![A-Za-z0-9_])")

#: A snake_case identifier mentioned in a TOOL position: after a call marker,
#: inside parentheses, introduced as "the <name> tool", or backticked.
_CALL_MARKER_RE = re.compile(
    r"\b(?:call|calls|calling|use|uses|using|invoke|invokes|via|with|try)\s+(?:the\s+)?"
    r"(?P<name>" + _SNAKE + r")",
    re.IGNORECASE,
)
_PAREN_NAME_RE = re.compile(r"\(\s*`?(?P<name>" + _SNAKE + r")`?\s*\)")
_NAMED_TOOL_RE = re.compile(r"\bthe\s+`?(?P<name>" + _SNAKE + r")`?\s+tools?\b", re.IGNORECASE)
_BACKTICKED_RE = re.compile(r"`(?P<name>" + _SNAKE + r")`")


def _all_snake_names(text: str) -> set[str]:
    """Every snake_case identifier in `text`."""
    return {m.group("name") for m in _ALL_SNAKE_RE.finditer(text)}


def _tool_position_names(text: str) -> set[str]:
    """Snake_case identifiers the text mentions as tools.

    The doctrine appended to the chat prompt also carries non-tool identifiers
    (invariant ids, role names, config keys, file paths). Those are not tool
    calls, so only identifiers in a tool position count here.
    """
    names: set[str] = set()
    for pattern in (_CALL_MARKER_RE, _PAREN_NAME_RE, _NAMED_TOOL_RE, _BACKTICKED_RE):
        names |= {m.group("name") for m in pattern.finditer(text)}
    return names


def test_prompt_never_names_an_unbound_tool(tmp_path: Path) -> None:
    """The chat system prompt may only name tools `bind_tools` really exposes."""
    # (0) The constant under test is the LIVE chat system prompt: the chat handler
    #     builds its system message from it. Read the real source, no mocking.
    chat_source = (REPO / "hydra" / "cli" / "cmd_chat.py").read_text(encoding="utf-8")
    assert "build_agent_system_prompt(DEFAULT_SYSTEM_PROMPT)" in chat_source, (
        "DEFAULT_SYSTEM_PROMPT is no longer the chat system prompt — "
        "this contract test is pointed at a dead constant"
    )

    # (1) The REAL chat system prompt, through the real builder.
    assert DEFAULT_SYSTEM_PROMPT.strip(), "the chat system prompt is empty"
    system_prompt = build_agent_system_prompt(DEFAULT_SYSTEM_PROMPT)
    assert system_prompt.strip(), "the built chat system prompt is empty"

    # (2) The REAL exposed tool names, from the real binder, called the way the
    #     chat path calls it (same root, same approval policy, same memory
    #     arguments). notify_telegram stays False so a test never pages the
    #     operator; the memory root is a real temp dir so a test never writes
    #     into the operator's own memory store.
    memory_root = tmp_path / "memory"
    memory_root.mkdir(parents=True, exist_ok=True)
    tools = bind_tools(
        REPO,
        approval_policy="ask",
        memory_root=memory_root,
        memory_workspace_root=REPO,
        notify_telegram=False,
    )
    bound = {tool.name for tool in tools}
    assert bound, "the binder exposed no tools — the contract below would be vacuous"
    assert "fs_read" in bound, (
        f"the binder exposed an unexpected tool set: {sorted(bound)}"
    )

    # (3) Every tool-ish snake_case name the prompt mentions. The hand-written
    #     chat prompt carries tool names only, so it is read exhaustively; the
    #     doctrine appended to it is read in tool position only.
    mentioned = _all_snake_names(DEFAULT_SYSTEM_PROMPT) | _tool_position_names(system_prompt)
    assert mentioned, "no tool names were extracted — the contract below would be vacuous"

    # (4) The contract: no name the prompt mentions may be missing from the
    #     binder's tool list.
    unbound = sorted(mentioned - bound)
    assert not unbound, (
        "the chat system prompt names tool(s) the binder does not expose: "
        + ", ".join(unbound)
        + f" — exposed tools are: {sorted(bound)}"
    )
