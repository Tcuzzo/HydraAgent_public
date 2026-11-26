"""Preserve existing opt-in corpus names for legal POSIX path components."""
from pathlib import PurePosixPath

import pytest

from hydra.local_memory import _claude_project_slug


@pytest.mark.parametrize("path,expected", [
    (PurePosixPath("/synthetic/project:client"), "-synthetic-project:client"),
    (PurePosixPath(r"/synthetic/project\client"), r"-synthetic-project\client"),
])
def test_posix_memory_slug_keeps_legal_colon_and_backslash(path, expected):
    assert _claude_project_slug(path) == expected
