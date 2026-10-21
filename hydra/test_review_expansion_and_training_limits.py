"""Enforce limits before edit expansion and at every training exit."""
import json
import sys
import tracemalloc

import pytest

from hydra import local_training
from skills.fs_edit import run as edit
from skills.fs_read import SkillError


def test_edit_refuses_expansion_before_allocating_oversized_output(tmp_path):
    target = tmp_path/'small.txt'
    target.write_bytes(b'a' * 4096)
    tracemalloc.start()
    try:
        with pytest.raises(SkillError, match='exceeds max_bytes'):
            edit('small.txt', 'a', 'b' * 4096, tmp_path, count='all')
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert target.read_bytes() == b'a' * 4096
    assert peak < 2 * 1024 * 1024, peak


def test_fast_training_exit_cannot_bypass_or_expand_log_budget(tmp_path, monkeypatch):
    train = tmp_path/'train.jsonl'
    evaluation = tmp_path/'eval.jsonl'
    for path, state in ((train, 'training state'), (evaluation, 'evaluation state')):
        path.write_text(json.dumps({'state': state, 'questions': {'go': {'type': 'noul', 'label': True}}}) + '\n')
    out = tmp_path/'training-run'
    plan = local_training.prepare_training('kev', train, evaluation, out, timeout=5)
    script = tmp_path/'ml-transport.py'
    script.write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "plan=json.loads(Path(sys.argv[1]).read_text())\n"
        "for _ in range(33): os.write(1,b'x'*1048576)\n"
        "Path(plan['out'],'report.json').write_text(json.dumps({**plan,'status':'completed','promoted':False}))\n")
    real_spawn = local_training.popen_portable
    def worker_transport(argv, **kwargs):
        return real_spawn([sys.executable, str(script), argv[-1]], **kwargs)
    monkeypatch.setattr(local_training, 'popen_portable', worker_transport)
    with pytest.raises(local_training.TrainingError, match='log budget'):
        local_training.run_training(plan)
    assert (out/'training.log').stat().st_size <= local_training.MAX_LOG_BYTES
    report = json.loads((out/'report.json').read_text())
    assert report['status'] == 'failed' and report['promoted'] is False
