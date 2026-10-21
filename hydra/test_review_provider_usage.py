"""Final synthesis is separate from tool iterations and must be accounted for."""
import json

from hydra.cli.tool_binding import bind_tools
from hydra.llm import ChatResponse, ToolCall
from hydra.loop import AgentLoop


def test_synthesis_reports_actual_provider_requests(tmp_path):
    (tmp_path/'data.txt').write_bytes(b'cedar\n')
    class Model:
        calls = 0
        def chat(self, messages, *, model, **kwargs):
            self.calls += 1
            args = {'path': 'data.txt'}
            calls = [ToolCall('read', 'fs_read', json.dumps(args), args)] if self.calls == 1 else []
            return ChatResponse(content='' if calls else 'Observed cedar.', model=model,
                finish_reason='tool_calls' if calls else 'stop', prompt_tokens=0,
                completion_tokens=0, raw={}, tool_calls=calls)
    model = Model()
    tools = [t for t in bind_tools(tmp_path, include_mcp=False) if t.name == 'fs_read']
    result = AgentLoop(model, model='scripted', system_prompt='Observe facts.').run(
        'Read data.txt', tools=tools, max_iterations=1)
    assert result.tool_calls_made == 1 and result.iterations == 1
    assert model.calls == 2 and result.final_response == 'Observed cedar.'
    assert getattr(result, 'provider_calls', None) == model.calls
