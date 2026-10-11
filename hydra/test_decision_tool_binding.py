from hydra.cli.tool_binding import bind_tools


def test_any_chat_model_gets_local_decision_and_source_tools(tmp_path):
    tools = {tool.name: tool for tool in bind_tools(tmp_path, include_mcp=False)}
    assert {'needle_select', 'laya_decide', 'source_lookup', 'source_read'} <= tools.keys()
    assert 'executes no actions' in tools['laya_decide'].description


def test_needle_never_invokes_proposed_tool(monkeypatch):
    from hydra.decision_tools import needle_select
    from hydra.llm import ChatResponse, ToolCall
    from hydra.loop import Tool
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        def chat(self, *args, **kwargs):
            return ChatResponse('', 'cpu-selector', 'tool_calls', 0, 0, {}, [ToolCall('id', 'read', '{}', {})])
    monkeypatch.setattr('hydra.needle_client.NeedleClient', Client)
    result = needle_select('read source', ['read'], [Tool('read', 'Read', {}, lambda: calls.append(1))])
    assert result['selections'] == [{'tool': 'read', 'arguments': {}}]
    assert result['executed_actions'] is False
    assert calls == []
