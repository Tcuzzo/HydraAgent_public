"""Deterministic chat intent routing for high-confidence operator commands."""
from __future__ import annotations

import re
from dataclasses import dataclass


_AUDIT_WORDS = ("audit", "debug", "diagnose", "inspect")
_REMOTE_AUDIT_WORDS = ("remote server", "remote runtime", "remote host")
_REVIEW_WORDS = (
    "review",
    "check",
    "look through",
    "look at",
    "go through",
    "full look",
    "what is broken",
    "what's broken",
    "what needs repair",
    "end to end",
    "keep receipts",
)
_CORRECTION_WORDS = ("look again", "check again", "use this", "that directory", "correct", "instead")
_LOCATE_WORDS = ("find", "locate", "can you see", "is there", "look for")
_LOCATE_STOP_WORDS = {
    "can",
    "you",
    "see",
    "find",
    "locate",
    "is",
    "there",
    "look",
    "for",
    "in",
    "under",
    "the",
    "a",
    "an",
    "instance",
    "directory",
    "dir",
    "folder",
    "project",
    "repo",
    "repository",
}
_PATH_RE = re.compile(r"(?P<path>(?:~|/|\./|\.\./)[^\s,;]+)")


@dataclass(frozen=True)
class ChatIntent:
    kind: str
    target: str


def _extract_path(text: str) -> str | None:
    match = _PATH_RE.search(text)
    if match:
        return match.group("path").strip()
    return None


def _word_in(word: str, text: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text))


def route_chat_intent(
    prompt: str,
    *,
    last_audit_target: str | None = None,
    last_locate_target: str | None = None,
) -> ChatIntent | None:
    stripped = prompt.strip()
    if not stripped:
        return None
    lower = stripped.lower()
    path = _extract_path(stripped)
    command_text = stripped[: stripped.find(path)].strip() if path else stripped
    command_lower = command_text.lower()
    if any(_word_in(word, lower) for word in _REMOTE_AUDIT_WORDS) and (
        any(word in lower for word in _AUDIT_WORDS)
        or any(word in lower for word in _REVIEW_WORDS)
    ):
        return ChatIntent("remote_audit", "remote")
    if any(word in command_lower for word in _AUDIT_WORDS):
        target = path or last_audit_target or last_locate_target
        if target:
            return ChatIntent("audit", target)
    if last_audit_target and path and any(word in lower for word in _CORRECTION_WORDS):
        return ChatIntent("audit", path)
    if path and any(word in lower for word in _LOCATE_WORDS):
        before_path = stripped[: stripped.find(path)].strip()
        words = re.findall(r"[A-Za-z0-9_.-]+", before_path)
        candidates = [word for word in words if word.lower() not in _LOCATE_STOP_WORDS]
        if candidates:
            return ChatIntent("locate", f"{candidates[-1]}\n{path}")
    return None
