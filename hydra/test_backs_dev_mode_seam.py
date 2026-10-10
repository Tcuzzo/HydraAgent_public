#!/usr/bin/env python3
"""BACKS dev-mode seam — sniper + anti-mock-theater contract.

This test asserts REAL side-effects only. It never mocks the seam under test
(`hydra.dev_mode_seam`, `hydra.skill_spine`, or `hydra.declarative_runtime`),
the database, or the filesystem. The grounding guard resolves symbols against the
installed artifact with real ``importlib`` — a fabricated symbol is flagged, a real
symbol resolves. The doctrine is asserted from the REAL loaded YAML content, not a
canned constant, so a green here means the .hydraAgent/ contracts are actually
loaded into the agent system prompt.

Target before running (sniper discipline, inv sniper_4):
  pytest hydra/test_backs_dev_mode_seam.py -q
"""
from __future__ import annotations

import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hydra.dev_mode_seam import (
    build_backs_dev_mode_doctrine,
    dev_mode_doctor_report,
    extract_referenced_symbols,
    load_dev_mode_contracts,
    refute_fabricated_claim,
    resolve_symbol_against_repo,
)
from hydra.declarative_runtime import doctor_runtime_catalog, load_runtime_catalog
from hydra.skill_spine import build_agent_system_prompt, route_skill_names, route_skill_records

REPO = Path(__file__).resolve().parents[1]


# --- A. The seam loads from the real .hydraAgent/ files ----------------------

def test_dev_mode_contracts_load_from_real_files() -> None:
    contracts = load_dev_mode_contracts()
    # Every contract file is really present and parsed — no silent missing gate.
    assert contracts["loaded"]["invariants"], "backs-invariants.yaml not loaded"
    assert contracts["loaded"]["sniper"], "sniper-testing.yaml not loaded"
    assert contracts["loaded"]["anti_mock"], "anti-mock-theater.yaml not loaded"
    assert contracts["loaded"]["playbook"], "dev-mode-elite-build.yaml not loaded"
    # Real content: the invariants list is populated from the real YAML.
    invs = contracts["invariants"].get("invariants", [])
    assert len(invs) >= 20, f"expected >=20 invariants, got {len(invs)}"
    titles = {i.get("title") for i in invs if isinstance(i, dict)}
    assert "Resolve every symbol against the installed artifact" in titles


def test_dev_mode_doctor_report_is_ok() -> None:
    report = dev_mode_doctor_report()
    assert report["status"] == "OK", report
    assert report["invariant_count"] >= 20
    assert "resolve_symbols_against_repo" in report["anti_hallucination_guards"]
    assert "refute_fabricated_claim" in report["anti_hallucination_guards"]
    assert report["playbook_stages"] == ["Design", "Plan", "Build", "Test", "Ship"]


# --- B. The doctrine rides into the REAL agent system prompt -----------------

def test_doctrine_is_injected_into_real_system_prompt() -> None:
    # REAL build_agent_system_prompt — no mock of skill_spine.
    prompt = build_agent_system_prompt("Base agent prompt.", REPO / "skills")
    assert prompt.startswith("Base agent prompt.")
    # The dev-mode doctrine header is present.
    assert "BACKS dev-mode seam (hallucination-resistance floor)" in prompt
    # A REAL invariant title from the YAML (not a canned constant) appears in
    # the prompt — proof the YAML is loaded into the runtime, not paraphrased.
    assert "Resolve every symbol against the installed artifact" in prompt
    # The runtime guards are named in the prompt.
    assert "resolve_symbol_against_repo" in prompt
    assert "refute_fabricated_claim" in prompt
    # Sniper + anti-mock-theater rules ride in the prompt.
    assert "Sniper testing" in prompt
    assert "Anti-mock-theater" in prompt
    # The legacy evolution doctrine the contract test guards is still intact.
    assert "Hydra evolution doctrine" in prompt


def test_doctrine_loud_when_contracts_missing(tmp_path: Path) -> None:
    # A root with no .hydraAgent/ must say so LOUD — no silent gate (inv_5).
    doctrine = build_backs_dev_mode_doctrine(tmp_path)
    assert "NOT LOADED" in doctrine
    assert "model priors" in doctrine


def test_installed_dev_mode_loads_real_packaged_contracts(tmp_path, monkeypatch):
    from hydra import dev_mode_seam

    packaged = tmp_path / "hydra" / "runtime_data" / "catalog"
    for relative, _ in dev_mode_seam._CONTRACTS.values():
        destination = packaged / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / ".hydraAgent" / relative, destination)
    monkeypatch.setattr(dev_mode_seam, "DEV_MODE_ROOT", tmp_path / "missing-source-root")
    monkeypatch.setattr(dev_mode_seam, "BUNDLED_DEV_MODE_ROOT", packaged, raising=False)

    contracts = load_dev_mode_contracts()
    assert all(contracts["loaded"].values())
    assert Path(contracts["root"]) == packaged
    doctrine = build_backs_dev_mode_doctrine()
    assert "NOT LOADED" not in doctrine
    assert "Resolve every symbol against the installed artifact" in doctrine
    # An explicit empty root must not silently pick up installed defaults.
    assert "NOT LOADED" in build_backs_dev_mode_doctrine(tmp_path / "explicit-missing")


# --- C. Real routing to the dev-mode skills ----------------------------------

def test_dev_mode_skills_route_from_real_keywords() -> None:
    assert "optimus" in route_skill_names("boot dev mode and load the invariants")
    assert "elite_build_understanding" in route_skill_names("drain refute the fabricated claim, falsify it")
    assert "the_path" in route_skill_names("I am lost, help me find the route")


def test_dev_mode_skills_resolve_to_real_skill_files() -> None:
    records = route_skill_records("boot dev mode and load the invariants")
    names = [r.name for r in records]
    assert "optimus" in names
    # The skill body is a real file under skills/optimus/SKILL.md.
    optimus = next(r for r in records if r.name == "optimus")
    assert optimus.path.name == "SKILL.md"
    assert optimus.path.parent.name == "optimus"


def test_legacy_routing_contract_unchanged() -> None:
    # The pinned exact-routing contract test must still hold (inv_14: no regression).
    assert route_skill_names("fix the failing launch") == [
        "systematic-debugging",
        "test-driven-development",
    ]
    assert "verification-before-completion" not in route_skill_names("define a suffix safely")


def test_legacy_prompts_do_not_pull_in_dev_mode_skills() -> None:
    # Broader regression: legacy prompts must not accidentally route to the new
    # dev-mode skills, and a mixed legacy + dev-mode prompt routes both cleanly.
    legacy = route_skill_names("build a task planner working memory bundle")
    assert "optimus" not in legacy
    assert "elite_build_understanding" not in legacy
    assert "the_path" not in legacy
    # A mixed prompt: legacy debug intent + dev-mode intent both surface.
    mixed = route_skill_names("fix the failing launch in dev mode, load the invariants")
    assert "systematic-debugging" in mixed
    assert "test-driven-development" in mixed
    assert "optimus" in mixed


# --- D. The grounding guard refutes a fabricated symbol (real importlib) ----

def test_resolve_real_symbol_against_installed_artifact() -> None:
    # A symbol that genuinely exists in the installed hydra package resolves.
    res = resolve_symbol_against_repo("hydra.skill_spine.route_skill_names")
    assert res.resolved, res.evidence
    assert "hydra.skill_spine" in res.evidence


def test_resolve_fabricated_symbol_is_unresolved() -> None:
    # A fabricated submodule/attribute does NOT resolve — the hallucination guard.
    res = resolve_symbol_against_repo("hydra.frobnicate")
    assert not res.resolved
    assert "frobnicate" in res.evidence


def test_resolve_fabricated_attribute_is_unresolved() -> None:
    res = resolve_symbol_against_repo("hydra.skill_spine.quantum_router")
    assert not res.resolved
    assert "quantum_router" in res.evidence


def test_resolve_fabricated_nested_attribute_is_unresolved() -> None:
    # A real symbol with a fake extra attribute must NOT resolve — the guard
    # catches a hallucinated method on a real function, not just fake modules.
    res = resolve_symbol_against_repo("hydra.skill_spine.route_skill_names.nonexistent")
    assert not res.resolved
    assert "nonexistent" in res.evidence


def test_bare_symbol_is_loudly_unresolved_not_silent() -> None:
    # A single-component name (no dot) is intentionally not resolved as a code
    # symbol — resolving bare words would flag every common noun. The choice is
    # documented here and is LOUD (clear evidence), not a silent gate.
    res = resolve_symbol_against_repo("hydra")
    assert not res.resolved
    assert "dotted symbol" in res.evidence


def test_refute_flags_fabricated_claim() -> None:
    # A claim that references a hallucinated symbol is refuted.
    result = refute_fabricated_claim(
        "Use hydra.frobnicate.quantum_router() to route the dev-mode loop."
    )
    assert result.refuted
    assert "hydra.frobnicate.quantum_router" in result.flagged


def test_refute_passes_real_claim() -> None:
    # A claim that references only real symbols is NOT refuted.
    result = refute_fabricated_claim(
        "The loop calls hydra.skill_spine.route_skill_names to pick skills."
    )
    assert not result.refuted, result.evidence


def test_extract_symbols_ignores_files_and_versions() -> None:
    # File references and version strings are not treated as code symbols.
    syms = extract_referenced_symbols("See README.md and hydra v1.2.3 for hydra.skill_spine.")
    assert "README.md" not in syms
    assert "v1.2.3" not in syms
    assert "hydra.skill_spine" in syms


# --- E. The declarative catalog carries the seam + the new skills validate ---

def test_declarative_catalog_carries_dev_mode_seam() -> None:
    catalog = load_runtime_catalog(REPO)
    assert catalog.policies["backs_invariants"], "backs_invariants policy not in catalog"
    assert catalog.policies["sniper_testing"]
    assert catalog.policies["anti_mock_theater"]
    assert catalog.policies["dev_mode_playbook"]
    skill_ids = {s.get("skill_id") for s in catalog.skills.get("skills", [])}
    assert {"optimus", "elite_build_understanding", "the_path"} <= skill_ids


def test_new_skills_validate_clean() -> None:
    catalog = load_runtime_catalog(REPO)
    report = doctor_runtime_catalog(catalog)
    # No error-level finding may target one of the new dev-mode skills.
    new_ids = {"optimus", "elite_build_understanding", "the_path"}
    error_targets = {
        f.get("target") for f in report.get("findings", []) if f.get("level") == "error"
    }
    assert not (new_ids & error_targets), f"new skills have validation errors: {new_ids & error_targets}"
