"""RED contract — path-confinement must use path-component equality, not a string prefix.

_is_safe_path used str(resolved).startswith(str(repo_resolved)), so a sibling
directory sharing a name prefix (repo_root=/home/op/repo vs target=/home/op/repo_evil/
secret) passed the check, was classified BOUNDED_WRITE, and with auto-approval on
was AUTO-APPROVED — the path-confinement security gate was bypassed for any sibling
path sharing a name prefix. Found by the 2026-08-03 bug hunt (HIGH, security).

The fix: Path.is_relative_to (path-component equality, not a string prefix). Requires
Python 3.11+ (the repo's floor).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hydra.guardrails import create_guardrails


def test_path_inside_repo_is_safe(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    g = create_guardrails(repo_root=repo)
    assert g._is_safe_path(str(repo / "src" / "main.py")) is True


def test_sibling_dir_sharing_name_prefix_is_not_safe(tmp_path: Path) -> None:
    """The prefix-bypass: /repo vs /repo_evil must NOT pass a startswith check."""
    repo = tmp_path / "repo"
    repo.mkdir()
    evil = tmp_path / "repo_evil"
    evil.mkdir()
    g = create_guardrails(repo_root=repo)
    assert g._is_safe_path(str(evil / "secret.txt")) is False, (
        "a sibling directory sharing a name prefix (repo vs repo_evil) must NOT "
        "pass the path-confinement check — str.startswith would let it through + "
        "auto-approve it as a BOUNDED_WRITE (the security bypass)."
    )
    # And a deeper sibling path also must not pass.
    assert g._is_safe_path(str(evil / "deep" / "nested.py")) is False


def test_parent_traversal_is_not_safe(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    g = create_guardrails(repo_root=repo)
    assert g._is_safe_path(str(tmp_path / "outside.py")) is False
    assert g._is_safe_path(str(repo / ".." / "outside.py")) is False