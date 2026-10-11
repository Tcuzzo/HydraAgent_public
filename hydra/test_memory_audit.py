import json

from hydra.memory_audit import audit


def test_snapshot_deduplicates_objects_and_keeps_originals(tmp_path):
    root = tmp_path / 'memory'
    root.mkdir()
    (root / 'first.md').write_text('same memory')
    (root / 'second.md').write_text('same memory')
    (root / 'api_token.txt').write_text('must not archive')
    archive = tmp_path / 'snapshot'
    result = audit(root, archive=archive)
    assert result['exact_duplicate_groups'] == [['first.md', 'second.md']]
    assert result['originals_changed'] is False
    assert len(list((archive / 'objects').iterdir())) == 1
    assert (root / 'second.md').exists()
    manifest = json.loads((archive / 'manifest.json').read_text())
    assert len(manifest['files']) == 2


def test_snapshot_refuses_active_tree(tmp_path):
    import pytest
    with pytest.raises(ValueError, match='separate'):
        audit(tmp_path, archive=tmp_path / 'archive')


def test_database_inventory_obeys_global_deadline(tmp_path, monkeypatch):
    import pytest
    (tmp_path / 'one.db').touch()
    (tmp_path / 'two.db').touch()
    clock = iter([0, 1, 2, 31])
    calls = []
    monkeypatch.setattr('hydra.memory_audit.time.monotonic', lambda: next(clock))
    monkeypatch.setattr('hydra.memory_audit.database_inventory', lambda path, **kwargs: calls.append(path) or {'status': 'inspected'})
    with pytest.raises(ValueError, match='bound'):
        audit(tmp_path)
    assert len(calls) == 1
