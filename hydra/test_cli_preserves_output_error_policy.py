"""Explicit, already-functional output error policies survive real CLI dispatch."""
import subprocess
import sys

import pytest

from hydra.test_cli_redirected_output_encoding import TEXT, ordinary_reply
from hydra.test_public_standalone_profile_env_dir import profile_directories


@pytest.mark.parametrize("policy", ["replace", "xmlcharrefreplace"])
def test_ask_respects_existing_cp1252_policy(tmp_path, policy):
    with profile_directories(tmp_path) as (env, workspace, providers, _selected, ambient):
        with ordinary_reply() as (endpoint, requests):
            (providers / ".env.custom").write_text(f"CUSTOM_ENDPOINT={endpoint}/v1\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-B", "-m", "hydra", "ask", "hello", "--provider", "custom",
                 "--profile", "local", "--env-dir", str(providers), "--model", "explicit-cli",
                 "--root", str(workspace), "--approval-policy", "deny",
                 "--max-iterations", "1", "--timeout", "2"],
                cwd=workspace, env={**env, "PYTHONIOENCODING": f"cp1252:{policy}"},
                capture_output=True, timeout=20,
            )
        assert result.returncode == 0, result.stderr
        assert requests == [("/v1/chat/completions", "explicit-cli")]
        assert [path for path, _ in ambient] == []
        assert result.stdout.rstrip(b"\r\n") == TEXT.encode("cp1252", errors=policy)
