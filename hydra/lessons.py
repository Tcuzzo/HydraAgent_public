"""Append-only durable lesson writer for Hydra local memory."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hydra.local_memory import DEFAULT_MEMORY_ROOT
from hydra.file_lock import locked_path


LESSON_RELATIVE_PATH = Path("workspace") / "memory" / "hydra-lessons.md"
SECRET_FRAGMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer|credential|oauth|password|secret|token)\b\s*[:=]\s*\S+"
)


@dataclass(frozen=True)
class LessonError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def _clean_text(value: str) -> str:
    value = " ".join(value.strip().split())
    return SECRET_FRAGMENT_RE.sub("[redacted secret]", value)


def remember_lesson(
    lesson: str,
    *,
    source: str,
    tags: list[str] | None = None,
    memory_root: str | Path | None = None,
) -> dict[str, Any]:
    clean_lesson = _clean_text(lesson)
    clean_source = _clean_text(source)
    clean_tags = [_clean_text(tag) for tag in (tags or []) if _clean_text(tag)]
    if not clean_lesson:
        raise LessonError("lesson must be non-empty")
    if not clean_source:
        raise LessonError("source is required for durable lessons")

    root = Path(memory_root).expanduser().resolve() if memory_root else DEFAULT_MEMORY_ROOT
    path = root / LESSON_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tag_text = ", ".join(clean_tags) if clean_tags else "untagged"
    entry = (
        f"## {timestamp}\n"
        f"- source: {clean_source}\n"
        f"- tags: {tag_text}\n"
        f"- lesson: {clean_lesson}\n\n"
    )
    # Creation and append are one operation across chat, team and listener
    # processes; concurrent first writers must never truncate an earlier lesson.
    with locked_path(path):
        with path.open("a", encoding="utf-8") as f:
            if f.tell() == 0:
                f.write("# Hydra Lessons\n\n")
            f.write(entry)
    return {
        "schema": "hydra.lesson.v1",
        "status": "OK",
        "path": str(path),
        "source": clean_source,
        "tags": clean_tags,
        "lesson": clean_lesson,
    }
