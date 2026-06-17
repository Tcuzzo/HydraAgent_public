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
        command = " ".join(str(arguments.get("command", "")).split())
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
    if not tokens:
        return False
    lowered = [token.lower() for token in tokens]
    if "$(" in command or "`" in command:
        return True
    for token in lowered:
        base = token.split("/")[-1]
        if base in DESTRUCTIVE_BASH_TOKENS:
            return True
    return (
        _has_destructive_git(lowered)
        or _has_destructive_systemctl(lowered)
        or _has_destructive_docker(lowered)
        or _has_destructive_network_mutation(lowered)
        or _has_destructive_rsync(lowered)
        or _has_recursive_permission_change(lowered)
    )


def _shell_tokens(command: str) -> list[str]:
    spaced = _strip_safe_redirections(command)
    for marker in (">>|", ">|", ">>", ">", "&&", "||", ";", "|"):
        spaced = spaced.replace(marker, f" {marker} ")
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


def _has_recursive_permission_change(tokens: list[str]) -> bool:
    if not any(token in {"chmod", "chown"} for token in tokens):
        return False
    return any(token in {"-r", "--recursive"} for token in tokens)
