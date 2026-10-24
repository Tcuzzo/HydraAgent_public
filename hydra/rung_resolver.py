"""hydra.rung_resolver — turn a BACKS rung name into a live route at call time.

Hydra's routing file names BACKS rungs only (``plan``, ``work``, ``build``, ``auto``
or a ladder rung identity such as the local GPU rung). No model, provider or key
name lives in Hydra. At call time this module asks the BACKS rung resolver
(Kev/Needle on the BACKS host) what a rung means right now:

* a capability rung (``plan`` / ``work`` / ``build`` / ``auto``) resolves to the BACKS
  gateway capability. BACKS picks the live model per request through Needle (and
  Kev for ``auto``) and holds the keys in its vault; Hydra sends no key.
* a local GPU rung (``local: true`` in the routing file) resolves to the model the
  BACKS ladder assigns that rung, served by this host's local GPU endpoint.

Never-fail rule (operator 2026-10-23): a rung that can't be resolved or that fails
mid-call raises ``RungResolutionError`` -- a retryable ``LlmError`` -- so the router
falls through the role's rung chain and finally the local GPU rung. The local rung
still works with BACKS down: it asks this host's GPU endpoint what it is serving.
Hydra only gives up when no rung at all is reachable. There is no baked model list
and nothing is cached on disk; each response's route receipt (rung -> Kev/Needle
decision -> model) is logged, never stored. Hydra keeps no learning or routing
logic of its own: BACKS Kev/Needle own the choice.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from hydra.llm import LlmError, OllamaClient

_LOG = logging.getLogger("hydra.rung")

#: Hydra's own transport names (not model providers): the BACKS gateway and the
#: host-local GPU endpoint.
GATEWAY = "backs"
LOCAL = "local"

#: Where the BACKS host lives: env var, else ``<env_dir>/.env.backs``. Never in the repo.
URL_ENV = "HYDRA_BACKS_URL"
ENV_FILE = ".env.backs"
ENV_FILE_KEY = "BACKS_ENDPOINT"


class RungResolutionError(LlmError):
    """A rung could not be resolved or failed mid-call; the router moves to the next rung."""


def backs_url(env_dir: str | Path | None = None) -> str:
    url = os.environ.get(URL_ENV, "").strip()
    if not url:
        from hydra.providers import DEFAULT_ENV_DIR, _parse_env_file

        root = Path(env_dir) if env_dir else DEFAULT_ENV_DIR
        url = _parse_env_file(root / ENV_FILE).get(ENV_FILE_KEY, "").strip()
    if not url:
        raise RungResolutionError(
            f"BACKS rung resolver location is not set: export {URL_ENV} or put "
            f"{ENV_FILE_KEY}=<url> in ~/.hydraAgent/workspace/{ENV_FILE}. Hydra names no "
            "model itself, so it can't route without BACKS."
        )
    return url.rstrip("/")


def _get(url: str, timeout: float) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": OllamaClient._USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RungResolutionError(f"BACKS rung resolver at {url} failed: {exc}") from exc


def _capability_for(rung: str, catalog: Any) -> str | None:
    for item in (catalog or {}).get("data") or []:
        cap_id = str(item.get("id") or "")
        names = {str(item.get("display_name") or "").strip().lower(), cap_id.rsplit("-", 1)[-1].lower()}
        if rung.lower() in names:
            return cap_id
    return None


def _ladder_model(rung: str, rows: Any) -> str | None:
    for candidates in ((rows or {}).get("roles") or {}).values():
        for row in candidates or []:
            if row.get("identity") == rung and row.get("model"):
                return str(row["model"])
    return None


def _local_served_model(env_dir: str | Path | None, get: Callable[[str, float], Any], timeout: float) -> str | None:
    """What this host's GPU endpoint is serving right now (loaded first, else installed)."""
    from hydra.providers import resolve as resolve_transport

    base = resolve_transport(LOCAL, env_dir=env_dir, rung_model=False).endpoint.rstrip("/")
    for path, key in (("/api/ps", "models"), ("/api/tags", "models")):
        try:
            rows = (get(f"{base}{path}", timeout) or {}).get(key) or []
        except RungResolutionError:
            continue
        for row in rows:
            name = str(row.get("model") or row.get("name") or "")
            if name and "embed" not in name.lower():
                return name
    return None


def resolve(rung: str, *, local: bool = False, env_dir: str | Path | None = None,
            fetch: Callable[[str, float], Any] | None = None, timeout: float = 15.0) -> tuple[str, str]:
    """Resolve one rung to Hydra's ``(transport, route)`` pair.

    Raises ``RungResolutionError`` (retryable) when this rung can't be used, so the
    caller moves on to the next rung.
    """
    if not rung:
        raise RungResolutionError("routing entry has no rung name")
    get = fetch or _get
    if local:
        model = None
        why = "BACKS ladder has no model for this rung"
        try:
            model = _ladder_model(rung, get(f"{backs_url(env_dir)}/api/system/needle_engineer_rungs", timeout))
        except RungResolutionError as exc:
            why = "BACKS unreachable"
            _LOG.warning("rung %s: BACKS ladder unreachable (%s); asking the local GPU", rung, exc)
        if model:
            _LOG.info("rung %s -> BACKS ladder -> local GPU model %s", rung, model)
            return LOCAL, model
        model = _local_served_model(env_dir, get, min(timeout, 5.0))
        if not model:
            raise RungResolutionError(f"local GPU rung {rung!r} is not reachable")
        _LOG.warning("rung %s -> %s -> local GPU serving %s", rung, why, model)
        return LOCAL, model
    base = backs_url(env_dir)
    cap = _capability_for(rung, get(f"{base}/v1/models", timeout))
    if not cap:
        raise RungResolutionError(f"BACKS has no capability rung named {rung!r}")
    _LOG.info("rung %s -> BACKS capability %s (Kev/Needle pick the model per request)", rung, cap)
    return GATEWAY, cap


class RungGatewayClient(OllamaClient):
    """OpenAI-compatible client for the BACKS gateway that logs each route receipt."""

    last_receipt: dict[str, str] = {}

    def _read_json(self, req: urllib.request.Request, *, timeout: float) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                receipt = {k.lower(): v for k, v in resp.headers.items() if k.lower().startswith("x-backs-")}
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
            raise RungResolutionError(f"HTTP {e.code} from {req.full_url}: {body[:300] or e.reason}") from e
        except urllib.error.URLError as e:
            raise RungResolutionError(f"could not reach {req.full_url}: {getattr(e, 'reason', e)}") from e
        except socket.timeout as e:
            raise RungResolutionError(f"timed out talking to {req.full_url} after {timeout}s") from e
        if receipt:
            self.last_receipt = receipt
            line = (
                f"hydra: rung {receipt.get('x-backs-route-tier', '?')} -> Kev/Needle decision "
                f"{receipt.get('x-backs-decision') or '(tier pick)'} -> rung "
                f"{receipt.get('x-backs-identity', '?')} -> model {receipt.get('x-backs-model', '?')}"
            )
            _LOG.info(line)
            print(line, file=sys.stderr)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise RungResolutionError(f"non-JSON response from {req.full_url}: {raw[:200]!r}") from e
