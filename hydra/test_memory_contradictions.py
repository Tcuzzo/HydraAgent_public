import pytest


def test_distillation_respects_the_configured_auditor_provider(monkeypatch):
    from types import SimpleNamespace
    from hydra import providers, model_routing, memory_distill
    seen = []
    class Client:
        def chat(self, messages, **kwargs):
            seen.append(kwargs['model'])
            return SimpleNamespace(content='["A durable fact"]')
    def factory(provider):
        seen.append(provider)
        return Client(), None
    monkeypatch.setattr(providers, 'make_client', factory)
    monkeypatch.setattr(model_routing, 'load_routing', lambda: SimpleNamespace(role_pair=lambda role: ('local-custom', 'local-model')))
    assert memory_distill._make_cloud_extractor()('A turn') == ['A durable fact']
    assert seen == ['local-custom', 'local-model']

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
