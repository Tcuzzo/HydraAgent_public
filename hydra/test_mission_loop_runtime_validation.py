from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

from hydra.mission_loop import run_mission_loop


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _failing_commands(report: dict) -> str:
    """Every dispatched command that did not succeed, with its real output.

    A bare ``assert verdict == "GREEN"`` reports the symptom and hides the cause,
    which is worth a whole CI round to anyone debugging this from a red run.
    """
    rows = [
        {
            "step": step["kind"],
            "id": row["id"],
            "command": row["command"],
            "status": row["status"],
            "returncode": row["returncode"],
            "stdout": row["stdout"][:400],
            "stderr": row["stderr"][:400],
        }
        for step in report["steps"]
        if isinstance(step.get("data"), dict)
        and isinstance(step["data"].get("results"), list)
        for row in step["data"]["results"]
        if row.get("status") != "ok"
    ]
    return "mission verdict RED; commands that did not succeed:\n" + json.dumps(
        rows, indent=2
    )


def test_mission_loop_runs_runtime_validation_harnesses(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _write_executable(
        tmp_path / "harness" / "run_integrity.sh",
        "#!/usr/bin/env bash\nprintf '{\"locked_files_unchanged\":true}\\n'\n",
    )
    _write_executable(
        tmp_path / "harness" / "run_tools.sh",
        "#!/usr/bin/env bash\nprintf '{\"pass\":true,\"score\":1.0}\\n'\n",
    )

    report = run_mission_loop(root=tmp_path, operator_prompt="runtime validation proof")

    validation = next(step for step in report["steps"] if step["kind"] == "runtime_validation")
    assert report["summary"]["verdict"] == "GREEN", _failing_commands(report)
    assert validation["data"]["succeeded"] == 2
    assert {row["id"] for row in validation["data"]["results"]} == {
        "validate-integrity",
        "validate-tools",
    }
