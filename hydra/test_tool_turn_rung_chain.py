"""Tool turns follow the rung chain the BACKS router picked.

Live finding (FRICTION rows 3, 4, 5, 25): a tool turn went straight to the gateway
capability whatever rung the router had picked; when that capability answered HTTP 403
the turn died, the receipt named the first-picked rung, and `hydra ask` then fell back
to an unconfigured local provider ("ollama/None") and crashed with a LoopError traceback.

The contract: a tool turn tries the router's rungs in order and steps to the next rung on
HTTP 401/402/403; the receipt names the rung that actually answered; when every rung
fails the operator gets one line naming the rungs tried and their status codes.

Only the leaf HTTP transport (urllib.request.urlopen) is faked. Every rung, role and
model name below is a placeholder.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from hydra import model_routing, rung_resolver
from hydra.llm import ChatMessage
from hydra.rung_resolver import RungGatewayClient, RungResolutionError

BASE = "http://backs.test:8000"
TOOLS = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
CAPS = {"auto": "backs/gw-auto", "plan": "backs/gw-plan", "build": "backs/gw-build", "work": "backs/gw-work"}


class _Resp:
    def __init__(self, status, payload, headers=None):
        self.status = status
        self._raw = json.dumps(payload).encode()
        self.headers = dict(headers or {})

    def read(self, *a):
        return self._raw

    def getheaders(self):
        return list(self.headers.items())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeBacks:
    """A BACKS router + gateway at the socket edge.

    `chains[task]` is the router's chain; `status[rung]` is what the gateway answers
    when a chat completion is pinned to that rung (a model id naming the rung);
    `status["capability"]` is what a bare capability id gets."""

    def __init__(self, chains, status):
        self.chains = chains
        self.status = status
        self.completions: list[str] = []
        self.outcomes: list[dict] = []

    def _rung_for(self, model_id):
        for task_rows in self.chains.values():
            for row in task_rows:
                if model_id.lower().endswith(row["rung"].lower()):
                    return row["rung"]
        return "capability"

    def urlopen(self, req, timeout=None, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if not url.startswith(BASE):
            raise urllib.error.URLError("unreachable in test")
        path = url[len(BASE):]
        body = json.loads(req.data.decode()) if getattr(req, "data", None) else {}
        if path == "/v1/models":
            return _Resp(200, {"data": [{"id": cap, "display_name": tier.title()} for tier, cap in CAPS.items()]})
        if path == "/api/router/resolve":
            task = body.get("task_type")
            rows = [{"rung": r["rung"], "role": r.get("role", "worker"), "model": f"model-of-{r['rung']}",
                     "tier": "cloud", "usable": r.get("usable", True)} for r in self.chains.get(task, [])]
            return _Resp(200, {"contract": "backs.router.v1", "task_type": task, "chain": rows})
        if path == "/api/router/outcome":
            self.outcomes.append(body)
            return _Resp(200, {"ok": True})
        if path == "/api/router/complete":
            raise urllib.error.HTTPError(url, 503, "no", {}, io.BytesIO(b'{"ok":false,"message":"No rung could answer."}'))
        if path == "/v1/chat/completions":
            model = body.get("model", "")
            self.completions.append(model)
            rung = self._rung_for(model)
            code = self.status.get(rung, 404)
            if code != 200:
                err = json.dumps({"type": "error", "error": {"type": "permission_error",
                                                             "message": f"upstream said {code}"}}).encode()
                raise urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(err))
            ident = rung if rung != "capability" else "capability-head"
            return _Resp(200, {"model": model, "choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": f"answer from {ident}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
                headers={"X-BACKS-Identity": ident, "X-BACKS-Model": f"model-of-{ident}",
                         "X-BACKS-Route-Tier": "legacy", "X-BACKS-Decision": "picked"})
        raise urllib.error.HTTPError(url, 404, "nf", {}, io.BytesIO(b"{}"))


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("HYDRA_BACKS_URL", BASE)
    monkeypatch.setenv("HYDRA_MODEL_HEALTH_PATH", str(tmp_path / "health.json"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("_DOWN", "_CHAINS"):
        getattr(rung_resolver, name).clear()
    model_routing.set_resolver(None)
    yield
    for name in ("_DOWN", "_CHAINS"):
        getattr(rung_resolver, name).clear()
    model_routing.set_resolver(None)


def _install(monkeypatch, fake):
    monkeypatch.setattr(urllib.request, "urlopen", fake.urlopen)
    return fake


def _tool_turn(model="router/answer/work"):
    client = RungGatewayClient(endpoint=BASE)
    return client, client.chat([ChatMessage("user", "read a file")], model=model, tools=TOOLS)


def test_403_on_first_rung_steps_to_the_second_rung(monkeypatch):
    fake = _install(monkeypatch, FakeBacks({"answer": [{"rung": "rung-a"}, {"rung": "rung-b"}]},
                                           {"rung-a": 403, "rung-b": 200, "capability": 403}))
    client, resp = _tool_turn()
    assert resp.content == "answer from rung-b"
    assert [fake._rung_for(m) for m in fake.completions] == ["rung-a", "rung-b"]
    tried = [(o["rung"], o["success"]) for o in fake.outcomes]
    assert tried == [("rung-a", False), ("rung-b", True)], fake.outcomes
    assert fake.outcomes[0]["error_class"] in {"auth", "quota"}


@pytest.mark.parametrize("code", [401, 402, 403])
def test_each_auth_or_billing_status_steps_to_the_next_rung(monkeypatch, code):
    fake = _install(monkeypatch, FakeBacks({"answer": [{"rung": "rung-a"}, {"rung": "rung-b"}]},
                                           {"rung-a": code, "rung-b": 200}))
    _client, resp = _tool_turn()
    assert resp.content == "answer from rung-b"


def test_unusable_rows_are_skipped(monkeypatch):
    fake = _install(monkeypatch, FakeBacks({"answer": [{"rung": "rung-x", "usable": False}, {"rung": "rung-b"}]},
                                           {"rung-x": 200, "rung-b": 200}))
    _client, resp = _tool_turn()
    assert resp.content == "answer from rung-b"
    assert "rung-x" not in [fake._rung_for(m) for m in fake.completions]


def test_receipt_names_the_rung_that_actually_answered(monkeypatch, capsys):
    _install(monkeypatch, FakeBacks({"answer": [{"rung": "rung-a"}, {"rung": "rung-b"}]},
                                    {"rung-a": 403, "rung-b": 200, "capability": 403}))
    client, _resp = _tool_turn()
    err = capsys.readouterr().err
    assert client.last_receipt.get("x-backs-identity") == "rung-b"
    assert "answered by rung rung-b" in err
    assert "answered by rung rung-a" not in err
    assert "fell through rung-a:403" in err


def test_every_rung_failing_is_one_line_naming_rungs_and_status_codes(monkeypatch):
    _install(monkeypatch, FakeBacks({"answer": [{"rung": "rung-a"}, {"rung": "rung-b"}]},
                                    {"rung-a": 403, "rung-b": 402, "capability": 401}))
    with pytest.raises(RungResolutionError) as info:
        _tool_turn()
    text = str(info.value)
    assert "\n" not in text
    assert "rung-a:403" in text and "rung-b:402" in text and "work:401" in text


def test_a_non_stepping_4xx_stops_the_walk(monkeypatch):
    fake = _install(monkeypatch, FakeBacks({"answer": [{"rung": "rung-a"}, {"rung": "rung-b"}]},
                                           {"rung-a": 400, "rung-b": 200}))
    with pytest.raises(RungResolutionError, match="rung-a:400"):
        _tool_turn()
    assert [fake._rung_for(m) for m in fake.completions] == ["rung-a"]


# --- hydra ask: role fallbacks, never an unconfigured local provider -------------------

def _ask(tmp_path, *extra):
    from hydra.__main__ import main
    return main(["ask", "list the files here", "--root", str(tmp_path), "--memory-root", str(tmp_path / "mem"),
                 "--approval-policy", "deny", *extra])


def test_ask_with_every_rung_failing_is_one_clean_line_and_nonzero(monkeypatch, tmp_path, capsys):
    _install(monkeypatch, FakeBacks({"answer": [{"rung": "rung-a"}], "default": [{"rung": "rung-c"}]},
                                    {"rung-a": 403, "rung-c": 403, "capability": 403}))
    try:
        rc = _ask(tmp_path)
    except BaseException as exc:  # a crash is exactly the bug under test
        pytest.fail(f"hydra ask raised instead of a clean error: {type(exc).__name__}: {exc}")
    err = capsys.readouterr().err
    assert rc != 0
    assert "ollama/None" not in err and "/None" not in err
    assert "Falling back to local GPU" not in err
    assert "Traceback" not in err
    lines = [line for line in err.splitlines() if line.startswith("ask error:")]
    assert len(lines) == 1, err
    assert "rung-a:403" in lines[0] and "rung-c:403" in lines[0]


def test_ask_takes_the_role_fallback_rung_before_giving_up(monkeypatch, tmp_path, capsys):
    _install(monkeypatch, FakeBacks({"answer": [{"rung": "rung-a"}], "default": [{"rung": "rung-c"}]},
                                    {"rung-a": 403, "rung-c": 200, "capability": 403}))
    rc = _ask(tmp_path)
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert "answer from rung-c" in out.out
    assert "ollama/None" not in out.err
    assert "answered by rung rung-c" in out.err
