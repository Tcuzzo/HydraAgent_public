"""README, QUICKSTART, and MODELS.md name commands, flags, and env vars that exist."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from hydra.__main__ import _build_parser
from hydra.decision_ladder import jev_key_name

ROOT = Path(__file__).resolve().parents[1]
DOCS = (ROOT / "README.md", ROOT / "QUICKSTART.md", ROOT / "docs" / "MODELS.md")


def _text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in DOCS)


def _flags(parser: argparse.ArgumentParser) -> set[str]:
    found: set[str] = set()

    def walk(node: argparse.ArgumentParser) -> None:
        for action in node._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    walk(child)
            found.update(action.option_strings)

    walk(parser)
    return found


def test_documented_hydra_commands_exist():
    parser = _build_parser()
    sub = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    mentioned = set(re.findall(r"(?<![\w-])hydra\s+([a-z][a-z0-9-]*)", _text()))
    missing = sorted(mentioned - set(sub.choices))
    assert not missing, f"documented commands missing from the CLI: {missing}"


def test_documented_hydra_flags_exist():
    flags = _flags(_build_parser())
    mentioned: set[str] = set()
    command = re.compile(r"(?<![\w.-])hydra\s+[a-z][a-z0-9-]*")
    for line in _text().splitlines():
        if command.search(line) is None:
            continue
        mentioned.update(re.findall(r"--[a-z][a-z0-9-]*", line))
    missing = sorted(mentioned - flags)
    assert not missing, f"documented flags missing from the CLI: {missing}"


def test_documented_env_vars_are_read():
    text = _text()
    mentioned = set(re.findall(r"\b([A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+)\b", text))
    mentioned -= set(re.findall(r"\b([A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+)\.md\b", text))
    mentioned = {name for name in mentioned if not name.startswith("MYSERVER_")}
    blob = []
    for path in (ROOT / "hydra").rglob("*"):
        if path.suffix not in {".py", ".yaml", ".yml"} or path.name.startswith("test_"):
            continue
        blob.append(path.read_text(encoding="utf-8", errors="replace"))
    source = "\n".join(blob)
    dynamic = {jev_key_name()}
    suffixes = set(re.findall(r"_[A-Z][A-Z0-9_]+", source))

    def present(name: str) -> bool:
        if name in source or name in dynamic:
            return True
        return any(name.endswith(suffix) and len(name) > len(suffix) for suffix in suffixes)

    missing = sorted(name for name in mentioned if not present(name))
    assert not missing, f"documented env vars missing from code: {missing}"


def test_documented_repo_files_exist():
    missing = []
    for match in re.findall(r"`((?:hydra|docs|skills|\.hydraAgent)/[^`\s]+)`", _text()):
        rel = match.strip().rstrip(".,)")
        if "<" in rel or "*" in rel or rel.endswith("/"):
            continue
        if not re.search(r"\.(?:py|md|ya?ml|json|toml|txt)$", rel):
            continue
        if rel == ".hydraAgent/hydra.yaml":
            continue
        if not (ROOT / rel).exists():
            missing.append(rel)
    assert not missing, f"documented files missing: {missing}"
