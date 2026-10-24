"""Operator law (2026-10-23): no model IDs, provider names or secret names in Hydra's
routing surface. Routing names BACKS rungs only; Kev/Needle resolve them at call time.

Three checks, each reporting file:line and the offending token (never a value):

* model tags   - ``name:tag`` model references (``family:14b``, ``family:cloud`` ...).
* secret names - ``*_API_KEY`` / ``*_TOKEN`` / ``*_SECRET`` style key names.
* hosted URLs  - a provider's hosted endpoint baked into routing code (only this
                 host's local endpoint may appear; the BACKS URL comes from env).
* live vocab   - every model and provider name the BACKS rung ladder knows right now,
                 fetched from BACKS (so this file names none of them either). Runs
                 wherever BACKS is reachable; elsewhere it is reported as skipped.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "hydra"

#: The routing surface: every file that decides which model answers a turn.
SURFACE = (
    "model_routing.yaml",
    "model_routing.py",
    "rung_resolver.py",
    "model_router.py",
    "runtime_route.py",
    "providers.py",
    "cli/cmd_chat.py",
    "cli/cmd_ask.py",
)
#: Operator env files Hydra's routing reads (outside the repo). Only key NAMES are scanned.
ENV_FILES = (".env.ollama", ".env.backs")

MODEL_TAG = re.compile(r"(?<![\w/.-])[A-Za-z][\w.-]*:(?:\d+(?:\.\d+)?[bBmM]|cloud|latest|free)\b")
SECRET_NAME = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_(?:API_KEY|API_TOKEN|ACCESS_TOKEN|SECRET|SECRET_KEY|TOKEN)\b")
#: Provider-name check skips the transport registry, whose builtin transport keys the
#: media studio still calls by name (tracked separately; see uplift report).
PROVIDER_EXEMPT = {"providers.py"}


def _surface_files() -> list[Path]:
    files = [PKG / rel for rel in SURFACE if (PKG / rel).exists()]
    assert files, "routing surface not found"
    return files


def _env_files() -> list[Path]:
    root = Path(os.environ.get("HYDRA_ENV_DIR", Path.home() / ".hydraAgent" / "workspace"))
    return [root / name for name in ENV_FILES if (root / name).is_file()]


def _hits(pattern: re.Pattern[str], files: list[Path]) -> list[str]:
    out = []
    for path in files:
        for no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in pattern.finditer(line):
                out.append(f"{path.name}:{no}: {m.group(0)}")
    return out


def _env_key_hits(pattern: re.Pattern[str]) -> list[str]:
    out = []
    for path in _env_files():
        for no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            key = line.split("=", 1)[0].strip()  # key name only; the value is never read out
            if key and not key.startswith("#") and pattern.search(key):
                out.append(f"{path.name}:{no}: {key}")
    return out


def test_scanner_catches_planted_literals(tmp_path):
    planted = tmp_path / "x.py"
    planted.write_text('M = "family7.5-coder:14b"\nK = "VENDOR_API_KEY"\nN = "x:cloud"\n')
    assert len(_hits(MODEL_TAG, [planted])) == 2
    assert _hits(SECRET_NAME, [planted]) == ["x.py:2: VENDOR_API_KEY"]
    clean = tmp_path / "y.py"
    clean.write_text('rung = "plan"\nurl = "http://localhost:11434"\nenv = "<NAME>_ENDPOINT"\n')
    assert _hits(MODEL_TAG, [clean]) == [] and _hits(SECRET_NAME, [clean]) == []
    assert _hits(HOSTED_URL, [clean]) == []
    hosted = tmp_path / "z.py"
    hosted.write_text('base_url = "https://api.vendor.cloud"\n')
    assert _hits(HOSTED_URL, [hosted]) == ["z.py:1: https://api.vendor.cloud"]


def test_no_model_tags_in_routing_surface():
    hits = _hits(MODEL_TAG, _surface_files())
    assert not hits, "model IDs in Hydra routing surface:\n" + "\n".join(hits)


HOSTED_URL = re.compile(r"https?://(?!localhost\b|127\.0\.0\.1\b|<)[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def test_no_hosted_provider_urls_in_routing_surface():
    files = [f for f in _surface_files() if f.name not in PROVIDER_EXEMPT]
    hits = _hits(HOSTED_URL, files)
    assert not hits, "hosted provider endpoints in Hydra routing surface:\n" + "\n".join(hits)


def test_no_secret_names_in_routing_surface():
    hits = _hits(SECRET_NAME, _surface_files()) + _env_key_hits(SECRET_NAME)
    assert not hits, "secret names in Hydra routing surface:\n" + "\n".join(hits)


def _backs_vocab() -> tuple[set[str], set[str]] | None:
    url = os.environ.get("HYDRA_BACKS_URL", "").strip()
    if not url:
        for path in _env_files():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("BACKS_ENDPOINT="):
                    url = line.split("=", 1)[1].strip()
    if not url:
        return None
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/system/needle_engineer_rungs", timeout=10) as r:
            rows = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - unreachable BACKS => vocab check can't run here
        return None
    models, providers = set(), set()
    for cands in (rows.get("roles") or {}).values():
        for row in cands or []:
            if row.get("model"):
                models.add(str(row["model"]))
            if row.get("provider"):
                providers.add(str(row["provider"]))
    return models, providers


def _word(token: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w.:/-])" + re.escape(token) + r"(?![\w.:/-])", re.IGNORECASE)


def test_no_backs_ladder_names_in_routing_surface():
    vocab = _backs_vocab()
    if vocab is None:
        pytest.skip("BACKS rung ladder not reachable here; live-vocabulary scan not run")
    models, providers = vocab
    files = _surface_files()
    hits = []
    for token in sorted(models):
        hits += _hits(_word(token), files)
    scoped = [f for f in files if f.name not in PROVIDER_EXEMPT]
    for token in sorted(providers):
        hits += _hits(_word(token), scoped)
    # Model families ("<family>2.5-coder", "<family>-3-super" ...) named without a tag.
    families = {m.group(0) for m in (re.match(r"[A-Za-z]{3,}", name) for name in models) if m}
    for fam in sorted(families):
        hits += _hits(re.compile(r"(?<![\w])" + re.escape(fam) + r"(?=[\d.-])", re.IGNORECASE), scoped)
    assert not hits, "BACKS ladder model/provider names in Hydra routing surface:\n" + "\n".join(hits)
