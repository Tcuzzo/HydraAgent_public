import pytest

from hydra.unified_memory import UnifiedMemory, BackendUnavailable


def test_identical_vectors_do_not_erase_opposite_instructions(tmp_path):
    try:
        store = UnifiedMemory(path=tmp_path / 'memory.db', embedder=lambda text: [1., 0., 0.])
    except BackendUnavailable as exc:
        pytest.skip(str(exc))
    with store:
        first = store.add('Allow automatic model purchases', kind='policy')
        second = store.add('Do not allow automatic model purchases', kind='policy')
        assert first != second
        assert store.add('Do not allow automatic model purchases', kind='policy') == second
        from hydra.memory_distill import consolidate
        assert consolidate(store) == 0
        assert store._db.execute('SELECT COUNT(*) FROM entries WHERE superseded_by IS NULL').fetchone()[0] == 2
