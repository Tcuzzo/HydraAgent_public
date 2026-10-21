"""Real persistence contracts for source refresh and evidence-grounded learning.

Vectors are deterministic test inputs; these tests claim persistence, filtering,
and next-run context delivery, not natural-language embedding accuracy.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hydra import semantic_recall as recall
from hydra.lessons import remember_lesson
from hydra.unified_memory import UnifiedMemory, vec0_unavailable_reason


requires_vec = pytest.mark.skipif(vec0_unavailable_reason() is not None,
                                  reason="sqlite-vec unavailable")


def vector(text):
    return [1.0, 0.0]


def corpus(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    root.mkdir()
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.setattr(recall, "_default_store_path", lambda: tmp_path / "store.sqlite")
    return root, workspace


@requires_vec
def test_remembered_lesson_enters_next_recall_without_manual_migration(tmp_path, monkeypatch):
    root, workspace = corpus(tmp_path, monkeypatch)
    remember_lesson("The argon bridge uses transport tango.", source="operator", memory_root=root)
    result = recall.build_semantic_memory_context("argon bridge", root=root,
                                                   workspace_root=workspace, embedder=vector)
    assert "transport tango" in result.context
    assert result.data["backend"] == "unified"
    assert result.data["chunks_returned"] >= 1


@requires_vec
def test_edited_and_removed_sources_stop_appearing_without_erasing_audit(tmp_path, monkeypatch):
    root, workspace = corpus(tmp_path, monkeypatch)
    path = root / "workspace/MEMORY.md"
    path.parent.mkdir()
    path.write_text("argon uses obsolete transport")
    recall.build_semantic_memory_context("argon", root=root, workspace_root=workspace, embedder=vector)
    path.write_text("argon uses corrected transport")
    refreshed = recall.build_semantic_memory_context("argon", root=root, workspace_root=workspace, embedder=vector)
    assert "corrected transport" in refreshed.context
    assert "obsolete transport" not in refreshed.context
    path.unlink()
    empty = recall.build_semantic_memory_context("argon", root=root, workspace_root=workspace, embedder=vector)
    assert empty.context == ""
    with UnifiedMemory(path=tmp_path / "store.sqlite", embedder=vector) as store:
        assert store._db.execute("SELECT COUNT(*) FROM entries_history").fetchone()[0] >= 2


def test_embedding_outage_keeps_relevant_lexical_memory_and_no_raw_dump(tmp_path, monkeypatch):
    root, workspace = corpus(tmp_path, monkeypatch)
    path = root / "workspace/MEMORY.md"
    path.parent.mkdir()
    path.write_text("# Cooking\nA cake recipe contains eggs.\n\n# Argon\nargon bridge uses tango.\n")
    def unavailable(text):
        raise RuntimeError("embedding service unavailable")
    result = recall.build_semantic_memory_context("argon bridge", root=root,
                                                   workspace_root=workspace, embedder=unavailable)
    assert "uses tango" in result.context
    assert "cake recipe" not in result.context
    assert result.data["fallback"] is True
    assert result.data["backend"] == "lexical"


@requires_vec
def test_recall_budget_is_a_hard_limit_including_header(tmp_path, monkeypatch):
    root, workspace = corpus(tmp_path, monkeypatch)
    remember_lesson("argon " * 100, source="operator", memory_root=root)
    for budget in (0, 50, 500, 1000):
        result = recall.build_semantic_memory_context("argon", root=root, workspace_root=workspace,
                                                       max_chars=budget, embedder=vector)
        assert len(result.context) <= budget


def test_truth_context_skips_oversized_candidate_and_respects_small_budget(tmp_path):
    from hydra.memory_kernel import assemble_truth_context
    wiki = tmp_path / ".hydraAgent/wiki"
    wiki.mkdir(parents=True)
    (wiki / "long.md").write_text("# Argon long\n" + "argon " * 2000)
    (wiki / "short.md").write_text("# Argon concise\nargon transport works")
    result = assemble_truth_context("argon", repo_root=tmp_path, memory_root=tmp_path / "empty",
                                    budget_chars=500)
    assert "transport works" in result["text"]
    assert len(result["text"]) <= 500


def test_context_assembler_consumes_memory_result_dataclass(tmp_path):
    from hydra.context_engine_v2 import ContextAssembler
    repo = tmp_path / "project"
    repo.mkdir()
    memory = tmp_path / "memory"
    remember_lesson("Argon bridge requires tango.", source="operator", memory_root=memory)
    items = ContextAssembler(repo_root=repo, memory_root=memory)._retrieve_relevant_context("Argon bridge", None, 10)
    assert any(item.source == "lesson" and "requires tango" in item.content for item in items)


@requires_vec
def test_scope_filter_applies_before_top_k_pool(tmp_path):
    with UnifiedMemory(path=tmp_path / "store.sqlite", embedder=vector) as store:
        for index in range(40):
            store.add(f"argon foreign {index}", scope="other")
        target = store.add("argon local knowledge", scope="wanted")
        hits = store.search("argon", scope="wanted", top_k=1)
        assert [h.memory_id for h in hits] == [target]


def test_execution_learning_is_local_retrievable_and_never_certifies_narrative(tmp_path, monkeypatch):
    from hydra.execution_learning import record_execution_lesson
    from hydra.loop import LoopResult, LoopStep
    root, workspace = corpus(tmp_path, monkeypatch)
    result = LoopResult(steps=[LoopStep(step=1, kind="tool_result", role="tool", tool_name="fs_read",
        tool_arguments={"path": "bridge.py"}, content=json.dumps({"ok": True, "content": "argon transport tango"}))],
        final_response="I proved the entire application flawless.", iterations=2, tool_calls_made=1,
        halted_reason="natural", messages=[])
    receipt = record_execution_lesson(repo_root=workspace, memory_root=root, goal="inspect argon bridge",
                                      result=result, provider="test", model="deterministic-test")
    assert receipt["status"] == "recorded"
    saved = json.loads(Path(receipt["path"]).read_text())
    assert saved["quality_verified"] is False
    assert "flawless" not in json.dumps(saved)
    assert saved["nodes"] and saved["edges"]
    def unavailable(text):
        raise RuntimeError("offline")
    recalled = recall.build_semantic_memory_context("argon bridge", root=root,
        workspace_root=workspace, embedder=unavailable)
    assert "bridge.py" in recalled.context
    assert "execution" in recalled.context.lower()
    assert "quality_verified" in recalled.context
    other = tmp_path / "other-project"
    other.mkdir()
    isolated = recall.build_semantic_memory_context("argon bridge", root=root,
        workspace_root=other, embedder=unavailable)
    assert "bridge.py" not in isolated.context


def test_failed_execution_is_retained_as_negative_memory(tmp_path):
    from hydra.execution_learning import record_execution_lesson
    from hydra.loop import LoopResult, LoopStep
    result = LoopResult(steps=[LoopStep(step=1, kind="tool_result", role="tool", tool_name="bash",
        tool_arguments={"command": "python missing.py"}, content=json.dumps({"exit_code": 2}),
        tool_error="failed")], final_response="Fixed", iterations=1, tool_calls_made=1,
        halted_reason="natural", messages=[])
    receipt = record_execution_lesson(repo_root=tmp_path, memory_root=tmp_path / "memory", goal="argon repair",
                                      result=result, provider="test", model="test")
    record = json.loads(Path(receipt["path"]).read_text())
    assert record["outcome"] == "failed"
    assert record["observations"][0]["outcome"] == "failed"
    assert record["quality_verified"] is False
