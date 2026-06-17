from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from hydra.skill_library_audit import audit_skill_library, render_text


REPO = Path(__file__).resolve().parents[1]


def test_skill_library_audit_counts_real_curated_docs() -> None:
    """Audit counts the actual curated SKILL.md files present on disk, no inflated claim."""
    report = audit_skill_library(
        repo_root=REPO,
        catalog_root=REPO / "hydra" / "schemes",
        runtime_root=REPO / "skills",
    )

    assert report["schema"] == "hydra.skill_library_audit.v1"
    real_count = len(list((REPO / "hydra" / "schemes").rglob("SKILL.md")))
    assert report["counts"]["implemented_skill_docs"] == real_count
    assert real_count >= 1, "at least one curated SKILL.md must exist"
    assert report["counts"]["runtime_modules"] >= 1
    # Claim is proven for the real (unclaimed) count — no fabricated 1200 assertion
    assert report["claims"]["production_claim_proven"] is True
    assert "claimed production skill count exceeds validated procedural skill docs" not in report["findings"]


def test_skill_library_audit_reports_software_design_bundle_present() -> None:
    """The software-design bundle is present and contains frontend/design terms."""
    report = audit_skill_library(repo_root=REPO)

    frontend_design = report["frontend_design"]

    assert frontend_design["software_design_bundle_present"] is True
    assert frontend_design["frontend_terms_present"] is True
    assert frontend_design["implemented_skill_docs"] >= 1
    assert "hydra/schemes/bundles/software-design/SKILL.md" in frontend_design["evidence_paths"]


def test_skill_library_audit_text_renders_real_count() -> None:
    """render_text includes the real implemented_skill_docs count with no fabricated floor."""
    report = audit_skill_library(repo_root=REPO)
    text = render_text(report)

    real_count = report["counts"]["implemented_skill_docs"]
    assert f"implemented_skill_docs: {real_count}" in text
    assert "production_claim_proven: true" in text


def test_skills_audit_cli_outputs_json() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hydra",
            "skills",
            "audit",
            "--skills-root",
            "hydra/schemes",
            "--runtime-skills-root",
            "skills",
            "--format",
            "json",
        ],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "hydra.skill_library_audit.v1"
    # Claim proven for the real unasserted count (no fabricated 1200)
    assert payload["claims"]["production_claim_proven"] is True
