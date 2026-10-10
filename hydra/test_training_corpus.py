"""Real-file contracts for local supervised corpus preparation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from hydra.training_corpus import CorpusError, _has_secret, _tools, build_corpus


TOOL_BINDING = '''"""Read/search tool bindings."""
tools = [
    Tool(name="fs_read", description="Read a workspace file", parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}),
    Tool(name="grep", description="Search workspace files", parameters={"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern"]})
]
'''


def _write(root: Path, relative: str, text: str) -> bytes:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    target.write_bytes(data)
    return data


def _snapshot(root: Path, files: dict[str, str]) -> None:
    records = []
    for relative, text in files.items():
        data = _write(root, relative, text)
        records.append({"path": relative, "mode": "100644", "type": "blob",
                        "sha": hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()})
    (root / "SOURCE_MANIFEST.json").write_text(json.dumps({
        "repository": "owner/private", "revision": "a" * 40,
        "scope": "selected runtime snapshot", "files": records,
    }), encoding="utf-8")


@pytest.fixture
def sources(tmp_path):
    public = tmp_path / "public"
    private = tmp_path / "private"
    public.mkdir()
    _write(public, "hydra/cli/tool_binding.py", TOOL_BINDING)
    _write(public, "hydra/loop.py", '"""Agent loop orchestration."""\ndef run_agent():\n    pass\n')
    _write(public, "hydra/memory_kernel.py", '"""Persist knowledge."""\ndef remember():\n    pass\n')
    _write(public, "hydra/providers.py", '"""Provider routing."""\ndef select_provider():\n    pass\n')
    _write(public, "hydra/guardrails.py", '"""Permission boundaries."""\ndef authorize():\n    pass\n')
    _write(public, "README.md", "# Hydra product\nSetup and launch guide.\n")
    _write(public, ".env", "password=should_never_be_used")
    _write(public, "docs/connection.md", '# Connection\napi_key = "really-private-key-value"\n')
    for args in (["init"], ["add", "--all"], ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"]):
        subprocess.run(["git", "-C", str(public), *args], check=True, capture_output=True)
    _write(public, "hydra/untracked.py", '"""Untracked source must not enter a corpus."""\ndef hidden():\n    pass\n')
    _snapshot(private, {
        "hydra/loop.py": '"""Private agent orchestration revision."""\ndef run_agent():\n    pass\n',
        "hydra/renamed_memory.py": (public / "hydra/memory_kernel.py").read_text(encoding="utf-8"),
        "hydra/setup.py": '"""Set up the runtime."""\ndef configure():\n    pass\n',
    })
    return public, private


def test_corpus_is_typed_grounded_and_excludes_untracked_secrets(tmp_path, sources):
    public, private = sources
    out = tmp_path / "data"
    manifest = build_corpus(public, private, out)
    rows = [json.loads(line) for line in (out / "provenance.jsonl").read_text().splitlines()]
    source_paths = {alias["path"] for row in rows for alias in row["sources"]}
    assert "hydra/untracked.py" not in source_paths
    assert "docs/connection.md" not in source_paths
    assert ".env" not in source_paths
    assert manifest["duplicate_files_removed"] == 1
    assert manifest["external_network_calls"] == 0
    assert manifest["sources"][1]["inventory"] == "pinned_snapshot"
    for name in ("needle_train", "needle_eval"):
        for row in map(json.loads, (out / f"{name}.jsonl").read_text().splitlines()):
            assert set(row) == {"query", "tools", "answers"}
            for answer in row["answers"]:
                assert answer["name"] in {tool["name"] for tool in row["tools"]}
                assert answer["arguments"]["path"] in source_paths
    for name in ("laya_train", "laya_eval"):
        for row in map(json.loads, (out / f"{name}.jsonl").read_text().splitlines()):
            assert row["questions"]["area"]["type"] == "choice"
            assert row["expected"]["area"] in row["questions"]["area"]["criteria"]
    all_output = "".join(path.read_text(encoding="utf-8") for path in out.iterdir())
    assert "really-private-key-value" not in all_output


def test_duplicate_content_and_same_paths_never_cross_split(tmp_path, sources):
    out = tmp_path / "data"
    build_corpus(*sources, out)
    paths, digests, groups = {}, {}, {}
    for row in map(json.loads, (out / "provenance.jsonl").read_text().splitlines()):
        split = row["dataset"].split("_")[-1].split(".")[0]
        for mapping, keys in ((paths, [alias["path"] for alias in row["sources"]]),
                              (digests, [row["content_sha256"]]), (groups, [row["group"]])):
            for key in keys:
                assert mapping.setdefault(key, split) == split
    assert set(groups.values()) == {"train", "eval"}


def test_generation_is_deterministic(tmp_path, sources):
    first, second = tmp_path / "first", tmp_path / "second"
    build_corpus(*sources, first)
    build_corpus(*sources, second)
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()}


def test_output_cannot_be_inside_either_source(tmp_path, sources):
    for source in sources:
        with pytest.raises(CorpusError, match="separate"):
            build_corpus(*sources, source / "training-data")


def test_existing_nonempty_output_is_not_overwritten(tmp_path, sources):
    out = tmp_path / "data"
    out.mkdir()
    sentinel = out / "keep.txt"
    sentinel.write_text("keep")
    with pytest.raises(CorpusError, match="new or empty"):
        build_corpus(*sources, out)
    assert sentinel.read_text() == "keep"


def test_relative_sibling_output_is_allowed(tmp_path, sources, monkeypatch):
    monkeypatch.chdir(sources[0])
    manifest = build_corpus(Path("."), Path("../private"), Path("../data"))
    assert manifest["rows"]["needle_train"] > 0
    assert (tmp_path / "data" / "manifest.json").is_file()


def test_snapshot_symlink_mode_is_excluded(tmp_path, sources):
    private = sources[1]
    manifest_path = private / "SOURCE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["mode"] = "120000"
    manifest_path.write_text(json.dumps(manifest))
    manifest = build_corpus(*sources, tmp_path / "data")
    assert manifest["sources"][1]["excluded"]["non_regular_git_mode"] == 1


def test_changed_snapshot_blob_is_refused(tmp_path, sources):
    _write(sources[1], "hydra/loop.py", '"""Changed without manifest update."""\n')
    with pytest.raises(CorpusError, match="hash mismatch"):
        build_corpus(*sources, tmp_path / "data")


def test_schema_file_change_after_inventory_is_refused(sources):
    with pytest.raises(CorpusError, match="changed during"):
        _tools(sources[0], "0" * 64)


@pytest.mark.parametrize("secret", [
    "-----BEGIN PRIVATE KEY-----", "ghp_" + "A" * 36,
    "sk-proj-" + "a" * 40, 'password = "actual-password-value"',
])
def test_secret_literals_are_excluded(secret):
    assert _has_secret(secret)
