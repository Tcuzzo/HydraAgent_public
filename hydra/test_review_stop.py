"""The real Textual stop path must revoke pending tool execution."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from gateways.tui.hydra_app import HydraApp
from hydra.cli.tool_binding import bind_tools
from hydra.llm import ChatResponse, ToolCall
from hydra.work_executor import resolve_work_model


class DelayedModelTransport:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def chat(self, messages, *, model, **kwargs):
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            assert self.release.wait(5), 'Test failed to release scripted model transport'
            args = {'path': 'after-stop.txt', 'content': 'tool executed after stop\n'}
            call = ToolCall(id='late-write', name='fs_write', arguments_raw=json.dumps(args), arguments=args)
            return ChatResponse(content='', model=model, finish_reason='tool_calls',
                                prompt_tokens=0, completion_tokens=0, raw={}, tool_calls=[call])
        return ChatResponse(content='Wrote the file.', model=model, finish_reason='stop',
                            prompt_tokens=0, completion_tokens=0, raw={}, tool_calls=[])


def drive_ui(root):
    pair = resolve_work_model('steering') or ('test', 'test')
    client = DelayedModelTransport()
    tools = [t for t in bind_tools(root, approval_policy='allow', include_mcp=False,
                                  memory_root=root / 'memory') if t.name == 'fs_write']
    app = HydraApp(client=client, model=pair[1], cfg=SimpleNamespace(name=pair[0]),
                   system_prompt='Execute the requested file write.', tools=tools,
                   workspace_root=root, max_iterations=2, timeout=5)

    def observer():
        assert client.entered.wait(3)
        deadline = time.monotonic() + 4
        while app.return_value != 0 and time.monotonic() < deadline:
            time.sleep(.005)
        stopped = app.return_value == 0
        assert stopped, 'Real UI never accepted stop'
        (root / 'stop-accepted.json').write_text(json.dumps({'return_value': app.return_value,
            'target_existed_at_stop': (root / 'after-stop.txt').exists()}))
        client.release.set()
        deadline = time.monotonic() + 1
        while not (root / 'after-stop.txt').exists() and time.monotonic() < deadline:
            time.sleep(.005)
        target = root / 'after-stop.txt'
        (root / 'stop-evidence.json').write_text(json.dumps({
            'stop_accepted': stopped, 'file_exists_after_stop': target.exists(),
            'content': target.read_text() if target.exists() else None,
            'model_calls': client.calls}))
        # Terminate this test-owned UI harness after observing the side effect.
        # This avoids making Textual/executor teardown part of the assertion.
        os._exit(0)

    threading.Thread(target=observer, daemon=True).start()

    async def drive():
        async with app.run_test(size=(100, 30)) as pilot:
            app.query_one('#operator-input').value = 'run write after-stop.txt'
            await pilot.press('enter')
            assert await asyncio.to_thread(client.entered.wait, 3)
            assert app._turn_in_flight
            app.query_one('#operator-input').value = 'stop'
            await pilot.press('enter')
            await asyncio.sleep(2)

    asyncio.run(drive())
    time.sleep(2)


def test_stop_prevents_pending_model_response_from_writing(tmp_path):
    process = subprocess.run([sys.executable, __file__, '--drive', str(tmp_path)],
                             capture_output=True, text=True, timeout=10, env=dict(os.environ))
    assert process.returncode == 0, process.stdout + process.stderr
    before = json.loads((tmp_path / 'stop-accepted.json').read_text())
    evidence = json.loads((tmp_path / 'stop-evidence.json').read_text())
    assert before == {'return_value': 0, 'target_existed_at_stop': False}
    assert evidence['stop_accepted']
    assert not evidence['file_exists_after_stop'], 'Stop closed the UI but the pending model proposal still wrote the file: ' + json.dumps(evidence)


if __name__ == '__main__':
    drive_ui(Path(sys.argv[2]))
