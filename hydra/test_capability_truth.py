from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys

import yaml

from hydra.capability_truth import capability_truth_report
from hydra.branding import hydra_mark, hydra_signal, hydra_title


REPO = Path(__file__).resolve().parents[1]


def test_visible_skill_claims_match_discoverable_skill_docs() -> None:
    """index.yaml counts must match the actual SKILL.md files on disk (no inflated stale claims)."""
    skill_docs = sorted((REPO / "hydra" / "schemes").rglob("SKILL.md"))
    index = yaml.safe_load((REPO / "hydra" / "schemes" / "index.yaml").read_text(encoding="utf-8"))

    assert index["status"] == "MATERIALIZED"
    assert index["implemented_skill_docs"] == len(skill_docs)
    assert index["catalog_entries_claimed"] == 0
    assert index["catalog_entries_scaffolded"] >= len(skill_docs)
    # Signal must reflect the real curated count; no fabricated "1423 skills" or "1400 gen"
    signal = hydra_signal(repo_root=REPO)
    real_count = len(skill_docs)
    assert f"{real_count} skills" in signal
    assert "1423 skills" not in signal


def test_tui_identity_reads_as_hydra_agent_with_black_dragon() -> None:
    assert hydra_mark() == "🐲 H1 H2 H3 H4 H5"
    assert "HYDRA AGENT" in hydra_title()
    assert "BLACK HYDRA" not in hydra_title()
    assert "🦞" not in hydra_title()


def test_truth_docs_do_not_claim_unimplemented_skill_counts() -> None:
    paths = [
        REPO / "BUILD_COMPLETE.md",
        REPO / "hydra" / "schemes" / "README.md",
        REPO / "hydra" / "schemes" / "QUICKSTART.md",
        REPO / "hydra" / "schemes" / "IMPLEMENTATION_SUMMARY.md",
    ]

    combined = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in paths if path.exists())

    forbidden = [
        "1401+ skills",
        "1201",
        "1,200+ production-ready skills",
        "Every skill includes",
        "#1 Across",
        "Status:** ✅ **COMPLETE",
    ]
    for phrase in forbidden:
        assert phrase not in combined


def test_capability_truth_report_proves_visible_build_claims() -> None:
    """capability_truth_report proves the lean curated capability set — no fabricated 1200+ claim."""
    report = capability_truth_report(REPO)

    assert report["schema"] == "hydra.capability_truth.v1"
    assert report["status"] == "PROVEN"
    assert report["counts"]["blocked"] == 0
    capability_ids = {item["capability_id"] for item in report["capabilities"]}
    assert "curated_skill_library" in capability_ids
    assert "software_design_bundle_present" in capability_ids
    assert "planner_skill_library_search" in capability_ids
    assert "declarative_runtime_wired" in capability_ids


def test_capabilities_truth_cli_outputs_json() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "hydra", "capabilities", "truth", "--format", "json"],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "hydra.capability_truth.v1"
    assert payload["status"] == "PROVEN"
