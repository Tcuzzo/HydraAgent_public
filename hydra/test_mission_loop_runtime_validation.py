from __future__ import annotations

import stat
import subprocess
from pathlib import Path

from hydra.mission_loop import run_mission_loop


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


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
    import json as _j
    _bad = [
        {"step": s["kind"], "id": r["id"], "command": r["command"],
         "status": r["status"], "rc": r["returncode"],
         "stdout": r["stdout"][:400], "stderr": r["stderr"][:400]}
        for s in report["steps"]
        if isinstance(s.get("data"), dict) and isinstance(s["data"].get("results"), list)
        for r in s["data"]["results"]
        if r.get("status") != "ok"
    ]
    _why = "DIAG non-ok rows:\n" + _j.dumps(_bad, indent=2)

    validation = next(step for step in report["steps"] if step["kind"] == "runtime_validation")
    assert report["summary"]["verdict"] == "GREEN", _why
    assert validation["data"]["succeeded"] == 2
    assert {row["id"] for row in validation["data"]["results"]} == {
        "validate-integrity",
        "validate-tools",
    }
