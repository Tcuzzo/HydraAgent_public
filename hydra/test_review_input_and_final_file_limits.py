"""Check unrecoverable source reads and live native final-output growth."""
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc

import pytest

from hydra.codex_client import CodexClient
from hydra.edit_checkpoints import get_default_store
from hydra.llm import LlmError
from skills.fs_edit import run as edit
from skills.fs_read import SkillError


def test_unrecoverable_edit_source_is_refused_before_full_allocation(tmp_path):
    source = tmp_path / "large.txt"
    limit = get_default_store(tmp_path).max_bytes
    with source.open("wb") as output:
        output.truncate(limit + 1)
    tracemalloc.start()
    try:
        with pytest.raises(SkillError, match="recoverable checkpoint limit"):
            edit(source, "\0", "", tmp_path, count="all")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert source.stat().st_size == limit + 1
    assert peak < 2 * 1024 * 1024, peak
    assert not get_default_store(tmp_path).peek_all()


def test_native_oversized_answer_stops_before_the_process_finishes(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    program = tmp_path / "native answer writer.py"
    program.write_text(
        "#!" + sys.executable + "\n"
        "from pathlib import Path\nimport sys,time\n"
        "sys.stdin.buffer.read()\n"
        "root=Path(sys.argv[sys.argv.index('-C')+1])\n"
        "out=Path(sys.argv[sys.argv.index('-o')+1])\n"
        "(root/'answer-written').write_text('ready')\n"
        "out.write_bytes(b'x'*(1024*1024+65536))\n"
        "time.sleep(8)\n"
        "(root/'late-work').write_text('unexpected completion')\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    with pytest.raises(LlmError, match="exceeds.*1 MiB|exceeds.*1048576"):
        CodexClient(codex_bin=str(program), cd=str(tmp_path)).chat(
            [{"role": "user", "content": "probe"}], timeout=12,
        )
    assert time.monotonic() - started < 6
    assert (tmp_path / "answer-written").exists()
    assert not (tmp_path / "late-work").exists()
    assert not list(tmp_path.glob("hydra-codex-*.txt"))
