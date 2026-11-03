"""Shared HTTP identity for outbound calls.

The default User-Agent must not contain the letters "hydra": a common WAF
rule blocks that token before the request reaches the model. App attribution
goes in HTTP-Referer and X-Title instead. Override the agent with
HYDRA_USER_AGENT when a host requires a specific string.
"""
from __future__ import annotations

import os

_DEFAULT_AGENT = "HAgent/1.0"
_REFERER = "https://github.com/Tcuzzo/HydraAgent_public"
_TITLE = "HAgent"


def user_agent() -> str:
    override = os.environ.get("HYDRA_USER_AGENT", "").strip()
    return override or _DEFAULT_AGENT


def attribution_headers() -> dict[str, str]:
    return {
        "User-Agent": user_agent(),
        "HTTP-Referer": _REFERER,
        "X-Title": _TITLE,
    }


def summarize_error_body(status: int, body: str, host: str = "") -> str:
    """Replace an HTML error page with a status line. Other bodies pass through empty."""
    text = str(body or "").lstrip()
    head = text[:400].lower()
    if not head.startswith(("<!doctype", "<html")) and "<html" not in head:
        return ""
    where = f" from {host}" if host else ""
    return (
        f"HTTP {status}{where} (HTML error page, not a model response). "
        "A Cloudflare block often means the client User-Agent was refused."
    )
