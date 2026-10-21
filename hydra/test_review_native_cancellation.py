"""Owned native-provider processes must stop before delayed side effects."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time

import pytest

from hydra.codex_client import CodexClient
from hydra.llm import LlmError


@pytest.mark.parametrize("termination", ["cancel", "timeout"])
def test_native_stop_terminates_owned_descendants_before_delayed_writes(tmp_path, termination):
    launcher = tmp_path / "native-stop-model"
    started = tmp_path / "descendant-started.json"
    descendant_write = tmp_path / "descendant-write.txt"
    parent_write = tmp_path / "parent-write.txt"
    descendant_code = (
        "import os, time\nfrom pathlib import Path\n"
        f"Path({str(started)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(4)\n"
        f"Path({str(descendant_write)!r}).write_text('unrevoked child')\n"
    )
    launcher.write_text(
        "#!" + sys.executable + "\n"
        "import subprocess, sys, time\nfrom pathlib import Path\n"
        f"subprocess.Popen([sys.executable, '-c', {descendant_code!r}])\n"
        "time.sleep(4.5)\n"
        f"Path({str(parent_write)!r}).write_text('unrevoked parent')\n"
        "args = sys.argv\n"
        "Path(args[args.index('-o') + 1]).write_text('late answer')\n",
        encoding="utf-8",
    )
    launcher.chmod(0o700)
    client = CodexClient(codex_bin=str(launcher), cd=str(tmp_path))
    # An event is a passive cancellation token, not a fake process or runner.
    # Assigning it is compatible with the pre-fix client, which ignores it.
    cancellation = threading.Event()
    client.cancel_event = cancellation
    result = {}

    def invoke():
        try:
            result["response"] = client.chat([{"role": "user", "content": "run"}],
                                             timeout=2.5 if termination == "timeout" else 8)
        except Exception as exc:
            result["error"] = exc
        result["finished_at"] = time.monotonic()

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    start_deadline = time.monotonic() + 5
    while not started.exists() and worker.is_alive() and time.monotonic() < start_deadline:
        time.sleep(0.02)
    assert started.exists(), result
    revoked_at = time.monotonic()
    if termination == "cancel":
        cancellation.set()
    worker.join(timeout=6)
    assert not worker.is_alive(), "Native invocation did not return within the bounded test window"
    # Wait past both scheduled side effects, even when the invocation returns early.
    observation_deadline = revoked_at + 4.7
    while time.monotonic() < observation_deadline:
        time.sleep(min(0.05, observation_deadline - time.monotonic()))
    assert not descendant_write.exists(), "Native descendant wrote after cancellation/deadline"
    assert not parent_write.exists(), "Native parent wrote after cancellation/deadline"
    assert isinstance(result.get("error"), LlmError), result
    message = str(result["error"]).lower()
    assert ("cancel" if termination == "cancel" else "timed out") in message
    if termination == "cancel":
        assert result["finished_at"] - revoked_at < 2, "Cancellation did not interrupt the native process promptly"
