import os
from pathlib import Path

import pytest

from hydra.source_index import SourceIndex


def test_incremental_lookup_reads_only_changes_and_checks_key(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    code = root / 'routes.py'
    code.write_text('def route_model():\n    return 1\n')
    index = SourceIndex(root, cache=tmp_path / 'cache.db')
    assert index.refresh()['read_files'] == 1
    assert index.refresh()['read_files'] == 0
    hit = index.search('route_model')['matches'][0]
    assert hit['path'] == 'routes.py'
    assert 'route_model' in index.read(hit['key'])['content']
    code.write_text('def route_model():\n    return 2\n')
    with pytest.raises(ValueError, match='stale'):
        index.read(hit['key'])
    assert index.refresh()['read_files'] == 1
    code.unlink()
    assert index.refresh()['removed_files'] == 1
    assert index.search('route_model')['matches'] == []


def test_private_paths_and_links_never_enter_index(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    (root / '.env').write_text('SECRET=private')
    (root / 'credentials').mkdir()
    (root / 'credentials' / 'key.py').write_text('secret = 1')
    (root / 'ok.py').write_text('def visible(): pass')
    index = SourceIndex(root, cache=tmp_path / 'cache.db')
    assert index.refresh()['files'] == 1
    assert index.search('SECRET')['matches'] == []


def test_same_stat_modified_content_key_is_rejected(tmp_path):
    code = tmp_path / 'a.py'
    code.write_text('old = 1')
    index = SourceIndex(tmp_path, cache=tmp_path.parent / (tmp_path.name + '.db'))
    index.refresh()
    key = index.search('old')['matches'][0]['key']
    stamp = code.stat()
    code.write_text('new = 2')
    os.utime(code, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(ValueError, match='stale'):
        index.read(key)


def test_refresh_limit_does_not_delete_existing_snapshot(tmp_path):
    (tmp_path / 'a.py').write_text('a = 1')
    index = SourceIndex(tmp_path, cache=tmp_path.parent / (tmp_path.name + '.db'))
    index.refresh()
    (tmp_path / 'b.py').write_text('b = 2')
    with pytest.raises(ValueError, match='limit'):
        index.refresh(max_files=1)
    assert index.search('a', refresh=False)['matches']


def test_rejected_content_key_invalidates_unchanged_metadata(tmp_path, monkeypatch):
    code = tmp_path / 'a.py'
    code.write_text('def old_symbol(): pass')
    index = SourceIndex(tmp_path, cache=tmp_path / 'cache.db')
    stamp = index._stamp(code)
    monkeypatch.setattr(index, '_stamp', lambda path: stamp)
    key = index.search('old_symbol')['matches'][0]['key']
    code.write_text('def new_symbol(): pass')
    with pytest.raises(ValueError, match='stale'):
        index.read(key)
    found = index.search('new_symbol')
    assert found['matches'][0]['key'] != key
    assert 'new_symbol' in index.read(found['matches'][0]['key'])['content']
