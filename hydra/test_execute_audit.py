"""Exercise the execute CLI through real AgentLoop turns and a temp-file tool."""
from types import SimpleNamespace
import json

import pytest

from hydra.cli import cmd_execution as command
from hydra.llm import ChatResponse, LlmError, ToolCall
from hydra.loop import Tool
from hydra.roles import RoleSet, RoleSpec


class Client:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def chat(self, messages, **kwargs):
        self.calls.append((list(messages), kwargs))
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply


def response(text, calls=()):
    return ChatResponse(text, 'configured-model', 'stop', 1, 1, {}, list(calls))


@pytest.fixture
def execute_case(tmp_path, monkeypatch):
    roles = RoleSet(*(RoleSpec(role, role, role + '-model', role + '-family')
                      for role in ('planner', 'doer', 'auditor')))
    review = json.dumps({'decision': 'pass', 'reason': 'The provided evidence supports the change.'})
    clients = {name: Client([response(text)]) for name, text in
               [('planner', 'Implement the request.'), ('doer', 'Completed.'), ('auditor', review)]}
    created = []
    def make(provider, **kwargs):
        created.append(provider)
        client = clients[provider]
        if isinstance(client, Exception):
            raise client
        return client, SimpleNamespace(model=provider + '-model')
    monkeypatch.setattr(command, 'make_client', make)
    monkeypatch.setattr(command, '_resolved_roles_or_exit', lambda args: roles)
    monkeypatch.setattr(command, 'provider_available', lambda *a, **kw: (True, 'available'))
    monkeypatch.setattr(command, 'bind_tools', lambda *a, **kw: [])
    args = SimpleNamespace(mission='Create the requested file.', config=None, env_dir=None,
        root=str(tmp_path), max_iterations=3, fallback_iterations=3, timeout=4,
        approval_policy='ask', no_audit=False, audit_mode='auto',
        no_unload_between_roles=False, unload_between_roles=False)
    return args, roles, clients, created


def test_configured_http_auditor_really_runs_after_actual_tool_work(execute_case, tmp_path, monkeypatch, capsys):
    args, roles, clients, created = execute_case
    marker = tmp_path / 'result.txt'
    tool = Tool('write_marker', 'Write requested marker', {'type': 'object', 'properties': {}},
                lambda: marker.write_text('completed'))
    clients['doer'].replies = [response('', [ToolCall('call-1', tool.name, '{}', {})]), response('Completed.')]
    monkeypatch.setattr(command, 'bind_tools', lambda *a, **kw: [tool])
    assert command.cmd_execute(args) == 0
    assert marker.read_text() == 'completed'
    assert len(clients['auditor'].calls) == 1
    messages, kwargs = clients['auditor'].calls[0]
    assert kwargs['model'] == 'auditor-model' and not kwargs['tools']
    assert clients['auditor'].runtime_family == roles.auditor.family
    packet = '\n'.join(m.get('content', '') for m in messages)
    assert 'Completed.' in packet and args.mission in packet
    assert '## Canon' in packet and '## Essence' in packet
    assert 'auditor' in created
    output = capsys.readouterr().out
    assert 'not implemented' not in output
    assert 'TEXT REVIEW: pass' in output


@pytest.mark.parametrize('review', ['unstructured praise', '{"decision":"inconclusive","reason":"missing evidence"}',
                                   '{"decision":"fail","reason":"incorrect result"}',
                                   '{"decision":[],"reason":"invalid"}', '[]'])
def test_unusable_or_negative_review_is_nonzero(execute_case, review):
    args, _, clients, _ = execute_case
    clients['auditor'].replies = [response(review)]
    assert command.cmd_execute(args) != 0


@pytest.mark.parametrize('role', ['planner', 'doer', 'auditor'])
def test_exhausted_step_is_nonzero_and_stops_following_steps(execute_case, role):
    args, _, clients, _ = execute_case
    args.max_iterations = 1
    clients[role].replies = [response('', [ToolCall('repeat', 'missing_tool', '{}', {})])]
    assert command.cmd_execute(args) != 0
    if role == 'planner':
        assert clients['doer'].calls == []
    if role != 'auditor':
        assert clients['auditor'].calls == []


def test_unavailable_auditor_is_literal_failure_before_work(execute_case):
    args, _, clients, _ = execute_case
    clients['auditor'] = LlmError('optional dependency missing', kind='configuration')
    assert command.cmd_execute(args) != 0
    assert clients['doer'].calls == []


def test_no_audit_does_not_probe_or_construct_an_auditor(execute_case, monkeypatch, capsys):
    args, _, clients, created = execute_case
    args.no_audit = True
    monkeypatch.setattr(command, 'provider_available', lambda *a, **kw: pytest.fail('unrequested probe'))
    assert command.cmd_execute(args) == 0
    assert 'auditor' not in created and clients['auditor'].calls == []
    assert 'skipped' in capsys.readouterr().out


def test_single_model_review_is_allowed_and_labeled(execute_case, monkeypatch, capsys):
    args, roles, clients, _ = execute_case
    shared = RoleSpec('auditor', 'doer', 'doer-model', 'doer-family')
    monkeypatch.setattr(command, '_resolved_roles_or_exit', lambda args: RoleSet(roles.planner, roles.doer, shared))
    clients['doer'].replies.append(response('{"decision":"pass","reason":"consistent"}'))
    assert command.cmd_execute(args) == 0
    assert 'self-review; not independent' in capsys.readouterr().out


def test_native_reviewer_receives_read_only_sandbox(execute_case, monkeypatch):
    args, roles, clients, _ = execute_case
    clients['codex'] = clients['auditor']
    role = RoleSpec('auditor', 'codex', 'configured-review-model', 'configured-family')
    monkeypatch.setattr(command, '_resolved_roles_or_exit', lambda args: RoleSet(roles.planner, roles.doer, role))
    assert command.cmd_execute(args) == 0
    assert clients['codex'].sandbox == 'read-only'
    assert clients['codex'].cd == args.root
    assert clients['codex'].calls[0][1]['model'] == 'configured-review-model'


@pytest.mark.parametrize('field,value', [('max_iterations', 0), ('timeout', float('nan'))])
def test_invalid_budget_fails_before_any_model_factory(execute_case, field, value):
    args, _, _, created = execute_case
    setattr(args, field, value)
    assert command.cmd_execute(args) != 0
    assert created == []


@pytest.mark.parametrize('opt_in', [False, True])
def test_cleanup_uses_configured_endpoint_only_when_requested_and_failure_warns(execute_case, monkeypatch, capsys, opt_in):
    from hydra import model_residency
    args, roles, clients, _ = execute_case
    args.unload_between_roles = opt_in
    args.env_dir = 'explicit-environment'
    local_plan = RoleSpec('planner', 'ollama', 'plan-model', 'plan-family')
    local_doer = RoleSpec('doer', 'ollama', 'work-model', 'work-family')
    monkeypatch.setattr(command, '_resolved_roles_or_exit', lambda args: RoleSet(local_plan, local_doer, roles.auditor))
    clients['ollama'] = Client([response('Plan.'), response('Done.')])
    calls = []
    def unavailable(action, model, *, env_dir):
        calls.append((action, model, env_dir))
        raise OSError('unload endpoint unavailable')
    monkeypatch.setattr(model_residency, 'residency', unavailable)
    assert command.cmd_execute(args) == 0
    assert calls == ([('unload', 'plan-model', args.env_dir)] if opt_in else [])
    assert ('optional model unload failed' in capsys.readouterr().err) is opt_in


def test_explicit_codex_mode_does_not_use_another_provider(execute_case):
    args, _, clients, created = execute_case
    args.audit_mode = 'codex'
    assert command.cmd_execute(args) != 0
    assert created == []
