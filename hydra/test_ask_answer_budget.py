"""Ask wiring: a reply cut off at its output budget is never printed as the final answer."""
from types import SimpleNamespace

from hydra.__main__ import _build_parser
from hydra.cli import cmd_ask as module
from hydra.llm import ChatResponse


def test_ask_does_not_print_a_cut_off_reply(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HYDRA_HOME", str(tmp_path / "home"))
    seen = []

    class Client:
        def chat(self, messages, *, model, max_tokens=1024, tools=None, **kwargs):
            seen.append(max_tokens)
            cut = max_tokens <= 1024
            return ChatResponse(content="half an answer" if cut else "the whole answer", model=model,
                                finish_reason="length" if cut else "stop", prompt_tokens=0,
                                completion_tokens=max_tokens if cut else 50, raw={}, tool_calls=[])

    args = _build_parser().parse_args(["ask", "hi", "--provider", "ollama", "--root", str(tmp_path),
                                       "--max-iterations", "2", "--memory-root", str(tmp_path / "memory")])
    monkeypatch.setattr(module, "_make_client_or_setup", lambda _: (Client(), SimpleNamespace(name="ollama", model="m")))
    monkeypatch.setattr(module, "_bind_tools", lambda *a, **kw: [])
    assert module.cmd_ask(args) == 0
    out = capsys.readouterr().out
    assert "the whole answer" in out and "half an answer" not in out
    assert max(seen) > 1024
