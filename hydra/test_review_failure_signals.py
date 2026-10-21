import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from gateways.tui.elite import EliteTUI
from gateways.tui.hydra_app import HydraApp
from hydra.llm import ChatResponse, ToolCall
from hydra.loop import AgentLoop, Tool
from hydra.work_executor import resolve_work_model
from skills.bash import run as run_bash

class ScriptedModel:
    def __init__(self, *responses):
        self.responses=list(responses)
    def chat(self, messages, *, model, **kwargs):
        response=self.responses.pop(0)
        calls=[response] if isinstance(response, ToolCall) else []
        return ChatResponse(content='' if calls else response, model=model,
            finish_reason='tool_calls' if calls else 'stop', prompt_tokens=0,
            completion_tokens=0, raw={}, tool_calls=calls)

def failing_call():
    return ToolCall('failure', 'bash', '{"command":"exit 7"}', {'command':'exit 7'})

def test_failed_process_is_rendered_as_failure_by_both_ui_surfaces(tmp_path):
    client=ScriptedModel(failing_call(), 'The command failed.')
    tool=Tool('bash','Run a test command',{'type':'object','properties':{'command':{'type':'string'}}},
              lambda command: run_bash(command,root=tmp_path))
    result=AgentLoop(client,model='scripted',system_prompt='Test.').run('Run exit 7', tools=[tool],max_iterations=2)
    step=next(s for s in result.steps if s.kind=='tool_result')
    assert json.loads(step.content)['exit_code']==7
    lines=[]
    fake_app=SimpleNamespace(_stat_tools=0,_tools_used_this_turn=0,
        call_from_thread=lambda function,*args: function(*args),
        _post_log=lines.append,_update_stat_bar=lambda:None)
    HydraApp._on_loop_step(fake_app,step)
    assert '[red]✗[/red]' in lines[-1] and '[green]✓[/green]' not in lines[-1]
    legacy=[]
    fake_elite=SimpleNamespace(_update_status=lambda *_:None,_screen_transcript=[],
        _print_above_live=legacy.append,_turn_had_tools=False)
    EliteTUI._on_step(fake_elite,step)
    assert '[red]✗[/red]' in legacy[-1] and '[green]✓[/green]' not in legacy[-1]
    print('ACTUAL_RESULT',step.content)
    print('TEXTUAL',lines[-1])
    print('LEGACY',legacy[-1])

def test_default_textual_launch_writes_explicit_trace_path(tmp_path,monkeypatch):
    monkeypatch.setattr('hydra.work_executor.SELF_AUDIT_LOG_PATH',str(tmp_path/'audit.jsonl'))
    monkeypatch.setenv('HYDRA_OPERATOR_AUTH_DIR',str(tmp_path/'auth'))
    pair=resolve_work_model('steering') or ('scripted','scripted')
    trace=tmp_path/'requested-trace.jsonl'
    client=ScriptedModel('Hello operator.')
    tui=EliteTUI(client,pair[1],SimpleNamespace(name=pair[0]),tmp_path,'Test.',
        tools=[],initial_messages=[],trace_out_path=trace)
    apps=[]
    class HeadlessApp(HydraApp):
        def run(self,**kwargs): apps.append(self)
        def call_from_thread(self,callback,*args,**kwargs): return callback(*args,**kwargs)
    tui._launch_textual_app(HeadlessApp)
    app=apps[0]
    result=app._run_agent_loop_sync('hello','convo')
    app._handle_turn_done(result.final_response)
    assert ('hydra','Hello operator.') in app._chat_lines
    assert app._last_turn_result.halted_reason=='natural'
    assert trace.exists()
    row=json.loads(trace.read_text().splitlines()[-1])
    assert row["schema"]=="hydra.chat_trace_turn.v1" and row["final_response"]=="Hello operator."
    assert row["halted_reason"]=="natural" and row["tool_calls_made"]==0
    print('TEXTUAL_TURN_COMPLETED',app._chat_lines)
    print('REQUESTED_TRACE_EXISTS',trace.exists())
