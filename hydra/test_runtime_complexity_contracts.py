"""Additional contracts for the privacy-preserving responsibility refactor."""
from pathlib import PurePosixPath, PureWindowsPath

import pytest

from hydra import environment, model_routing
from hydra.local_memory import _claude_project_slug


@pytest.mark.parametrize("workspace,expected", [
    (PurePosixPath("/synthetic/project:archive"), "-synthetic-project:archive"),
    (PurePosixPath(r"/synthetic/project\archive"), r"-synthetic-project\archive"),
    (PureWindowsPath(r"\\?\C:\synthetic\project"), "--?-C--synthetic-project"),
    (PureWindowsPath(r"\\?\UNC\fixture\share\project"), "--?-UNC-fixture-share-project"),
    (PureWindowsPath(r"\\fixture\share\project"), "--fixture-share-project"),
    (PureWindowsPath(r"C:synthetic\project"), "C-synthetic-project"),
    (PureWindowsPath(r"\synthetic\project"), "-synthetic-project"),
])
def test_project_slug_preserves_path_flavor_contract(workspace, expected):
    assert _claude_project_slug(workspace) == expected


def test_partial_copy_failure_removes_only_new_session(tmp_path, monkeypatch):
    source = tmp_path / "project"
    source.mkdir()
    (source / "keep.txt").write_text("source retained")
    root = tmp_path / "sessions"
    existing = root / "existing"
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("session retained")

    def fail_copy(source, destination, **kwargs):
        destination.mkdir()
        (destination / "partial.txt").write_text("incomplete")
        raise OSError("synthetic copy failure")

    monkeypatch.setattr(environment.shutil, "copytree", fail_copy)
    with pytest.raises(environment.EnvironmentError, match="could not copy source repo") as error:
        environment.create_session(source_repo=source, env_root=root, title="copy failure")
    assert isinstance(error.value.__cause__, OSError)
    assert list(root.iterdir()) == [existing]
    assert (existing / "keep.txt").read_text() == "session retained"
    assert (source / "keep.txt").read_text() == "source retained"


def test_routing_rejects_non_mapping_yaml_without_caching_failure(tmp_path):
    path = tmp_path / "routing.yaml"
    path.write_text("- synthetic\n")
    with pytest.raises(model_routing.RoutingConfigError, match="top-level must be a mapping"):
        model_routing.load_routing(path, refresh=True)
    assert str(path) not in model_routing._CACHE
