"""Progress logs cannot substitute for a native provider's final answer file."""
import sys

import pytest

from hydra.codex_client import CodexClient
from hydra.llm import LlmError


@pytest.mark.parametrize('log', ['progress: starting native turn', 'stream error: disconnected before final answer'])
def test_native_log_only_exit_is_not_a_final_answer(tmp_path, log):
    script = tmp_path/'native-log-only.py'
    script.write_text('#!' + sys.executable + '\nimport sys\nsys.stdin.buffer.read()\n'
                      + f'print({log!r})\n', encoding='utf-8')
    with pytest.raises(LlmError):
        CodexClient(codex_bin=str(script), cd=str(tmp_path)).chat(
            [{'role': 'user', 'content': 'Inspect the workspace.'}], timeout=5)
