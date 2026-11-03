#!/usr/bin/env python3
"""Run me: `python3 example.py`

Shows the karpathy loop doing a REAL keep and a REAL revert on a throwaway git
repo — no models, no network, no GPU. Your proof the loop works before you trust
it on a real agent. stdlib only (needs `git` on PATH).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from program import parse_program, run_program
from evolve import cleanup_sandbox

PROGRAM = """# Lower the tuning number, keep success up

## Goal
Improve success rate first, then drive the cost number down.

## Metric
- success_rate | max | 0.95
- cost | min

## Fair game
- config.txt

## Budget
rounds: 1
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)


def _make_repo(root: Path) -> Path:
    repo = root / "agent"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "you@example.com")
    _git(repo, "config", "user.name", "You")
    (repo / "config.txt").write_text("cost=10\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "baseline")
    return repo


def _proposer(value: str):
    def propose(target_dir):
        (Path(target_dir) / "config.txt").write_text(f"cost={value}\n")
    return propose


def _blind_tester(candidate: dict):
    # Blind: it scores ONLY by reading the tree, never the proposer's reasoning.
    def test(target_dir):
        text = (Path(target_dir) / "config.txt").read_text()
        return {"success_rate": 0.95, "cost": 10.0} if text == "cost=10\n" else dict(candidate)
    return test


def main() -> None:
    prog = parse_program(PROGRAM)
    sandbox_root = Path(os.getenv("KARPATHY_SANDBOX_ROOT") or tempfile.gettempdir()).resolve()
    sandbox_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=sandbox_root) as d:
        repo = _make_repo(Path(d))
        # A: a cheaper candidate that still clears the success bar -> KEPT
        res = run_program(
            prog, target_dir=repo,
            propose=_proposer("3"),
            test=_blind_tester({"success_rate": 0.96, "cost": 3.0}),
            sandbox_root=sandbox_root,
        )
        clone = Path(res["sandbox_dir"])
        print("KEEP test  ->", res["kept"], "kept;",
              "clone config:", (clone / "config.txt").read_text().strip(),
              "source config:", (repo / "config.txt").read_text().strip())
        cleanup_sandbox(clone)

    with tempfile.TemporaryDirectory(dir=sandbox_root) as d:
        repo = _make_repo(Path(d))
        # B: cheaper but BELOW the 0.95 success bar -> THROWN AWAY (byte-identical)
        res = run_program(
            prog, target_dir=repo,
            propose=_proposer("1"),
            test=_blind_tester({"success_rate": 0.90, "cost": 1.0}),
            sandbox_root=sandbox_root,
        )
        clone = Path(res["sandbox_dir"])
        print("REVERT test ->", res["kept"], "kept;",
              "clone config:", (clone / "config.txt").read_text().strip(),
              "(rejected: below the bar)")
        cleanup_sandbox(clone)


if __name__ == "__main__":
    main()
