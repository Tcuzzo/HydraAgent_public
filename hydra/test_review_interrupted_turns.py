"""Completed actions survive interrupted chat and team transports."""
import asyncio
import copy
import importlib.util
import json
import os
import sys
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateways.tui.hydra_app import HydraApp
from hydra.cli.tool_binding import bind_tools
from hydra.execution_learning import execution_records
from hydra.llm import ChatResponse, LlmError, ToolCall
from hydra.semantic_recall import build_semantic_memory_context
from hydra.work_executor import resolve_work_model

class ScriptedTransport:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.requests = []
    def chat(self, messages, *, model, **kwargs):
        self.requests.append(copy.deepcopy(messages))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return ChatResponse(content='' if isinstance(output, ToolCall) else output, model=model,
            finish_reason='tool_calls' if isinstance(output, ToolCall) else 'stop', prompt_tokens=0,
            completion_tokens=0, raw={}, tool_calls=[output] if isinstance(output, ToolCall) else [])

def offline(_):
    raise RuntimeError('offline embedding transport')

def make_app(tmp_path, monkeypatch, transport):
    root = tmp_path / 'project'
    root.mkdir()
    memory = tmp_path / 'memory'
    monkeypatch.setenv('HYDRA_OPERATOR_AUTH_DIR', str(tmp_path / 'auth'))
    monkeypatch.setattr('hydra.work_executor.SELF_AUDIT_LOG_PATH', str(tmp_path / 'audit.jsonl'))
    pair = resolve_work_model('steering') or ('scripted','scripted')
    tools = [t for t in bind_tools(root, approval_policy='allow', memory_root=memory, include_mcp=False)
             if t.name == 'fs_write']
    traces = []
    app = HydraApp(client=transport, model=pair[1], cfg=SimpleNamespace(name=pair[0]),
        system_prompt='Track observed state.', tools=tools, workspace_root=root, memory_root=memory,
        recall_builder=partial(build_semantic_memory_context, embedder=offline),
        max_iterations=4, timeout=5, approval_policy='allow', on_trace=lambda *a, **kw: traces.append(a[1]))
    return app, root, memory, traces

def write_call():
    args={'path':'receipt.txt','content':'durable-action-marker\n'}
    return ToolCall('write-once', 'fs_write', json.dumps(args), args)

async def turns(app, prompts):
    async with app.run_test(size=(100, 30)) as pilot:
        for prompt in prompts:
            app.query_one('#operator-input').value = prompt
            await pilot.press('enter')
            await asyncio.wait_for(app.workers.wait_for_complete(), 10)
            await pilot.pause()

def test_success_keeps_observation_for_next_turn(tmp_path, monkeypatch):
    transport=ScriptedTransport(write_call(), 'The file was written.', 'The prior action is known.')
    app, root, memory, traces=make_app(tmp_path, monkeypatch, transport)
    asyncio.run(turns(app, ['run creation of receipt.txt', 'What happened earlier?']))
    assert (root/'receipt.txt').read_text() == 'durable-action-marker\n'
    assert any(m.get('role')=='tool' and m.get('tool_call_id')=='write-once' for m in transport.requests[-1]), transport.requests[-1]
    assert execution_records(memory, root)
    assert traces[0].tool_calls_made == 1

def test_provider_error_keeps_completed_action_for_next_turn(tmp_path, monkeypatch):
    transport=ScriptedTransport(write_call(), LlmError('scripted provider interruption'), 'Can continue.')
    app, root, memory, traces=make_app(tmp_path, monkeypatch, transport)
    asyncio.run(turns(app, ['run creation of receipt.txt', 'Continue the prior task.']))
    assert (root/'receipt.txt').read_text() == 'durable-action-marker\n'
    evidence = {
        'actual_file': (root/'receipt.txt').read_text(),
        'interrupted_transport_saw_receipt': any(m.get('role')=='tool' for m in transport.requests[1]),
        'next_turn_saw_receipt': any(m.get('role')=='tool' for m in transport.requests[2]),
        'retained_tool_messages': [m for m in app._chat_messages if m.get('role')=='tool'],
        'learned_records': len(execution_records(memory, root)),
        'trace_tool_counts': [r.tool_calls_made for r in traces],
        'displayed_lines': app._chat_lines,
    }
    print('INTERRUPTION_EVIDENCE', json.dumps(evidence))
    assert evidence['next_turn_saw_receipt'], evidence
    assert evidence['learned_records'] == 1, evidence
    assert any(r.tool_calls_made == 1 and r.halted_reason == 'provider_error' for r in traces), evidence


def test_legacy_provider_error_keeps_observations_memory_and_trace(tmp_path, monkeypatch):
    from gateways.tui.elite import EliteTUI
    transport = ScriptedTransport(write_call(), LlmError('scripted provider interruption'), 'Can continue.')
    app, root, memory, _ = make_app(tmp_path, monkeypatch, transport)
    trace = tmp_path / 'legacy-trace.jsonl'
    tui = EliteTUI(transport, app.model, app.cfg, root, 'Track observed state.',
        tools=app.tools, initial_messages=[], memory_root=memory,
        recall_builder=partial(build_semantic_memory_context, embedder=offline),
        trace_out_path=trace, max_iterations=4, timeout=5)
    try:
        tui._stream_turn('run creation of receipt.txt', source='peer:test')
    except LlmError:
        pass
    assert (root / 'receipt.txt').read_bytes() == b'durable-action-marker\n'
    tui._stream_turn('Continue the prior task.', source='peer:test')
    assert any(m.get('role') == 'tool' for m in transport.requests[-1])
    assert len(execution_records(memory, root)) == 1
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]['tool_calls_made'] == 1 and rows[0]['halted_reason'] == 'provider_error'


def test_team_provider_error_preserves_observations_and_memory(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from hydra.teams import run_team
    root = tmp_path / 'project'
    root.mkdir()
    memory = tmp_path / 'memory'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if any(m.get('role') == 'tool' for m in request['messages']):
                self.send_response(503)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            call = write_call()
            message = {'role':'assistant','content':'','tool_calls':[{'id':call.id,'type':'function',
                'function':{'name':call.name,'arguments':call.arguments_raw}}]}
            payload = json.dumps({'model':request['model'],'choices':[{'message':message,
                'finish_reason':'tool_calls'}]}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('PARTIALTEAM_ENDPOINT', f'http://127.0.0.1:{server.server_port}/v1')
    monkeypatch.setenv('HYDRA_CHECKPOINTS_DIR', str(tmp_path / 'checkpoints'))
    plan = {'goal':'Write receipt.txt then summarize','provider':'partialteam','model':'scripted',
        'max_iterations':4,'timeout_seconds':20,
        'tasks':[{'id':'write','specialist':'seam_bot'},
                 {'id':'review','specialist':'code_reviewer','depends_on':['write']}]}
    try:
        report = run_team(plan, root=root, output_root=tmp_path/'runs', memory_root=memory,
                          approval_policy='allow')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    assert (root/'receipt.txt').read_bytes() == b'durable-action-marker\n'
    assert report['verdict'] == 'RED'
    row = next(row for row in report['outcomes'] if row['id'] == 'write')
    assert row.get('halted_reason') == 'provider_error', row
    assert row.get('tool_calls') == 1 and row['evidence'][0]['tool'] == 'fs_write', row
    assert not row['ok'] and len(execution_records(memory, root)) == 1
    assert next(row for row in report['results'] if row['id'] == 'review')['status'] == 'blocked'

def test_team_provider_error_retains_completed_action_receipt(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from hydra.teams import run_team
    root=tmp_path/'team-workspace'; root.mkdir()
    monkeypatch.setenv('HYDRA_CHECKPOINTS_DIR', str(tmp_path/'checkpoints'))
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a): pass
        def do_POST(self):
            request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            if any(m.get('role')=='tool' for m in request['messages']):
                status=401; body={'error':{'message':'scripted transport auth interruption'}}
            else:
                status=200; body={'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':'','tool_calls':[
                    {'id':'actual-write','type':'function','function':{'name':'fs_write','arguments':json.dumps({'path':'team-receipt.txt','content':'actual-team-action\n'})}}
                ]}}]}
            data=json.dumps(body).encode(); self.send_response(status)
            self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    monkeypatch.setenv('CONTINUITYWIRE_ENDPOINT',f'http://127.0.0.1:{server.server_port}/v1')
    try:
        report=run_team({'goal':'Write team-receipt.txt','provider':'continuitywire','model':'scripted','family':'scripted',
             'timeout_seconds':15,'max_iterations':4,'tasks':[{'id':'writer','specialist':'seam_bot'}]},
             root=root,output_root=tmp_path/'runs',memory_root=tmp_path/'memory',approval_policy='allow')
    finally:
        server.shutdown(); server.server_close(); thread.join(2)
    assert (root/'team-receipt.txt').read_text()=='actual-team-action\n'
    assert any(m.get('role')=='tool' for m in requests[-1]['messages'])
    outcome=report['outcomes'][0]
    print('TEAM_INTERRUPTION_EVIDENCE',json.dumps({'report':report,'learning_records':len(execution_records(tmp_path/'memory',root))}))
    assert any(r.get('tool')=='fs_write' for r in outcome.get('evidence',[])), outcome

def test_accepted_memory_command_survives_active_turn_completion(tmp_path, monkeypatch):
    from hydra.test_review_turn_continuity import make_live_chat, PausedModel
    command,args,root,apps,shells,_,_=make_live_chat(tmp_path,monkeypatch)
    client=PausedModel()
    monkeypatch.setattr(command,'make_client',lambda *a,**kw:(client,SimpleNamespace(name='scripted',model='scripted')))
    memory=Path(args.memory_root)/'workspace'; memory.mkdir(parents=True)
    (memory/'MEMORY.md').write_text('manually_loaded_continuity_marker is 731.\n')
    assert command.cmd_chat(args)==0
    app=apps[0]; observed={}
    async def run():
        async with app.run_test(size=(100,30)) as pilot:
            app.query_one('#operator-input').value='What is one plus one?'
            await pilot.press('enter')
            assert await asyncio.to_thread(client.started.wait,3)
            app.query_one('#operator-input').value='/memory hydra'
            await pilot.press('enter'); await pilot.pause()
            observed['accepted_context_present']='manually_loaded_continuity_marker' in json.dumps(app._chat_messages)
            client.release.set()
            await asyncio.wait_for(app.workers.wait_for_complete(),10); await pilot.pause()
            observed['after_completion_present']='manually_loaded_continuity_marker' in json.dumps(app._chat_messages)
            app.query_one('#operator-input').value='What context have I loaded?'
            await pilot.press('enter')
            await asyncio.wait_for(app.workers.wait_for_complete(),10); await pilot.pause()
    try: asyncio.run(run())
    finally: client.release.set()
    observed['next_turn_has_context']='manually_loaded_continuity_marker' in json.dumps(client.requests[-1])
    print('ACTIVE_CONTEXT_EVIDENCE',json.dumps(observed))
    assert observed['accepted_context_present'], observed
    assert observed['after_completion_present'] and observed['next_turn_has_context'], observed
