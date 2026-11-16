"""hydra.cli.cmd_code — Multi-language code runner (Rust, Go, C, YAML, MD, JSON).

Not just Python — run code in any language with syntax highlighting.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.syntax import Syntax

#: A hung compiler or child program must not wedge the CLI forever.
COMPILE_TIMEOUT_SECONDS = 120.0
RUN_TIMEOUT_SECONDS = 300.0
#: Conventional exit status for a command that ran out of time.
TIMEOUT_EXIT_CODE = 124


def _command_text(cmd: object) -> str:
    """The command as readable text for an error message."""
    if isinstance(cmd, (list, tuple)):
        return " ".join(str(part) for part in cmd)
    return str(cmd)


def register_code_command(sub: argparse._SubParsersAction) -> None:
    """Register code subcommand."""
    p_code = sub.add_parser(
        "code",
        help="Run code in any language (Rust, Go, C, Python, etc.) with syntax highlighting",
    )
    p_code.add_argument(
        "file",
        type=Path,
        help="Code file to run",
    )
    p_code.add_argument(
        "--lang",
        default=None,
        help="Language override (auto-detected from extension if omitted)",
    )
    p_code.add_argument(
        "--highlight",
        action="store_true",
        help="Force syntax highlighting even when piping",
    )
    p_code.set_defaults(func=cmd_code)


def cmd_code(args: argparse.Namespace) -> int:
    """Execute code file with syntax highlighting."""
    file_path = args.file.expanduser().resolve()
    
    if not file_path.exists():
        print(f"❌ File not found: {file_path}", file=sys.stderr)
        return 1
    
    # Auto-detect language from extension
    lang_map = {
        ".rs": "rust",
        ".go": "go",
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".hpp": "cpp",
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".md": "markdown",
        ".json": "json",
        ".sh": "bash",
        ".bash": "bash",
    }
    
    lang = args.lang or lang_map.get(file_path.suffix.lower(), "text")
    
    # Read and display with syntax highlighting
    console = Console(force_terminal=args.highlight or None)
    code = file_path.read_text(encoding="utf-8")
    
    console.print(f"\n📄 Running {lang} code from {file_path.name}:\n")
    console.print(Syntax(code, lang, theme="monokai", line_numbers=True))
    
    # Execute based on language. C + Rust are compile-then-run: the old code
    # passed "&&" as a literal gcc arg (so gcc failed on a bogus source file and
    # C was never compiled) and compiled Rust but never ran the binary. Both are
    # now: compile to a binary next to the source, then run it.
    binary = file_path.with_suffix("")
    compile_then_run = {"c": "gcc", "rust": "rustc"}
    direct_executors = {
        "python": [sys.executable, str(file_path)],
        "go": ["go", "run", str(file_path)],
        "javascript": ["node", str(file_path)],
        "typescript": ["npx", "ts-node", str(file_path)],
        "bash": ["bash", str(file_path)],
    }

    if lang not in compile_then_run and lang not in direct_executors:
        console.print(f"\n⚠️  No executor for {lang} — showing code only")
        return 0

    try:
        if lang in compile_then_run:
            compiler = compile_then_run[lang]
            compile_cmd = [compiler, str(file_path), "-o", str(binary)]
            compile_result = subprocess.run(
                compile_cmd, capture_output=True, text=True, timeout=COMPILE_TIMEOUT_SECONDS)
            if compile_result.returncode != 0:
                console.print(f"\n❌ {lang} compile failed:\n{compile_result.stderr}")
                return compile_result.returncode
            run_result = subprocess.run(
                [str(binary)], capture_output=False, text=True, timeout=RUN_TIMEOUT_SECONDS)
            return run_result.returncode
        cmd = direct_executors[lang]
        result = subprocess.run(cmd, capture_output=False, text=True, timeout=RUN_TIMEOUT_SECONDS)
        return result.returncode
    except subprocess.TimeoutExpired as exc:
        print(f"\n❌ {lang} timed out after {exc.timeout:g}s: {_command_text(exc.cmd)}",
              file=sys.stderr)
        return TIMEOUT_EXIT_CODE
    except FileNotFoundError:
        print(f"\n❌ Executor not found for {lang}. Install the runtime first.", file=sys.stderr)
        return 1

