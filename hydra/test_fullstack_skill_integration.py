"""Installed playbooks must route and be readable outside a project root."""
from __future__ import annotations

from pathlib import Path

import pytest

from hydra.cli.tool_binding import _skill_show_tool
from hydra.skill_spine import CORE_SKILL_NAMES, build_routed_skill_context, route_skill_records


def _skill(root: Path, name: str, body: str = "Follow the local instructions.") -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"---\nname: {name}\ndescription: Installed skill.\n---\n# Guide\n{body}\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(("name", "prompt"), [
    ("frontend-design", "Design a responsive UI for this app"),
    ("webapp-testing", "Capture browser proof for this workflow"),
    ("differential-review", "Do a security review of the latest diff"),
    ("variant-analysis", "Find bug variants after this fix"),
])
def test_optional_fullstack_skills_route_when_installed(tmp_path, name, prompt):
    path = _skill(tmp_path, name)
    assert name not in CORE_SKILL_NAMES
    assert [r.name for r in route_skill_records(prompt, tmp_path)] == [name]
    context = build_routed_skill_context(prompt, tmp_path)
    assert str(path) in context
    assert "skill_show" in context


def test_explicit_installed_skill_name_routes_without_keyword_registration(tmp_path):
    _skill(tmp_path, "my-team-review")
    assert [r.name for r in route_skill_records("Use my-team-review", tmp_path)] == ["my-team-review"]
    assert route_skill_records("Use my-team-reviewer", tmp_path) == []


def test_skill_show_reads_the_same_duplicate_name_selected_by_routing(tmp_path, monkeypatch):
    from hydra import skill_spine

    first_root = tmp_path / "global" / "nested" / "installed"
    preferred_root = tmp_path / "repo"
    _skill(first_root, "team-review", "Wrong duplicate copy")
    preferred = _skill(preferred_root, "team-review", "Selected local copy")
    monkeypatch.setattr(skill_spine, "skill_roots", lambda root=None: [first_root, preferred_root])
    routed = route_skill_records("Use team-review")
    assert routed[0].path == preferred
    shown = _skill_show_tool(name="team-review")
    assert shown["content"] == preferred.read_text(encoding="utf-8")


def test_skill_show_reads_real_instructions_and_supports_pagination(tmp_path, monkeypatch):
    path = _skill(tmp_path / "installed", "full-guide", "Intro. " * 100 + "FINAL REQUIRED CHECK")
    monkeypatch.setenv("HYDRA_SKILLS_ROOT", str(path.parent.parent))
    text = path.read_text(encoding="utf-8")
    first = _skill_show_tool(name="full-guide", max_chars=200)
    assert first["content"] == text[:200]
    assert first["next_offset"] == 200
    final = _skill_show_tool(name="full-guide", max_chars=12000, offset=200)
    assert first["content"] + final["content"] == text
    assert final["next_offset"] is None
    assert final["truncated"] is False


def test_skill_show_reads_confined_linked_reference(tmp_path, monkeypatch):
    path = _skill(tmp_path, "review")
    reference = path.parent / "references" / "method.md"
    reference.parent.mkdir()
    reference.write_text("Check caller trust boundaries.", encoding="utf-8")
    monkeypatch.setenv("HYDRA_SKILLS_ROOT", str(tmp_path))
    result = _skill_show_tool(name="review", relative_path="references/method.md")
    assert result["ok"] is True
    assert result["content"] == "Check caller trust boundaries."


@pytest.mark.parametrize("relative_path", ["../outside.txt", "/outside.txt", "C:/outside.txt"])
def test_skill_show_rejects_reference_escape(tmp_path, monkeypatch, relative_path):
    _skill(tmp_path, "review")
    monkeypatch.setenv("HYDRA_SKILLS_ROOT", str(tmp_path))
    result = _skill_show_tool(name="review", relative_path=relative_path)
    assert result["ok"] is False
    assert "relative" in result["error"] or "outside" in result["error"]


def test_skill_show_rejects_symlinked_reference_escape(tmp_path, monkeypatch):
    path = _skill(tmp_path / "installed", "review")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside the trusted skill directory", encoding="utf-8")
    try:
        (path.parent / "linked.md").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    monkeypatch.setenv("HYDRA_SKILLS_ROOT", str(path.parent.parent))
    result = _skill_show_tool(name="review", relative_path="linked.md")
    assert result["ok"] is False
    assert "outside" in result["error"]


def test_skill_show_bounds_oversized_reference(tmp_path, monkeypatch):
    path = _skill(tmp_path, "review")
    (path.parent / "huge.md").write_text("x" * (1024 * 1024 + 1), encoding="utf-8")
    monkeypatch.setenv("HYDRA_SKILLS_ROOT", str(tmp_path))
    result = _skill_show_tool(name="review", relative_path="huge.md")
    assert result["ok"] is False
    assert "limit" in result["error"]
