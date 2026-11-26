"""Supplemental public-behavior contracts; original sealed tests are unchanged."""
from pathlib import Path

import pytest

from hydra import environment
from hydra.skill_library_search import search_skill_library
from hydra.skill_spine import iter_skill_paths


def _skill(root: Path, name: str = 'fixture') -> Path:
    path = root / name / 'SKILL.md'
    path.parent.mkdir(parents=True)
    path.write_text('---\nname: fixture\ndescription: needle fixture\n---\n# Fixture\nneedle\n')
    return path


def test_missing_session_source_is_rejected_without_creating_state(tmp_path):
    target = tmp_path / 'sessions'
    with pytest.raises(environment.EnvironmentError, match='source repo is not a directory'):
        environment.create_session(source_repo=tmp_path / 'missing', env_root=target, title='fixture')
    assert not target.exists()


def test_blank_session_title_is_rejected_without_creating_state(tmp_path):
    source = tmp_path / 'project'
    source.mkdir()
    target = tmp_path / 'sessions'
    with pytest.raises(environment.EnvironmentError, match='title must be non-empty'):
        environment.create_session(source_repo=source, env_root=target, title='  \t ')
    assert not target.exists()


def test_session_name_collisions_preserve_existing_sessions(tmp_path, monkeypatch):
    source = tmp_path / 'project'
    source.mkdir()
    (source / 'data.txt').write_text('copied source')
    target = tmp_path / 'sessions'
    for name in ('fixture-session', 'fixture-session-2'):
        existing = target / name
        existing.mkdir(parents=True)
        (existing / 'retain.txt').write_text(name)
    monkeypatch.setattr(environment, '_session_id', lambda title: 'fixture-session')
    result = environment.create_session(source_repo=source, env_root=target, title='new session')
    assert result['session_id'] == 'fixture-session-3'
    assert Path(result['workspace']).joinpath('data.txt').read_text() == 'copied source'
    for name in ('fixture-session', 'fixture-session-2'):
        assert (target / name / 'retain.txt').read_text() == name
    assert (target / 'fixture-session-3' / 'session.json').is_file()


def test_missing_default_catalog_uses_internal_legacy_catalog(tmp_path):
    legacy = tmp_path / 'hydra' / 'skills'
    _skill(legacy)
    result = search_skill_library(query='needle', root=tmp_path)
    assert result['skills_root'] == str(legacy.resolve())
    assert result['total_scanned'] == 1
    assert result['returned'] == 1


def test_absent_catalogs_return_empty_search(tmp_path):
    result = search_skill_library(query='needle', root=tmp_path)
    assert result['hits'] == []
    assert result['total_scanned'] == 0


def test_escaping_default_catalog_does_not_fall_back_or_read_outside(tmp_path):
    project = tmp_path / 'project'
    outside = tmp_path / 'outside'
    _skill(outside, 'outside-fixture')
    _skill(project / 'hydra' / 'skills', 'inside-fixture')
    (project / 'hydra' / 'schemes').symlink_to(outside, target_is_directory=True)
    result = search_skill_library(query='needle', root=project)
    assert result['hits'] == []
    assert result['total_scanned'] == 0
    assert (outside / 'outside-fixture' / 'SKILL.md').is_file()


def test_discovery_ignores_broken_and_cyclic_links_but_keeps_valid_skill(tmp_path):
    root = tmp_path / 'catalog'
    valid = _skill(root, 'valid')
    broken = root / 'broken' / 'SKILL.md'
    broken.parent.mkdir()
    broken.symlink_to(root / 'missing-target')
    cycle = root / 'cycle' / 'SKILL.md'
    cycle.parent.mkdir()
    cycle.symlink_to(cycle)
    assert list(iter_skill_paths(root, boundary=root)) == [valid.resolve()]
    assert broken.is_symlink()
    assert cycle.is_symlink()
