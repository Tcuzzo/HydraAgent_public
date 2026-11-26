"""Test CLI dispatch's stream lifetime separately from real subprocess output contracts."""
import io
import sys

import pytest

from hydra import __main__ as entry


@pytest.mark.parametrize("raise_in_handler", [False, True])
def test_dispatch_restores_each_stream_after_return_or_exception(monkeypatch, raise_in_handler):
    original_stdin = sys.stdin
    stdout_bytes = io.BytesIO()
    stderr_bytes = io.BytesIO()
    stdout = io.TextIOWrapper(stdout_bytes, encoding="cp1252", errors="strict")
    stderr = io.TextIOWrapper(stderr_bytes, encoding="cp437", errors="strict")
    outcome = RuntimeError("command failed after writing diagnostics")

    def handler(_args):
        assert sys.stdin is original_stdin
        assert sys.stdout.encoding == "cp1252"
        assert sys.stderr.encoding == "cp437"
        print("stdout Café € 東京 done")
        print("stderr Café Ω 東京 done", file=sys.stderr)
        if raise_in_handler:
            raise outcome
        return 7

    monkeypatch.setattr(entry, "cmd_ask", handler)
    with monkeypatch.context() as streams:
        streams.setattr(sys, "stdout", stdout)
        streams.setattr(sys, "stderr", stderr)
        if raise_in_handler:
            with pytest.raises(RuntimeError) as exc:
                entry.main(["ask", "hello", "--profile", "local"])
            assert exc.value is outcome
        else:
            assert entry.main(["ask", "hello", "--profile", "local"]) == 7
        assert sys.stdin is original_stdin
        assert sys.stdout is stdout and sys.stderr is stderr
        assert (stdout.encoding, stdout.errors) == ("cp1252", "strict")
        assert (stderr.encoding, stderr.errors) == ("cp437", "strict")
        stdout.flush()
        stderr.flush()

    out = stdout_bytes.getvalue().decode("cp1252", errors="strict")
    err = stderr_bytes.getvalue().decode("cp437", errors="strict")
    assert "stdout Café €" in out and out.rstrip().endswith("done")
    assert "stderr Café Ω" in err and err.rstrip().endswith("done")


def test_in_memory_text_captures_keep_unicode_and_identity(monkeypatch):
    original_stdin = sys.stdin
    stdout, stderr = io.StringIO(), io.StringIO()

    def handler(_args):
        assert sys.stdin is original_stdin
        assert sys.stdout is stdout and sys.stderr is stderr
        print("Café € 東京")
        print("diagnostic 東京", file=sys.stderr)
        return 0

    monkeypatch.setattr(entry, "cmd_ask", handler)
    with monkeypatch.context() as streams:
        streams.setattr(sys, "stdout", stdout)
        streams.setattr(sys, "stderr", stderr)
        assert entry.main(["ask", "hello", "--profile", "local"]) == 0
        assert sys.stdout is stdout and sys.stderr is stderr
        assert sys.stdin is original_stdin

    assert stdout.getvalue() == "Café € 東京\n"
    assert stderr.getvalue() == "diagnostic 東京\n"
