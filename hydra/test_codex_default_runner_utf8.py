"""The production Codex runner owns UTF-8 for real subprocess pipes."""
from __future__ import annotations

import subprocess
import sys

import pytest

from hydra.codex_client import _default_runner


@pytest.mark.parametrize("explicit_encoding", [False, True])
def test_production_runner_preserves_unicode_under_a_legacy_locale(
    tmp_path, monkeypatch, explicit_encoding,
):
    script = tmp_path / "native utf8 transport.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(sys.stdin.buffer.read())\n"
        "sys.stdout.buffer.write('\\ntransport \\u2713'.encode('utf-8'))\n"
        "sys.stderr.buffer.write('diagnostic \\u4e2d\\u6587'.encode('utf-8'))\n",
        encoding="utf-8",
    )
    # Simulate the fallback used by Windows legacy locales while retaining the
    # actual production runner and real OS pipe encoding/decoding.
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp1252")
    options = {"encoding": "utf-8"} if explicit_encoding else {}
    prompt = "Inspect the r\u00e9sum\u00e9 \u2550 \u4e2d\u6587 \U0001f600"
    encoding_error = None
    try:
        result = _default_runner(
            [sys.executable, str(script)], input=prompt, timeout=5, **options,
        )
    except UnicodeError as exc:
        encoding_error = exc

    assert encoding_error is None, f"production transport inherited legacy encoding: {encoding_error}"
    assert result.returncode == 0
    assert result.stdout == prompt + "\ntransport \u2713"
    assert result.stderr == "diagnostic \u4e2d\u6587"
