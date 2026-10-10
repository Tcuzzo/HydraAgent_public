"""Build local, source-grounded Needle/Laya navigation training data.

This creates starter tool-routing and subsystem labels, not coding solutions or
proof of coding competence. Private derived data is written only outside both
source repositories. No network, model inference, or training happens here.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any

MAX_FILE_BYTES = 512 * 1024
SCHEMA = "hydra.training_corpus.v1"
AREAS = {
    "models": "Provider clients, model selection, routing and model configuration",
    "memory": "Persistent memory, recall, lessons and conversation storage",
    "tools": "Filesystem, shell, process execution and tool dispatch",
    "safety": "Approval, identity, authorization, trust and injection safeguards",
    "setup": "Installation, launch, diagnostics and environment setup",
    "runtime": "Agent loops, missions, workers, monitoring and runtime coordination",
    "documentation": "Product guides and reference documentation",
}
EXCLUDED_PARTS = {
    ".git", ".ssh", ".aws", ".azure", ".gnupg", ".venv", "venv",
    "node_modules", "__pycache__", "secrets", "credentials", "auth",
    "evidence", "sessions", "logs", "outputs", "dist", "build",
}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|access[_-]?token|client[_-]?secret)\b\s*[:=]\s*[\"']([^\"'\r\n]{8,})[\"']"),
)
PLACEHOLDER_MARKERS = ("example", "placeholder", "your_", "your-", "changeme", "dummy", "test-", "<", "${")


class CorpusError(ValueError):
    """The source inventory or requested output is unsafe or incomplete."""


@dataclass
class SourceFile:
    repository: str
    revision: str
    path: str
    content_hash: str
    blob_sha: str
    modified: bool
    summary: str
    symbol: str | None
    area: str


def _git(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CorpusError(f"cannot inventory Git source {root}: {exc}") from exc
    if result.returncode:
        raise CorpusError(f"cannot inventory Git source {root}: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def _inventory(root: Path, label: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    manifest_path = root / "SOURCE_MANIFEST.json"
    if manifest_path.is_file() and not (root / ".git").exists():
        if manifest_path.is_symlink():
            raise CorpusError("snapshot manifest must not be a symlink")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        revision = manifest.get("revision", "")
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise CorpusError("snapshot revision must be a pinned 40-character commit SHA")
        if not isinstance(manifest.get("files"), list):
            raise CorpusError("snapshot manifest must list files")
        items = [dict(item) for item in manifest["files"] if item.get("type") == "blob"]
        return {
            "label": label, "repository": manifest.get("repository", label),
            "revision": revision, "inventory": "pinned_snapshot",
            "scope": manifest.get("scope", "selected files; not a full clone"),
            "snapshot_failures": manifest.get("failures", []),
        }, items
    revision = _git(root, "rev-parse", "HEAD").decode().strip()
    items = []
    for record in _git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        header, raw_path = record.split(b"\t", 1)
        mode, sha, stage = header.decode("ascii").split()
        if stage != "0":
            raise CorpusError("resolve Git index conflicts before generating training data")
        items.append({"path": raw_path.decode("utf-8"), "mode": mode, "sha": sha})
    return {"label": label, "repository": label, "revision": revision,
            "inventory": "git_tracked_working_tree", "scope": "tracked source files; working changes fingerprinted"}, items


def _eligible_path(raw: str) -> bool:
    path = PurePosixPath(raw)
    if (path.is_absolute() or ".." in path.parts or "\\" in raw or ":" in raw
            or not path.parts or any(part.lower() in EXCLUDED_PARTS for part in path.parts)):
        return False
    lower = path.name.lower()
    if (lower.startswith(".env") or lower.startswith("test_")
            or any(marker in lower for marker in ("secret", "credential", "password", "token"))):
        return False
    if path.suffix == ".py":
        return path.parts[0] in {"hydra", "skills", "core", "gateways"}
    return path.suffix.lower() == ".md" and (len(path.parts) == 1 or path.parts[0] == "docs")


def _has_secret(text: str) -> bool:
    for index, pattern in enumerate(SECRET_PATTERNS):
        for match in pattern.finditer(text):
            value = match.group(1) if match.lastindex else match.group()
            if index < 3 or not any(marker in value.lower() for marker in PLACEHOLDER_MARKERS):
                return True
    return False


def _area(path: str) -> str:
    name = PurePosixPath(path).stem.lower()
    if path.endswith(".md"):
        return "documentation"
    if any(word in name for word in ("memory", "recall", "lesson")):
        return "memory"
    if any(word in name for word in ("guardrail", "policy", "auth", "injection", "trust")):
        return "safety"
    if path.startswith("skills/") or name in {"proc", "exec_backend", "container_sandbox", "file_lock", "atomic_write", "tool_binding", "tool_bridge"}:
        return "tools"
    if any(word in name for word in ("model", "provider", "llm", "routing", "runtime_route")):
        return "models"
    if any(word in name for word in ("setup", "doctor", "environment", "locate", "__main__")):
        return "setup"
    return "runtime"


def _describe(path: str, text: str) -> tuple[str, str | None]:
    if path.endswith(".py"):
        tree = ast.parse(text)
        summary = ast.get_docstring(tree) or ""
        symbols = [node for node in tree.body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                   and not node.name.startswith("_")]
        symbol = symbols[0].name if symbols else None
        summary = summary.split("\n\n", 1)[0]
        return " ".join(summary.split())[:350], symbol
    heading = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")), "")
    return heading[:350], None


def _load_source(root: Path, label: str) -> tuple[dict[str, Any], list[SourceFile], Counter]:
    metadata, items = _inventory(root, label)
    rejected: Counter = Counter()
    files = []
    for item in sorted(items, key=lambda item: item.get("path", "")):
        relative = item.get("path", "")
        if not _eligible_path(relative):
            rejected["excluded_path_or_file_type"] += 1
            continue
        if item.get("mode") not in {"100644", "100755"}:
            rejected["non_regular_git_mode"] += 1
            continue
        path = root / relative
        chain = [path, *path.parents]
        if any(part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction())
               for part in chain if part != root and part.is_relative_to(root)):
            rejected["symlink_or_junction"] += 1
            continue
        if not path.resolve().is_relative_to(root):
            rejected["outside_source"] += 1
            continue
        try:
            with path.open("rb") as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                rejected["oversized"] += 1
                continue
            text = content.decode("utf-8-sig")
        except (OSError, UnicodeError):
            rejected["unreadable_or_non_utf8"] += 1
            continue
        blob_sha = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
        if metadata["inventory"] == "pinned_snapshot" and blob_sha != item.get("sha"):
            raise CorpusError(f"snapshot blob hash mismatch for {label}:{relative}")
        if _has_secret(text):
            rejected["secret_like_content"] += 1
            continue
        try:
            summary, symbol = _describe(relative, text)
        except (SyntaxError, ValueError):
            rejected["unparseable_source"] += 1
            continue
        if not summary and not symbol:
            rejected["no_navigation_evidence"] += 1
            continue
        files.append(SourceFile(label, metadata["revision"], relative,
                                hashlib.sha256(content).hexdigest(), blob_sha,
                                blob_sha != item.get("sha"), summary, symbol, _area(relative)))
    metadata.update(inventoried_files=len(items), selected_files=len(files), excluded=dict(rejected))
    return metadata, files, rejected


def _tools(public_root: Path, expected_hash: str) -> list[dict[str, Any]]:
    """Read literal runtime schemas without importing or executing repository code."""
    path = public_root / "hydra/cli/tool_binding.py"
    if path.is_symlink() or not path.resolve().is_relative_to(public_root):
        raise CorpusError("tool schema source must stay inside the public repository")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise CorpusError("tool schema source changed during corpus generation; retry from a stable checkout")
    tree = ast.parse(content.decode("utf-8-sig"))
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "Tool":
            continue
        fields = {keyword.arg: keyword.value for keyword in node.keywords}
        try:
            name = ast.literal_eval(fields["name"])
            if name not in {"fs_read", "grep", "glob", "list_directory"}:
                continue
            found[name] = {key: ast.literal_eval(fields[key]) for key in ("name", "description", "parameters")}
        except (KeyError, ValueError, TypeError):
            continue
    if "fs_read" not in found:
        raise CorpusError("public tool_binding.py must declare a literal fs_read schema")
    return [found[name] for name in sorted(found)]


def _groups(files: list[SourceFile]) -> dict[str, str]:
    """Union same paths and same bytes, including aliases across repositories."""
    parent = list(range(len(files)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    seen = {}
    for index, source in enumerate(files):
        for key in (("path", source.path), ("content", source.content_hash)):
            if key in seen:
                parent[find(index)] = find(seen[key])
            else:
                seen[key] = index
    members = defaultdict(list)
    for index, source in enumerate(files):
        members[find(index)].append(source)
    result = {}
    for members_in_group in members.values():
        # Path-based IDs keep a file's split stable as its content changes.
        paths = sorted({source.path for source in members_in_group})
        group = hashlib.sha256("\n".join(paths).encode()).hexdigest()
        for source in members_in_group:
            result[source.content_hash] = group
    return result


def _dump_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_corpus(public_repo: Path, private_repo: Path, out: Path, *, eval_fraction: float = 0.2) -> dict[str, Any]:
    if not 0 < eval_fraction < 1:
        raise CorpusError("eval_fraction must be between zero and one")
    # Some Windows restricted filesystems preserve '..' for nonexistent leaves
    # in resolve(); normalize the resolved string before component comparisons.
    public_repo, private_repo, out = (Path(os.path.normpath(Path(path).expanduser().resolve()))
                                      for path in (public_repo, private_repo, out))
    if public_repo == private_repo:
        raise CorpusError("public and private sources must be distinct")
    if any(out.is_relative_to(source) or source.is_relative_to(out) for source in (public_repo, private_repo)):
        raise CorpusError("output must be separate from both source repositories")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise CorpusError("output directory must be new or empty")
    inventories, files = [], []
    for root, label in ((public_repo, "public"), (private_repo, "private")):
        metadata, selected, _ = _load_source(root, label)
        if not selected:
            raise CorpusError(f"no eligible, source-grounded files in {label} repository")
        inventories.append(metadata)
        files.extend(selected)
    schema_source = next((source for source in files if source.repository == "public"
                          and source.path == "hydra/cli/tool_binding.py"), None)
    if schema_source is None:
        raise CorpusError("tool schema source must be an eligible, tracked public file")
    tools = _tools(public_repo, schema_source.content_hash)
    tool_names = {tool["name"] for tool in tools}
    groups = _groups(files)
    unique_groups = sorted(set(groups.values()))
    if len(unique_groups) < 2:
        raise CorpusError("need at least two independent file groups for train/eval separation")
    splits = {group: ("eval" if int(group[:8], 16) / 2**32 < eval_fraction else "train") for group in unique_groups}
    # Preserve nonempty train/eval sets even for very small smoke corpora.
    if len(set(splits.values())) < 2:
        splits[unique_groups[0]] = "eval"
        splits[unique_groups[-1]] = "train"
    aliases = defaultdict(list)
    representatives = {}
    for source in files:  # public-first ensures identical public/private bytes use public prose
        aliases[source.content_hash].append(source)
        representatives.setdefault(source.content_hash, source)
    datasets = {(model, split): [] for model in ("needle", "laya") for split in ("train", "eval")}
    provenance = []

    def add(model, split, row, source, task):
        row_index = len(datasets[model, split])
        datasets[model, split].append(row)
        provenance.append({
            "dataset": f"{model}_{split}.jsonl", "row": row_index, "task": task,
            "group": groups[source.content_hash], "content_sha256": source.content_hash,
            "label_method": "deterministic source navigation; not human-authored coding solutions",
            "sources": [{"repository": alias.repository, "revision": alias.revision,
                         "path": alias.path, "git_blob_sha": alias.blob_sha,
                         "working_tree_modified": alias.modified} for alias in aliases[source.content_hash]],
        })

    for digest, source in sorted(representatives.items(), key=lambda pair: (pair[1].path, pair[1].repository)):
        split = splits[groups[digest]]
        query = f"Read '{source.path}' in the Hydra {source.repository} workspace so I can inspect it."
        add("needle", split, {"query": query, "tools": tools,
                             "answers": [{"name": "fs_read", "arguments": {"path": source.path}}]}, source, "read_path")
        if source.symbol and "grep" in tool_names:
            add("needle", split, {
                "query": f"Search '{source.path}' for the symbol '{source.symbol}' in the Hydra {source.repository} workspace.",
                "tools": tools, "answers": [{"name": "grep", "arguments": {"pattern": source.symbol, "path": source.path}}],
            }, source, "find_declared_symbol")
        # Unsupported action; none of the supplied read/search tools sends email.
        add("needle", split, {"query": f"Email '{source.path}' to a teammate using the available tools.",
                             "tools": tools, "answers": []}, source, "unsupported_tool_refusal")
        state = f"Hydra {source.repository} workspace file: {source.path}\n"
        if source.summary:
            state += f"Source module documentation: {source.summary}\n"
        if source.symbol:
            state += f"Verified top-level declaration: {source.symbol}\n"
        add("laya", split, {
            "state": state,
            "questions": {"area": {"type": "choice", "instructions": "Choose the subsystem responsible for this source file.", "criteria": AREAS}},
            "expected": {"area": source.area},
        }, source, "subsystem_navigation")

    out.mkdir(parents=True, exist_ok=True)
    file_hashes = {}
    for (model, split), rows in datasets.items():
        name = f"{model}_{split}.jsonl"
        data = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")
        (out / name).write_bytes(data)
        file_hashes[name] = hashlib.sha256(data).hexdigest()
    (out / "provenance.jsonl").write_text("".join(json.dumps(row) + "\n" for row in provenance), encoding="utf-8")
    manifest = {
        "schema": SCHEMA, "purpose": "Starter tool routing and repository subsystem navigation",
        "limitations": ["Not a code-generation or bug-fixing benchmark", "Labels are derived from paths, AST declarations and module documentation",
                        "Private snapshot coverage may be partial", "Secret filtering is conservative and cannot guarantee detection of every secret"],
        "privacy": "Local derived private data: do not commit, upload or publish this directory or its trained adapters",
        "sources": inventories, "selected_files_before_dedup": len(files),
        "unique_content_files": len(representatives), "duplicate_files_removed": len(files) - len(representatives),
        "split_policy": "Same relative paths and identical content across repositories share one deterministic group",
        "eval_fraction_requested": eval_fraction,
        "groups": dict(Counter(splits.values())),
        "rows": {f"{model}_{split}": len(rows) for (model, split), rows in datasets.items()},
        "dataset_sha256": file_hashes,
        "tool_schema_source": "public:hydra/cli/tool_binding.py (literal AST extraction)",
        "tool_schema_source_sha256": schema_source.content_hash,
        "tool_schema_sha256": hashlib.sha256(json.dumps(tools, sort_keys=True).encode()).hexdigest(),
        "external_network_calls": 0,
    }
    _dump_json(out / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-repo", type=Path, required=True)
    parser.add_argument("--private-repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--eval-fraction", type=float, default=0.2)
    args = parser.parse_args(argv)
    try:
        manifest = build_corpus(args.public_repo, args.private_repo, args.out, eval_fraction=args.eval_fraction)
    except (CorpusError, OSError, ValueError) as exc:
        parser.exit(2, f"training corpus: {exc}\n")
    print(json.dumps({"output": os.path.normpath(args.out.resolve()), "rows": manifest["rows"], "groups": manifest["groups"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
