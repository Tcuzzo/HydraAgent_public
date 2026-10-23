"""RED proof for the fake: a local rung printed its tool call as text and the loop stopped with no tool run.
Uses the real AgentLoop and the real ask wrapper; only the provider wire is faked, replaying the exact text the
local rung printed live."""
from __future__ import annotations

from hydra.answer_budget import AnswerBudgetClient, Rung, RungLedger
from hydra.llm import ChatResponse
from hydra.loop import AgentLoop, Tool

URL = "https://docs.python.org/3/whatsnew/index.html"
PRINTED = '{\n  "name": "http_fetch",\n  "arguments": {\n    "url": "' + URL + '"\n  }\n}'


class Wire:
    def __init__(self, first_reply):
        self.first_reply = first_reply
        self.calls = 0

    def chat(self, messages, *, model=None, max_tokens=1024, **kw):
        self.calls += 1
        saw_tool_result = any(m.get("role") == "tool" for m in messages if isinstance(m, dict))
        text = f"The page lists the What's New documents. Source: {URL}" if saw_tool_result else self.first_reply
        return ChatResponse(content=text, model=model or "m", finish_reason="stop", prompt_tokens=1,
                            completion_tokens=1, raw={})


def _run(tmp_path, first_reply):
    fetched = []
    tool = Tool(name="http_fetch", description="fetch a url", invoke=lambda url: fetched.append(url) or
                {"url": url, "text": "What's New in Python"},
                parameters={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]})
    client = AnswerBudgetClient([Rung("local", "m", client=Wire(first_reply))], question="q",
                                ledger=RungLedger(tmp_path), log=lambda m: None)
    result = AgentLoop(client, model="m", system_prompt="answer").run("read the page and cite it", tools=[tool],
                                                                       max_iterations=4)
    return result, fetched


def test_tool_call_printed_as_text_is_run_for_real(tmp_path):
    result, fetched = _run(tmp_path, PRINTED)
    assert fetched == [URL], "the printed tool call must run through the real tool loop"
    assert result.tool_calls_made == 1
    assert URL in result.final_response and "{" not in result.final_response


def test_tagged_and_fenced_forms_also_run(tmp_path):
    for text in (f"<tool_call>{PRINTED}</tool_call>", f"```json\n{PRINTED}\n```"):
        _result, fetched = _run(tmp_path, text)
        assert fetched == [URL], text


def test_plain_answer_is_left_alone(tmp_path):
    result, fetched = _run(tmp_path, f"Python's What's New index is at {URL}.")
    assert fetched == [] and result.tool_calls_made == 0
