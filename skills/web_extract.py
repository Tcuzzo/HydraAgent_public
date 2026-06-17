"""skills.web_extract — fetch a URL and extract clean text from its HTML.

Composes on top of the §10.6 ``http_fetch`` skill: same allow-list contract,
same hard byte cap, same timeout. After the bytes come back, this skill
strips out ``<script>``, ``<style>``, ``<noscript>``, and ``<template>``
content, collapses whitespace, and emits a clean text rendering that an
LLM (or a human) can actually read.

Stdlib-only — no BeautifulSoup, no lxml. Uses ``html.parser.HTMLParser``.
Title and h1/h2 headings are surfaced for trace, but the heavy work is just
"give me the readable text of this page under a byte cap."

Maturity: SCAFFOLDED. Promoted by §10.74-web-extract eval.
"""
from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any

from skills import http_fetch


class SkillError(Exception):
    """A skill refused the request or could not complete it."""


DEFAULT_MAX_BYTES = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_MAX_OUTPUT_CHARS = 16_000

_SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})
_BLOCK_TAGS = frozenset({
    "p", "div", "section", "article", "header", "footer", "aside",
    "nav", "ul", "ol", "li", "table", "tr", "td", "th",
    "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr", "pre",
})


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._title_parts: list[str] = []
        self._in_title = False
        self._headings: list[tuple[int, str]] = []
        self._heading_level: int | None = None
        self._heading_parts: list[str] = []

    # --- parser callbacks ---
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag_lower == "title":
            self._in_title = True
            return
        if tag_lower in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_level = int(tag_lower[1])
            self._heading_parts = []
            self._chunks.append("\n\n")
            return
        if tag_lower in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in _SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if tag_lower == "title":
            self._in_title = False
            return
        if tag_lower in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            text = "".join(self._heading_parts).strip()
            if text and self._heading_level is not None:
                self._headings.append((self._heading_level, text))
            self._heading_level = None
            self._heading_parts = []
            self._chunks.append("\n")
            return
        if tag_lower in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self._title_parts.append(data)
            return
        if self._heading_level is not None:
            self._heading_parts.append(data)
        self._chunks.append(data)

    # --- accessors ---
    def title(self) -> str:
        return _collapse_ws("".join(self._title_parts)).strip()

    def text(self) -> str:
        return _normalize_blocks("".join(self._chunks))

    def headings(self) -> list[dict[str, Any]]:
        return [{"level": lvl, "text": txt} for lvl, txt in self._headings]


def _collapse_ws(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", text)


def _normalize_blocks(text: str) -> str:
    text = unescape(text)
    text = _collapse_ws(text)
    # Collapse 3+ newlines into 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip leading/trailing whitespace on each line, then drop blanks at ends
    lines = [line.strip() for line in text.split("\n")]
    out: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if blank:
                continue
            blank = True
        else:
            blank = False
        out.append(line)
    return "\n".join(out).strip()


def run(
    url: str,
    allowed_hosts: list[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
) -> dict[str, Any]:
    if max_output_chars <= 0:
        raise SkillError("max_output_chars must be a positive integer")
    try:
        fetched = http_fetch.run(
            url=url,
            allowed_hosts=allowed_hosts,
            timeout=timeout,
            max_bytes=max_bytes,
        )
    except http_fetch.SkillError as e:
        raise SkillError(str(e)) from e
    body = fetched["body"]
    content_type = (fetched.get("content_type") or "").lower()
    looks_html = "html" in content_type or _looks_like_html(body)
    if not looks_html:
        text = body
        title = ""
        headings: list[dict[str, Any]] = []
        skipped_tags: list[str] = []
    else:
        parser = _Extractor()
        try:
            parser.feed(body)
            parser.close()
        except Exception as e:  # noqa: BLE001
            raise SkillError(f"HTML parse error: {type(e).__name__}: {e}") from e
        text = parser.text()
        title = parser.title()
        headings = parser.headings()
        skipped_tags = sorted(_SKIP_TAGS)

    truncated = False
    if len(text) > max_output_chars:
        text = text[:max_output_chars] + "\n[truncated]"
        truncated = True

    return {
        "ok": True,
        "url": url,
        "status": fetched["status"],
        "content_type": fetched["content_type"],
        "bytes_read": fetched["bytes_read"],
        "looks_html": looks_html,
        "title": title,
        "headings": headings[:30],
        "text": text,
        "text_chars": len(text),
        "truncated": truncated,
        "skipped_tags": skipped_tags,
    }


def _looks_like_html(body: str) -> bool:
    head = body[:2048].lower()
    return "<html" in head or "<!doctype html" in head or "<body" in head
