"""Operator autonomy classifier: in ask mode a command runs free only if it is known safe.

Doctrine: the gate is an ALLOWLIST of known-safe shapes, not a list of bad ones. A
bash command auto-allows only when EVERY segment (split on ; && || | & and newline)
is a safe shape from ``_SAFE_COMMANDS``: a command on the table, with only flags that
table lists for it, and only literal words as arguments. Anything else needs approval,
and the reason names why (unknown command, flag not on the safe list, environment
assignment, expansion, redirection to a file, subshell, here-document...).

A literal word is a quoted string with no ``$``, backtick or backslash in a double
quote, or an unquoted word made only of ``[A-Za-z0-9_./+=:@%,^~-]`` (``*`` and ``?``
only for commands marked ``globs``). Brackets, braces, parentheses, ``!`` and ``#``
never appear in a literal word, so no glob, brace, extglob or expansion form can name
a command. The table lists exact command names; ``python3.12`` or ``/bin/cp`` are not
on it. Environment assignments in front of a command are never auto-allowed.

File mutation tools (``fs_write``, ``fs_edit``) need approval; every other tool is
auto-allowed. ``deny`` mode blocks whatever ask mode would ask about.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


SCHEMA = "hydra.autonomy_decision.v1"

# Exact-match auto-allow for bash commands that are:
# - Read-only (no mutation)
# - Deterministic (same output every time)
# - Proof/status oriented (not open-ended exploration)
SAFE_BASH_COMMANDS = frozenset(
    {
        # Git inspection
        "pwd",
        "git status --short",
        "git status --short --branch",
        "git diff --check",
        "git log --oneline -5",
        "git branch -a",

        # Hydra proof/status
        "python3 -m hydra status",
        "python3 -m hydra task-eval agent-parity --format text",
        "python3 -m hydra ledger list",
        "python3 -m hydra roles",
        "python3 -m hydra providers",
        "python3 -m hydra models",
        "python3 -m hydra skills list",
        "python3 -m hydra skills doctrine",

        # Filesystem inspection (bounded)
        "ls -la",
        "find . -maxdepth 2 -type f",
    }
)


def classify_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    policy_mode: str,
    *,
    mission_level: str | None = None,
) -> dict[str, str]:
    """Classify a tool call. When ``mission_level`` is a D4 mission class
    (dangerous / destructive_or_off_lan / huge_batch), the decision
    carries a namespaced ``risk_tier`` of ``mission:<class>``; otherwise it is
    the tool-level default ``T2``. Pass-through to the single approval path —
    no second gate (S0 D4)."""
    decision = _classify_core(tool_name, arguments, policy_mode)
    decision["risk_tier"] = f"mission:{mission_level}" if mission_level else "T2"
    return decision


def _classify_core(tool_name: str, arguments: dict[str, Any], policy_mode: str) -> dict[str, str]:
    if tool_name == "bash":
        reason = unsafe_reason(str(arguments.get("command", "")))
        if reason is None:
            return _decision("auto_allow", "known safe command shape")
        if policy_mode == "deny":
            return _decision("blocked", f"deny policy blocks this command: {reason}")
        return _decision("needs_approval", f"needs approval: {reason}")

    if tool_name in {"fs_write", "fs_edit"}:
        if policy_mode == "deny":
            return _decision("blocked", "deny policy blocks file mutation")
        return _decision("needs_approval", "file mutation requires approval")

    # Durable memory, read-only tools, and local stats are not gated. The skill
    # implementations still enforce scope and bounds.
    return _decision("auto_allow", "non-destructive tool")


def render_autonomy_text(decision: dict[str, str]) -> str:
    return f"{decision['decision']}: {decision['reason']}"


def _decision(decision: str, reason: str) -> dict[str, str]:
    return {
        "schema": SCHEMA,
        "decision": decision,
        "reason": reason,
    }


def is_read_only_local_inspection_command(command: str) -> bool:
    return unsafe_reason(command) is None


def is_destructive_bash_command(command: str) -> bool:
    """True when the command is not a known safe shape (the gate's one question)."""
    return unsafe_reason(command) is not None


def unsafe_reason(command: str) -> str | None:
    """None when every segment of ``command`` is a known safe shape, else the plain
    reason the first unsafe part needs approval."""
    try:
        for words in _split_segments(command):
            _judge_words(words)
    except _Unsafe as exc:
        return str(exc)
    return None


class _Unsafe(Exception):
    """A shape the classifier will not auto-allow; the message is the plain reason."""


@dataclass(frozen=True)
class _Word:
    text: str
    quoted: bool = False
    glob: bool = False


# --- Tokenizer: words, separators, and the only redirections that stay free ---------

_SPACE = re.compile(r"[ \t]+")
_SEPARATOR = re.compile(r"&&|\|\||;|\||&(?!>)|\n|\r")
_FREE_REDIRECT = re.compile(r"(?:(?:[12]?>>?|&>)[ \t]?/dev/null|2>&1)(?=[ \t\r\n;&|]|$)")
_PIECE = re.compile(r"""'([^']*)'|"([^"]*)"|(\\;)|([^\s;&|<>()$`"'\\]+)""")
_UNQUOTED_OK = re.compile(r"[A-Za-z0-9_./+=:@%,^~*?-]+")
_EXACT_WORDS = frozenset({"[", "]", "[[", "]]", "{}", "!="})
_STOP_REASONS = {
    "$": "an expansion or substitution ($)",
    "`": "a command substitution (backtick)",
    "\\": "a backslash escape",
    "<": "a redirection, here-document or here-string (<)",
    ">": "a redirection to a file (>)",
    "(": "a subshell or substitution (()",
    ")": "a subshell or substitution ())",
    '"': "an unterminated quote",
    "'": "an unterminated quote",
}


def _split_segments(command: str) -> list[list[_Word]]:
    segments: list[list[_Word]] = [[]]
    pos = 0
    while pos < len(command):
        pos = _next_token(command, pos, segments)
    return segments


def _next_token(command: str, pos: int, segments: list[list[_Word]]) -> int:
    for matcher in (_SPACE, _FREE_REDIRECT):
        found = matcher.match(command, pos)
        if found:
            return found.end()
    separator = _SEPARATOR.match(command, pos)
    if separator:
        segments.append([])
        return separator.end()
    word, end = _scan_word(command, pos)
    segments[-1].append(word)
    return end


def _scan_word(command: str, pos: int) -> tuple[_Word, int]:
    start, text, quoted, glob = pos, "", False, False
    while True:
        piece = _PIECE.match(command, pos)
        if piece is None:
            break
        part, is_quoted, is_glob = _piece_text(piece, pos == start)
        text, quoted, glob = text + part, quoted or is_quoted, glob or is_glob
        pos = piece.end()
    if pos == start:
        raise _Unsafe(f"it contains {_STOP_REASONS.get(command[pos], 'a character that is not literal')}")
    _check_stop(command, pos)
    return _Word(text, quoted, glob), pos


def _piece_text(piece: re.Match[str], first: bool) -> tuple[str, bool, bool]:
    single, double, escaped, bare = piece.groups()
    if single is not None:
        return single, True, False
    if double is not None:
        if any(char in double for char in "$`\\"):
            raise _Unsafe("it has $, a backtick or a backslash inside double quotes")
        return double, True, False
    if escaped is not None:
        return ";", True, False
    return _bare_text(bare, first)


def _bare_text(bare: str, first: bool) -> tuple[str, bool, bool]:
    if bare in _EXACT_WORDS:
        return bare, False, False
    if not _UNQUOTED_OK.fullmatch(bare) or (first and bare.startswith("~")):
        raise _Unsafe(f"non-literal word '{bare}' (glob, bracket, brace, tilde or other expansion)")
    return bare, False, "*" in bare or "?" in bare


def _check_stop(command: str, pos: int) -> None:
    char = command[pos] if pos < len(command) else ""
    if char in _STOP_REASONS:
        raise _Unsafe(f"it contains {_STOP_REASONS[char]}")


# --- Segments: loops and conditionals are fine when what they run is safe -----------

_KEYWORDS = frozenset({"if", "then", "else", "elif", "do", "while", "until"})
_CLOSERS = frozenset({"fi", "done"})
_FOR = frozenset({"for"})
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _is_word(word: _Word, names: frozenset[str]) -> bool:
    return not word.quoted and word.text in names


def _judge_words(words: list[_Word]) -> None:
    while words and _is_word(words[0], _KEYWORDS):
        words = words[1:]
    if not words or (len(words) == 1 and _is_word(words[0], _CLOSERS)):
        return
    if _is_word(words[0], _FOR):
        _judge_for(words)
    else:
        _judge_command(words)


def _judge_for(words: list[_Word]) -> None:
    if len(words) < 3 or not _NAME.fullmatch(words[1].text) or words[2].text != "in":
        raise _Unsafe("a 'for' loop that is not 'for NAME in literal words'")


def _judge_command(words: list[_Word]) -> None:
    name = words[0].text
    if "=" in name:
        raise _Unsafe(f"environment assignment '{name}' before a command")
    spec = _SAFE_COMMANDS.get(name)
    if spec is None:
        raise _Unsafe(f"unknown command '{name}'")
    _CHECKERS[spec.checker](name, spec, words[1:])


# --- The table: what a safe command may take ---------------------------------------


@dataclass(frozen=True)
class _Safe:
    flags: frozenset[str] = frozenset()
    valued: frozenset[str] = frozenset()  # flags that take one literal value
    optval: frozenset[str] = frozenset()  # long flags that may carry =value
    args: bool = True  # non-flag arguments allowed
    maxargs: int = 99
    globs: bool = False  # a glob operand is allowed (never with an output operand or a maxargs limit)
    dashsafe: bool = False  # no flag of the real tool writes or executes, so a glob that expands to a flag is harmless
    numeric: bool = False  # head/tail style -5
    checker: str = "generic"
    subs: dict[str, "_Safe"] = field(default_factory=dict)


def _s(flags: str = "", valued: str = "", optval: str = "", **kwargs: Any) -> _Safe:
    optional = frozenset(optval.split())
    return _Safe(frozenset(flags.split()) | optional, frozenset(valued.split()), optional, **kwargs)


_PYTEST = _s(
    "-q -x -v -vv -s -l --lf --ff --collect-only --co -rA --tb=short",
    valued="-k -m -r --tb --maxfail --durations",
)
_TEST_OPS = "-f -d -e -r -w -x -s -z -n -L -h -nt -ot -eq -ne -lt -gt -le -ge"
_NO_ARGS = _s(args=False)
_GIT_SUBS = {
    "--version": _NO_ARGS,
    "status": _s("-s --short -b --branch --porcelain --ignored -u", globs=True),
    "log": _s(
        "--oneline --graph --stat --name-only --name-status -p --patch --decorate --all -c "
        "--no-merges --merges --first-parent --abbrev-commit --shortstat --reverse",
        valued="-n --max-count --since --until --author --grep --format --pretty",
        numeric=True,
    ),
    "diff": _s(
        "--check --stat --name-only --name-status --cached --staged -p --patch -w --no-color "
        "--shortstat --numstat --summary --no-renames",
        valued="-U --unified",
    ),
    "show": _s("--stat --name-only --name-status -s --no-patch --oneline --no-color", valued="--format --pretty"),
    "branch": _s("-a -r -v -vv --list --show-current", args=False),
    "rev-parse": _s("--short --abbrev-ref --show-toplevel --verify --is-inside-work-tree"),
    "ls-files": _s("-m -o -c -s -z --others --modified --cached --exclude-standard"),
    "blame": _s("-w -l -s -e", valued="-L"),
    "remote": _s("-v", args=False),
    "add": _s("-A --all -u -n -v --dry-run"),
    "commit": _s("-a -s -q --amend --no-edit --allow-empty --all", valued="-m --message"),
    "config": _s("--get --list -l --local", checker="git_config"),
}
_SAFE_COMMANDS: dict[str, _Safe] = {
    "ls": _s("-l -a -A -h -R -1 -t -r -S -d -F -i -s -k -G --all --almost-all --human-readable", optval="--color", globs=True, dashsafe=True),
    "cat": _s("-n -b -s -A -E -T -v --number", globs=True, dashsafe=True),
    "head": _s("-q -v", valued="-n -c", numeric=True, globs=True, dashsafe=True),
    "tail": _s("-q -v", valued="-n -c", numeric=True, globs=True, dashsafe=True),
    "wc": _s("-l -w -c -m -L", globs=True, dashsafe=True),
    "echo": _s("-n -e -E"),
    "printf": _s(),
    "pwd": _NO_ARGS,
    "true": _NO_ARGS,
    "false": _NO_ARGS,
    "cd": _s(checker="cd"),
    "which": _s("-a"),
    "man": _s(),
    "file": _s("-b -i -L --mime --mime-type", globs=True),
    "stat": _s("-L -f -t", valued="-c --format", globs=True, dashsafe=True),
    "du": _s("-h -s -a -c -k -m -x", valued="-d --max-depth", globs=True, dashsafe=True),
    "df": _s("-h -T -i -k -a", globs=True, dashsafe=True),
    "tree": _s("-a -d -f -i -C -F -h -s --noreport", valued="-L -I", globs=True),
    "diff": _s("-u -r -q -y -w -b -i -B -N -c --brief --recursive --unified", optval="--color", globs=True, dashsafe=True),
    "cmp": _s("-s -l", globs=True, dashsafe=True),
    "sort": _s("-n -r -u -f -b -h -V -s -c -z -g -M", valued="-k -t", globs=True),
    "uniq": _s("-c -d -u -i", maxargs=1),
    "cut": _s("-s -z", valued="-d -f -c -b", globs=True, dashsafe=True),
    "tr": _s("-d -s -c"),
    "grep": _s(
        "-r -R -n -i -v -l -L -c -w -x -E -F -P -h -H -s -o -q -a -I -z",
        valued="-e -m -A -B -C --include --exclude --exclude-dir --max-count",
        optval="--color",
        globs=True,
        dashsafe=True,
    ),
    "rg": _s(
        "-n -i -l -c -v -w -F -S -s -u -N -o -q --hidden --no-heading --files --json --no-ignore "
        "--count --line-number --ignore-case --files-with-matches --smart-case --fixed-strings",
        valued="-g -t -A -B -C -m -e --glob --type --max-count --max-depth",
        optval="--color",
        globs=True,
    ),
    "ag": _s("-i -l -c -n -w -Q -s -S --nocolor --hidden", valued="-A -B -C -G -m", globs=True),
    "pytest": _PYTEST,
    "test": _s(_TEST_OPS),
    "[": _s(_TEST_OPS),
    "[[": _s(_TEST_OPS),
    "find": _s(checker="find"),
    "git": _s(checker="git", subs=_GIT_SUBS),
    "python": _s(checker="python"),
    "python3": _s(checker="python"),
    "node": _s(checker="subs", subs={"--version": _NO_ARGS, "-v": _NO_ARGS}),
    "npm": _s(
        checker="subs",
        subs={
            "--version": _NO_ARGS,
            "-v": _NO_ARGS,
            "ls": _s("--all --json -g", valued="--depth"),
            "list": _s("--all --json -g", valued="--depth"),
            "view": _s("--json"),
        },
    ),
    "pip": _s(checker="subs", subs={"--version": _NO_ARGS, "list": _s("--outdated", valued="--format"), "show": _s(), "freeze": _NO_ARGS}),
    "pip3": _s(checker="subs", subs={"--version": _NO_ARGS, "list": _s("--outdated", valued="--format"), "show": _s(), "freeze": _NO_ARGS}),
    "bash": _s(checker="shell"),
    "sh": _s(checker="shell"),
    "uptime": _s("-p -s"),
    "whoami": _NO_ARGS,
    "hostname": _NO_ARGS,
    "uname": _s("-a -r -s -m -n -o"),
    "date": _s("-u -I", args=False),
    "ps": _s("-e -f -a -x -u -l -A -w -H -j", valued="-o -p"),
    "free": _s("-h -m -g -b -k -t -w --human --si"),
    "nvidia-smi": _s("-L -q --list-gpus", valued="--query-gpu --format -i --id"),
    "ollama": _s(checker="subs", subs={"ps": _NO_ARGS, "list": _NO_ARGS, "show": _s()}),
    "docker": _s(
        checker="subs",
        subs={
            "ps": _s("-a -q -s -l --no-trunc", valued="--format -f --filter -n"),
            "images": _s("-a -q --no-trunc", valued="--format --filter"),
            "version": _NO_ARGS,
            "info": _NO_ARGS,
        },
    ),
    "systemctl": _s(
        checker="subs",
        subs={
            "status": _s("--no-pager -l", valued="-n"),
            "is-active": _s("--no-pager"),
            "is-enabled": _s("--no-pager"),
            "list-units": _s("--no-pager --all", valued="--type --state"),
        },
    ),
    "ssh": _s("-q -v -T -4 -6 -C -n", valued="-p -i -l", checker="ssh"),
    "nmap": _s("-sn -Pn -n -v -F -sT -sV --open", valued="-p -T --top-ports"),
    "awk": _s(valued="-F", checker="awk", globs=True),
    "sed": _s("-n -E -r -s -z -u --quiet --silent --regexp-extended", valued="-e --expression", checker="sed", globs=True),
}
_HYDRA_SUBS = {
    "status": _NO_ARGS,
    "roles": _NO_ARGS,
    "providers": _NO_ARGS,
    "models": _NO_ARGS,
    "skills": _s(checker="subs", subs={"list": _NO_ARGS, "doctrine": _NO_ARGS}),
    "ledger": _s(checker="subs", subs={"list": _NO_ARGS}),
    "task-eval": _s(valued="--format", maxargs=1),
}
_PY_MODULES = {
    "pytest": _PYTEST,
    "json.tool": _s("--sort-keys --compact --no-ensure-ascii", valued="--indent", maxargs=1),
    "py_compile": _s(checker="pycompile"),
    "hydra": _s(checker="subs", subs=_HYDRA_SUBS),
}


# --- Generic flag and argument check -----------------------------------------------


def _not_safe(name: str, flag: str) -> _Unsafe:
    return _Unsafe(f"flag '{flag}' is not on {name}'s safe list")


def _literal_value(name: str, word: _Word, glob_ok: bool = False) -> str:
    if word.glob and not glob_ok:
        raise _Unsafe(
            f"glob '{word.text}' is not allowed for {name} here "
            "(the shell could expand it to a flag or an extra operand)"
        )
    return word.text


def _glob_allowed(spec: _Safe, word: _Word, after_dashdash: bool) -> bool:
    """A glob is judged before the shell expands it, so it must be unable to become a flag
    or an extra operand: the command takes any number of operands (no output operand), and
    the glob cannot match a dash-word (starts with ./ or a literal non-dash character, or
    follows --), unless no flag of the real tool writes or runs anything."""
    if not spec.globs or spec.maxargs != 99:
        return False
    return after_dashdash or spec.dashsafe or word.text.startswith("./") or word.text[0] not in "-*?"


def _take_next(name: str, spec: _Safe, args: list[_Word], index: int, flag: str, values: list[tuple[str, str]]) -> int:
    if index >= len(args):
        raise _Unsafe(f"flag '{flag}' of {name} needs a value")
    values.append((flag, _literal_value(name, args[index])))
    return index + 1


def _take_long(name: str, spec: _Safe, args: list[_Word], index: int, values: list[tuple[str, str]]) -> int:
    flag, equals, value = args[index].text.partition("=")
    if flag in spec.valued and equals:
        values.append((flag, value))
        return index + 1
    if flag in spec.valued:
        return _take_next(name, spec, args, index + 1, flag, values)
    if flag in spec.optval and equals:
        return index + 1
    raise _not_safe(name, flag)


def _take_short(name: str, spec: _Safe, args: list[_Word], index: int, values: list[tuple[str, str]]) -> int:
    """The one bundle parser: walk each letter; a letter that takes a value consumes the
    rest of the bundle or the next word; an unknown letter is not safe."""
    text = args[index].text
    for position, letter in enumerate(text[1:], 1):
        flag = f"-{letter}"
        if flag in spec.valued:
            rest = text[position + 1 :]
            if rest:
                values.append((flag, rest))
                return index + 1
            return _take_next(name, spec, args, index + 1, flag, values)
        if flag not in spec.flags:
            raise _not_safe(name, flag if len(text) == 2 else text)
    return index + 1


def _take_flag(name: str, spec: _Safe, args: list[_Word], index: int, values: list[tuple[str, str]]) -> int:
    text = _literal_value(name, args[index])  # an unquoted glob in a flag word is expanded by the shell first
    if text in spec.flags:
        return index + 1
    if text.startswith("--"):
        return _take_long(name, spec, args, index, values)
    if spec.numeric and text[1:].isdigit():
        return index + 1
    return _take_short(name, spec, args, index, values)


def _is_flag(word: _Word) -> bool:
    return word.text.startswith("-") and word.text != "-"


def _check_positional(name: str, spec: _Safe, word: _Word, count: int, after_dashdash: bool) -> None:
    if not spec.args or count >= spec.maxargs:
        raise _Unsafe(f"{name} does not take the argument '{word.text}' on the safe list")
    _literal_value(name, word, word.glob and _glob_allowed(spec, word, after_dashdash))


@dataclass
class _Parsed:
    positional: list[_Word] = field(default_factory=list)
    values: list[tuple[str, str]] = field(default_factory=list)  # (flag, value) for valued flags


def _parse_args(name: str, spec: _Safe, args: list[_Word]) -> _Parsed:
    """Validate flags and arguments for any table entry; returns what was taken."""
    parsed = _Parsed()
    index, only_args = 0, False
    while index < len(args):
        word = args[index]
        if not only_args and word.text == "--":
            only_args, index = True, index + 1
        elif not only_args and _is_flag(word):
            index = _take_flag(name, spec, args, index, parsed.values)
        else:
            _check_positional(name, spec, word, len(parsed.positional), only_args)
            parsed.positional.append(word)
            index += 1
    return parsed


def _check_generic(name: str, spec: _Safe, args: list[_Word]) -> list[_Word]:
    return _parse_args(name, spec, args).positional


def _check_plain(name: str, spec: _Safe, args: list[_Word]) -> None:
    _check_generic(name, spec, args)


# --- Commands that need more than flags: subcommands, scripts, programs ------------


def _check_subs(name: str, spec: _Safe, args: list[_Word]) -> None:
    if not args:
        raise _Unsafe(f"{name} with no subcommand")
    sub = spec.subs.get(args[0].text)
    if sub is None:
        raise _Unsafe(f"{name} subcommand '{args[0].text}' is not on the safe list")
    _CHECKERS[sub.checker](f"{name} {args[0].text}", sub, args[1:])


_PARENT = frozenset({".."})
_DEVICE_DIRS = frozenset({"..", "dev", "proc", "fd"})


def _path_ok(text: str, banned: frozenset[str]) -> bool:
    """A path that stays in the workspace: relative, not '-', no '..', and none of the
    ``banned`` directory names (a script must also not live under dev/, proc/ or fd/)."""
    return bool(text) and "{}" not in text and text[0] not in "/-~" and not banned.intersection(text.split("/"))


def _require_path(label: str, text: str, banned: frozenset[str]) -> None:
    if not _path_ok(text, banned):
        raise _Unsafe(
            f"{label} '{text}' must be a relative path inside the workspace "
            "(not absolute, not '-', no '..', no find placeholder, not under dev/, proc/ or fd/)"
        )


def _check_cd(name: str, spec: _Safe, args: list[_Word]) -> None:
    positional = _check_generic(name, spec, args)
    if len(positional) != 1 or not _path_ok(positional[0].text, _PARENT):
        raise _Unsafe("cd only goes to one relative path inside the workspace (no .., /, ~ or -)")


def _check_script(name: str, args: list[_Word]) -> None:
    """The one rule for every command that runs a file: python, bash, sh (and so find -exec)."""
    _require_path(f"{name} script", args[0].text, _DEVICE_DIRS)
    for word in args:
        _literal_value(name, word)


def _check_git(name: str, spec: _Safe, args: list[_Word]) -> None:
    index = 0
    while index < len(args) and args[index].text.startswith("-") and args[index].text not in spec.subs:
        index = _git_global_option(args, index)
    _check_subs(name, spec, args[index:])


def _git_global_option(args: list[_Word], index: int) -> int:
    text = args[index].text
    if text == "--no-pager":
        return index + 1
    if text == "-C" and index + 1 < len(args) and _path_ok(args[index + 1].text, _PARENT):
        return index + 2
    raise _Unsafe(f"git option '{text}' is not on the safe list (no -c, --config-env, --exec-path or -C outside the workspace)")


def _check_git_config_checker(name: str, spec: _Safe, args: list[_Word]) -> None:
    _check_git_config(args)


_GIT_SAFE_KEY = re.compile(r"(user|color|format)\.[\w.-]+|core\.whitespace")


def _check_git_config(args: list[_Word]) -> None:
    config = _GIT_SUBS["config"]
    positional = _check_generic("git config", config, args)
    reading = any(word.text in {"--get", "--list", "-l"} for word in args)
    if not reading and (not positional or not _GIT_SAFE_KEY.fullmatch(positional[0].text)):
        raise _Unsafe("git config can only set user.*, color.*, format.* or core.whitespace")


def _check_python(name: str, spec: _Safe, args: list[_Word]) -> None:
    if not args:
        raise _Unsafe(f"{name} with no script would run code from stdin")
    first = args[0].text
    if first in {"--version", "-V"} and len(args) == 1:
        return
    if first == "-m":
        _check_python_module(name, args[1:])
        return
    if first.startswith("-"):
        raise _not_safe(name, first)
    _check_script(name, args)


def _check_python_module(name: str, args: list[_Word]) -> None:
    module = _PY_MODULES.get(args[0].text) if args else None
    if module is None:
        raise _Unsafe(f"{name} -m module '{args[0].text if args else ''}' is not on the safe list")
    _CHECKERS[module.checker](f"{name} -m {args[0].text}", module, args[1:])


def _check_shell(name: str, spec: _Safe, args: list[_Word]) -> None:
    if not args:
        raise _Unsafe(f"{name} with no script would read commands from stdin")
    if args[0].text == "-c":
        _check_shell_script(name, args)
        return
    if args[0].text.startswith("-"):
        raise _not_safe(name, args[0].text)
    _check_script(name, args)


def _check_shell_script(name: str, args: list[_Word]) -> None:
    if len(args) < 2:
        raise _Unsafe(f"{name} -c with no script")
    reason = unsafe_reason(args[1].text)
    if reason is not None:
        raise _Unsafe(f"the {name} -c script: {reason}")


def _check_ssh(name: str, spec: _Safe, args: list[_Word]) -> None:
    """Plain ssh runs free (operator law). ssh joins the remote words with spaces and the
    remote shell parses them again, so the joined string is judged as a fresh command."""
    index = 0
    while index < len(args) and args[index].text.startswith("-"):
        index = _take_flag(name, spec, args, index, [])
    if index >= len(args):
        raise _Unsafe("ssh without a host")
    reason = unsafe_reason(" ".join(word.text for word in args[index + 1 :]))
    if reason is not None:
        raise _Unsafe(f"the ssh remote command: {reason}")


_FIND_PATTERNS = frozenset({"-name", "-iname", "-path", "-ipath", "-regex", "-iregex"})
_FIND_NUMERIC = {
    "-maxdepth": re.compile(r"\d+"),
    "-mindepth": re.compile(r"\d+"),
    "-mtime": re.compile(r"[+-]?\d+"),
    "-mmin": re.compile(r"[+-]?\d+"),
    "-size": re.compile(r"[+-]?\d+[cwbkMG]?"),
    "-perm": re.compile(r"[-/]?[0-7]{1,4}"),
    "-type": re.compile(r"[fdlbcps]"),
    "-user": re.compile(r"[A-Za-z0-9_.-]+"),
}
_FIND_VALUED = _FIND_PATTERNS | frozenset(_FIND_NUMERIC) | {"-newer"}
_FIND_PLAIN = frozenset(
    "-print -print0 -empty -not -and -or -a -o -prune -readable -writable -executable -true -false -depth -mount -xdev".split()
)


def _check_find(name: str, spec: _Safe, args: list[_Word]) -> None:
    index = 0
    while index < len(args) and not args[index].text.startswith("-"):
        _require_path("find start path", _literal_value(name, args[index]), _DEVICE_DIRS)
        index += 1
    while index < len(args):
        index = _find_primary(args, index)


def _find_value(primary: str, word: _Word) -> None:
    """Values of find primaries: an unquoted glob is expanded by the shell before find sees
    it (quoted patterns are fine); numeric primaries take literal numbers; -newer takes a path."""
    _literal_value("find", word)
    pattern = _FIND_NUMERIC.get(primary)
    if pattern is not None and not pattern.fullmatch(word.text):
        raise _Unsafe(f"find {primary} needs a plain value, not '{word.text}'")
    if primary == "-newer":
        _require_path("find -newer file", word.text, _DEVICE_DIRS)


def _find_primary(args: list[_Word], index: int) -> int:
    text = args[index].text
    if text in _FIND_PLAIN:
        return index + 1
    if text in _FIND_VALUED and index + 1 < len(args):
        _find_value(text, args[index + 1])
        return index + 2
    if text in {"-exec", "-execdir"}:
        return _find_exec(args, index)
    raise _Unsafe(f"find primary '{text}' is not on the safe list (no -delete, -fprint, -fls or -ok)")


def _find_exec(args: list[_Word], index: int) -> int:
    end = index + 1
    while end < len(args) and args[end].text not in {";", "+"}:
        end += 1
    if end >= len(args) or end == index + 1:
        raise _Unsafe("find -exec without a command and its closing ';'")
    command = args[index + 1 : end]
    _judge_command(command)
    if args[end].text == "+":
        _check_plus(command)
    return end + 1


def _check_plus(command: list[_Word]) -> None:
    """With '+', find appends every match to one command line, so '{}' acts like an unbounded
    glob operand: the same rule as a glob word, so a command with an output operand needs approval."""
    spec = _SAFE_COMMANDS.get(command[0].text)
    if spec is None or not _glob_allowed(spec, _Word("{}", glob=True), True):
        raise _Unsafe(f"find -exec {command[0].text} ... + gives it many operands, one of which may be an output file")


_AWK_TOKEN = re.compile(r"\s*(?:\$(?:\d+|NF)|NR|NF|print|==|!=|<=|>=|<|>|\d+|[{},])")
_AWK_OPS = frozenset({"==", "!=", "<=", ">=", "<", ">"})


def _awk_tokens(program: str) -> list[str] | None:
    tokens: list[str] = []
    pos = 0
    while True:
        found = _AWK_TOKEN.match(program, pos)
        if found is None:
            break
        tokens.append(found.group(0).strip())
        pos = found.end()
    return tokens if not program[pos:].strip() else None


def _awk_pattern_len(tokens: list[str]) -> int:
    lhs = bool(tokens) and (tokens[0] in {"NR", "NF"} or (tokens[0][:1] == "$" and tokens[0][1:].isdigit()))
    return 3 if lhs and len(tokens) >= 3 and tokens[1] in _AWK_OPS and tokens[2].isdigit() else 0


def _awk_action_ok(tokens: list[str]) -> bool:
    if tokens[:2] != ["{", "print"] or tokens[-1:] != ["}"]:
        return False
    body = tokens[2:-1]
    fields, commas = body[0::2], body[1::2]
    return not body or (len(body) % 2 == 1 and all(t[:1] == "$" for t in fields) and set(commas) <= {","})


def _awk_program_ok(program: str) -> bool:
    """optional pattern (NR/NF/$N compared with an integer) and optional {print fields} action"""
    tokens = _awk_tokens(program)
    if not tokens:
        return False
    size = _awk_pattern_len(tokens)
    rest = tokens[size:]
    return _awk_action_ok(rest) if rest else size > 0


def _check_awk(name: str, spec: _Safe, args: list[_Word]) -> None:
    positional = _check_generic(name, spec, args)
    program = positional[0].text if positional else ""
    if not _awk_program_ok(program):
        raise _Unsafe(
            "awk program is not a plain print of fields with an optional NR/NF/field comparison "
            "(no function call, string, pipe, redirection or getline)"
        )


_SED_ADDR = r"(?:\d+|\$|/[^/\\\n]*/)"
_SED_COMMAND = re.compile(
    rf"(?:{_SED_ADDR}(?:,{_SED_ADDR})?)?!?\s*"
    r"(?:[pdqnN=lP]"
    r"|s([^\w\s\\;])((?:(?!\1)[^\\\n])*)\1((?:(?!\1)[^\\\n])*)\1[gpiI0-9]*"
    r"|y([^\w\s\\;])((?:(?!\4)[^\\\n])*)\4((?:(?!\4)[^\\\n])*)\4)"
    r"\s*(?:;|$)"
)


_SPACE_RUN = re.compile(r"\s*")


def _sed_script_safe(script: str) -> bool:
    pos = _SPACE_RUN.match(script, 0).end()
    while pos < len(script):
        found = _SED_COMMAND.match(script, pos)
        if found is None:
            return False
        pos = _SPACE_RUN.match(script, found.end()).end()
    return True


def _check_sed(name: str, spec: _Safe, args: list[_Word]) -> None:
    parsed = _parse_args(name, spec, args)
    scripts = [value for flag, value in parsed.values if flag in {"-e", "--expression"}]
    scripts = scripts or [parsed.positional[0].text if parsed.positional else ""]
    if not all(script and _sed_script_safe(script) for script in scripts):
        raise _Unsafe("sed script that is not plain print/delete/substitute (no e, w, r or other commands)")


def _check_pycompile(name: str, spec: _Safe, args: list[_Word]) -> None:
    positional = _check_generic(name, spec, args)
    if not positional:
        raise _Unsafe(f"{name} needs at least one file")
    for word in positional:
        _require_path(f"{name} file", word.text, _DEVICE_DIRS)


_CHECKERS: dict[str, Callable[[str, _Safe, list[_Word]], None]] = {
    "generic": _check_plain,
    "subs": _check_subs,
    "git": _check_git,
    "python": _check_python,
    "shell": _check_shell,
    "find": _check_find,
    "ssh": _check_ssh,
    "cd": _check_cd,
    "pycompile": _check_pycompile,
    "git_config": _check_git_config_checker,
    "awk": _check_awk,
    "sed": _check_sed,
}
