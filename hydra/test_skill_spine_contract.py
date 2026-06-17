from __future__ import annotations

from pathlib import Path

from hydra.skill_spine import (
    build_agent_system_prompt,
    find_skill,
    list_skill_records,
    render_skill_doctor,
    route_skill_names,
    route_skill_records,
    skill_doctor_report,
)


REPO = Path(__file__).resolve().parents[1]


def test_skill_discovery_finds_nested_skill_files(tmp_path: Path) -> None:
    skill = tmp_path / "bundles" / "coding" / "skills" / "repair-build" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: repair-build\ndescription: Repair broken builds.\n---\n# Repair Build\nRun the repo checks.\n",
        encoding="utf-8",
    )

    records = list_skill_records(tmp_path)

    assert [record.name for record in records] == ["repair-build"]


def test_skill_discovery_ignores_hidden_nested_skill_files(tmp_path: Path) -> None:
    visible = tmp_path / "visible" / "SKILL.md"
    hidden = tmp_path / ".vendor" / "hidden" / "SKILL.md"
    visible.parent.mkdir(parents=True)
    hidden.parent.mkdir(parents=True)
    visible.write_text(
        "---\nname: visible-skill\ndescription: Visible skill.\n---\n# Visible\nUse this.\n",
        encoding="utf-8",
    )
    hidden.write_text(
        "---\nname: hidden-skill\ndescription: Hidden skill.\n---\n# Hidden\nDo not trust this.\n",
        encoding="utf-8",
    )

    records = list_skill_records(tmp_path)

    assert [record.name for record in records] == ["visible-skill"]


def test_skill_routing_uses_token_boundaries() -> None:
    assert route_skill_names("fix the failing launch") == [
        "systematic-debugging",
        "test-driven-development",
    ]
    assert "verification-before-completion" not in route_skill_names("define a suffix safely")


def test_task_planner_skill_routes_from_local_skill_root() -> None:
    records = route_skill_records("build a task planner working memory bundle", REPO / "skills")

    assert [record.name for record in records] == ["task_planner"]


def test_agent_system_prompt_includes_evolutionary_runtime_doctrine() -> None:
    prompt = build_agent_system_prompt("Base agent prompt.", REPO / "skills")
    lower_prompt = prompt.lower()

    assert prompt.startswith("Base agent prompt.")
    assert "Hydra evolution doctrine" in prompt
    assert "AlphaEvolve runtime template" in prompt
    assert "parent context plus inspirations" in prompt
    assert "candidate diff or action plan" in prompt
    assert "run the evaluator" in lower_prompt
    assert "archive the result" in lower_prompt
    assert "Darwin Godel Machine discipline" in prompt
    assert "candidate variant until empirical validation passes" in prompt
    assert "human oversight" in prompt
    assert "approval policy" in prompt


def test_default_skill_doctor_counts_repo_native_core_skills() -> None:
    report = skill_doctor_report()

    assert report["status"] == "OK"
    assert "task_planner" in report["core_skills_present"]
    assert "task_planner" not in report["missing_core_skills"]


def test_text_skill_doctor_shows_all_default_skill_roots() -> None:
    text = render_skill_doctor()

    assert "skills_roots:" in text
    assert str(REPO / "skills") in text


def test_default_routing_finds_repo_native_task_planner_skill() -> None:
    records = route_skill_records("build a task planner working memory bundle")

    assert "task_planner" in [record.name for record in records]


def test_default_skill_index_includes_curated_hydra_schemes() -> None:
    """skill_doctor_report includes hydra/schemes in skills_roots and can find curated skills."""
    report = skill_doctor_report()

    # hydra/schemes is one of the scanned roots
    assert str(REPO / "hydra" / "schemes") in report["skills_roots"]
    # At least the curated bundles are discoverable (18 SKILL.md files in hydra/schemes)
    assert report["total_skills"] >= 1

    # A curated skill from hydra/schemes/bundles/software-design/skills/ is discoverable
    skill = find_skill("system-architecture-design")
    assert "hydra/schemes/bundles/software-design" in skill.path.as_posix()
