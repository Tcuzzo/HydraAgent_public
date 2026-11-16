"""Shared test setup for hydra/test_*.

Unit tests never reach BACKS: each rung resolves offline to a placeholder route named
after the rung itself, so tests check routing by rung and no test names a real model.
The live proof (hydra ask against BACKS) exercises the real resolver.

Tests are also **hermetic**: the operator's own environment (endpoints, keys, the
real ``~/.hydraAgent/workspace`` env files) must not decide what a test resolves to.
Before this, a stale ``.env.backs`` on the machine outranked the value a test
exported, so a "local" test silently talked to a real host -- a test that passed
for the wrong reason, and the reason the suite was not reproducible.
"""
from __future__ import annotations

import pytest

from hydra import model_routing, rung_resolver


def offline_resolver(rung: str, *, local: bool = False, **_: object) -> tuple[str, str]:
    if local:
        return rung_resolver.LOCAL, f"rung-{rung}"
    return rung_resolver.GATEWAY, f"rung-{rung}"


#: Endpoint variables that would point a test at a real host. The value is never
#: read -- only the variable name matters.
ENDPOINT_VARS = (
    "HYDRA_BACKS_URL",
    "BACKS_ENDPOINT",
    "BACKS_URL",
    "MINIMAX_ENDPOINT",
    "MINIMAX_BASE_URL",
    "OLLAMA_ENDPOINT",
    "OLLAMA_BASE_URL",
    "OLLAMA_CLOUD_ENDPOINT",
    "OLLAMA_CLOUD_BASE_URL",
    "MYSERVER_ENDPOINT",
    "MYSERVER_BASE_URL",
    "TEST_ENDPOINT",
    "HYDRA_JEV_BASE_URL",
)

#: Credential variables: a test must never inherit a real key.
CREDENTIAL_VARS = (
    "MINIMAX_API_KEY",
    "MYSERVER_API_KEY",
    "OPENROUTER_API_KEY",
    "OLLAMA_CLOUD_API_KEY",
    "HYDRA_TELEGRAM_BOT_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
)

#: Variables that pin a model, a rung or a path to the operator's machine.
ROUTING_VARS = (
    "HYDRA_LAYA_MODEL",
    "HYDRA_KEV_MODEL",
    "HYDRA_JEV_MODEL",
    "HYDRA_NEEDLE_MODEL",
    "HYDRA_VISION_MODEL",
    "HYDRA_TEXT_DECISION_MODEL",
    "HYDRA_LLM_DECISION_MODEL",
    "HYDRA_CODEX_BIN",
    "HYDRA_MODEL_HEALTH_PATH",
    "HYDRA_CHAT_BUDGET_PATH",
    "OLLAMA_MODEL",
    "MYSERVER_MODEL",
)


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch, tmp_path_factory):
    """Keep every test away from the operator's real environment.

    * ``HYDRA_ENV_DIR`` points at an empty directory outside the test's own
      ``tmp_path``, so the real ``~/.hydraAgent/workspace/.env.*`` files (which
      hold a real BACKS endpoint) are never read -- and so the directory never
      shows up in a test that asserts its working directory is empty.
    * Endpoint, credential and routing variables are cleared, so a test starts
      from the same state everywhere.
    """
    env_dir = tmp_path_factory.mktemp("hermetic-env-dir")
    monkeypatch.setenv("HYDRA_ENV_DIR", str(env_dir))

    for name in ENDPOINT_VARS + CREDENTIAL_VARS + ROUTING_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _offline_rungs():
    model_routing.set_resolver(offline_resolver)
    yield
    model_routing.set_resolver(None)
