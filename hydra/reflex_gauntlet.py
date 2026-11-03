"""Post-edit gauntlet: sniper tests, lint, optional format, and a fast mutation check.

A missing tool is reported as ``skipped (tool missing)``. That is never a pass.
The hook is off unless ``HYDRA_REFLEX_GAUNTLET=1`` or a config enables it.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


SCHEMA = "hydra.reflex.gauntlet.v1"
FILE_EDIT_TOOLS = frozenset({"fs_edit", "fs_write", "apply_patch"})
_OPERATOR = re.compile(
    r">=|<=|==|!=|\band\b|\bor\b|\+|(?<![<>=!])-(?![=.])|(?<![<>=])>(?!=)|(?<![<>=])<(?!=)"
)
_FLIPS = {
    ">=": ">",
    "<=": "<",
    "==": "!=",
    "!=": "==",
    "and": "or",
    "or": "and",
    "+": "-",
    "-": "+",
    ">": ">=",
    "<": "<=",
}
WhichFn = Callable[[str], str | None]


@dataclass
class GauntletConfig:
    enabled: bool = False
    sniper: bool = True
    lint: bool = True
    format: bool | None = None
    mutation: bool = True
    timeout_seconds: float = 20.0


def after_file_edit(
    root: str | Path,
    paths: list[str],
    *,
    snapshots: dict[str, bytes | None] | None = None,
    config: GauntletConfig | None = None,
    which: WhichFn | None = None,
) -> dict[str, Any]:
    """Review changed files. On reject, restore snapshots when they were provided."""
    root_path = Path(root)
    active = config if config is not None else _config_from_env(root_path)
    if not active.enabled:
        verdict = _verdict(
            [
                _gate("sniper", "skipped (not configured)", ""),
                _gate("lint", "skipped (not configured)", ""),
                _gate("format", "skipped (not configured)", ""),
                _gate("mutation", "skipped (not configured)", ""),
            ]
        )
        return verdict
    changed = [str(path) for path in paths]
    tests = _covering_tests(root_path, changed) if active.sniper or active.mutation else []
    gates = [
        _sniper_gate(root_path, tests, active) if active.sniper else _gate("sniper", "skipped (not configured)", ""),
        _lint_gate(root_path, changed, active, which) if active.lint else _gate("lint", "skipped (not configured)", ""),
        _format_gate(root_path, changed, active, which),
        _mutation_gate(root_path, changed, tests, active) if active.mutation else _gate("mutation", "skipped (not configured)", ""),
    ]
    verdict = _verdict(gates)
    if verdict["reject"] and snapshots:
        _restore(snapshots)
    return verdict


def guard_invoke(
    tool_name: str,
    arguments: dict[str, Any] | None,
    invoke: Callable[[], Any],
    *,
    root: str | Path | None,
) -> tuple[Any, dict[str, Any] | None]:
    """Run a tool. After a file edit, reject and restore when the gauntlet fails."""
    root_path = Path(root) if root else Path.cwd()
    if tool_name not in FILE_EDIT_TOOLS or not gauntlet_enabled(root_path):
        return invoke(), None
    rels = paths_from_arguments(arguments or {})
    snapshots: dict[str, bytes | None] = {}
    for rel in rels:
        target = (root_path / rel).resolve()
        snapshots[str(target)] = target.read_bytes() if target.is_file() else None
    result = invoke()
    verdict = after_file_edit(
        root_path,
        rels,
        snapshots=snapshots,
        config=GauntletConfig(enabled=True),
    )
    return result, verdict


def gauntlet_enabled(root: str | Path | None = None) -> bool:
    if os.environ.get("HYDRA_REFLEX_GAUNTLET") == "1":
        return True
    if root is None:
        return False
    path = Path(root) / ".hydraAgent" / "reflex_gauntlet.json"
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return bool(isinstance(data, dict) and data.get("enabled"))


def paths_from_arguments(arguments: dict[str, Any]) -> list[str]:
    path = arguments.get("path") if isinstance(arguments, dict) else None
    if isinstance(path, str) and path.strip():
        return [path]
    return []


def _config_from_env(root: Path) -> GauntletConfig:
    return GauntletConfig(enabled=gauntlet_enabled(root))


def _verdict(gates: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "reject": any(gate["status"] == "fail" for gate in gates),
        "gates": gates,
    }


def _gate(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _restore(snapshots: dict[str, bytes | None]) -> None:
    for raw, content in snapshots.items():
        path = Path(raw)
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _covering_tests(root: Path, changed: list[str]) -> list[Path]:
    changed_resolved = {(root / rel).resolve() for rel in changed}
    needles: list[str] = []
    for rel in changed:
        path = Path(rel)
        if len(path.stem) >= 3:
            needles.append(path.stem)
        if path.name:
            needles.append(path.name)
    found: list[Path] = []
    if not needles or not root.is_dir():
        return found
    for candidate in sorted(root.rglob("*.py")):
        if candidate.resolve() in changed_resolved:
            continue
        if not (candidate.name.startswith("test_") or candidate.name.endswith("_test.py")):
            continue
        text = candidate.read_text(encoding="utf-8", errors="replace")
        if any(needle in text or needle in candidate.name for needle in needles):
            found.append(candidate)
    return found


def _sniper_gate(root: Path, tests: list[Path], config: GauntletConfig) -> dict[str, str]:
    if not tests:
        return _gate("sniper", "skipped (no covering tests)", "")
    if _pytest_missing():
        return _gate("sniper", "skipped (tool missing)", "pytest")
    code, detail = _run_pytest(root, tests, config.timeout_seconds)
    if code is None:
        return _gate("sniper", "skipped (time box)", detail)
    if code == 0:
        return _gate("sniper", "pass", detail)
    return _gate("sniper", "fail", detail)


def _lint_gate(
    root: Path,
    changed: list[str],
    config: GauntletConfig,
    which: WhichFn | None,
) -> dict[str, str]:
    return _external_tool_gate(
        "lint",
        root,
        changed,
        config,
        which,
        command=lambda binary, files: [binary, "check", *files],
        tool_name="ruff",
    )


def _format_gate(
    root: Path,
    changed: list[str],
    config: GauntletConfig,
    which: WhichFn | None,
) -> dict[str, str]:
    if not _format_configured(root, config):
        return _gate("format", "skipped (not configured)", "")
    return _external_tool_gate(
        "format",
        root,
        changed,
        config,
        which,
        command=lambda binary, files: [binary, "format", "--check", *files],
        tool_name="ruff",
    )


def _external_tool_gate(
    name: str,
    root: Path,
    changed: list[str],
    config: GauntletConfig,
    which: WhichFn | None,
    *,
    command: Callable[[str, list[str]], list[str]],
    tool_name: str,
) -> dict[str, str]:
    binary = _which(tool_name, which)
    if not binary:
        return _gate(name, "skipped (tool missing)", tool_name)
    if not changed:
        return _gate(name, "skipped (no changed files)", "")
    try:
        proc = subprocess.run(
            command(binary, changed),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _gate(name, "skipped (time box)", tool_name)
    except OSError as exc:
        return _gate(name, "skipped (tool missing)", str(exc))
    detail = (proc.stdout + proc.stderr).strip()[-500:]
    if proc.returncode == 0:
        return _gate(name, "pass", detail)
    return _gate(name, "fail", detail)


def _mutation_gate(
    root: Path,
    changed: list[str],
    tests: list[Path],
    config: GauntletConfig,
) -> dict[str, str]:
    sources = [root / rel for rel in changed if rel.endswith(".py")]
    if not sources:
        return _gate("mutation", "skipped (no changed functions)", "")
    if not tests:
        return _gate("mutation", "skipped (no covering tests)", "")
    if _pytest_missing():
        return _gate("mutation", "skipped (tool missing)", "pytest")
    source_path = sources[0]
    original = source_path.read_text(encoding="utf-8")
    flipped = flip_first_operator(original)
    if flipped is None:
        return _gate("mutation", "skipped (no operator)", "")
    mutant, detail = flipped
    try:
        source_path.write_text(mutant, encoding="utf-8")
        code, run_detail = _run_pytest(root, tests, config.timeout_seconds)
    finally:
        source_path.write_text(original, encoding="utf-8")
    if code is None:
        return _gate("mutation", "skipped (time box)", detail)
    if code == 0:
        return _gate("mutation", "fail", f"mutant survived: {detail}. {run_detail}".strip())
    return _gate("mutation", "pass", f"mutant killed: {detail}")


def flip_first_operator(source: str) -> tuple[str, str] | None:
    """Flip the first operator inside a function. Returns the mutant and a note."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    lines = source.splitlines(keepends=True)
    best: tuple[int, str] | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.end_lineno:
            continue
        start = sum(len(lines[index]) for index in range(node.lineno - 1))
        end = sum(len(lines[index]) for index in range(node.end_lineno))
        match = _OPERATOR.search(source[start:end])
        if match is None:
            continue
        position = start + match.start()
        if best is None or position < best[0]:
            best = (position, match.group(0))
    if best is None:
        return None
    position, operator = best
    replacement = _FLIPS[operator]
    mutant = source[:position] + replacement + source[position + len(operator):]
    return mutant, f"{operator} -> {replacement}"


def _format_configured(root: Path, config: GauntletConfig) -> bool:
    if config.format is False:
        return False
    if config.format is True:
        return True
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return False
    return "[tool.ruff.format]" in pyproject.read_text(encoding="utf-8", errors="replace")


def _pytest_missing() -> bool:
    return importlib_find("pytest") is None


def importlib_find(name: str) -> Any:
    from importlib.util import find_spec

    return find_spec(name)


def _run_pytest(root: Path, tests: list[Path], timeout: float) -> tuple[int | None, str]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=line",
        "-p",
        "no:cacheprovider",
        *[str(path) for path in tests],
    ]
    try:
        proc = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        detail = str(exc.stderr or exc.stdout or "")[-300:]
        return None, detail
    except OSError as exc:
        return None, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()[-500:]


def _which(name: str, which: WhichFn | None) -> str | None:
    finder = which if which is not None else shutil.which
    found = finder(name)
    return str(found) if found else None
