"""Windows path semantics are testable without impersonating a Windows filesystem."""
from pathlib import PurePosixPath, PureWindowsPath

import pytest

from hydra.local_memory import _claude_project_slug
from hydra.source_boundary import relative_source


@pytest.mark.parametrize("path,owner", [
    (r"\\?\C:\synthetic\project\data\evidence.txt", r"C:\synthetic\project"),
    (r"C:\synthetic\project\data\evidence.txt", r"\\?\C:\synthetic\project"),
    (r"\\?\UNC\fixture\share\project\data\evidence.txt", r"\\fixture\share\project"),
    (r"\\fixture\share\project\data\evidence.txt", r"\\?\UNC\fixture\share\project"),
])
def test_windows_extended_aliases_preserve_internal_source_location(path, owner):
    assert relative_source(PureWindowsPath(path), PureWindowsPath(owner)) == PureWindowsPath("data/evidence.txt")


@pytest.mark.parametrize("path,owner", [
    (r"\\?\C:\synthetic\project-private\evidence.txt", r"C:\synthetic\project"),
    (r"\\?\D:\synthetic\project\evidence.txt", r"C:\synthetic\project"),
    (r"\\?\UNC\fixture\other-share\project\evidence.txt", r"\\fixture\share\project"),
    (r"\\?\UNC\other-fixture\share\project\evidence.txt", r"\\fixture\share\project"),
])
def test_windows_alias_normalization_keeps_external_sources_out(path, owner):
    with pytest.raises(ValueError):
        relative_source(PureWindowsPath(path), PureWindowsPath(owner))


@pytest.mark.parametrize("workspace,expected", [
    (PureWindowsPath(r"C:\synthetic\project"), "C--synthetic-project"),
    (PureWindowsPath(r"\\fixture\share\project"), "--fixture-share-project"),
    (PurePosixPath("/synthetic/project"), "-synthetic-project"),
])
def test_claude_project_slug_is_one_portable_component(workspace, expected):
    slug = _claude_project_slug(workspace)
    assert slug == expected
    assert PureWindowsPath(slug).anchor == ""
    assert len(PureWindowsPath(slug).parts) == 1
    assert len(PurePosixPath(slug).parts) == 1
