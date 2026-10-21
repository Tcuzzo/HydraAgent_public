"""Verify generated selector examples and exact bundled bytes after checkout."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_generated_grep_training_label_finds_its_actual_source_symbol(tmp_path):
    from hydra.cli import tool_binding
    from hydra.training_corpus import build_corpus
    source = tmp_path / "source"
    (source / "hydra/cli").mkdir(parents=True)
    items = {"hydra/cli/tool_binding.py": Path(tool_binding.__file__).read_bytes(),
             "hydra/example.py": b'def expected_symbol():\n    return 42\n'}
    manifest = {"revision": "0" * 40, "repository": "synthetic-test-fixture", "files": []}
    for name, data in items.items():
        (source / name).write_bytes(data)
        sha = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        manifest["files"].append({"path": name, "type": "blob", "mode": "100644", "sha": sha})
    (source / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest))
    out = tmp_path / "corpus"
    build_corpus(source, None, out)
    rows = [json.loads(line) for split in ("train", "eval")
            for line in (out / f"needle_{split}.jsonl").read_text().splitlines()]
    label = next(row["answers"][0] for row in rows if row["answers"]
                 and row["answers"][0]["name"] == "grep"
                 and row["answers"][0]["arguments"]["path"] == "hydra/example.py")
    tool = next(t for t in tool_binding.bind_tools(source, approval_policy="deny", include_mcp=False)
                if t.name == "grep")
    result = tool.invoke(**label["arguments"])
    assert result["files_scanned"] == 1 and result["count"] == 1
    assert "expected_symbol" in result["matches"][0]["line"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_windows_style_checkout_preserves_bundled_library_hashes(tmp_path):
    from hydra.specialists import LIBRARY_ROOT
    repository = Path(__file__).resolve().parents[1]
    relative = LIBRARY_ROOT.relative_to(repository)
    target = tmp_path / "checkout"
    shutil.copytree(LIBRARY_ROOT, target / relative)
    attributes = repository / ".gitattributes"
    if attributes.exists():
        shutil.copyfile(attributes, target / ".gitattributes")
    def git(*args):
        return subprocess.run(["git", "-c", "core.autocrlf=true", "-c", "core.safecrlf=false",
            *args], cwd=target, check=True, capture_output=True, text=True, timeout=15)
    git("init", "--quiet")
    git("add", ".")
    # The index alone is sufficient to exercise Git's checkout byte conversion.
    for path in (target / relative).glob("*.md"):
        path.unlink()
    git("checkout", "--", ".")
    manifest = json.loads((target / relative / "manifest.json").read_text())
    for row in manifest["entries"]:
        actual = hashlib.sha256((target / relative / row["file"]).read_bytes()).hexdigest()
        assert actual == row["sha256"], row["file"]
