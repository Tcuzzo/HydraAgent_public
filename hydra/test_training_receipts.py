"""Local training must preserve its evidence and enforce a real process deadline."""
import hashlib
import json
import sys

import pytest

from hydra.test_training_corpus import sources
from hydra.training_corpus import build_corpus


def test_training_plan_seals_corpus_provenance(tmp_path, sources):
    from hydra.local_training import prepare_training
    corpus = tmp_path / "corpus"
    build_corpus(sources[0], None, corpus)
    plan = prepare_training("kev", corpus / "kev_train.jsonl", corpus / "kev_eval.jsonl", tmp_path / "run")
    for name in ("manifest.json", "provenance.jsonl"):
        item = plan["provenance_files"][name]
        assert item["sha256"] == hashlib.sha256((corpus / name).read_bytes()).hexdigest()
        assert item["path"] == str(corpus / name)
    assert plan["split_validation"] == "exact_inputs_and_file_groups"


def test_training_deadline_stops_real_worker_and_preserves_failure(tmp_path, monkeypatch):
    from hydra import local_training
    from hydra.proc import popen_portable
    files = []
    for split in ("train", "eval"):
        path = tmp_path / f"{split}.jsonl"
        path.write_text(json.dumps({"state": split, "questions": {"q": {"type": "noul", "label": True}}}) + "\n")
        files.append(path)
    plan = local_training.prepare_training("kev", *files, tmp_path / "run", timeout=0.1)
    children = []

    def slow_worker(command, **kwargs):
        child = popen_portable([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(local_training, "popen_portable", slow_worker)
    with pytest.raises(local_training.TrainingError, match="exceeded"):
        local_training.run_training(plan)
    assert children and all(child.poll() is not None for child in children)
    receipt = json.loads((tmp_path / "run" / "report.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["promoted"] is False
