"""Operator-facing claim verification: web_extract + token-match.

`verify_claim(claim, url, allowed_hosts, …)` fetches the URL via §10.74
``web_extract``, tokenizes the claim, and counts how many distinct claim
tokens appear in the extracted page text. A PASS verdict is returned when
the matched-token ratio meets ``pass_threshold`` (default 0.6).

This is the deterministic backbone of "did the source actually back the
claim". It is not a semantic check — that belongs in §10.65 LLM judge —
but it's a fast, reproducible first line that catches drift between what
an agent said and what the cited URL actually contains.
"""
from __future__ import annotations

import re
from typing import Any

from skills import web_extract


SCHEMA = "hydra.verify_claim.v1"
DEFAULT_PASS_THRESHOLD = 0.6
_TOKEN_RE = re.compile(r"[A-Za-z0-9_§\.\-]{3,}")
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "into", "have",
    "has", "are", "was", "were", "but", "not", "you", "can", "all", "any",
    "its", "out", "use", "over", "more", "such", "than", "their", "they",
    "them", "your", "our", "his", "her", "she", "him", "who", "what",
    "when", "where", "which", "why", "how", "via", "per", "yes", "very",
})


class VerifyClaimError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


def verify_claim(
    claim: str,
    url: str,
    allowed_hosts: list[str],
    *,
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
    max_bytes: int = 256 * 1024,
    timeout: float = 15,
    max_output_chars: int = 16_000,
) -> dict[str, Any]:
    if not isinstance(claim, str) or not claim.strip():
        raise VerifyClaimError("claim must be a non-empty string")
    if not isinstance(url, str) or not url.strip():
        raise VerifyClaimError("url must be a non-empty string")
    if not (0.0 <= pass_threshold <= 1.0):
        raise VerifyClaimError("pass_threshold must be in [0.0, 1.0]")

    tokens = _tokenize_claim(claim)
    if not tokens:
        raise VerifyClaimError("claim did not yield any searchable tokens after stop-word removal")

    try:
        extracted = web_extract.run(
            url=url,
            allowed_hosts=allowed_hosts,
            timeout=timeout,
            max_bytes=max_bytes,
            max_output_chars=max_output_chars,
        )
    except web_extract.SkillError as e:
        raise VerifyClaimError(f"fetch failed: {e}") from e

    haystack = extracted["text"].lower()
    matches: list[dict[str, Any]] = []
    misses: list[str] = []
    for token in tokens:
        count = haystack.count(token)
        if count > 0:
            matches.append({"token": token, "count": count})
        else:
            misses.append(token)

    matched = len(matches)
    total = len(tokens)
    score = round(matched / total, 4) if total else 0.0
    verdict = "PASS" if score >= pass_threshold else "FAIL"

    return {
        "schema": SCHEMA,
        "verdict": verdict,
        "score": score,
        "pass_threshold": pass_threshold,
        "claim": claim,
        "url": url,
        "tokens": tokens,
        "matches": matches,
        "misses": misses,
        "matched_token_count": matched,
        "total_token_count": total,
        "source": {
            "title": extracted.get("title", ""),
            "headings": extracted.get("headings", []),
            "text_chars": extracted.get("text_chars", 0),
            "truncated": extracted.get("truncated", False),
            "status": extracted.get("status"),
        },
        "policy": "deterministic token-match; not a semantic check (use §10.65 llm_judge for that)",
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"Hydra verify-claim: {report['verdict']} score={report['score']} "
        f"matched={report['matched_token_count']}/{report['total_token_count']}",
        f"claim: {report['claim']!r}",
        f"url: {report['url']}",
        f"source: title={report['source']['title']!r} chars={report['source']['text_chars']} "
        f"truncated={report['source']['truncated']}",
    ]
    if report["matches"]:
        lines.append("matches:")
        for m in report["matches"][:10]:
            lines.append(f"  - {m['token']} x{m['count']}")
        if len(report["matches"]) > 10:
            lines.append(f"  - … +{len(report['matches']) - 10} more")
    if report["misses"]:
        lines.append("misses:")
        for token in report["misses"][:10]:
            lines.append(f"  - {token}")
        if len(report["misses"]) > 10:
            lines.append(f"  - … +{len(report['misses']) - 10} more")
    return "\n".join(lines) + "\n"


def _tokenize_claim(claim: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _TOKEN_RE.finditer(claim.lower()):
        token = match.group(0)
        if token in _STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out
