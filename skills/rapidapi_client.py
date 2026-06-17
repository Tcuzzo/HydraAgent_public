"""Strategic RapidAPI client (Twitter + YouTube).

ALL API keys have limits — this client is built to NEVER burn through:
  - reads the X-RateLimit-Requests-Remaining/Limit headers on every call and
    persists them, so we always know the real remaining quota;
  - REFUSES a call when remaining <= a floor (default 25) unless force=True;
  - disk-caches responses by (api, path, params) for a TTL so repeat reads cost 0;
  - sends a browser User-Agent (youtube138 Cloudflare-blocks otherwise: err 1010).

Verified live 2026-06-12: youtube138 = 10,000/mo (browser UA required),
twitter241 = 1,000/mo. Key/hosts in ~/.config/hydra/rapidapi.env.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENV_PATH = Path.home() / ".config/hydra/rapidapi.env"
STATE_PATH = Path.home() / ".config/hydra/rapidapi_state.json"
CACHE_DIR = Path.home() / ".cache/hydra/rapidapi"
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
REMAINING_FLOOR = 25  # refuse below this to never burn the last of the quota


class RapidAPIError(Exception):
    pass


class RapidAPIBudgetError(RapidAPIError):
    pass


def _env() -> dict:
    if not ENV_PATH.is_file():
        raise RapidAPIError(f"missing {ENV_PATH} (set the key/hosts in that env file)")
    out = {}
    for line in ENV_PATH.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


_HOSTS = {
    "youtube": "LISA_RAPIDAPI_YOUTUBE_HOST",
    "twitter": "LISA_RAPIDAPI_TWITTER_HOST",
}


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2))
    except Exception:
        pass


def remaining(api: str) -> int | None:
    """Last-known remaining quota for an api (from the most recent call)."""
    return _load_state().get(api, {}).get("remaining")


def _cache_key(api: str, path: str, params: dict) -> Path:
    raw = api + "|" + path + "|" + json.dumps(params or {}, sort_keys=True)
    import hashlib
    return CACHE_DIR / (hashlib.sha256(raw.encode()).hexdigest()[:24] + ".json")


def get(api: str, path: str, params: dict | None = None, *, cache_ttl: float = 1800.0,
        force: bool = False, opener=None) -> dict:
    """GET a RapidAPI endpoint with quota-awareness + caching.

    api: "youtube" | "twitter". path: e.g. "/search/". params: query dict.
    Returns {"ok", "status", "data", "remaining", "limit", "cached"}.
    Raises RapidAPIBudgetError when remaining <= floor (unless force=True).
    """
    if api not in _HOSTS:
        raise RapidAPIError(f"unknown api {api!r}; known: {list(_HOSTS)}")
    params = params or {}
    cpath = _cache_key(api, path, params)
    if cache_ttl > 0 and cpath.is_file():
        try:
            c = json.loads(cpath.read_text())
            if time.time() - c.get("at", 0) < cache_ttl:
                return {**c["resp"], "cached": True}
        except Exception:
            pass

    rem = remaining(api)
    if rem is not None and rem <= REMAINING_FLOOR and not force:
        raise RapidAPIBudgetError(
            f"{api}: only {rem} requests left (floor {REMAINING_FLOOR}) — refusing to burn it; pass force=True to override")

    env = _env()
    key = env.get("RAPIDAPI_KEY", "")
    host = env.get(_HOSTS[api], "")
    if not key or not host:
        raise RapidAPIError(f"{api}: key/host not configured in {ENV_PATH}")
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    url = f"https://{host}{path}{qs}"
    req = urllib.request.Request(url, headers={
        "x-rapidapi-key": key, "x-rapidapi-host": host,
        "User-Agent": _UA, "Accept": "application/json"})
    _open = opener or (lambda r: urllib.request.urlopen(r, timeout=30))
    try:
        with _open(req) as r:
            status = r.status
            hdrs = dict(r.headers)
            raw = r.read()
    except urllib.error.HTTPError as e:
        status = e.code
        hdrs = dict(e.headers)
        raw = e.read()
    except Exception as e:
        raise RapidAPIError(f"{api} request failed: {type(e).__name__}: {str(e)[:120]}") from e

    def _h(name):
        for k, v in hdrs.items():
            if k.lower() == name:
                return v
        return None
    lim = _h("x-ratelimit-requests-limit")
    rem_now = _h("x-ratelimit-requests-remaining")
    try:
        data = json.loads(raw) if raw else None
    except Exception:
        data = {"_raw": raw[:300].decode("utf-8", "replace")}
    # persist the real remaining quota
    state = _load_state()
    state[api] = {"remaining": int(rem_now) if rem_now and rem_now.isdigit() else None,
                  "limit": int(lim) if lim and lim.isdigit() else None,
                  "at": time.time(), "last_status": status}
    _save_state(state)
    resp = {"ok": status == 200, "status": status, "data": data,
            "remaining": state[api]["remaining"], "limit": state[api]["limit"], "cached": False}
    if status == 200 and cache_ttl > 0:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cpath.write_text(json.dumps({"at": time.time(), "resp": resp}))
        except Exception:
            pass
    return resp


# convenience wrappers for the content factory's trend mining
def youtube_search(query: str, **kw) -> dict:
    return get("youtube", "/search/", {"q": query, "hl": "en", "gl": "US"}, **kw)


def twitter_search(query: str, count: int = 20, **kw) -> dict:
    return get("twitter", "/search-v2", {"type": "Top", "count": count, "query": query}, **kw)
