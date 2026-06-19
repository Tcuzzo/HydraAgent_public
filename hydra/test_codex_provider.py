"""Tests for the generic Codex CLI provider (hydra.codex_client + providers wiring).

The Codex provider shells out to OpenAI's official `codex` CLI, which performs
the browser "Sign in with ChatGPT" OAuth and runs the model on the user's
ChatGPT subscription — no API key. These tests cover the PURE parts only:

  * `codex_command(...)` argv builder — exact argv shape.
  * `resolve_codex_bin()` — HYDRA_CODEX_BIN override / shutil.which / clear error.
  * `CodexClient.chat(...)` with an INJECTED fake runner — never the real CLI.
  * `providers.make_client("codex")` dispatch — returns a CodexClient, raises a
    clear install/login error when the binary is absent, never an import crash.

No test invokes the real `codex` binary or any network/account.
"""
from __future__ import annotations

import os

import pytest

from hydra.codex_client import CodexClient, codex_command, resolve_codex_bin
from hydra.llm import ChatMessage, ChatResponse, LlmError


# --------------------------------------------------------------------------
# codex_command — argv builder (pure)
# --------------------------------------------------------------------------


def test_codex_command_argv_shape():
    argv = codex_command(
        "hello", cwd="/work/dir", output_file="/tmp/out.txt", codex_bin="codex"
    )
    assert argv == [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "-C",
        "/work/dir",
        "-o",
        "/tmp/out.txt",
        "-",
    ]


def test_codex_command_default_bin_is_codex():
    argv = codex_command("p", cwd="/c", output_file="/o")
    assert argv[0] == "codex"
    assert argv[1] == "exec"
    assert argv[-1] == "-"  # prompt comes on STDIN


def test_codex_command_with_model_inserts_model_flag():
    argv = codex_command(
        "p", cwd="/c", output_file="/o", codex_bin="codex", model="o3"
    )
    assert "-m" in argv
    assert argv[argv.index("-m") + 1] == "o3"
    # model flag does not disturb the verified tail
    assert argv[-3:] == ["-o", "/o", "-"]


def test_codex_command_no_model_omits_model_flag():
    argv = codex_command("p", cwd="/c", output_file="/o")
    assert "-m" not in argv


# --------------------------------------------------------------------------
# resolve_codex_bin — bin resolution (pure, monkeypatched)
# --------------------------------------------------------------------------


def test_resolve_codex_bin_uses_env_override(monkeypatch):
    monkeypatch.setenv("HYDRA_CODEX_BIN", "/custom/path/codex")
    # which must NOT be consulted when the override is set
    monkeypatch.setattr(
        "hydra.codex_client.shutil.which",
        lambda *a, **k: pytest.fail("which should not be called when override set"),
    )
    assert resolve_codex_bin() == "/custom/path/codex"


def test_resolve_codex_bin_uses_which_when_no_override(monkeypatch):
    monkeypatch.delenv("HYDRA_CODEX_BIN", raising=False)
    monkeypatch.setattr(
        "hydra.codex_client.shutil.which", lambda name, **k: "/usr/local/bin/codex"
    )
    assert resolve_codex_bin() == "/usr/local/bin/codex"


def test_resolve_codex_bin_raises_clear_error_when_absent(monkeypatch):
    monkeypatch.delenv("HYDRA_CODEX_BIN", raising=False)
    monkeypatch.setattr("hydra.codex_client.shutil.which", lambda name, **k: None)
    with pytest.raises(LlmError) as exc:
        resolve_codex_bin()
    msg = str(exc.value).lower()
    assert "codex" in msg
    assert "install" in msg
    assert "login" in msg  # tells the user to run `codex login`


def test_resolve_codex_bin_empty_override_falls_back_to_which(monkeypatch):
    monkeypatch.setenv("HYDRA_CODEX_BIN", "")  # empty is not a real path
    monkeypatch.setattr(
        "hydra.codex_client.shutil.which", lambda name, **k: "/bin/codex"
    )
    assert resolve_codex_bin() == "/bin/codex"


# --------------------------------------------------------------------------
# CodexClient.chat — INJECTED fake runner (no real subprocess)
# --------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, *, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _runner_writing(answer: str, *, returncode=0, stdout="", stderr=""):
    """Build a fake runner that writes `answer` to the `-o` output file, mimicking
    how the real `codex exec -o <file>` produces a clean final message."""

    def _runner(argv, *, input=None, capture_output=True, text=True, timeout=None):
        # The output file is the token right after "-o" in argv.
        out_path = argv[argv.index("-o") + 1]
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(answer)
        return _FakeProc(returncode=returncode, stdout=stdout, stderr=stderr)

    return _runner


def test_chat_parses_answer_from_output_file():
    client = CodexClient(
        codex_bin="codex", cd="/work", runner=_runner_writing("42 is the answer")
    )
    resp = client.chat([ChatMessage(role="user", content="meaning of life?")])
    assert isinstance(resp, ChatResponse)
    assert resp.content == "42 is the answer"
    assert resp.finish_reason == "stop"


def test_chat_accepts_raw_dict_messages():
    client = CodexClient(
        codex_bin="codex", cd="/work", runner=_runner_writing("ok")
    )
    resp = client.chat([{"role": "user", "content": "ping"}])
    assert resp.content == "ok"


def test_chat_flattens_system_and_user_messages():
    seen = {}

    def _runner(argv, *, input=None, **k):
        seen["prompt"] = input
        out_path = argv[argv.index("-o") + 1]
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("done")
        return _FakeProc(returncode=0)

    client = CodexClient(codex_bin="codex", cd="/work", runner=_runner)
    client.chat(
        [
            ChatMessage(role="system", content="be terse"),
            ChatMessage(role="user", content="hi"),
        ]
    )
    assert "be terse" in seen["prompt"]
    assert "hi" in seen["prompt"]


def test_chat_empty_prompt_raises():
    client = CodexClient(codex_bin="codex", cd="/work", runner=_runner_writing("x"))
    with pytest.raises(LlmError):
        client.chat([ChatMessage(role="user", content="   ")])


def test_chat_nonzero_exit_with_no_answer_raises():
    def _runner(argv, *, input=None, **k):
        # write nothing to the output file
        return _FakeProc(returncode=1, stderr="codex blew up")

    client = CodexClient(codex_bin="codex", cd="/work", runner=_runner)
    with pytest.raises(LlmError) as exc:
        client.chat([ChatMessage(role="user", content="hi")])
    assert "codex" in str(exc.value).lower()


def test_chat_clean_exit_empty_answer_raises_not_silent():
    def _runner(argv, *, input=None, **k):
        out_path = argv[argv.index("-o") + 1]
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("   ")  # whitespace only -> no real answer
        return _FakeProc(returncode=0)

    client = CodexClient(codex_bin="codex", cd="/work", runner=_runner)
    with pytest.raises(LlmError):
        client.chat([ChatMessage(role="user", content="hi")])


def test_chat_recovered_transient_in_logs_still_succeeds():
    """A transient warning in stderr + a real answer + exit 0 is success, not an
    outage — never a fake red."""

    def _runner(argv, *, input=None, **k):
        out_path = argv[argv.index("-o") + 1]
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("PONG")
        return _FakeProc(returncode=0, stderr="warning: transient 403, recovered")

    client = CodexClient(codex_bin="codex", cd="/work", runner=_runner)
    resp = client.chat([ChatMessage(role="user", content="ping")])
    assert resp.content == "PONG"


def test_chat_timeout_surfaces_clear_error():
    class _Timeout(Exception):
        pass

    _Timeout.__name__ = "TimeoutExpired"

    def _runner(argv, *, input=None, timeout=None, **k):
        raise _Timeout()

    client = CodexClient(codex_bin="codex", cd="/work", runner=_runner)
    with pytest.raises(LlmError) as exc:
        client.chat([ChatMessage(role="user", content="hi")], timeout=5.0)
    assert "timed out" in str(exc.value).lower()


def test_list_models_returns_configured_model():
    client = CodexClient(codex_bin="codex", cd="/work", model="codex", runner=_runner_writing("x"))
    assert client.list_models() == ["codex"]


# --------------------------------------------------------------------------
# providers.make_client("codex") dispatch
# --------------------------------------------------------------------------


def test_make_client_codex_returns_codex_client_when_bin_present(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "hydra.codex_client.shutil.which", lambda name, **k: "/usr/bin/codex"
    )
    monkeypatch.delenv("HYDRA_CODEX_BIN", raising=False)
    from hydra.providers import make_client

    client, cfg = make_client("codex", env_dir=tmp_path)
    assert isinstance(client, CodexClient)
    assert cfg.name == "codex"
    # codex needs no api key
    assert cfg.api_key is None


def test_make_client_codex_raises_clear_error_when_bin_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("HYDRA_CODEX_BIN", raising=False)
    monkeypatch.setattr("hydra.codex_client.shutil.which", lambda name, **k: None)
    from hydra.providers import make_client

    with pytest.raises(LlmError) as exc:
        make_client("codex", env_dir=tmp_path)
    msg = str(exc.value).lower()
    assert "install" in msg and "login" in msg


def test_codex_is_listed_provider():
    from hydra.providers import list_providers

    assert "codex" in list_providers()


def test_codex_not_forbidden():
    from hydra.providers import FORBIDDEN_PROVIDER_NAMES

    assert "codex" not in FORBIDDEN_PROVIDER_NAMES


def test_make_client_codex_honors_env_bin_override(monkeypatch, tmp_path):
    monkeypatch.setenv("HYDRA_CODEX_BIN", "/opt/codex/bin/codex")
    # which would fail; the override must win and avoid the error path
    monkeypatch.setattr("hydra.codex_client.shutil.which", lambda name, **k: None)
    from hydra.providers import make_client

    client, _cfg = make_client("codex", env_dir=tmp_path)
    assert client.codex_bin == "/opt/codex/bin/codex"
