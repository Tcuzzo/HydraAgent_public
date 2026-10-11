import json
import pytest
from hydra.source_index import SourceIndex
from hydra import source_semantic as semantic


def test_vectors_are_incremental_model_scoped_and_stale_excluded(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    root.mkdir()
    code = root / 'payments.py'
    code.write_text('def invoice():\n    return "body-not-sent"\n')
    cfg = {'backend': 'openai', 'model': 'local', 'revision': 'one', 'endpoint': 'http://127.0.0.1:8080/v1'}
    calls = []
    monkeypatch.setattr(semantic, 'config', lambda: cfg)
    def embedding(request, **kwargs):
        calls.extend(request['texts'])
        return {'vectors': [[1., 0.] for _ in request['texts']]}
    monkeypatch.setattr(semantic, 'predict_local', embedding)
    index = SourceIndex(root, cache=tmp_path / 'index.db')
    assert semantic.fill(index)['embedded'] == 1
    assert 'body-not-sent' not in calls[0]
    assert semantic.fill(index)['embedded'] == 0
    found = semantic.search(index, 'billing')
    assert found['matches'][0]['path'] == 'payments.py'
    assert index.read(found['matches'][0]['key'])['freshness'] == 'content_verified'
    code.write_text('def replaced(): pass\n')
    assert semantic.search(index, 'billing')['matches'] == []
    assert semantic.fill(index)['embedded'] == 1
    cfg['revision'] = 'two'
    assert semantic.search(index, 'billing')['matches'] == []
    assert semantic.fill(index)['embedded'] == 1


def test_no_remote_source_upload_and_invalid_vectors(tmp_path, monkeypatch):
    cfg = {'backend': 'openai', 'model': 'm', 'revision': 'r', 'endpoint': 'https://remote.example/v1'}
    monkeypatch.setattr(semantic, 'config', lambda: cfg)
    index = SourceIndex(tmp_path, cache=tmp_path / 'cache.db')
    with pytest.raises(ValueError, match='local'):
        semantic.fill(index)
    cfg['endpoint'] = 'http://localhost:8080/v1'
    monkeypatch.setattr(semantic, 'predict_local', lambda *a, **kw: {'vectors': [[0., 0.]]})
    with pytest.raises(ValueError, match='nonzero'):
        semantic.search(index, 'query')


def test_batch_does_not_start_an_endless_embedding_loop(tmp_path, monkeypatch):
    (tmp_path / 'a.py').write_text('def first(): pass')
    (tmp_path / 'b.py').write_text('def second(): pass')
    monkeypatch.setattr(semantic, 'config', lambda: {'backend': 'openvino', 'model': 'm', 'revision': 'r'})
    monkeypatch.setattr(semantic, 'predict_local', lambda req, **kw: {'vectors': [[1., 2.] for _ in req['texts']]})
    index = SourceIndex(tmp_path, cache=tmp_path / 'cache.db')
    result = semantic.fill(index, batch=1)
    assert result['status'] == 'partial' and result['embedded'] == 1 and result['pending'] == 1
