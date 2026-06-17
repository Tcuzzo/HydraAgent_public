"""Unit tests for hydra.doctor — the dependency health + security engine.

Network is injected, so these test the pure logic: version comparison, PyPI /
OSV response parsing, diagnosis, summary, and the upgrade command.
"""
from __future__ import annotations

import sys

from hydra.doctor import (
    DepReport,
    diagnose,
    is_outdated,
    latest_from_pypi,
    packages_to_fix,
    summarize,
    upgrade_command,
    vulns_from_osv,
)


# ── is_outdated ─────────────────────────────────────────────────────────────


def test_outdated_when_latest_is_greater():
    assert is_outdated("1.0.0", "2.0.0") is True


def test_not_outdated_when_equal():
    assert is_outdated("2.0.0", "2.0.0") is False


def test_not_outdated_when_installed_is_newer():
    assert is_outdated("2.1.0", "2.0.0") is False


def test_outdated_handles_missing_values():
    assert is_outdated(None, "2.0.0") is False
    assert is_outdated("1.0.0", None) is False


def test_outdated_respects_semver_ordering():
    # purely lexical compare would call 9 > 10; proper version compare must not
    assert is_outdated("1.9.0", "1.10.0") is True


# ── PyPI / OSV parsing ──────────────────────────────────────────────────────


def test_latest_from_pypi_reads_info_version():
    assert latest_from_pypi({"info": {"version": "3.4.5"}}) == "3.4.5"


def test_latest_from_pypi_handles_malformed():
    assert latest_from_pypi({}) is None
    assert latest_from_pypi({"info": {}}) is None


def test_vulns_from_osv_collects_ids():
    payload = {"vulns": [{"id": "CVE-2026-1"}, {"id": "PYSEC-2026-2"}]}
    assert vulns_from_osv(payload) == ["CVE-2026-1", "PYSEC-2026-2"]


def test_vulns_from_osv_empty_when_clean():
    assert vulns_from_osv({}) == []
    assert vulns_from_osv({"vulns": []}) == []


# ── diagnose / summarize ────────────────────────────────────────────────────


def _fakes(installed, latest, vulns):
    return (
        lambda n: installed.get(n),
        lambda n: latest.get(n),
        lambda n, v: vulns.get(n, []),
    )


def test_diagnose_flags_outdated_and_vulnerable():
    inst, lat, vul = _fakes(
        installed={"a": "1.0.0", "b": "2.0.0", "c": "1.0.0"},
        latest={"a": "1.0.0", "b": "3.0.0", "c": "1.0.0"},
        vulns={"c": ["CVE-2026-9"]},
    )
    reports = diagnose(["a", "b", "c"], installed_of=inst, latest_of=lat, vulns_of=vul)
    by = {r.name: r for r in reports}
    assert by["a"].outdated is False and by["a"].vulnerable is False
    assert by["b"].outdated is True and by["b"].vulnerable is False
    assert by["c"].outdated is False and by["c"].vulnerable is True
    assert by["c"].vulns == ["CVE-2026-9"]


def test_diagnose_skips_vuln_lookup_for_uninstalled():
    seen = []
    inst = lambda n: None  # noqa: E731 (nothing installed)
    lat = lambda n: "1.0.0"  # noqa: E731
    vul = lambda n, v: seen.append(n) or []  # noqa: E731
    reports = diagnose(["x"], installed_of=inst, latest_of=lat, vulns_of=vul)
    assert reports[0].installed is None
    assert seen == []  # never queried OSV for a missing package


def test_summarize_counts():
    reports = [
        DepReport("a", "1.0.0", "1.0.0"),
        DepReport("b", "1.0.0", "2.0.0"),                  # outdated
        DepReport("c", "1.0.0", "1.0.0", vulns=["CVE-1"]),  # vulnerable
        DepReport("d", None, "1.0.0"),                     # missing
    ]
    s = summarize(reports)
    assert s == {"total": 4, "outdated": 1, "vulnerable": 1, "missing": 1}


# ── upgrade command ─────────────────────────────────────────────────────────


def test_upgrade_command_uses_current_interpreter_pip():
    cmd = upgrade_command(["rich", "pip"])
    assert cmd[:5] == [sys.executable, "-m", "pip", "install", "-U"]
    assert cmd[5:] == ["rich", "pip"]


def test_hydra_dependency_names_includes_core_deps_even_uninstalled():
    # When the package metadata isn't available (e.g. running from a source
    # checkout), it must still return the real dependency list via fallback.
    from hydra.doctor import hydra_dependency_names

    names = hydra_dependency_names()
    assert "rich" in names
    assert "pyyaml" in names


def test_packages_to_fix_picks_outdated_and_vulnerable_only():
    reports = [
        DepReport("ok", "1.0.0", "1.0.0"),
        DepReport("old", "1.0.0", "2.0.0"),                 # outdated
        DepReport("vuln", "1.0.0", "1.0.0", vulns=["CVE-1"]),  # vulnerable
        DepReport("missing", None, "1.0.0"),                # not installed -> skip
    ]
    assert packages_to_fix(reports) == ["old", "vuln"]
