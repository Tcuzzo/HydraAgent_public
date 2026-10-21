"""Undo must not consume or restore missing and corrupted recovery bytes."""
import pytest
from hydra.edit_checkpoints import CheckpointStore, undo


@pytest.mark.parametrize('damage', ['missing', 'corrupted'])
def test_invalid_recovery_content_leaves_current_file_and_checkpoint_intact(tmp_path, damage):
    target = tmp_path / 'document'
    target.write_bytes(b'current operator data')
    store = CheckpointStore(tmp_path, _base_dir=tmp_path/'checkpoints')
    store.record(target_path=target, op='modify', pre_image=b'original bytes')
    checkpoint = store.peek_all()[-1]
    blob = store._content_path(checkpoint['seq'])
    if damage == 'missing':
        blob.unlink()
    else:
        blob.write_bytes(b'corrupted preimage')
    try:
        undo(store)
    except (ValueError, OSError):
        pass
    assert target.read_bytes() == b'current operator data'
    assert store.peek_all() == [checkpoint]
