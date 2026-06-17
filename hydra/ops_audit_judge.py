"""Bridge the §10.53 ops audit and the §10.60 rubric judge.

Loads a rubric (JSON list of rule dicts), scores the audit bundle's
``summary.md`` against it, and writes the judge report alongside the bundle
as ``judge_report.json``. The bundle and its other evidence files are never
modified — this is purely additive.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hydra.rubric_judge import RubricJudgeError, judge as rubric_judge


SCHEMA = "hydra.ops_audit_judge.v1"


class OpsAuditJudgeError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


def judge_audit_summary(
    bundle_dir: str | Path,
    rubric_path: str | Path,
    *,
    pass_threshold: float = 1.0,
    write_report: bool = True,
) -> dict[str, Any]:
    """Score ``<bundle_dir>/summary.md`` against ``rubric_path``."""
    bundle = Path(bundle_dir).expanduser().resolve()
    if not bundle.is_dir():
        raise OpsAuditJudgeError(f"bundle_dir is not a directory: {bundle}")
    summary_path = bundle / "summary.md"
    if not summary_path.is_file():
        raise OpsAuditJudgeError(f"bundle missing summary.md: {summary_path}")
    rubric_p = Path(rubric_path).expanduser().resolve()
    if not rubric_p.is_file():
        raise OpsAuditJudgeError(f"rubric file not found: {rubric_p}")
    try:
        rubric_raw = json.loads(rubric_p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise OpsAuditJudgeError(f"rubric is not valid JSON: {e}") from e
    if not isinstance(rubric_raw, list):
        raise OpsAuditJudgeError("rubric file must contain a JSON array of rules")

    draft = summary_path.read_text(encoding="utf-8", errors="replace")
    try:
        report = rubric_judge(draft, rubric_raw, pass_threshold=pass_threshold)
    except RubricJudgeError as e:
        raise OpsAuditJudgeError(str(e)) from e

    wrapped = {
        "schema": SCHEMA,
        "bundle_dir": str(bundle),
        "summary_path": str(summary_path),
        "rubric_path": str(rubric_p),
        "pass_threshold": pass_threshold,
        "judge_report": report,
    }
    if write_report:
        out_path = bundle / "judge_report.json"
        out_path.write_text(json.dumps(wrapped, indent=2, sort_keys=True), encoding="utf-8")
    return wrapped
