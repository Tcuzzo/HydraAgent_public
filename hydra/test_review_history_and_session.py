"""Prior answers and failed session persistence remain observable."""
import io
import json
from types import SimpleNamespace

from rich.console import Console

from hydra.cli.tool_binding import bind_tools
from hydra.llm import ChatResponse, ToolCall
from hydra.loop import AgentLoop
from hydra.turn_memory import finish_turn


class ReadTransport:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, *, model, **kwargs):
        self.calls += 1
        args = {'path': 'data.txt'}
        calls = [ToolCall('read-cedar', 'fs_read', json.dumps(args), args)] if self.calls == 1 else []
        return ChatResponse(content='I will inspect the file now.' if calls else
                            'The observed file contains actual input: cedar.',
                            model=model, finish_reason='tool_calls' if calls else 'stop',
                            prompt_tokens=0, completion_tokens=0, raw={}, tool_calls=calls)


def read_tools(root, memory):
    return [t for t in bind_tools(root, include_mcp=False, memory_root=memory)
            if t.name == 'fs_read']


def test_bounded_turn_does_not_replace_a_prior_answer(tmp_path):
    (tmp_path / 'data.txt').write_bytes(b'actual input: cedar\n')
    previous = [{'role': 'system', 'content': 'Track observed facts.'},
                {'role': 'user', 'content': 'What is the deployment label?'},
                {'role': 'assistant', 'content': 'The earlier confirmed label is Cedar.'}]
    result = AgentLoop(ReadTransport(), model='scripted', system_prompt='Track observed facts.').run(
        'Read data.txt', tools=read_tools(tmp_path, tmp_path/'memory'),
        max_iterations=1, initial_messages=previous)
    assert result.tool_calls_made == 1 and result.messages[-1]['role'] == 'tool'
    finished = finish_turn([result], previous, result.final_response)
    assert finished.messages[2] == previous[2]
    assert finished.messages[-1] == {'role': 'assistant', 'content': result.final_response}
    assert any(m.get('role') == 'tool' for m in finished.messages)


def test_legacy_repl_reports_real_session_save_failure(tmp_path, monkeypatch):
    from gateways.tui.elite import EliteTUI
    from hydra.work_executor import resolve_work_model
    monkeypatch.setenv('HYDRA_LEGACY_REPL', '1')
    monkeypatch.setenv('HYDRA_OPERATOR_AUTH_DIR', str(tmp_path/'auth'))
    monkeypatch.setattr('hydra.work_executor.SELF_AUDIT_LOG_PATH', str(tmp_path/'audit.jsonl'))
    blocked = tmp_path/'not-a-directory'
    blocked.write_bytes(b'file blocks session persistence')
    monkeypatch.setattr('hydra.session_memory.SESSION_MEMORY_DIR', blocked)
    root = tmp_path/'workspace'
    root.mkdir()
    (root/'data.txt').write_bytes(b'actual input: cedar\n')
    pair = resolve_work_model('steering') or ('scripted', 'scripted')
    client = ReadTransport()
    tui = EliteTUI(client, pair[1], SimpleNamespace(name=pair[0]), root,
        'Track observed facts.', session_id='persist-probe', initial_messages=[],
        tools=read_tools(root, tmp_path/'memory'), approval_policy='allow', max_iterations=2)
    output = io.StringIO()
    tui.console = Console(file=output, force_terminal=False, width=100)
    inputs = iter(['run a read of data.txt and show its contents', '/exit'])
    tui._read_operator_input = lambda: next(inputs)
    tui.run()
    console = output.getvalue()
    assert 'The observed file contains actual input: cedar.' in console
    assert tui._last_turn_result.tool_calls_made == 1
    assert not (blocked/'persist-probe.jsonl').exists()
    assert 'not saved' in console.lower() or 'could not save' in console.lower(), console
