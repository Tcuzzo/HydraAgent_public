"""RED contract — config-loading guards + verification honesty + list_models contract.

Five flaws from the 2026-08-03 seam audit, each closing a silent-failure class:

1. ``ModelRouter._load_config`` does ``yaml.safe_load(...)`` with no None-guard.
   ``yaml.safe_load("")`` (or a comment-only doc) returns ``None`` -> ``config.get``
   raises ``AttributeError``. The router must tolerate an empty/malformed YAML doc
   instead of crashing on an ``AttributeError`` that hides the real cause.

2. ``create_guardrails`` does ``yaml.safe_load(...).get("guardrails", {})`` with no
   None-guard. An empty YAML doc -> ``None.get(...)`` -> ``AttributeError``. Guard
   with ``isinstance(data, dict)`` (the same pattern ``roles.py`` already uses).

3. ``classify_action_tier`` returns ``ActionTier.BOUNDED_WRITE`` as the DEFAULT for
   any unrecognized ``action_type``. Combined with ``allow_bounded_write_auto=True``
   this AUTO-APPROVES unknown actions — a fail-OPEN security gate. Unknown actions
   must fail CLOSED (require approval), never auto-approve.

4. ``route_and_execute`` Phase 3 verification is a bare ``pass`` stub while
   ``requires_verifier=True`` for non-SIMPLE tasks, so it returns success SILENTLY as
   if verified. The result must carry an HONEST ``verified`` / ``verification`` field
   so callers know the work is unverified — never a silent false-verified.

5. ``OllamaClient.list_models`` docstring says "On failure RAISES LlmError loudly" but
   the local-Ollama branch catches ``Exception`` + returns ``[]`` (silent), while the
   cloud branch raises. The local branch must ALSO raise ``LlmError`` on failure so the
   contract is consistent (callers like ``provider_available`` already wrap in
   try/except + fail-open; ``cmd_models`` already catches ``LlmError``).

These tests exercise the REAL seams (real ``ModelRouter``/``Guardrails`` constructed
from on-disk YAML; real ``route_and_execute`` with a fake router; real
``OllamaClient`` against a dead port). No mock theater — the only mock is the fake
router in test 4, which is the documented injection point.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hydra.guardrails import ActionTier, Guardrails, create_guardrails
from hydra.llm import LlmError, OllamaClient
from hydra.model_router import ModelRouter, TaskComplexity, route_and_execute


# --------------------------------------------------------------------------- #
# Finding 1 — ModelRouter._load_config None-guard
# --------------------------------------------------------------------------- #
def test_model_router_load_config_tolerates_empty_yaml_doc(tmp_path: Path) -> None:
    """An empty/comment-only hydra.yaml (safe_load -> None) must NOT crash the
    router with AttributeError; it must load with role defaults filled in."""
    cfg = tmp_path / "hydra.yaml"
    cfg.write_text("# comment-only config — safe_load returns None\n", encoding="utf-8")

    router = ModelRouter(config_path=cfg)
    # A present-but-empty file does not crash; the role defaults the loader
    # injects for missing roles (router, auditor) are filled from the SSOT.
    # (worker/planner/doer come from _load_defaults only when the file is
    # MISSING — a present-but-empty file is not that path.) The point of this
    # test is the no-crash guard, not the full default stack.
    assert "router" in router.models
    assert "auditor" in router.models


def test_model_router_load_config_tolerates_truly_empty_file(tmp_path: Path) -> None:
    cfg = tmp_path / "hydra.yaml"
    cfg.write_text("", encoding="utf-8")  # zero bytes -> safe_load -> None

    router = ModelRouter(config_path=cfg)
    assert "router" in router.models


# --------------------------------------------------------------------------- #
# Finding 2 — create_guardrails None-guard
# --------------------------------------------------------------------------- #
def test_create_guardrails_tolerates_empty_yaml_doc(tmp_path: Path) -> None:
    """An empty/comment-only guardrails.yaml must NOT crash with AttributeError;
    it must construct with default GuardrailConfig."""
    cfg = tmp_path / "guardrails.yaml"
    cfg.write_text("# comment-only\n", encoding="utf-8")

    g = create_guardrails(repo_root=tmp_path, config_path=cfg)
    assert isinstance(g, Guardrails)
    # Defaults preserved.
    assert g.config.allow_read_only_auto is True
    assert g.config.allow_bounded_write_auto is True


def test_create_guardrails_tolerates_truly_empty_file(tmp_path: Path) -> None:
    cfg = tmp_path / "guardrails.yaml"
    cfg.write_text("", encoding="utf-8")
    g = create_guardrails(repo_root=tmp_path, config_path=cfg)
    assert isinstance(g, Guardrails)


# --------------------------------------------------------------------------- #
# Finding 3 — classify_action_tier fails CLOSED for unknown action types
# --------------------------------------------------------------------------- #
def test_unknown_action_type_is_not_auto_approved(tmp_path: Path) -> None:
    """An unrecognized action_type must NOT be classified as BOUNDED_WRITE (which
    auto-approves). It must fail CLOSED — require approval."""
    g = create_guardrails(repo_root=tmp_path)
    tier = g.classify_action_tier("totally_unknown_action_xyz", {"path": str(tmp_path / "x")})
    assert tier is not ActionTier.BOUNDED_WRITE, (
        f"unknown action_type classified as {tier!r} — auto-approve of unknown "
        "actions is a fail-OPEN security gate; must require approval."
    )
    # And the full permission check must DENY auto-approval for the unknown action.
    allowed, reason, ctx = g.check_action_permission(
        "totally_unknown_action_xyz", {"path": str(tmp_path / "x")}
    )
    assert allowed is False, (
        f"unknown action_type was auto-approved (allowed={allowed!r}, "
        f"reason={reason!r}) — unknown actions must require approval."
    )


def test_known_bounded_write_still_auto_approved(tmp_path: Path) -> None:
    """Regression guard: a known bounded-write inside the repo still classifies as
    BOUNDED_WRITE and auto-approves (the fix must not over-tighten known actions)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    g = create_guardrails(repo_root=repo)
    tier = g.classify_action_tier("write_file", {"path": str(repo / "main.py")})
    assert tier is ActionTier.BOUNDED_WRITE
    allowed, _reason, _ctx = g.check_action_permission(
        "write_file", {"path": str(repo / "main.py")}
    )
    assert allowed is True


# --------------------------------------------------------------------------- #
# Finding 4 — route_and_execute verification honesty
# --------------------------------------------------------------------------- #
class _FakeRouter:
    models = {
        "doer": SimpleNamespace(
            model="qwen3-coder:480b", provider="ollama-cloud", latency_target_ms=0
        ),
        "auditor": SimpleNamespace(
            model="llama-3.3-70b-versatile", provider="ollama-cloud"
        ),
    }

    def get_client_for_task(self, task):
        return object(), SimpleNamespace(
            recommended_model="doer",
            complexity=TaskComplexity.MODERATE,
            reasoning="test",
            estimated_cost_usd=0.0,
            estimated_latency_ms=0,
            requires_verifier=True,
            requires_human_approval=False,
        )

    def create_verification_stack(self, generator_model):
        return "auditor"


def _factory(client, model):
    return object()


def test_route_and_execute_does_not_silently_claim_verified() -> None:
    """When requires_verifier=True but verification is not implemented, the result
    must NOT be silent — it must carry an honest field telling callers the work is
    UNVERIFIED. A silent success that omits verification is a false-verified."""
    res = route_and_execute("build it", _factory, tools=[], router=_FakeRouter())
    assert res["requires_verifier"] is True
    # Honest field present...
    assert "verified" in res or "verification" in res, (
        f"route_and_execute returned success with requires_verifier=True but no "
        f"verification/verified field — silent false-verified. keys={list(res.keys())}"
    )
    # ...and it must NOT claim verified=True.
    verified = res.get("verified", None)
    if verified is not None:
        assert verified is False, (
            f"route_and_execute claimed verified={verified!r} while verification is "
            "not implemented — that is a silent false-verified."
        )


def test_route_and_execute_simple_task_verification_not_required() -> None:
    """Regression guard: a SIMPLE task (requires_verifier=False) must not be forced
    into the not-implemented bucket."""
    class SimpleRouter(_FakeRouter):
        def get_client_for_task(self, task):
            return object(), SimpleNamespace(
                recommended_model="doer",
                complexity=TaskComplexity.SIMPLE,
                reasoning="simple",
                estimated_cost_usd=0.0,
                estimated_latency_ms=0,
                requires_verifier=False,
                requires_human_approval=False,
            )
    res = route_and_execute("list files", _factory, tools=[], router=SimpleRouter())
    assert res["requires_verifier"] is False


# --------------------------------------------------------------------------- #
# Finding 5 — local list_models raises LlmError loudly (contract consistency)
# --------------------------------------------------------------------------- #
def test_local_list_models_raises_llm_error_on_failure() -> None:
    """The local-Ollama branch of list_models must RAISE LlmError on failure (the
    docstring says so; the cloud branch does so). Returning [] silently is a
    contract violation — callers (provider_available, cmd_models) already handle
    LlmError."""
    # Port 1 is reserved/no-listener -> guaranteed connection failure, no real
    # network dependency. This exercises the REAL OllamaClient transport path.
    client = OllamaClient(endpoint="http://127.0.0.1:1")
    with pytest.raises(LlmError):
        client.list_models(timeout=2.0)