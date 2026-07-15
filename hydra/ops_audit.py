"""Local ops pack execution: runs read-only pack commands and writes evidence.

This module is the §10.53 surface for the infrastructure ops harness. It loads
a §10.51-validated pack, runs each declared read-only command against a local
target, and writes a structured evidence bundle under
``<evidence_root>/ops/<run_id>/``. Missing commands become missing evidence
rather than fake success or fake failure. Secret-like values in command output
are redacted before write. Non-local targets are explicitly refused as not
implemented in this slice — the design forbids silent gates.

Since §10.66, every command also runs with two injected environment variables
``HYDRA_TARGET`` (the target value) and ``HYDRA_TARGET_TYPE`` (the parsed
target type) so packs can write commands that reference the target.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from hydra.ops_packs import (
    OpsPackError,
    TargetSpec,
    load_pack,
    parse_target,
    render_plan,
)
from hydra.proc import resolve_bash


SCHEMA = "hydra.ops_audit.v1"
SUPPORTED_TARGETS = {"local", "router", "file", "ssh"}
MAX_OUTPUT_BYTES = 64 * 1024
HEAD_LINES = 12
COMMAND_NOT_FOUND_RC = 127
SSH_CONNECT_TIMEOUT = 5  # seconds, BatchMode never prompts

_SECRET_LINE_PATTERN = re.compile(
    r"(?im)^(?P<prefix>[^\n]*?(?<![A-Za-z0-9])"
    r"(?:password|passwd|token|secret|bearer|"
    r"api[_-]?key|access[_-]?key|private[_-]?key)(?![A-Za-z0-9])\s*[:=]\s*)"
    r"(?P<value>\S+)"
)


@dataclass(frozen=True)
class OpsAuditError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def run_audit(
    pack_id: str,
    target_raw: str,
    *,
    evidence_root: str | Path,
    packs_dir: str | Path,
    run_id: str | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Run a read-only ops pack against a local target and write evidence.

    Returns a small result dict; the full evidence bundle is on disk under
    ``<evidence_root>/ops/<run_id>/``.
    """
    try:
        pack = load_pack(pack_id, packs_dir=packs_dir)
    except OpsPackError as e:
        raise OpsAuditError(str(e)) from e
    try:
        target = parse_target(target_raw)
    except OpsPackError as e:
        raise OpsAuditError(str(e)) from e

    if target.type not in SUPPORTED_TARGETS:
        raise OpsAuditError(
            f"not implemented: ops audit supports targets "
            f"{sorted(SUPPORTED_TARGETS)} in this slice; got {target.type!r}"
        )

    try:
        plan = render_plan(pack, target)
    except OpsPackError as e:
        raise OpsAuditError(str(e)) from e

    started_at = now()
    final_run_id = run_id or _new_run_id(started_at)
    evidence_root_path = Path(evidence_root).expanduser().resolve()
    run_dir = evidence_root_path / "ops" / final_run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    target_env = {
        "HYDRA_TARGET": target.value,
        "HYDRA_TARGET_TYPE": target.type,
    }
    commands_log = [
        _execute_command(
            cmd,
            cwd=run_dir,
            now=now,
            extra_env=target_env,
            target=target,
        )
        for cmd in plan["commands"]
    ]
    findings = _findings(commands_log)
    rot_signals = _scan_rot_signals(commands_log, pack)
    risk_register = _risk_register(plan, commands_log)
    finished_at = now()

    summary = {
        "schema": SCHEMA,
        "run_id": final_run_id,
        "pack_id": pack["id"],
        "pack_name": pack["name"],
        "target": target.to_dict(),
        "duration_seconds": round(finished_at - started_at, 3),
        "command_outcomes": _count_by(commands_log, "outcome"),
        "rot_signals_matched": sum(1 for s in rot_signals if s["matched"]),
        "secret_policy": "secret-like values are redacted before write",
    }

    request = {
        "schema": SCHEMA,
        "pack_id": pack_id,
        "target": target_raw,
        "run_id": final_run_id,
        "evidence_root": str(evidence_root_path),
    }
    repairs_md = _render_repairs(commands_log, rot_signals, pack)
    summary_md = _render_summary(summary, commands_log, rot_signals)

    _write_json(run_dir / "request.json", request)
    _write_json(run_dir / "target.json", target.to_dict())
    _write_json(run_dir / "pack.json", pack)
    _write_json(run_dir / "plan.json", plan)
    _write_jsonl(run_dir / "commands.jsonl", commands_log)
    _write_json(run_dir / "findings.json", findings)
    _write_json(run_dir / "rot_signals.json", rot_signals)
    _write_json(run_dir / "risk_register.json", risk_register)
    (run_dir / "recommended_repairs.md").write_text(repairs_md, encoding="utf-8")
    (run_dir / "summary.md").write_text(summary_md, encoding="utf-8")
    _write_json(run_dir / "summary.json", summary)

    return {
        "schema": SCHEMA,
        "run_id": final_run_id,
        "run_dir": str(run_dir),
        "summary": summary,
        "rot_signals_matched": summary["rot_signals_matched"],
    }


def _ssh_argv_and_script(
    target_value: str,
    command_str: str,
    *,
    extra_env: dict[str, str] | None,
) -> tuple[list[str], str]:
    """Assemble (argv, stdin-script) for SSH execution of ``command_str``.

    ``target_value`` is ``user@host`` or ``user@host:port``. The remote shell
    exports HYDRA_TARGET / HYDRA_TARGET_TYPE before running the command so
    pack authors can reference them on the remote host.
    """
    user_host = target_value
    port: str | None = None
    if "@" in user_host:
        user_part, _, rest = user_host.partition("@")
        if ":" in rest:
            host, _, port = rest.partition(":")
            user_host = f"{user_part}@{host}"
    argv = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
        "-o", "StrictHostKeyChecking=accept-new",
    ]
    if port:
        argv.extend(["-p", port])
    argv.extend([user_host, "bash", "-s"])
    env_exports: list[str] = []
    for key, value in (extra_env or {}).items():
        env_exports.append(f"export {key}={shlex.quote(value)}")
    script = "\n".join(env_exports + [command_str, ""])
    return argv, script


def _execute_command(
    command: dict[str, Any],
    *,
    cwd: Path,
    now: Callable[[], float],
    extra_env: dict[str, str] | None = None,
    target: TargetSpec | None = None,
) -> dict[str, Any]:
    command_str = command["command"]
    timeout = command["timeout_seconds"]
    env = None
    if extra_env:
        env = {**os.environ, **extra_env}
    started = now()

    use_ssh = target is not None and target.type == "ssh"
    try:
        if use_ssh:
            ssh_argv, ssh_script = _ssh_argv_and_script(
                target.value, command_str, extra_env=extra_env
            )
            try:
                proc = subprocess.run(
                    ssh_argv,
                    cwd=str(cwd),
                    input=ssh_script,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except FileNotFoundError:
                return {
                    "id": command["id"],
                    "command": command_str,
                    "evidence_key": command["evidence_key"],
                    "risk_tier": command["risk_tier"],
                    "outcome": "missing",
                    "returncode": COMMAND_NOT_FOUND_RC,
                    "stdout": "",
                    "stderr": "[ssh binary not available on this host]",
                    "stdout_bytes": 0,
                    "stderr_bytes": 0,
                    "duration_ms": int((now() - started) * 1000),
                }
        else:
            proc = subprocess.run(
                [resolve_bash(), "-c", command_str],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=env,
            )
    except subprocess.TimeoutExpired:
        return {
            "id": command["id"],
            "command": command_str,
            "evidence_key": command["evidence_key"],
            "risk_tier": command["risk_tier"],
            "outcome": "timeout",
            "returncode": None,
            "stdout": "",
            "stderr": f"[timeout after {timeout}s]",
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "duration_ms": int((now() - started) * 1000),
        }

    stdout_raw = proc.stdout or ""
    stderr_raw = proc.stderr or ""
    stdout = _redact(_bound_output(stdout_raw))
    stderr = _redact(_bound_output(stderr_raw))
    rc = proc.returncode
    if rc == COMMAND_NOT_FOUND_RC and "command not found" in stderr_raw.lower():
        outcome = "missing"
    elif rc == 0:
        outcome = "ok"
    else:
        outcome = "error"
    return {
        "id": command["id"],
        "command": command_str,
        "evidence_key": command["evidence_key"],
        "risk_tier": command["risk_tier"],
        "outcome": outcome,
        "returncode": rc,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_bytes": len(stdout_raw.encode("utf-8", errors="replace")),
        "stderr_bytes": len(stderr_raw.encode("utf-8", errors="replace")),
        "duration_ms": int((now() - started) * 1000),
    }


def _bound_output(text: str) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_OUTPUT_BYTES:
        return text
    return encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace") + "\n[truncated]"


def _redact(text: str) -> str:
    def _sub(m: re.Match[str]) -> str:
        return f"{m.group('prefix')}[REDACTED]"

    return _SECRET_LINE_PATTERN.sub(_sub, text)


def _findings(commands_log: list[dict[str, Any]]) -> dict[str, Any]:
    findings: dict[str, Any] = {}
    for row in commands_log:
        key = row["evidence_key"]
        lines = row["stdout"].splitlines()
        findings[key] = {
            "source_command_id": row["id"],
            "outcome": row["outcome"],
            "returncode": row["returncode"],
            "stdout_bytes": row["stdout_bytes"],
            "head_lines": lines[:HEAD_LINES],
            "has_output": row["stdout_bytes"] > 0,
        }
    return findings


def _scan_rot_signals(
    commands_log: list[dict[str, Any]],
    pack: dict[str, Any],
) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {row["evidence_key"]: row for row in commands_log}
    scanned: list[dict[str, Any]] = []
    for signal in pack.get("rot_signals", []):
        if not isinstance(signal, dict):
            continue
        sid = signal.get("id")
        source = signal.get("source")
        pattern = signal.get("match")
        if not (isinstance(sid, str) and isinstance(source, str) and isinstance(pattern, str)):
            continue
        row = by_key.get(source)
        if row is None:
            scanned.append({
                "id": sid,
                "source": source,
                "match": pattern,
                "matched": False,
                "reason": "no command produced evidence_key",
            })
            continue
        haystack = row["stdout"] + "\n" + row["stderr"]
        scanned.append({
            "id": sid,
            "source": source,
            "match": pattern,
            "matched": pattern in haystack,
            "command_outcome": row["outcome"],
        })
    return scanned


def _risk_register(
    plan: dict[str, Any],
    commands_log: list[dict[str, Any]],
) -> dict[str, Any]:
    tier_counts: dict[str, int] = {}
    rows: list[dict[str, str]] = []
    for cmd in commands_log:
        tier = cmd["risk_tier"]
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        rows.append({
            "command_id": cmd["id"],
            "risk_tier": tier,
            "outcome": cmd["outcome"],
        })
    return {
        "schema": SCHEMA,
        "target_type": plan["target"]["type"],
        "tier_counts": tier_counts,
        "commands": rows,
        "permission_policy": plan["permission_policy"],
    }


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        v = row.get(key)
        if v is None:
            continue
        out[v] = out.get(v, 0) + 1
    return out


def _render_repairs(
    commands_log: list[dict[str, Any]],
    rot_signals: list[dict[str, Any]],
    pack: dict[str, Any],
) -> str:
    lines = [f"# Recommended repairs for {pack['name']}", ""]
    missing = [c for c in commands_log if c["outcome"] == "missing"]
    errors = [c for c in commands_log if c["outcome"] == "error"]
    timeouts = [c for c in commands_log if c["outcome"] == "timeout"]
    matched = [s for s in rot_signals if s["matched"]]
    if not (missing or errors or timeouts or matched):
        lines.append("- none")
        return "\n".join(lines) + "\n"
    for cmd in missing:
        first_token = cmd["command"].split()[0] if cmd["command"].split() else cmd["id"]
        lines.append(
            f"- install or expose `{first_token}` so command `{cmd['id']}` can run."
        )
    for cmd in errors:
        lines.append(
            f"- investigate non-zero exit ({cmd['returncode']}) from command `{cmd['id']}`."
        )
    for cmd in timeouts:
        lines.append(
            f"- command `{cmd['id']}` timed out; raise timeout or narrow the command."
        )
    for signal in matched:
        lines.append(
            f"- rot signal `{signal['id']}` matched in `{signal['source']}` "
            f"(pattern `{signal['match']}`)."
        )
    return "\n".join(lines) + "\n"


def _render_summary(
    summary: dict[str, Any],
    commands_log: list[dict[str, Any]],
    rot_signals: list[dict[str, Any]],
) -> str:
    counts = summary["command_outcomes"]
    outcomes_str = (
        ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) if counts else "none"
    )
    lines = [
        f"# Ops audit summary: {summary['pack_id']}",
        "",
        f"- run_id: {summary['run_id']}",
        f"- target: {summary['target']['type']}",
        f"- duration_seconds: {summary['duration_seconds']}",
        f"- command outcomes: {outcomes_str}",
        f"- rot signals matched: {summary['rot_signals_matched']}",
        f"- secret policy: {summary['secret_policy']}",
        "",
        "## Commands",
    ]
    for row in commands_log:
        lines.append(
            f"- [{row['risk_tier']}] {row['id']} -> {row['outcome']} "
            f"(rc={row['returncode']}, stdout_bytes={row['stdout_bytes']}, "
            f"duration_ms={row['duration_ms']})"
        )
    if rot_signals:
        lines.append("")
        lines.append("## Rot signals")
        for s in rot_signals:
            mark = "MATCH" if s["matched"] else "ok"
            lines.append(f"- [{mark}] {s['id']} ({s['source']}: `{s['match']}`)")
    return "\n".join(lines) + "\n"


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")


def _new_run_id(started_at: float) -> str:
    ts = _dt.datetime.fromtimestamp(started_at, tz=_dt.timezone.utc).strftime(
        "local-%Y%m%dT%H%M%SZ"
    )
    return f"{ts}-{uuid.uuid4().hex[:6]}"
