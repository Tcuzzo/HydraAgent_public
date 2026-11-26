"""Session copies must not import external symlink targets from the host."""
from pathlib import Path
import sys

import pytest

from hydra.environment import EnvironmentError, create_session


@pytest.mark.parametrize("target_kind", ["file", "directory", "chain"])
def test_session_copy_refuses_external_targets_and_removes_only_its_partial_session(tmp_path, target_kind):
    source = tmp_path / "source"
    source.mkdir()
    (source / "ordinary.txt").write_text("project content", encoding="utf-8")
    external = tmp_path / "external" / "canary.txt"
    external.parent.mkdir()
    external.write_text("SYNTHETIC_EXTERNAL_SESSION_CANARY", encoding="utf-8")
    if target_kind == "directory":
        (source / "linked").symlink_to(external.parent, target_is_directory=True)
    else:
        (source / "linked").symlink_to(external)
        if target_kind == "chain":
            (source / "chain").symlink_to("linked")
    sessions = tmp_path / "sessions"
    existing = sessions / "existing"
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("existing session", encoding="utf-8")
    reads = []

    def observe_read(event, args):
        if event == "open" and args[0] == str(external):
            reads.append(event)

    sys.addaudithook(observe_read)
    with pytest.raises(EnvironmentError, match="source repo"):
        create_session(source_repo=source, env_root=sessions, title="synthetic copy")
    assert reads == [], "copy opened the external target before refusing it"
    assert list(sessions.iterdir()) == [existing]
    assert (existing / "keep.txt").read_text() == "existing session"


@pytest.mark.parametrize("absolute", [False, True])
def test_session_copy_preserves_internal_links_inside_the_new_workspace(tmp_path, absolute):
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    target = source / "data" / "evidence.txt"
    target.write_text("original project content", encoding="utf-8")
    (source / "file-link").symlink_to(target if absolute else Path("data/evidence.txt"))
    (source / "directory-link").symlink_to(source / "data" if absolute else Path("data"), target_is_directory=True)
    session = create_session(source_repo=source, env_root=tmp_path / "sessions", title="internal links")
    workspace = Path(session["workspace"])
    target.write_text("changed source content", encoding="utf-8")
    for link, content in ((workspace / "file-link", workspace / "file-link"),
                          (workspace / "directory-link", workspace / "directory-link/evidence.txt")):
        assert link.is_symlink()
        assert link.resolve().is_relative_to(workspace)
        assert content.read_text() == "original project content"


def test_session_copy_refuses_looping_link_and_cleans_partial_session(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "loop").symlink_to("loop")
    sessions = tmp_path / "sessions"
    with pytest.raises(EnvironmentError, match="source repo"):
        create_session(source_repo=source, env_root=sessions, title="loop")
    assert list(sessions.iterdir()) == []
