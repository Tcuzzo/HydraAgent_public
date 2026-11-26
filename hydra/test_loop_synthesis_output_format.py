"""The real loop's budget nudge must not override an explicit output contract.

The recording transport isolates prompt construction; it does not claim a model
will obey the resulting prompt. The separately gated VM proof tests that part.
"""
from pathlib import Path

import pytest

from hydra.llm import ChatResponse, ToolCall
from hydra.loop import AgentLoop, Tool


class RecordingClient:
    def __init__(self):
        self.messages = []

    def chat(self, messages, *, model, **kwargs):
        self.messages.append([dict(message) for message in messages])
        calls = []
        content = "recording transport finished"
        if len(self.messages) <= 2:
            calls = [ToolCall(id="call_0", name="read", arguments_raw='{}', arguments={})]
            content = ""
        return ChatResponse(content=content, model=model, finish_reason="stop",
            prompt_tokens=0, completion_tokens=0, raw={}, tool_calls=calls)


def injected_nudge(tmp_path: Path, prompt: str) -> str:
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("real tool evidence")
    client = RecordingClient()
    tool = Tool(name="read", description="read evidence", parameters={"type": "object", "properties": {}},
                invoke=lambda: {"content": evidence.read_text()})
    result = AgentLoop(client, model="recording-transport").run(prompt, tools=[tool], max_iterations=4)
    assert result.tool_calls_made == 2 and result.iterations == 3
    assert result.halted_reason == "natural"
    nudges = [message["content"] for message in client.messages[1]
              if message["role"] == "system" and "SYNTHESIS REQUIRED" in message["content"]]
    assert len(nudges) == 1
    return nudges[0]


@pytest.mark.parametrize("prompt", [
    "Read the evidence twice; respond with exactly FORMAT_OK and no additional text.",
    'Read the evidence twice; return only valid JSON matching {"ok":true}, with no Markdown.',
])
def test_injected_nudge_preserves_explicit_output_format(tmp_path, prompt):
    nudge = injected_nudge(tmp_path, prompt)
    assert "requested output format exactly" in nudge
    assert "Do not add headings or commentary that violate" in nudge
    assert "Now deliver your final report:" not in nudge
    assert nudge.index("requested output format") < nudge.index("KEY FINDINGS")


def test_unconstrained_audit_keeps_structured_synthesis_as_conditional_default(tmp_path):
    nudge = injected_nudge(tmp_path, "Audit the evidence and explain your findings.")
    assert "If no output format is specified" in nudge
    assert all(heading in nudge for heading in ("KEY FINDINGS", "ROOT CAUSES", "ACTIONABLE RECOMMENDATIONS"))
    assert "SYNTHESIS REQUIRED" in nudge
