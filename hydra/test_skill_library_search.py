from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys

from hydra.declarative_runtime import execute_agent_decision, load_runtime_catalog
from hydra.skill_library_search import search_skill_library


REPO = Path(__file__).resolve().parents[1]


def test_search_skill_library_finds_curated_skills() -> None:
    """Search returns curated skills that actually exist in hydra/schemes/bundles."""
    report = search_skill_library(query="system architecture design", root=REPO, limit=5)

    assert report["schema"] == "hydra.skill_library_search.v1"
    assert report["total_scanned"] >= 1, "must scan at least the curated SKILL.md files"
    assert report["returned"] >= 1, "must return at least one hit for 'system architecture design'"
    # system-architecture-design lives in hydra/schemes/bundles/software-design/skills/
    assert any(hit["skill_id"] == "system-architecture-design" for hit in report["hits"])


def test_declarative_skill_library_search_tool_executes() -> None:
    catalog = load_runtime_catalog(REPO)
    result = execute_agent_decision(
        {
            "schema": "hydra.agent_decision.v1",
            "intent": {"kind": "design", "confidence": 0.95, "target": "system architecture"},
            "selected_skills": [{"skill_id": "task_planner", "reason": "route skill library"}],
            "selected_tools": [{"tool_id": "skill_library.search", "reason": "find relevant skill contracts"}],
            "execution_mode": "direct",
            "requires_approval": False,
            "approval_reason": "",
            "plan": [
                {
                    "id": "search",
                    "action": "search skill library",
                    "tool_id": "skill_library.search",
                    "arguments": {"query": "system architecture design", "limit": 5},
                    "expected_evidence": "skill hits",
                }
            ],
            "verification": [{"check": "hits returned", "command": "none", "required": True}],
        },
        catalog,
        root=REPO,
    )

    report = result["results"][0]
    assert report["schema"] == "hydra.skill_library_search.v1"
    assert report["returned"] >= 1
    assert any(hit["skill_id"] == "system-architecture-design" for hit in report["hits"])


def test_skills_search_cli_outputs_json() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hydra",
            "skills",
            "search",
            "system architecture design",
            "--format",
            "json",
            "--limit",
            "5",
        ],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "hydra.skill_library_search.v1"
    assert any(hit["skill_id"] == "system-architecture-design" for hit in payload["hits"])
