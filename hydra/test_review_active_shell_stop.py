"""Operator stop revokes an already-running shell, not just later proposals."""
import asyncio
import json
import time
from types import SimpleNamespace

from gateways.tui.hydra_app import HydraApp
from hydra.cli.tool_binding import bind_tools
from hydra.llm import ChatResponse, ToolCall
from hydra.work_executor import resolve_work_model


def test_live_stop_terminates_active_shell_before_late_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv('HYDRA_OPERATOR_AUTH_DIR', str(tmp_path/'auth'))
    monkeypatch.setattr('hydra.work_executor.SELF_AUDIT_LOG_PATH', str(tmp_path/'audit.jsonl'))
    (tmp_path/'delayed_write.py').write_text(
        "from pathlib import Path\nimport time\nPath('started').write_text('ready')\n"
        "time.sleep(2)\nPath('after-stop.txt').write_text('late write')\n")
    class Transport:
        def chat(self, messages, *, model, **kwargs):
            args = {'command': 'python -B delayed_write.py', 'timeout': 8}
            return ChatResponse('', model, 'tool_calls', 0, 0, {},
                [ToolCall('delayed', 'bash', json.dumps(args), args)])
    pair = resolve_work_model('steering') or ('scripted', 'scripted')
    tools = [t for t in bind_tools(tmp_path, approval_policy='allow', include_mcp=False) if t.name == 'bash']
    app = HydraApp(client=Transport(), model=pair[1], cfg=SimpleNamespace(name=pair[0]),
        system_prompt='Run requested work.', tools=tools, workspace_root=tmp_path,
        approval_policy='allow', max_iterations=3, timeout=5)
    async def drive():
        async with app.run_test(size=(100, 30)) as pilot:
            app.query_one('#operator-input').value = 'Run the delayed write command.'
            await pilot.press('enter')
            deadline = time.monotonic() + 5
            while not (tmp_path/'started').exists():
                assert time.monotonic() < deadline, 'probe shell did not start'
                await asyncio.sleep(.02)
            assert not (tmp_path/'after-stop.txt').exists()
            app.query_one('#operator-input').value = 'stop'
            await pilot.press('enter')
            assert app._turn_cancel.is_set()
        await asyncio.sleep(2.3)
    asyncio.run(drive())
    assert not (tmp_path/'after-stop.txt').exists(), 'an active shell kept writing after operator stop'
