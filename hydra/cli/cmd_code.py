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
    
    console.print(f"\\n📄 Running {lang} code from {file_path.name}:\\n")
    console.print(Syntax(code, lang, theme="monokai", line_numbers=True))
    
    # Execute based on language
    executors = {
        "python": [sys.executable, str(file_path)],
        "rust": ["rustc", str(file_path), "-o", str(file_path.with_suffix(""))],
        "go": ["go", "run", str(file_path)],
        "c": ["gcc", str(file_path), "-o", str(file_path.with_suffix("")), "&&", str(file_path.with_suffix(""))],
        "javascript": ["node", str(file_path)],
        "typescript": ["npx", "ts-node", str(file_path)],
        "bash": ["bash", str(file_path)],
    }
    
    if lang not in executors:
        console.print(f"\\n⚠️  No executor for {lang} — showing code only")
        return 0
    
    cmd = executors[lang]
    
    try:
        result = subprocess.run(cmd, capture_output=False, text=True)
        return result.returncode
    except FileNotFoundError:
        console.print(f"\\n❌ Executor not found for {lang}. Install the runtime first.", file=sys.stderr)
        return 1

