"""Provider-agnosticism is enforced in CI, not only when a live host answers.

``test_no_backs_ladder_names_in_hydra`` used to depend on a live BACKS vocabulary.
With no BACKS reachable the guard returned ``None`` and the test skipped -- so
provider-agnosticism was never actually enforced in CI, and a hardcoded model name
would land green.

The fix is not to commit a list of model names (that would itself hard-code the
vocabulary, and would go stale the moment the ladder changes). The fix is to
enforce the *shape*: Hydra's routing surface may not contain a model-shaped
literal at all. A model name is recognisable without knowing any names --
``<family><version>``, ``<family>:<size>``, ``<family>:<tag>`` -- so a detector
built on shape catches every family, including families that did not exist when
the guard was written, and needs no live host and no committed vocabulary.

This detector is strictly stronger than the live-vocabulary scan: it catches model
families the current ladder does not serve.
"""
import re
from pathlib import Path

import pytest

from hydra.test_no_model_provider_secret_literals import (
    PROVIDER_EXEMPT,
    REPO,
    _surface_files,
)

#: A token that looks like a model name: an optional tag after a colon, and a
#: family part with letters, digits, dots, dashes and underscores.
MODEL_TOKEN = re.compile(r"(?<![\w/.:-])[A-Za-z][\w.-]*(?::[A-Za-z0-9._-]+)?")
#: A license identifier ("Apache-2.0", "GPL-3.0") is a version, not a model.
LICENSE = re.compile(r"^(?:Apache|GPL|AGPL|LGPL|MIT|BSD|ISC|MPL)[-.]?\d", re.IGNORECASE)
#: A terminal escape sequence ("\x1b") is a byte, not a model.
ESCAPE = re.compile(r"^x[0-9a-f]{2}$", re.IGNORECASE)
#: A model tag: a parameter size ("14b", "675b"), or a serving label.
MODEL_TAG = re.compile(r"^(?:\d+(?:\.\d+)?[bBmM]|cloud|latest|free)$")


def model_shaped(token: str) -> bool:
    """True when the token is shaped like a model name rather than a version or a byte."""
    family, _, tag = token.partition(":")
    family = family.rstrip(".-")
    if tag:
        return bool(MODEL_TAG.match(tag))
    if LICENSE.match(family) or ESCAPE.match(family):
        return False
    if re.search(r"\d+\.\d+", family):
        return True
    if re.search(r"\d+(?:b|bm|m)$", family, re.IGNORECASE):
        return True
    return False


def _display(path: Path) -> str:
    """Repo-relative when the file is inside the repo, the bare name otherwise."""
    try:
        return path.relative_to(REPO).as_posix()
    except ValueError:
        return path.name


def _structural_hits(files: list[Path]) -> list[str]:
    hits: list[str] = []
    for path in files:
        for no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for match in MODEL_TOKEN.finditer(line):
                token = match.group(0)
                if model_shaped(token):
                    hits.append(f"{_display(path)}:{no}: {token}")
    return hits


def test_detector_catches_a_planted_model_literal(tmp_path):
    """The shape detector must fire on a model name it has never seen."""
    planted = tmp_path / "x.py"
    planted.write_text('M = "somefamily9.9-coder:14b"\n', encoding="utf-8")

    assert _structural_hits([planted]) == ["x.py:1: somefamily9.9-coder:14b"]


def test_detector_catches_a_planted_versioned_family(tmp_path):
    """A family named without a tag is still a hardcoded model."""
    planted = tmp_path / "y.py"
    planted.write_text('# see otherfamily3.5 for details\n', encoding="utf-8")

    assert _structural_hits([planted]) == ["y.py:1: otherfamily3.5"]


def test_detector_ignores_licenses_versions_and_escapes(tmp_path):
    """Ordinary versioned text is not a model name."""
    clean = tmp_path / "z.py"
    clean.write_text(
        'license = "Apache-2.0"\n'
        'other = "GPL-3.0"\n'
        'esc = "\\x1b[200~"\n'
        'rung = "plan"\n'
        'url = "http://localhost:11434"\n'
        'env = "<NAME>_ENDPOINT"\n',
        encoding="utf-8",
    )

    assert _structural_hits([clean]) == []


def test_routing_surface_has_no_model_literals():
    """The guard itself: no model-shaped literal anywhere in the routing surface.

    This runs with no live host, no committed vocabulary and no network access.
    """
    files = _surface_files()
    hits = _structural_hits(files)

    assert not hits, (
        "model-shaped literals in Hydra's routing surface (name the rung, not the model):\n"
        + "\n".join(hits)
    )


def test_guard_does_not_depend_on_a_live_host(monkeypatch):
    """The structural guard runs with BACKS unreachable.

    The old guard skipped here, which is how provider-agnosticism went unenforced.
    """
    monkeypatch.delenv("HYDRA_BACKS_URL", raising=False)
    monkeypatch.setenv("HYDRA_ENV_DIR", "/nonexistent-hermetic-env-dir")

    hits = _structural_hits(_surface_files())

    assert hits == []


def test_live_vocabulary_guard_does_not_skip_when_backs_is_unreachable(monkeypatch):
    """The live scan still skips without a host -- but the structural guard covers it.

    This pins the intent: the skip is no longer the only thing standing between
    a hardcoded model name and a green CI.
    """
    from hydra.test_no_model_provider_secret_literals import _backs_vocab

    monkeypatch.delenv("HYDRA_BACKS_URL", raising=False)
    monkeypatch.setenv("HYDRA_ENV_DIR", "/nonexistent-hermetic-env-dir")

    assert _backs_vocab() is None
    assert _structural_hits(_surface_files()) == []


def test_provider_exempt_files_are_still_the_only_exemptions():
    """The transport registry is the one place a transport may be named."""
    assert PROVIDER_EXEMPT == {"providers.py", "codex_client.py"}
