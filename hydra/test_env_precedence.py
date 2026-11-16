"""Environment precedence: the process environment wins over the operator's env file.

The operator sets provider endpoints and keys in ``<env_dir>/.env.<provider>``. A
process that already exports one of those variables means something different --
a test harness pointing at a fake server, a CI override, a one-off run -- so the
process environment must win. Otherwise a stale file silently wins over an explicit
export and the run talks to the wrong host.

This also pins the alias behaviour: ``*_ENDPOINT`` and ``*_BASE_URL`` are the same
setting under two names, and the process environment wins for both.
"""
import os
from pathlib import Path

import pytest

from hydra import providers


def _write_env(env_dir: Path, provider: str, values: dict[str, str]) -> None:
    lines = [f"{key} = {value}" for key, value in values.items()]
    (env_dir / f".env.{provider}").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _minimax_env(endpoint: str, api_key: str = "file-key") -> dict[str, str]:
    """The hosted transport's file values: it needs a key, so the file carries one."""
    return {"MINIMAX_ENDPOINT": endpoint, "MINIMAX_API_KEY": api_key}


@pytest.fixture(autouse=True)
def _clean_process_env(monkeypatch):
    """No endpoint/key from the real operator environment may leak into these tests."""
    for key in list(os.environ):
        if key.endswith(("_ENDPOINT", "_BASE_URL", "_API_KEY", "_MODEL")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("HYDRA_ENV_DIR", raising=False)


def test_process_env_endpoint_wins_over_env_file(tmp_path):
    """The explicit export beats the value in the file."""
    _write_env(tmp_path, "minimax", _minimax_env("https://file.invalid/v1"))

    monkey = pytest.MonkeyPatch()
    monkey.setenv("MINIMAX_ENDPOINT", "http://127.0.0.1:9/should-not-be-used")
    try:
        config = providers.resolve("minimax", env_dir=tmp_path)
    finally:
        monkey.undo()

    assert config.endpoint == "http://127.0.0.1:9/should-not-be-used"


def test_process_env_endpoint_wins_over_env_file_for_base_url_alias(tmp_path):
    """``BASE_URL`` is an alias of ``ENDPOINT``; the process export still wins."""
    _write_env(tmp_path, "minimax", _minimax_env("https://file.invalid/v1"))

    monkey = pytest.MonkeyPatch()
    monkey.setenv("MINIMAX_BASE_URL", "http://127.0.0.1:9/should-not-be-used")
    try:
        config = providers.resolve("minimax", env_dir=tmp_path)
    finally:
        monkey.undo()

    assert config.endpoint == "http://127.0.0.1:9/should-not-be-used"


def test_process_env_alias_wins_over_env_file_canonical(tmp_path):
    """A process export under the alias beats the file's canonical key."""
    _write_env(tmp_path, "ollama", {"OLLAMA_ENDPOINT": "http://file.invalid:11434"})

    monkey = pytest.MonkeyPatch()
    monkey.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9/should-not-be-used")
    try:
        config = providers.resolve("ollama", env_dir=tmp_path)
    finally:
        monkey.undo()

    assert config.endpoint == "http://127.0.0.1:9/should-not-be-used"


def test_process_env_api_key_wins_over_env_file(tmp_path):
    """A key exported in the process beats the key in the file."""
    _write_env(tmp_path, "minimax", _minimax_env("https://file.invalid/v1"))

    monkey = pytest.MonkeyPatch()
    monkey.setenv("MINIMAX_API_KEY", "process-key")
    try:
        config = providers.resolve("minimax", env_dir=tmp_path)
    finally:
        monkey.undo()

    assert config.api_key == "process-key"


def test_env_file_still_used_when_process_env_is_silent(tmp_path):
    """The file is the fallback, not the loser by default."""
    _write_env(tmp_path, "minimax", _minimax_env("https://file.invalid/v1"))

    config = providers.resolve("minimax", env_dir=tmp_path)

    assert config.endpoint == "https://file.invalid/v1"


def test_hydra_env_dir_selects_the_env_directory(tmp_path):
    """``HYDRA_ENV_DIR`` picks the directory the operator's files live in."""
    env_dir = tmp_path / "operator"
    env_dir.mkdir()
    _write_env(env_dir, "minimax", _minimax_env("https://from-env-dir.invalid/v1"))

    monkey = pytest.MonkeyPatch()
    monkey.setenv("HYDRA_ENV_DIR", str(env_dir))
    try:
        config = providers.resolve("minimax")
    finally:
        monkey.undo()

    assert config.endpoint == "https://from-env-dir.invalid/v1"


def test_default_env_dir_is_reevaluated_on_every_call(monkeypatch, tmp_path):
    """The default env dir is read at call time, not frozen at import time.

    A frozen constant would make ``HYDRA_ENV_DIR`` (and a changed ``HOME``) dead
    for every consumer that relies on the default.
    """
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write_env(first, "minimax", _minimax_env("https://first.invalid/v1"))
    _write_env(second, "minimax", _minimax_env("https://second.invalid/v1"))

    monkeypatch.setenv("HYDRA_ENV_DIR", str(first))
    assert providers.default_env_dir() == first

    monkeypatch.setenv("HYDRA_ENV_DIR", str(second))
    assert providers.default_env_dir() == second


def test_resolve_and_backs_url_agree_on_the_backs_endpoint(tmp_path):
    """``resolve("backs")`` and the rung resolver must read the same BACKS host.

    They used to disagree: the rung resolver read ``BACKS_ENDPOINT`` from the env
    file while ``resolve`` looked for ``BACKS_ENDPOINT``/``BACKS_URL`` under the
    provider's own key names.
    """
    _write_env(tmp_path, "backs", {"BACKS_ENDPOINT": "http://backs.test:8000"})

    from hydra import rung_resolver

    assert providers.resolve("backs", env_dir=tmp_path).endpoint == "http://backs.test:8000"
    assert rung_resolver.backs_url(env_dir=tmp_path) == "http://backs.test:8000"


def test_process_env_backs_url_wins_over_env_file(tmp_path):
    """The exported BACKS host wins over the file, for both readers."""
    _write_env(tmp_path, "backs", {"BACKS_ENDPOINT": "http://file.invalid:8000"})

    from hydra import rung_resolver

    monkey = pytest.MonkeyPatch()
    monkey.setenv("HYDRA_BACKS_URL", "http://process.invalid:9000")
    try:
        assert rung_resolver.backs_url(env_dir=tmp_path) == "http://process.invalid:9000"
        assert providers.resolve("backs", env_dir=tmp_path).endpoint == "http://process.invalid:9000"
    finally:
        monkey.undo()


def test_backs_endpoint_alias_is_honoured(tmp_path):
    """``BACKS_ENDPOINT`` in the file is the same setting as the exported URL."""
    _write_env(tmp_path, "backs", {"BACKS_ENDPOINT": "http://backs.test:8000"})

    from hydra import rung_resolver

    assert rung_resolver.backs_url(env_dir=tmp_path) == "http://backs.test:8000"
