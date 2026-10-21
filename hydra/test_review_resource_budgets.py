"""Bound real native output, temporary results and memory context work."""
import sys
import tempfile
import tracemalloc

import pytest

from hydra.codex_client import CodexClient
from hydra.context_engine_v2 import ContextAssembler, WorkingMemory
from hydra.llm import LlmError
from hydra.local_memory import build_local_memory_context


def launcher(tmp_path, body):
    path = tmp_path/'native resource provider.py'
    path.write_text('#!' + sys.executable + '\n' + body, encoding='utf-8')
    return path


def test_native_progress_capture_has_a_real_memory_ceiling(tmp_path):
    script = launcher(tmp_path,
        "import pathlib,sys\n"
        "sys.stdin.buffer.read()\n"
        "for stream in (sys.stdout.buffer,sys.stderr.buffer):\n"
        " for i in range(128): stream.write(b'x'*65536)\n"
        " stream.flush()\n"
        "pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text('done')\n")
    tracemalloc.start()
    try:
        response = CodexClient(codex_bin=str(script), cd=str(tmp_path)).chat(
            [{'role': 'user', 'content': 'probe'}], timeout=15, max_tokens=1)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert response.content == 'done'
    assert len(response.raw['stdout']) <= 1024 * 1024
    assert len(response.raw['stderr']) <= 1024 * 1024
    assert peak < 8 * 1024 * 1024, peak
    assert response.raw.get('stdout_truncated') and response.raw.get('stderr_truncated')


def test_native_timeout_removes_actual_partial_answer_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    script = launcher(tmp_path,
        "import pathlib,sys,time\n"
        "sys.stdin.buffer.read()\n"
        "pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_text('partial answer')\n"
        "(pathlib.Path(sys.argv[sys.argv.index('-C')+1])/'answer-created').write_text('ready')\n"
        "time.sleep(15)\n")
    with pytest.raises(LlmError, match='timed out'):
        CodexClient(codex_bin=str(script), cd=str(tmp_path)).chat(
            [{'role': 'user', 'content': 'probe'}], timeout=1)
    assert (tmp_path/'answer-created').exists(), 'probe never reached the answer write'
    assert not list(tmp_path.glob('hydra-codex-*.txt'))


def test_explicit_memory_import_bounds_reads_and_final_context(tmp_path):
    memory = tmp_path/'memory'
    source = memory/'workspace'/'MEMORY.md'
    source.parent.mkdir(parents=True)
    with source.open('wb') as output:
        for _ in range(128):
            output.write(b'Ordinary runtime memory.\n' * 2731)
    tracemalloc.start()
    try:
        result = build_local_memory_context(memory, max_chars=1000, workspace_root=tmp_path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.status == 'OK' and 'Ordinary runtime memory.' in result.context
    assert len(result.context) <= 1000
    assert peak < 4 * 1024 * 1024, peak


@pytest.mark.parametrize('with_working_memory', [False, True])
def test_small_context_budget_includes_formatted_overhead(tmp_path, with_working_memory):
    memory = tmp_path/'memory'
    source = memory/'workspace'/'MEMORY.md'
    source.parent.mkdir(parents=True)
    source.write_text('target runtime documentation\n' * 120, encoding='utf-8')
    working = WorkingMemory('task', 'Inspect the target runtime. ' * 100) if with_working_memory else None
    context, metadata = ContextAssembler(tmp_path, memory).assemble_context(
        'target runtime', max_tokens=32, working_memory=working)
    assert len(context) <= 32 * 4, (len(context), metadata)
    assert metadata['total_tokens'] <= 32
    assert metadata['compression_applied']
