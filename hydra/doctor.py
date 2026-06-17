"""hydra.doctor — dependency health + security engine for `hydra doctor`.

Checks each dependency's installed vs latest version (PyPI) and known
vulnerabilities (OSV advisory database), so an end user can keep their install
current and safe. Network access is wrapped in small functions and injected into
the pure `diagnose` engine, which is fully unit-testable offline.
"""
from __future__ import annotations

import importlib.metadata as _md
import json
import sys
import urllib.request
from dataclasses import dataclass, field

try:  # version-aware comparison; ships as a dependency, falls back if absent
    from packaging.version import InvalidVersion, Version

    _HAVE_PACKAGING = True
except Exception:  # pragma: no cover - defensive
    _HAVE_PACKAGING = False

PYPI_URL = "https://pypi.org/pypi/{name}/json"
OSV_URL = "https://api.osv.dev/v1/query"


@dataclass
class DepReport:
    name: str
    installed: str | None
    latest: str | None
    vulns: list[str] = field(default_factory=list)

    @property
    def outdated(self) -> bool:
        return is_outdated(self.installed, self.latest)

    @property
    def vulnerable(self) -> bool:
        return bool(self.vulns)


# ── pure logic (unit-tested) ────────────────────────────────────────────────


def is_outdated(installed: str | None, latest: str | None) -> bool:
    """True when a newer release than `installed` exists."""
    if not installed or not latest:
        return False
    if _HAVE_PACKAGING:
        try:
            return Version(latest) > Version(installed)
        except InvalidVersion:
            return installed != latest
    return installed != latest


def latest_from_pypi(payload: dict) -> str | None:
    try:
        return payload["info"]["version"]
    except (KeyError, TypeError):
        return None


def vulns_from_osv(payload: dict) -> list[str]:
    ids: list[str] = []
    for entry in (payload or {}).get("vulns", []) or []:
        vid = entry.get("id") if isinstance(entry, dict) else None
        if vid:
            ids.append(vid)
    return ids


def diagnose(names, *, installed_of, latest_of, vulns_of) -> list[DepReport]:
    """Build a report per package. OSV is only queried for installed packages."""
    reports: list[DepReport] = []
    for name in names:
        installed = installed_of(name)
        latest = latest_of(name)
        vulns = vulns_of(name, installed) if installed else []
        reports.append(DepReport(name=name, installed=installed, latest=latest, vulns=vulns))
    return reports


def summarize(reports) -> dict:
    return {
        "total": len(reports),
        "outdated": sum(1 for r in reports if r.outdated),
        "vulnerable": sum(1 for r in reports if r.vulnerable),
        "missing": sum(1 for r in reports if r.installed is None),
    }


def packages_to_fix(reports) -> list[str]:
    """Installed packages that are outdated or vulnerable (the --fix set)."""
    return [r.name for r in reports if r.installed and (r.outdated or r.vulnerable)]


def upgrade_command(names) -> list[str]:
    """The pip command that upgrades the given packages with the current interpreter."""
    return [sys.executable, "-m", "pip", "install", "-U", *list(names)]


# ── live fetchers (network; wrap the pure parsers) ──────────────────────────


def installed_version(name: str) -> str | None:
    try:
        return _md.version(name)
    except _md.PackageNotFoundError:
        return None


def _http_json(url: str, *, data: bytes | None = None, timeout: float = 10.0):
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", "User-Agent": "hydra-doctor"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed hosts)
        return json.loads(resp.read().decode("utf-8"))


def pypi_latest(name: str, *, timeout: float = 10.0) -> str | None:
    try:
        return latest_from_pypi(_http_json(PYPI_URL.format(name=name), timeout=timeout))
    except Exception:
        return None


def osv_vulns(name: str, version: str | None, *, timeout: float = 10.0) -> list[str]:
    if not version:
        return []
    try:
        body = json.dumps(
            {"version": version, "package": {"name": name, "ecosystem": "PyPI"}}
        ).encode("utf-8")
        return vulns_from_osv(_http_json(OSV_URL, data=body, timeout=timeout))
    except Exception:
        return []


# Used when package metadata is unavailable (running from a source checkout).
# Keep in sync with [project].dependencies in pyproject.toml.
_FALLBACK_DEPENDENCIES = (
    "jsonschema", "prompt_toolkit", "psutil", "pyfiglet",
    "qrcode", "rich", "textual", "pyyaml", "packaging",
)


def hydra_dependency_names() -> list[str]:
    """Hydra's declared runtime dependency names (metadata, with a fallback)."""
    names: list[str] = []
    try:
        for req in _md.requires("hydraagent") or []:
            # 'rich>=13.9 ; extra == "x"' -> skip extras; take the bare name
            if ";" in req and "extra ==" in req:
                continue
            token = req.split(";")[0].strip()
            name = (
                token.split("==")[0]
                .split(">=")[0]
                .split("<=")[0]
                .split("~=")[0]
                .split(">")[0]
                .split("<")[0]
                .split("[")[0]
                .strip()
            )
            if name:
                names.append(name)
    except _md.PackageNotFoundError:
        pass
    return names or list(_FALLBACK_DEPENDENCIES)
