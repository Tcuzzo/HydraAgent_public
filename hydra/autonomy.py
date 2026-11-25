"""Operator autonomy classifier for safe low-friction actions.

Doctrine: auto-allow everything except destructive or authority-class actions.
Normal repo inspection, local runtime inspection, system statistics, code edits,
tests, and durable memory are not approval-gated. The single gate is for
commands that delete, overwrite through shell redirection, force-rewrite git
history, restart/stop services, escalate privilege, kill processes, format
storage, or mutate external systems.
"""
from __future__ import annotations

import shlex
import re
from typing import Any


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

DESTRUCTIVE_BASH_TOKENS = frozenset(
    {
        ">",
        ">>",
        ">|",
        "rm",
        "rmdir",
        "truncate",
        "tee",
        "dd",
        "mkfs",
        "fdisk",
        "parted",
        "mount",
        "umount",
        "sudo",
        "su",
        "kill",
        "pkill",
        "killall",
        "reboot",
        "shutdown",
    }
)

# Command words that are destructive when something runs them. A word counts
# when a shell would run it as a command, or when it sits behind any word that
# is not known to treat its arguments as plain data (see _INERT): the default is
# to gate, so an unlisted wrapper (`flock`, `ssh host`, `strace`) is covered the
# same way as a listed one. `alias` is here because a defined alias hides the word.
DESTRUCTIVE_COMMAND_WORDS = frozenset({"mv", "cp", "chown", "chmod", "alias"})
_SEPARATORS = frozenset({"&&", "||", ";", "|", "&", "(", ")"})
# Shell keywords that hand the next word over to be run as the command.
_KEYWORDS = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}"})
# Commands whose arguments are data, never another command. Everything else is
# treated as possibly running its arguments (fail closed).
_INERT = frozenset(
    {
        "echo", "printf", "grep", "egrep", "fgrep", "rg", "ag", "which", "whereis", "type",
        "man", "cat", "ls", "head", "tail", "wc", "sort", "uniq", "diff", "cmp", "stat",
        "file", "du", "df", "pwd", "true", "false", "test", "[", "[[", "git", "pytest",
        "python", "python3", "ruff", "radon", "cd", "mkdir", "touch", "awk", "sed", "cut",
        "tr", "basename", "dirname", "realpath", "readlink", "date", "whoami", "id",
        "uname", "jq", "tree", "less", "more", "sleep", "export", "set", "unset", "local",
        "declare", "for", "case", "select", "find", "pip", "pip3", "npm", "node", "ps",
        "seq", "hostname", "uptime", "free",
    }
)
_EXEC_FLAGS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "ash", "csh", "tcsh", "fish"})
# Words that run a script read from stdin, a pipe, a here-string or a substitution.
_SCRIPT_READERS = _SHELLS | {"source", ".", "at", "batch"}
_STDIN_SHELL_FLAG = re.compile(r"^-[a-z]*s[a-z]*$")
_ENV_ASSIGNMENT = re.compile(r"^[a-z_][a-z0-9_]*=\S*$")
# A command word that is itself an expansion: $x, ${X:-cp}, cp${IFS}a, c*, {c,}p.
_EXPANSION = re.compile(r"[$*?]|\{[^}]*,")
_EXPECT, _CAND, _ARGS = "expect", "candidate", "args"

# Policy: plain ssh/scp/sftp/rsync/network commands are NOT destructive on
# their own and must NOT be approval-gated. They were formerly
# blanket-classified as destructive here (an undeclared gate, never listed in
# .hydraAgent/policies/danger-gates.yaml), which queued every `ssh user@host`
# for approval in the default 'ask' chat mode. That gate is removed — see
# bugs #3/#4. The genuinely destructive FORM of a network command (e.g.
# `rsync --delete`, which deletes files on the far side) is still gated below by
# _has_destructive_rsync(). The untrusted-surface block (hydra.channel_trust +
# ApprovalPolicy) is a SEPARATE layer and still stops an action tool from a
# public/non-operator surface; it does not depend on these tokens.


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
    # Bash is the only broad tool that can become destructive. Gate only
    # destructive/authority-class command shapes; let normal inspection,
    # tests, build commands, and non-destructive local work run.
    if tool_name == "bash":
        command = str(arguments.get("command", ""))
        if is_destructive_bash_command(command):
            if policy_mode == "deny":
                return _decision("blocked", "deny policy blocks destructive command")
            return _decision("needs_approval", "destructive command requires approval")
        return _decision("auto_allow", "non-destructive command")

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
    return not is_destructive_bash_command(command)


def is_destructive_bash_command(command: str) -> bool:
    if not command:
        return False
    try:
        tokens = _shell_tokens(command)
    except ValueError:
        return True
    return bool(tokens) and _tokens_destructive(command, [token.lower() for token in tokens])


def _tokens_destructive(command: str, lowered: list[str]) -> bool:
    if "$(" in command or "`" in command:
        return True
    if any(token.split("/")[-1] in DESTRUCTIVE_BASH_TOKENS for token in lowered):
        return True
    return any(shape(lowered) for shape in _DESTRUCTIVE_SHAPES)


def _shell_tokens(command: str) -> list[str]:
    spaced = _strip_safe_redirections(command).replace("\n", " ; ")
    for marker in (">>|", ">|", ">>", ">", "&&", "||", ";", "|", "(", ")"):
        spaced = spaced.replace(marker, f" {marker} ")
    spaced = re.sub(r"(?<![&>])&(?![&>])", " & ", spaced)
    try:
        return shlex.split(spaced)
    except ValueError:
        raise ValueError("invalid shell syntax")


def _strip_safe_redirections(command: str) -> str:
    # Redirecting noisy stderr/stdout to /dev/null during read-only searches is
    # not destructive. Keep real file writes gated by only stripping /dev/null
    # and fd-dup redirections like 2>&1.
    command = re.sub(r"(?<!\S)(?:[012]|&)?\s*>\s*/dev/null(?=$|\s|[;|&])", " ", command)
    command = re.sub(r"(?<!\S)[12]\s*>\s*&[12](?=$|\s|[;|&])", " ", command)
    return command


def _has_destructive_git(tokens: list[str]) -> bool:
    if "git" not in tokens:
        return False
    if "reset" in tokens and "--hard" in tokens:
        return True
    if "clean" in tokens:
        return True
    if "push" in tokens and any(token in {"--force", "-f", "--force-with-lease"} for token in tokens):
        return True
    if "checkout" in tokens and any(token in {"-f", "--force"} for token in tokens):
        return True
    if "restore" in tokens and any(token in {"--staged", "--worktree", "-W", "-S"} for token in tokens):
        return True
    return False


def _has_destructive_systemctl(tokens: list[str]) -> bool:
    if "systemctl" not in tokens:
        return False
    destructive = {"start", "stop", "restart", "reload", "enable", "disable", "mask", "unmask"}
    return any(token in destructive for token in tokens)


def _has_destructive_docker(tokens: list[str]) -> bool:
    if "docker" not in tokens:
        return False
    destructive = {"rm", "rmi", "stop", "restart", "kill", "prune", "down"}
    return any(token in destructive for token in tokens)


def _has_destructive_network_mutation(tokens: list[str]) -> bool:
    if not any(token in {"curl", "wget", "http"} for token in tokens):
        return False
    mutating_flags = {"-x", "--request", "-d", "--data", "--data-raw", "--data-binary", "-f", "--form", "-t", "--upload-file"}
    mutating_methods = {"post", "put", "patch", "delete"}
    for index, token in enumerate(tokens):
        if token in mutating_flags:
            if index + 1 >= len(tokens):
                return True
            if token in {"-x", "--request"}:
                return tokens[index + 1].lower() in mutating_methods
            return True
    return False


def _has_destructive_rsync(tokens: list[str]) -> bool:
    # Bare `rsync host:/src ./dst` (copy/transfer) is not destructive and runs
    # free — like ssh/scp. Only the delete-on-destination forms remove files, so
    # gate those: --delete, --delete-before/after/during/delay/excluded,
    # --del, and --remove-source-files.
    if "rsync" not in tokens:
        return False
    for token in tokens:
        if token == "--del" or token == "--remove-source-files" or token.startswith("--delete"):
            return True
    return False


def _skippable(token: str) -> bool:
    return token in _KEYWORDS or token.startswith("-") or bool(_ENV_ASSIGNMENT.match(token))


def _state_after(state: str, token: str) -> str:
    return _CAND if state == _CAND or token.split("/")[-1] not in _INERT else _ARGS


def _command_words(tokens: list[str]):
    """Yield ``(index, token, at_command_position)`` for every word a shell might
    run as a command: the first word of each segment (after separators, subshell
    parens, keywords such as ``if``/``do``, env assignments, ``find -exec``), and
    every later word of a segment whose command is not known to take plain data
    (so an unlisted wrapper is covered the way a listed one is)."""
    state = _EXPECT
    for index, token in enumerate(tokens):
        if token in _SEPARATORS or token in _EXEC_FLAGS:
            state = _EXPECT
        elif state != _ARGS and not _skippable(token):
            yield index, token, state == _EXPECT
            state = _state_after(state, token)


def _segment_args(tokens: list[str], index: int) -> list[str]:
    """The words after ``tokens[index]`` up to the next separator."""
    rest: list[str] = []
    for token in tokens[index + 1 :]:
        if token in _SEPARATORS:
            break
        rest.append(token)
    return rest


def _runs_unseen_script(word: str, rest: list[str]) -> bool:
    """A shell (or `source`/`at`) whose script comes from stdin, a pipe, a
    here-string, a heredoc or a process substitution cannot be read here: gated.
    A shell given a script file or a -c string is judged on that string instead."""
    if any(arg.startswith("<") for arg in rest):
        return True
    if word not in _SHELLS:
        return False
    return any(_STDIN_SHELL_FLAG.match(arg) for arg in rest) or all(arg.startswith("-") for arg in rest)


def _string_destructive(token: str) -> bool:
    """A quoted multi-word argument behind a non-inert command (`-c 'cp a b'`,
    `ssh host "cp a b"`, `env -S '...'`) is judged as a script by the same rule."""
    return any(char.isspace() for char in token) and is_destructive_bash_command(token)


def _word_destructive(tokens: list[str], index: int, token: str, at_command: bool) -> bool:
    base = token.split("/")[-1]
    if base in DESTRUCTIVE_COMMAND_WORDS or _string_destructive(token):
        return True
    if at_command and _EXPANSION.search(token):
        return True
    return base in _SCRIPT_READERS and _runs_unseen_script(base, _segment_args(tokens, index))


def _has_destructive_command_word(tokens: list[str]) -> bool:
    return any(
        _word_destructive(tokens, index, token, at_command)
        for index, token, at_command in _command_words(tokens)
    )


_OUTPUT_FLAG = re.compile(r"^-[a-z]*o[a-z]*$|^--output(-document|-file)?(=.*)?$")


def _has_download_to_file(tokens: list[str]) -> bool:
    # `wget -O file` / `curl -o file` write a downloaded body to disk, alone or
    # in combined short flags (`wget -qO f`, `curl -sSLo f`). Tokens are
    # lowercased, so -O and -o collapse to the same flag; both write a file.
    if not any(token in {"curl", "wget"} for token in tokens):
        return False
    return any(_OUTPUT_FLAG.match(token) for token in tokens)


def _has_package_mutation(tokens: list[str]) -> bool:
    # Installing or publishing a package runs third-party code or ships ours.
    if any(token in {"pip", "pip3"} for token in tokens) and "install" in tokens:
        return True
    return "npm" in tokens and "publish" in tokens


_DESTRUCTIVE_SHAPES = (
    _has_destructive_git,
    _has_destructive_systemctl,
    _has_destructive_docker,
    _has_destructive_network_mutation,
    _has_destructive_rsync,
    _has_destructive_command_word,
    _has_download_to_file,
    _has_package_mutation,
)
