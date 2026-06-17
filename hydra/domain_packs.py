"""Domain specialization packs for HydraAgent."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SCHEMA = "hydra.domain_pack.v1"


@dataclass(frozen=True)
class DomainPackError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


HYDRA_PACK: dict[str, Any] = {
    "schema": SCHEMA,
    "pack_id": "hydra",
    "name": "HydraAgent Operator Pack",
    "mission": (
        "Make HydraAgent useful on operator work by preserving targets, using real "
        "tools, checking evidence, and refusing narrated-only audits."
    ),
    "memory_sources": [
        "Hydra durable memory",
        "Hydra runtime notes",
        "Promoted evidence artifacts",
    ],
    "playbooks": [
        {
            "id": "runtime-audit",
            "title": "Runtime Audit",
            "trigger": "Operator asks to audit Hydra, heads, workers, logs, or runtime health.",
            "tools": ["audit", "locate", "glob", "grep", "list_directory", "bash", "remember"],
            "steps": [
                "Load relevant durable memory context.",
                "Pin the operator's requested target path for the whole turn.",
                "When the operator corrects the path, correct the pinned target and restart evidence collection.",
                "Do not keep auditing the old target after a correction.",
                "When the operator asks if a named runtime exists, run locate and audit the best located path on follow-up.",
                "Run deterministic audit before LLM interpretation.",
                "Search logs/config/state files with grep or glob.",
                "Report concrete files, commands, and uncertainties.",
                "When a failure pattern is proven, write a sourced durable lesson with remember.",
            ],
            "success_metric": "Audit cites real paths, live signals, best located path when relevant, and next repair targets without raw-output summarizing.",
            "verify": "Run `python3 -m hydra audit TARGET` and include structured evidence.",
        },
        {
            "id": "repo-surgery",
            "title": "Repo Surgery",
            "trigger": "Operator points Hydra at a repository and asks for a bug fix, refactor, or audit.",
            "tools": ["surgery", "status", "grep", "fs_read", "fs_edit", "bash", "trace-bundle", "remember"],
            "steps": [
                "Read repo truth before editing.",
                "Write or reuse a failing slice/test for the requested behavior.",
                "Patch the smallest code surface that satisfies the test.",
                "Run targeted verification and diff review.",
                "Run trace-bundle before final success on long repairs.",
                "Record a sourced durable lesson when the repair closes a repeatable failure mode.",
                "Summarize exact files changed and remaining risk.",
            ],
            "success_metric": "A reproducible test or audit artifact proves the requested repo behavior changed.",
            "verify": "Run the task-specific harness plus `python3 -m hydra status`; for long repairs run `python3 -m hydra trace-bundle` before final success.",
        },
        {
            "id": "research-synthesis",
            "title": "Research Synthesis",
            "trigger": "Operator asks for current external facts, agent architecture, tools, or coding-agent patterns.",
            "tools": ["http_fetch", "citation_checker", "grep", "fs_write", "remember"],
            "steps": [
                "Prefer official docs and primary repositories.",
                "Capture source URLs and what each source supports.",
                "Separate verified claims from inference.",
                "Write the resulting decision into a durable artifact when it affects runtime design.",
                "Use remember for a sourced durable lesson when the research changes operator behavior.",
            ],
            "success_metric": "Claims are tied to cited sources and uncertainty is explicit.",
            "verify": "Run citation checks or store source URLs in the report artifact.",
        },
        {
            "id": "local-ops",
            "title": "Local Ops",
            "trigger": "Operator asks about GPU models, Ollama, cloud fallback, setup, launch, or machine-local state.",
            "tools": ["models", "providers", "setup", "bash", "list_directory", "todo"],
            "steps": [
                "Check local provider/model availability before assuming.",
                "Respect unload-between-local-models policy when GPU cannot hold both models.",
                "Use cloud or local fallback only when operator config allows it.",
                "Require explicit operator approval for destructive or sudo actions unless the recorded policy allows yolo.",
                "Keep yolo runs interruptible through the stop command and report interrupted state.",
                "Keep secret values out of stdout and evidence.",
            ],
            "success_metric": "The operator receives a runnable command or clear blocked reason backed by local checks.",
            "verify": "Run `python3 -m hydra models --provider ollama` or provider-specific setup/status checks.",
        },
    ],
    "safety": [
        "Do not reveal API keys, OAuth contents, passwords, or token values.",
        "Do not claim current runtime health from stale memory; verify live files or commands.",
        "Do not delete, weaken, or skip tests to make a result look green.",
    ],
}

PACKS = {"hydra": HYDRA_PACK}


def get_domain_pack(pack_id: str) -> dict[str, Any]:
    key = pack_id.strip().lower()
    try:
        return PACKS[key]
    except KeyError as e:
        available = ", ".join(sorted(PACKS))
        raise DomainPackError(
            f"unknown domain pack {pack_id!r}; available packs: {available}"
        ) from e


def render_text(pack: dict[str, Any]) -> str:
    lines = [
        f"Hydra domain pack: {pack['pack_id']}",
        pack["name"],
        "",
        pack["mission"],
        "",
        "Playbooks",
    ]
    for playbook in pack["playbooks"]:
        lines.extend(
            [
                f"- {playbook['title']} ({playbook['id']})",
                f"  Trigger: {playbook['trigger']}",
                f"  Tools: {', '.join(playbook['tools'])}",
                f"  Verify: {playbook['verify']}",
                f"  Success: {playbook['success_metric']}",
            ]
        )
    lines.append("")
    lines.append("Safety")
    lines.extend(f"- {row}" for row in pack["safety"])
    return "\n".join(lines) + "\n"
