"""skills.http_fetch — bounded HTTP GET against an allow-listed host.

Constraints:
  - Caller must pass `allowed_hosts`. The URL's host must match (exact
    match against the netloc, case-insensitive). No regex, no wildcards.
  - Hard timeout (default 15s).
  - Max response size (default 256 KiB). Anything larger raises.

Returns a dict on success, raises `SkillError` on refusal or network
failure. No silent fallbacks.

Maturity: SCAFFOLDED. Promoted by §10.6-http-fetch eval.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request


class SkillError(Exception):
    """A skill refused the request or could not complete it."""


DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_MAX_BYTES = 256 * 1024
DEFAULT_USER_AGENT = "HydraAgent-Skill/1.0"


def run(
    url: str,
    allowed_hosts: list[str],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict:
    if not allowed_hosts:
        raise SkillError("allowed_hosts is empty; refusing fetch")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SkillError(f"unsupported scheme {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    allowed = {h.lower() for h in allowed_hosts}
    if host not in allowed:
        raise SkillError(
            f"host {host!r} not in allow-list {sorted(allowed)}"
        )
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes + 1)
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raise SkillError(f"HTTPError: {e}") from e
    except urllib.error.URLError as e:
        raise SkillError(f"URLError: {e}") from e
    except TimeoutError as e:
        raise SkillError(f"TimeoutError after {timeout}s: {e}") from e
    if len(raw) > max_bytes:
        raise SkillError(
            f"response exceeded max_bytes={max_bytes} (got >{len(raw)})"
        )
    return {
        "ok": True,
        "url": url,
        "status": status,
        "content_type": content_type,
        "bytes_read": len(raw),
        "body": raw.decode("utf-8", errors="replace"),
    }
