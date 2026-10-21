"""Adversarial persistence and provenance cases for runtime memory."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from hydra.unified_memory import BackendUnavailable, UnifiedMemory, vec0_unavailable_reason


def test_recent_lessons_are_discoverable_beyond_the_old_file_prefix(tmp_path):
    from hydra.lessons import remember_lesson
    from hydra.semantic_recall import build_semantic_memory_context
    memory = tmp_path / "memory"
    for index in range(40):
        remember_lesson("historical irrelevant filler " * 20, source=f"old-{index}", memory_root=memory)
    remember_lesson("zircon cache must be invalidated on schema update", source="new finding", memory_root=memory)
    def offline(text):
        raise RuntimeError("offline")
    context = build_semantic_memory_context("zircon cache", root=memory,
        workspace_root=tmp_path, embedder=offline).context
    assert "zircon cache must be invalidated" in context


@pytest.mark.skipif(vec0_unavailable_reason() is not None, reason="sqlite-vec unavailable")
def test_parallel_identical_writes_are_idempotent_and_complete(tmp_path):
    path = tmp_path / "memory.sqlite"
    with UnifiedMemory(path=path, embedder=lambda _: [1., 0.]):
        pass
    barrier = Barrier(4)
    def embedding(text):
        if text.startswith("search_document:"):
            barrier.wait(timeout=5)
        return [1., 0.]
    def write(_):
        with UnifiedMemory(path=path, embedder=embedding) as store:
            return store.add("zircon shared memory")
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(write, range(4)))
    assert len(set(ids)) == 1
    with UnifiedMemory(path=path, embedder=lambda _: [1., 0.]) as store:
        assert store._db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1
        assert store._db.execute("SELECT COUNT(*) FROM entries_vec").fetchone()[0] == 1
        assert store._db.execute("SELECT COUNT(*) FROM entries_fts").fetchone()[0] == 1


@pytest.mark.parametrize("invalid", [[float("nan"), 0.], [float("inf"), 0.], [True, 0.], ["bad", 0.]])
def test_non_numeric_or_non_finite_vectors_fail_before_persistence(tmp_path, invalid):
    with pytest.raises(BackendUnavailable, match="finite|numeric|numbers"):
        UnifiedMemory(path=tmp_path / "memory.sqlite", embedder=lambda _: invalid)
    assert not (tmp_path / "memory.sqlite").exists()


def test_episode_graph_links_only_actual_shared_artifacts(tmp_path):
    from hydra.execution_learning import record_execution_lesson, query_execution_knowledge
    from hydra.loop import LoopResult, LoopStep
    memory = tmp_path / "memory"
    (tmp_path / "bridge.py").write_text("pass")
    def execute(goal, filename):
        result = LoopResult(steps=[LoopStep(step=1, kind="tool_result", role="tool", tool_name="fs_read",
            tool_arguments={"path": filename, "api_key": "never-persist-me"}, content='{"ok":true}')],
            final_response="imagined truth", iterations=2, tool_calls_made=1, halted_reason="natural", messages=[])
        return record_execution_lesson(repo_root=tmp_path, memory_root=memory, goal=goal,
                                        result=result, provider="test", model="test")
    first = execute("zircon inspection", "bridge.py")
    second = execute("delta inspection", "bridge.py")
    execute("unrelated inspection", "other.py")
    graph = query_execution_knowledge("zircon", repo_root=tmp_path, memory_root=memory, limit=5)
    assert [r["id"] for r in graph["matches"]] == [first["id"]]
    assert [r["id"] for r in graph["related"]] == [second["id"]]
    assert "never-persist-me" not in json.dumps(graph)
    assert graph["quality_verified"] is False
