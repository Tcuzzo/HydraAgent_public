"""Ops pack loading and plan rendering for HydraAgent."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


SCHEMA = "hydra.ops_plan.v1"
TARGET_TYPES = {"local", "repo", "file", "cidr", "ssh", "winrm", "router"}

_RISK_BY_TARGET = {
    "local": "T0",
    "file": "T0",
    "repo": "T1",
    "cidr": "T2",
    "ssh": "T3",
    "winrm": "T3",
    "router": "T3",
}

_MUTATING_TOKENS = (
    "chmod ",
    "chown ",
    "cp ",
    "format ",
    "kill ",
    "killall ",
    "mkfs",
    "mv ",
    "pkill ",
    "shutdown",
    "reboot",
    "systemctl enable",
    "systemctl disable",
    "systemctl start",
    "systemctl restart",
    "systemctl reload",
    "systemctl stop",
    "service start",
    "service restart",
    "service reload",
    "service stop",
    "set-",
    "remove-",
    "new-",
)

_MUTATING_PATTERNS = (
    (re.compile(r"\brm\s+", re.IGNORECASE), "rm"),
    (re.compile(r"\bdel\s+", re.IGNORECASE), "del"),
    (re.compile(r"\berase\s+", re.IGNORECASE), "erase"),
    (
        re.compile(r"\biptables\b(?=[^;&|]*\s-(?:A|D|I|F|P|R|X|Z)\b)", re.IGNORECASE),
        "iptables mutating option",
    ),
    (
        re.compile(
            r"\bufw\s+(?:allow|deny|enable|disable|delete|reset|reload)\b",
            re.IGNORECASE,
        ),
        "ufw mutating command",
    ),
    (
        re.compile(
            r"\bnetsh\b(?=[^;&|]*(?:\bset\b|\badd\b|\bdelete\b|"
            r"\badvfirewall\s+firewall\s+(?:add|delete|set)\b))",
            re.IGNORECASE,
        ),
        "netsh mutating command",
    ),
    (
        re.compile(r"\bsed\b(?=[^;&|]*\s-i(?:\b|[^A-Za-z0-9_]))", re.IGNORECASE),
        "sed -i",
    ),
    (
        re.compile(r"\btee\b(?=[^;&|]*(?:\s+-\S+)*\s+(?!-)(?!/dev/null\b)\S+)", re.IGNORECASE),
        "tee to file",
    ),
    (
        re.compile(
            r"\bsystemctl\b(?=[^;&|]*\b(?:enable|disable|start|stop|restart|reload)\b)",
            re.IGNORECASE,
        ),
        "systemctl mutating command",
    ),
    (
        re.compile(
            r"\bservice\s+(?:\S+\s+)?(?:start|stop|restart|reload)\b",
            re.IGNORECASE,
        ),
        "service mutating command",
    ),
    (
        re.compile(r"\bdd\b(?=[^;&|]*\bof=)", re.IGNORECASE),
        "dd of=",
    ),
)


@dataclass(frozen=True)
class OpsPackError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class TargetSpec:
    type: str
    value: str

    def to_dict(self) -> dict[str, str]:
        return {"type": self.type, "value": self.value}


def parse_target(raw: str) -> TargetSpec:
    target = raw.strip()
    if not target:
        raise OpsPackError("target must be non-empty")
    if target == "local":
        return TargetSpec(type="local", value="local")
    if ":" not in target:
        raise OpsPackError("target must be 'local' or TYPE:VALUE")

    target_type, value = target.split(":", 1)
    if target_type not in TARGET_TYPES:
        raise OpsPackError(f"unknown target type {target_type!r}")
    if not value.strip():
        raise OpsPackError(f"target {target_type!r} requires a non-empty value")
    if target_type == "cidr" and "/" not in value:
        raise OpsPackError("cidr target requires slash prefix")
    if target_type == "router":
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise OpsPackError("router target must be http(s)://host")
    return TargetSpec(type=target_type, value=value)


def load_pack(pack_id: str, packs_dir: str | Path) -> dict[str, Any]:
    path = _pack_path(pack_id, Path(packs_dir))
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise OpsPackError(f"ops pack not found: {pack_id}") from e
    except yaml.YAMLError as e:
        raise OpsPackError(f"ops pack YAML error in {path}: {e}") from e
    return validate_pack(data, source_path=path)


def validate_pack(data: Any, source_path: str | Path | None = None) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise OpsPackError("ops pack must be a mapping")

    pack_id = _required_str(data, "id", "ops pack")
    name = _required_str(data, "name", f"ops pack {pack_id}")
    target_types = _required_list(data, "target_types", f"ops pack {pack_id}")
    commands = _required_list(data, "read_only_commands", f"ops pack {pack_id}")
    rot_signals = _required_list(data, "rot_signals", f"ops pack {pack_id}")
    success_metrics = _required_list(data, "success_metrics", f"ops pack {pack_id}")

    for target_type in target_types:
        if not isinstance(target_type, str) or not target_type.strip():
            raise OpsPackError(f"ops pack {pack_id} target_types must be non-empty strings")
        if target_type not in TARGET_TYPES:
            raise OpsPackError(f"ops pack {pack_id} has unknown target type {target_type!r}")

    command_ids: set[str] = set()
    validated_commands: list[dict[str, Any]] = []
    for command_row in commands:
        if not isinstance(command_row, dict):
            raise OpsPackError(f"ops pack {pack_id} read_only_commands entries must be mappings")
        command_id = _required_str(command_row, "id", f"ops pack {pack_id} command")
        if command_id in command_ids:
            raise OpsPackError(f"ops pack {pack_id} has duplicate command id {command_id!r}")
        command_ids.add(command_id)

        command = _required_str(command_row, "command", f"ops pack {pack_id} command {command_id}")
        timeout = command_row.get("timeout_seconds")
        if type(timeout) is not int or timeout <= 0:
            raise OpsPackError(
                f"ops pack {pack_id} command {command_id} requires positive integer timeout_seconds"
            )
        evidence_key = _required_str(
            command_row, "evidence_key", f"ops pack {pack_id} command {command_id}"
        )

        token = _mutating_token(command)
        if token:
            raise OpsPackError(
                f"ops pack {pack_id} read-only command {command_id} contains mutating token {token!r}"
            )

        copied_command = dict(command_row)
        copied_command["id"] = command_id
        copied_command["command"] = command
        copied_command["timeout_seconds"] = timeout
        copied_command["evidence_key"] = evidence_key
        validated_commands.append(copied_command)

    pack = dict(data)
    pack["id"] = pack_id
    pack["name"] = name
    pack["target_types"] = list(target_types)
    pack["read_only_commands"] = validated_commands
    pack["rot_signals"] = list(rot_signals)
    pack["success_metrics"] = list(success_metrics)
    pack["source_path"] = str(source_path) if source_path is not None else None
    return pack


def render_plan(pack: dict[str, Any], target: TargetSpec) -> dict[str, Any]:
    if target.type not in pack.get("target_types", []):
        raise OpsPackError(f"pack {pack.get('id', '<unknown>')} does not support target type {target.type}")

    risk_tier = _RISK_BY_TARGET.get(target.type, "T5")
    commands = [
        {
            "id": row["id"],
            "command": row["command"],
            "timeout_seconds": row["timeout_seconds"],
            "evidence_key": row["evidence_key"],
            "risk_tier": risk_tier,
            "execute": False,
        }
        for row in pack["read_only_commands"]
    ]
    return {
        "schema": SCHEMA,
        "pack_id": pack["id"],
        "pack_name": pack["name"],
        "target": target.to_dict(),
        "executed": False,
        "permission_policy": "operator-selected-later",
        "commands": commands,
        "rot_signals": list(pack["rot_signals"]),
        "success_metrics": list(pack["success_metrics"]),
    }


def _pack_path(pack_id: str, packs_dir: Path) -> Path:
    if not isinstance(pack_id, str) or not pack_id.strip():
        raise OpsPackError("pack id must be non-empty")
    if "/" in pack_id or "\\" in pack_id or ".." in pack_id:
        raise OpsPackError(f"invalid pack id {pack_id!r}")
    return packs_dir / f"{pack_id}.yaml"


def _required_str(data: dict[str, Any], key: str, owner: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OpsPackError(f"{owner} requires non-empty {key}")
    return value


def _required_list(data: dict[str, Any], key: str, owner: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list) or not value:
        raise OpsPackError(f"{owner} requires non-empty {key}")
    return value


def _mutating_token(command: str) -> str | None:
    lowered = command.lower()
    padded = f" {lowered} "
    for pattern, token in _MUTATING_PATTERNS:
        if pattern.search(command):
            return token
    for token in _MUTATING_TOKENS:
        needle = token.lower()
        haystack = padded if needle.endswith(" ") else lowered
        if needle in haystack:
            return token
    if re.search(r"(?:^|[&|; \t])(?:[0-9]*>>?|&>)\s*/", command):
        return "redirection to absolute path"
    return None
