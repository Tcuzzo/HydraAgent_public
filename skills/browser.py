"""skills.browser — HydraAgent's OWN real browser (Playwright + a system Chrome).

HydraAgent drives its own headless Chrome via Playwright. This uses **a separate
browser profile** — its own process, its own user-data, its own binary resolution. Chrome's
bundled-Chromium download is unavailable on this OS (Ubuntu 26.04), so we resolve an
existing Chrome-for-Testing binary on the host and point Playwright at it via
`executable_path`.

Contract (mirrors skills.bash): every public function returns a structured dict with an
`ok` flag; on failure `ok=False` + `error`. `resolve_chrome()` is a helper and raises
`SkillError` when no usable Chrome is found.

The agent loop is synchronous, so we use Playwright's sync API. A single browser/page is
kept resident across calls and torn down by `close()`.

Maturity: SCAFFOLDED (2026-06-10).
"""
from __future__ import annotations

import glob
import os
import shutil
from typing import Any

from skills.fs_read import SkillError

# Known Chrome-for-Testing / puppeteer cache locations on this host (globs). First
# executable match wins. Override with HYDRA_CHROME_PATH.
_CHROME_SEARCH_PATHS: list[str] = [
    os.path.expanduser("~/.cache/puppeteer/chrome/*/chrome-linux64/chrome"),
    os.path.expanduser("~/.claude-server-commander/puppeteer-cache/chrome/*/chrome-linux64/chrome"),
    os.path.expanduser("~/.cache/puppeteer/chrome-headless-shell/*/chrome-headless-shell-linux64/chrome-headless-shell"),
]

DEFAULT_NAV_TIMEOUT_MS = 20000
DEFAULT_SNAPSHOT_MAX_CHARS = 8000

# Resident session (lazy). sync Playwright handle + browser + page.
_pw: Any = None
_browser: Any = None
_page: Any = None


def resolve_chrome() -> str:
    """Return a path to a usable Chrome executable, or raise SkillError.

    Order: $HYDRA_CHROME_PATH (if it exists) → known host cache paths → raise.
    """
    env = os.environ.get("HYDRA_CHROME_PATH")
    if env and os.path.exists(env) and os.access(env, os.X_OK):
        return env
    for pattern in _CHROME_SEARCH_PATHS:
        for hit in sorted(glob.glob(pattern), reverse=True):  # newest version first
            if os.path.exists(hit) and os.access(hit, os.X_OK):
                return hit
    # last resort: a chrome on PATH
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    raise SkillError(
        "no usable Chrome found; set HYDRA_CHROME_PATH or install Chrome-for-Testing"
    )


def _ensure_page() -> Any:
    """Lazily start Playwright + launch Chrome + open a page. Reuses the resident one."""
    global _pw, _browser, _page
    if _page is not None:
        return _page
    from playwright.sync_api import sync_playwright

    chrome = resolve_chrome()
    _pw = sync_playwright().start()
    _browser = _pw.chromium.launch(
        headless=True,
        executable_path=chrome,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    _page = _browser.new_page()
    return _page


def navigate(url: str, *, timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS) -> dict:
    """Open a URL. Returns {ok, url, title, status}."""
    try:
        page = _ensure_page()
        resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        return {
            "ok": True,
            "url": page.url,
            "title": page.title(),
            "status": resp.status if resp is not None else None,
        }
    except Exception as e:  # structured error, never raise to the agent
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}", "url": url}


def snapshot(*, max_chars: int = DEFAULT_SNAPSHOT_MAX_CHARS) -> dict:
    """Return the page's ARIA accessibility tree as text (deterministic, LLM-friendly)."""
    try:
        if _page is None:
            return {"ok": False, "error": "no page open; call navigate() first"}
        text = _page.locator("body").aria_snapshot()
        truncated = len(text) > max_chars
        return {"ok": True, "snapshot": text[:max_chars], "truncated": truncated}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}


def screenshot(*, path: str | None = None, full_page: bool = False) -> dict:
    """Capture a PNG. Writes to `path` if given; returns {ok, path|bytes_len}."""
    try:
        if _page is None:
            return {"ok": False, "error": "no page open; call navigate() first"}
        data = _page.screenshot(path=path, full_page=full_page)
        out: dict[str, Any] = {"ok": True}
        if path:
            out["path"] = path
        else:
            out["bytes_len"] = len(data) if data else 0
        return out
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}


def get_text(*, max_chars: int = DEFAULT_SNAPSHOT_MAX_CHARS) -> dict:
    """Return the visible body text of the current page."""
    try:
        if _page is None:
            return {"ok": False, "error": "no page open; call navigate() first"}
        text = _page.locator("body").inner_text()
        truncated = len(text) > max_chars
        return {"ok": True, "text": text[:max_chars], "truncated": truncated}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}


def click(target: str, *, timeout_ms: int = 8000) -> dict:
    """Click an element. Tries visible text first, then a CSS selector."""
    if _page is None:
        return {"ok": False, "error": "no page open; call navigate() first"}
    try:
        _page.get_by_text(target, exact=False).first.click(timeout=timeout_ms)
        return {"ok": True, "target": target, "by": "text"}
    except Exception:
        pass
    try:
        _page.click(target, timeout=timeout_ms)
        return {"ok": True, "target": target, "by": "selector"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}", "target": target}


def type_text(selector: str, text: str, *, timeout_ms: int = 8000) -> dict:
    """Type `text` into the element matched by CSS `selector`."""
    try:
        if _page is None:
            return {"ok": False, "error": "no page open; call navigate() first"}
        _page.fill(selector, text, timeout=timeout_ms)
        return {"ok": True, "selector": selector}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}", "selector": selector}


def close() -> dict:
    """Tear down the resident browser session (idempotent)."""
    global _pw, _browser, _page
    errors = []
    for obj, meth in ((_browser, "close"), (_pw, "stop")):
        if obj is not None:
            try:
                getattr(obj, meth)()
            except Exception as e:  # best-effort teardown
                errors.append(str(e))
    _page = None
    _browser = None
    _pw = None
    return {"ok": True, "errors": errors} if errors else {"ok": True}
