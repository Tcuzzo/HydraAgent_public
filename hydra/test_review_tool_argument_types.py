"""Malformed model argument types must fail before a destructive invocation."""
import json

import pytest

from hydra.cli.tool_binding import bind_tools
from hydra.llm import ChatResponse, ToolCall
from hydra.loop import AgentLoop


@pytest.mark.parametrize('invalid_overwrite', ['false', 1, ['false']])
def test_loop_refuses_non_boolean_overwrite_before_mutation(tmp_path, monkeypatch, invalid_overwrite):
    monkeypatch.setenv('HYDRA_CHECKPOINTS_DIR', str(tmp_path/'checkpoints'))
    target = tmp_path/'keep.txt'
    target.write_bytes(b'valuable original\n')
    arguments = {'path': 'keep.txt', 'content': 'replacement\n', 'overwrite': invalid_overwrite}
    class Transport:
        calls = 0
        def chat(self, messages, *, model, **kwargs):
            self.calls += 1
            calls = [ToolCall('write', 'fs_write', json.dumps(arguments), arguments)] if self.calls == 1 else []
            return ChatResponse('' if calls else 'Stopped.', model, 'tool_calls' if calls else 'stop',
                                0, 0, {}, calls)
    result = AgentLoop(Transport(), model='scripted').run('Do not overwrite existing data.',
        tools=bind_tools(tmp_path, approval_policy='allow', include_mcp=False), max_iterations=2)
    assert target.read_bytes() == b'valuable original\n'
    assert any(step.tool_error for step in result.steps if step.kind == 'tool_result')


def test_direct_bound_tool_also_validates_its_argument_schema(tmp_path):
    target = tmp_path/'keep.txt'
    target.write_bytes(b'valuable original\n')
    tool = next(t for t in bind_tools(tmp_path, approval_policy='allow', include_mcp=False) if t.name == 'fs_write')
    with pytest.raises(ValueError, match='overwrite'):
        tool.invoke(path='keep.txt', content='replacement\n', overwrite='false')
    assert target.read_bytes() == b'valuable original\n'
