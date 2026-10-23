from __future__ import annotations

from types import SimpleNamespace

from hydra.text_tool_calls import bound_tool_names, parse_text_tool_calls, promote_text_tool_calls

TOOLS = [{"type": "function", "function": {"name": "http_fetch", "parameters": {}}}]
OK = {"http_fetch"}


def test_bare_fenced_tagged_list_and_string_args():
    bare = '{"name": "http_fetch", "arguments": {"url": "u"}}'
    for text in (bare, f"```json\n{bare}\n```", f"I'll fetch it.\n<tool_call>\n{bare}\n</tool_call>", f"[{bare}]",
                 '{"name": "http_fetch", "parameters": {"url": "u"}}',
                 '{"function": {"name": "http_fetch", "arguments": "{\\"url\\": \\"u\\"}"}}'):
        calls = parse_text_tool_calls(text, OK)
        assert [(c.name, c.arguments) for c in calls] == [("http_fetch", {"url": "u"})], text


def test_unbound_or_malformed_or_prose_is_not_a_call():
    assert parse_text_tool_calls('{"name": "rm_rf", "arguments": {}}', OK) == []
    assert parse_text_tool_calls('{"name": "http_fetch", "arguments": "not json"}', OK) == []
    assert parse_text_tool_calls('Example: {"name": "http_fetch", "arguments": {"url": "u"}} is the shape.', OK) == []
    assert parse_text_tool_calls('{"name": "http_fetch", "arguments": {"url": "u"}}', set()) == []


def test_promote_keeps_real_tool_calls_and_names():
    assert bound_tool_names(TOOLS) == OK
    real = SimpleNamespace(content="x", tool_calls=["real"], finish_reason="tool_calls")
    assert promote_text_tool_calls(real, TOOLS) is real
    printed = SimpleNamespace(content='{"name": "http_fetch", "arguments": {"url": "u"}}', tool_calls=[],
                              finish_reason="stop")
    out = promote_text_tool_calls(printed, TOOLS)
    assert out.tool_calls[0].name == "http_fetch" and out.content == "" and out.finish_reason == "tool_calls"
    assert promote_text_tool_calls(printed, None).tool_calls  # already promoted object stays promoted


def test_module_names_no_model_or_provider():
    import hydra.text_tool_calls as mod
    src = open(mod.__file__, encoding="utf-8").read().lower()
    for word in ("qwen", "gpt", "ollama", "nemotron", "gemma", "kimi", "claude", "openai", "anthropic", "api_key"):
        assert word not in src, word
