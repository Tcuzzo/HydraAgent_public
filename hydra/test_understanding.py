from __future__ import annotations

import json
import importlib
from types import SimpleNamespace


class _ModelLeaf:
    def __init__(self, payload: dict | None = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": list(messages), **kwargs})
        if self.error is not None:
            raise self.error
        return SimpleNamespace(content=json.dumps(self.payload))


def _typed_candidate() -> str:
    return """\
def encrypt_message(message: str) -> str:
    \"\"\"Return an encrypted form of message.\"\"\"
    return message
"""


def test_false_claim_is_refuted_by_grounded_auditor(monkeypatch) -> None:
    understanding = importlib.import_module("hydra.understanding")
    leaf = _ModelLeaf(
        {
            "passed": False,
            "failures": ["The function claims encryption but returns plaintext unchanged."],
            "recovery_actions": ["Implement encryption or remove the false claim."],
            "confidence": 0.99,
        }
    )
    monkeypatch.setattr(
        understanding.providers,
        "make_client",
        lambda provider: (leaf, SimpleNamespace(name=provider)),
    )

    result = understanding.check_candidate(
        _typed_candidate(),
        "Create a typed function that encrypts a message and can be unit tested.",
    )

    assert result.passed is False
    assert result.status == "refuted"
    assert result.failures == [
        "The function claims encryption but returns plaintext unchanged."
    ]
    assert result.recovery_actions == [
        "Implement encryption or remove the false claim."
    ]


def test_model_unavailable_never_reads_as_pass(monkeypatch) -> None:
    understanding = importlib.import_module("hydra.understanding")
    leaf = _ModelLeaf(error=RuntimeError("provider offline"))
    monkeypatch.setattr(
        understanding.providers,
        "make_client",
        lambda provider: (leaf, SimpleNamespace(name=provider)),
    )

    result = understanding.check_candidate(
        _typed_candidate(),
        "Create a typed function that encrypts a message and can be unit tested.",
    )

    assert result.passed is False
    assert result.status == "model_unavailable"
    assert result.failures
    assert "provider offline" in result.failures[0]
    assert result.recovery_actions
    assert "auditor" in result.recovery_actions[0].lower()


def test_auditor_call_requests_at_least_2000_tokens(monkeypatch) -> None:
    understanding = importlib.import_module("hydra.understanding")
    leaf = _ModelLeaf(
        {
            "passed": True,
            "failures": [],
            "recovery_actions": [],
            "confidence": 0.9,
        }
    )
    requested_providers: list[str] = []

    def fake_make_client(provider: str):
        requested_providers.append(provider)
        return leaf, SimpleNamespace(name=provider)

    monkeypatch.setattr(understanding.providers, "make_client", fake_make_client)

    understanding.check_candidate(
        _typed_candidate(),
        "Create a typed function that encrypts a message and can be unit tested.",
    )

    expected_provider, expected_model = understanding.load_routing().role_pair("auditor")
    assert requested_providers == [expected_provider]
    assert len(leaf.calls) == 1
    assert leaf.calls[0]["model"] == expected_model
    assert leaf.calls[0]["max_tokens"] >= 2000
