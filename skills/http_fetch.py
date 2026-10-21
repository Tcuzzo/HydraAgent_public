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
import json
import math
import subprocess
import sys

from hydra.http_policy import open_checked


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
    """Run the complete request in a killable worker under one wall deadline."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise SkillError("timeout must be finite and between 0 and 120 seconds")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= 1024 * 1024:
        raise SkillError("max_bytes must be between 1 and 1048576")
    from hydra.proc import kill_tree, popen_portable
    request = json.dumps(dict(url=url, allowed_hosts=allowed_hosts, timeout=timeout,
                              max_bytes=max_bytes, user_agent=user_agent))
    process = popen_portable([sys.executable, "-I", "-m", "skills.http_fetch", "--worker"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
    try:
        output, diagnostics = process.communicate(request, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        kill_tree(process)
        process.communicate(timeout=5)
        raise SkillError(f"HTTP fetch deadline exceeded after {timeout}s") from exc
    except BaseException:
        kill_tree(process)
        process.communicate(timeout=5)
        raise
    try:
        result = json.loads(output)
    except (ValueError, TypeError) as exc:
        raise SkillError(f"HTTP worker failed (exit {process.returncode}): {diagnostics[-500:]}") from exc
    if process.returncode or not result.get("ok"):
        raise SkillError(result.get("error", "HTTP worker failed"))
    return result


def _fetch(url, allowed_hosts, timeout, max_bytes, user_agent) -> dict:
    """Socket timeout is secondary; the caller enforces the full deadline."""
    if not allowed_hosts:
        raise SkillError("allowed_hosts is empty; refusing fetch")
    allowed = {h.lower() for h in allowed_hosts}
    def validate(target: str) -> None:
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme not in ("http", "https"):
            raise SkillError(f"unsupported scheme {parsed.scheme!r}")
        host = (parsed.hostname or "").lower()
        if host not in allowed:
            raise SkillError(f"host {host!r} not in allow-list {sorted(allowed)}")

    validate(url)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with open_checked(req, validate=validate, timeout=timeout) as resp:
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


if __name__ == "__main__":
    try:
        print(json.dumps(_fetch(**json.load(sys.stdin))))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:2000]}))
        raise SystemExit(1)
