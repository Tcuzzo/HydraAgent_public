from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from hydra.skill_library_materializer import discover_catalog_entries, materialize_skill_library


REPO = Path(__file__).resolve().parents[1]


def test_discovers_1400_catalog_entries_from_bundle_backlog() -> None:
    entries = discover_catalog_entries(REPO / "hydra" / "schemes" / "bundles")

    assert len(entries) == 1400
    assert entries[0].bundle == "ci-cd"
    assert entries[0].slug
    assert entries[0].summary


def test_materializer_writes_concrete_skill_docs(tmp_path: Path) -> None:
    entries = discover_catalog_entries(REPO / "hydra" / "schemes" / "bundles")[:3]

    report = materialize_skill_library(entries, output_root=tmp_path)

    assert report["schema"] == "hydra.skill_library_materializer.v1"
    assert report["entries_total"] == 3
    assert report["written"] == 3
    for item in report["skills"][:3]:
        path = Path(item["path"])
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert "## Activation" in text
        assert "## Procedure" in text
        assert "## Verification" in text


def test_skills_materialize_cli_dry_run_reports_catalog_count() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hydra",
            "skills",
            "materialize",
            "--bundles-root",
            "hydra/schemes/bundles",
            "--output-root",
            "hydra/schemes/generated",
            "--min-count",
            "1200",
            "--dry-run",
            "--format",
            "json",
        ],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )

    assert proc.returncode == 0
    assert '"entries_total": 1400' in proc.stdout
    assert '"meets_min_count": true' in proc.stdout
