"""Exercise Windows' default stdout error policy using real CLI pipes and a loopback reply."""
import subprocess
import sys

import pytest

from hydra import test_cli_redirected_output_encoding as reply_fixture
from hydra.test_public_standalone_profile_env_dir import profile_directories


def test_chat_with_windows_default_output_policy_completes_exit(tmp_path):
    with profile_directories(tmp_path) as (env, workspace, providers, selected, ambient):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "hydra", "chat", "--profile", "local", "--env-dir",
             str(providers), "--model", "explicit-cli", "--no-local-memory", "--root", str(workspace),
             "--approval-policy", "deny"],
            cwd=workspace, env={**env, "PYTHONIOENCODING": "cp1252:surrogateescape"},
            input="/exit\n", capture_output=True, encoding="cp1252", errors="strict", timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"root={workspace}" in result.stdout
        assert "Type a task" in result.stdout and "Hydra signing off." in result.stdout
        assert not [path for path, body in selected if body is not None]
        assert [path for path, _ in ambient] == []


@pytest.mark.parametrize(("stream_setting", "reply", "expected_bytes"), [
    pytest.param("cp1252:surrogateescape", "Café € 東京", b"Caf\xe9 \x80 \\u6771\\u4eac",
                 id="ordinary-unicode"),
    pytest.param("cp1252:surrogateescape", "Café € \udcff", b"Caf\xe9 \x80 \xff",
                 id="already-supported-raw-byte"),
    pytest.param("cp1252:surrogateescape", "Café € \udcff東\udcfe京\udcff",
                 b"Caf\xe9 \x80 \xff\\u6771\xfe\\u4eac\xff", id="mixed-raw-and-unicode"),
    pytest.param("utf-8:surrogateescape", "Café € 東京", "Café € 東京".encode("utf-8"),
                 id="utf8-unicode"),
    pytest.param("cp1252:replace", "Café € 東京", b"Caf\xe9 \x80 ??", id="caller-replace"),
    pytest.param("cp1252:xmlcharrefreplace", "Café € 東京", b"Caf\xe9 \x80 &#26481;&#20140;",
                 id="caller-xmlcharrefreplace"),
])
def test_ask_output_bytes_under_windows_policies(tmp_path, monkeypatch, stream_setting, reply, expected_bytes):
    # This is the loopback server's response payload. The Hydra subprocess is unpatched.
    monkeypatch.setattr(reply_fixture, "TEXT", reply)
    with profile_directories(tmp_path) as (env, workspace, providers, _selected, ambient):
        with reply_fixture.ordinary_reply() as (endpoint, requests):
            (providers / ".env.custom").write_text(f"CUSTOM_ENDPOINT={endpoint}/v1\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-B", "-m", "hydra", "ask", "hello", "--provider", "custom",
                 "--profile", "local", "--env-dir", str(providers), "--model", "explicit-cli",
                 "--root", str(workspace), "--approval-policy", "deny",
                 "--max-iterations", "1", "--timeout", "2"],
                cwd=workspace, env={**env, "PYTHONIOENCODING": stream_setting},
                capture_output=True, timeout=20,
            )
        assert requests == [("/v1/chat/completions", "explicit-cli")]
        assert [path for path, _ in ambient] == []
        assert result.returncode == 0, result.stderr
        output = result.stdout.rstrip(b"\r\n")
        encoding = stream_setting.split(":", 1)[0]
        assert output.decode(encoding, errors="strict") == expected_bytes.decode(encoding, errors="strict")
        assert output == expected_bytes
