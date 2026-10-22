"""Evidence-backed truth memory kernel for Hydra."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from hydra.lessons import LESSON_RELATIVE_PATH
from hydra.wiki_memory import WIKI_ROOT


INDEX_SCHEMA = "hydra.memory_kernel.index.v1"
CONTEXT_SCHEMA = "hydra.memory_kernel.context.v1"
QUALITY_SCHEMA = "hydra.memory_kernel.quality.v1"
BRIEFING_SCHEMA = "hydra.memory_kernel.briefing.v1"
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer|credential|oauth|password|secret|token)\b\s*[:=]\s*\S+"
)
TOKEN_RE = re.compile(r"[A-Za-z0-9_§\.\-]{2,}")


def build_memory_index(*, repo_root: Path, memory_root: Path | None = None) -> dict[str, Any]:
    repo = Path(repo_root).expanduser().resolve()
    memory = Path(memory_root).expanduser().resolve() if memory_root else Path.home() / ".hydra-memory"
    records: list[dict[str, Any]] = []
    records.extend(_lesson_records(memory, repo))
    records.extend(_wiki_records(repo))
    records.extend(_mission_evidence_records(repo))
    records.extend(_source_receipt_records(repo))
    records.sort(key=lambda row: (row["kind"], row["title"], row["provenance"]["path"]))
    return {
        "schema": INDEX_SCHEMA,
        "repo_root": str(repo),
        "memory_root": str(memory),
        "records_count": len(records),
        "records": records,
        "policy": "evidence-backed records only; redacts secret-like assignments; no credential lanes",
    }


def truth_memory_quality_report(
    *,
    repo_root: Path,
    memory_root: Path | None = None,
) -> dict[str, Any]:
    index = build_memory_index(repo_root=repo_root, memory_root=memory_root)
    repo = Path(repo_root).expanduser().resolve()
    checks = [
        _quality_check(
            "index-schema",
            index.get("schema") == INDEX_SCHEMA,
            f"schema={index.get('schema')}",
        ),
        _quality_check(
            "records-present",
            bool(index["records"]),
            f"records={len(index['records'])}",
        ),
        _quality_check(
            "records-have-provenance",
            all(_provenance_path(record) for record in index["records"]),
            "every record must carry a source path",
        ),
        _quality_check(
            "records-have-claims",
            all(str(record.get("claim", "")).strip() for record in index["records"]),
            "every record must carry a non-empty claim",
        ),
        _quality_check(
            "records-redacted-secrets",
            not SECRET_VALUE_RE.search(json.dumps(index["records"], sort_keys=True)),
            "secret-like assignments must not survive indexing",
        ),
        _quality_check(
            "provenance-paths-resolve",
            all(_path_exists(_provenance_path(record), repo) for record in index["records"]),
            "each provenance path must resolve on disk",
        ),
    ]
    verdict = "GREEN" if all(check["status"] == "PASS" for check in checks) else "RED"
    return {
        "schema": QUALITY_SCHEMA,
        "verdict": verdict,
        "records_count": len(index["records"]),
        "checks": checks,
        "records": index["records"],
    }


def assemble_truth_context(
    query: str,
    *,
    repo_root: Path,
    memory_root: Path | None = None,
    budget_chars: int = 6000,
) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    index = build_memory_index(repo_root=repo_root, memory_root=memory_root)
    tokens = _tokens(query)
    scored = []
    for record in index["records"]:
        score = _score(record["claim"], tokens) + _score(record["title"], tokens)
        if score:
            scored.append((score, record))
    scored.sort(key=lambda item: (-item[0], item[1]["kind"], item[1]["title"]))
    selected: list[dict[str, Any]] = []
    lines = [
        "Evidence-backed Hydra memory context.",
        "Use only as prior memory; verify live state before claiming current facts.",
        "",
    ]
    remaining = max(1000, budget_chars) - sum(len(line) + 1 for line in lines)
    for score, record in scored:
        block = (
            f"- [{record['kind']}] {record['title']}\n"
            f"  claim: {record['claim']}\n"
            f"  source: {record['provenance']['path']}\n"
        )
        if len(block) > remaining:
            break
        lines.append(block.rstrip())
        selected.append({**record, "score": score})
        remaining -= len(block)
    return {
        "schema": CONTEXT_SCHEMA,
        "query": query,
        "tokens": tokens,
        "records": selected,
        "text": "\n".join(lines).strip() + "\n",
        "proof": [
            f"records_indexed={index['records_count']}",
            f"records_selected={len(selected)}",
            f"budget_chars={budget_chars}",
        ],
    }


def assemble_memory_briefing(
    query: str,
    *,
    repo_root: Path,
    memory_root: Path | None = None,
    budget_chars: int = 6000,
) -> dict[str, Any]:
    quality = truth_memory_quality_report(repo_root=repo_root, memory_root=memory_root)
    context = assemble_truth_context(
        query,
        repo_root=repo_root,
        memory_root=memory_root,
        budget_chars=budget_chars,
    )
    gaps = []
    if quality["verdict"] != "GREEN":
        gaps.append("quality-verdict-not-green")
    if not context["records"]:
        gaps.append("no-records-selected")
    return {
        "schema": BRIEFING_SCHEMA,
        "query": query,
        "quality": {
            "schema": quality["schema"],
            "verdict": quality["verdict"],
            "records_count": quality["records_count"],
            "failed_checks": [check for check in quality["checks"] if check["status"] != "PASS"],
        },
        "selected_count": len(context["records"]),
        "selected_records": context["records"],
        "context_text": context["text"],
        "gaps": gaps,
        "operator_rule": "Use this memory as sourced prior context only; verify live repo state before claiming current facts.",
        "proof": [*context["proof"], f"quality_verdict={quality['verdict']}"],
    }


def _lesson_records(memory_root: Path, repo_root: Path) -> list[dict[str, Any]]:
    path = memory_root / LESSON_RELATIVE_PATH
    if not path.is_file():
        return []
    text = _read_text(path)
    entries = re.split(r"\n(?=## 20\d{2}-)", text)
    return [
        _record("lesson", entry.splitlines()[0].lstrip("# "), _redact(entry), path, repo_root)
        for entry in entries
        if entry.strip().startswith("## ")
    ]


def _wiki_records(repo_root: Path) -> list[dict[str, Any]]:
    root = repo_root / WIKI_ROOT
    if not root.is_dir():
        return []
    records = []
    for path in sorted(root.rglob("*.md")):
        if path.name == "index.md":
            continue
        text = _read_text(path)
        title = _first_heading(text) or path.stem
        records.append(_record("wiki", title, _redact(text), path, repo_root))
    return records


def _mission_evidence_records(repo_root: Path) -> list[dict[str, Any]]:
    root = repo_root / "evidence" / "missions"
    if not root.is_dir():
        return []
    records = []
    for path in sorted(root.glob("*/mission_loop.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        mission = payload.get("mission", {})
        summary = payload.get("summary", {})
        claim = f"mission={mission.get('operator_prompt', path.parent.name)} verdict={summary.get('verdict')} evidence={payload.get('evidence_path')}"
        records.append(_record("mission_evidence", path.parent.name, _redact(claim), path, repo_root))
    return records


def _source_receipt_records(repo_root: Path) -> list[dict[str, Any]]:
    root = repo_root / "evidence" / "source-reengineering"
    if not root.is_dir():
        return []
    records = []
    for path in sorted(root.glob("*/receipts.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            title = f"{payload.get('system_id', path.parent.name)} receipt"
            claim = f"{payload.get('learned_pattern', '')} Hydra feature: {payload.get('hydra_feature', '')} Eval: {payload.get('eval_path', '')}"
            records.append(_record("source_receipt", title, _redact(claim), path, repo_root))
    return records


def _record(kind: str, title: str, claim: str, path: Path, repo_root: Path) -> dict[str, Any]:
    return {
        "kind": kind,
        "title": _single_line(title)[:160],
        "claim": _single_line(claim)[:1200],
        "provenance": {"path": _relative(path, repo_root)},
    }


def _quality_check(check_id: str, passed: bool, detail: str) -> dict[str, str]:
    return {
        "id": check_id,
        "status": "PASS" if passed else "FAIL",
        "detail": detail,
    }


def _provenance_path(record: dict[str, Any]) -> str:
    provenance = record.get("provenance", {})
    if not isinstance(provenance, dict):
        return ""
    return str(provenance.get("path", "")).strip()


def _path_exists(path_text: str, repo_root: Path) -> bool:
    if not path_text:
        return False
    path = Path(path_text)
    if path.is_absolute():
        return path.exists()
    return (repo_root / path).exists()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _redact(text: str) -> str:
    return SECRET_VALUE_RE.sub("[redacted secret]", text)


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _single_line(text: str) -> str:
    return " ".join(str(text).split())


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _tokens(text: str) -> list[str]:
    seen = set()
    out = []
    for match in TOKEN_RE.finditer(text.lower()):
        token = match.group(0)
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _score(text: str, tokens: list[str]) -> int:
    lower = text.lower()
    return sum(lower.count(token) for token in tokens)
