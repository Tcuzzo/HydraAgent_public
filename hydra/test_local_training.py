"""Training contracts inspect real local JSONL and output boundaries."""
import json
from pathlib import Path

import pytest

from hydra.test_training_corpus import sources
from hydra.training_corpus import build_corpus


def test_public_corpus_includes_actual_kev_training_shape(tmp_path, sources):
    out = tmp_path / "public-data"
    manifest = build_corpus(sources[0], None, out)
    assert [source["label"] for source in manifest["sources"]] == ["public"]
    assert manifest["rows"]["kev_train"] and manifest["rows"]["kev_eval"]
    for split in ("train", "eval"):
        for row in map(json.loads, (out / f"kev_{split}.jsonl").read_text().splitlines()):
            question = row["questions"]["area"]
            assert question["label"] in question["criteria"]
            assert "expected" not in row


def test_training_refuses_duplicate_state_across_splits(tmp_path):
    from hydra.local_training import TrainingError, prepare_training
    row = {"state": "duplicate state", "questions": {"go": {"type": "noul", "label": True}}}
    first, second = tmp_path / "train.jsonl", tmp_path / "eval.jsonl"
    first.write_text(json.dumps(row) + "\n")
    second.write_text(json.dumps(row) + "\n")
    with pytest.raises(TrainingError, match="leakage"):
        prepare_training("kev", first, second, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_training_requires_finite_positive_deadline(tmp_path):
    from hydra.local_training import TrainingError, prepare_training
    with pytest.raises(TrainingError, match="timeout"):
        prepare_training("needle", tmp_path / "train", tmp_path / "eval", tmp_path / "out", timeout=float("inf"))


def test_prepared_training_preserves_source_fingerprints_and_no_promotion(tmp_path, sources):
    from hydra.local_training import prepare_training
    corpus = tmp_path / "corpus"
    build_corpus(*sources, corpus)
    plan = prepare_training("kev", corpus / "kev_train.jsonl", corpus / "kev_eval.jsonl", tmp_path / "run", epochs=1)
    assert len(plan["train_sha256"]) == len(plan["eval_sha256"]) == 64
    assert plan["promote"] is False
    assert plan["model"] == "jaredpalmer/kev-0.8b@v1.0"
    assert plan["epochs"] == 1
    assert not (tmp_path / "run").exists()


def test_training_rejects_invalid_kev_labels(tmp_path):
    from hydra.local_training import TrainingError, prepare_training
    paths = []
    for name in ("train", "eval"):
        path = tmp_path / f"{name}.jsonl"
        path.write_text(json.dumps({"state": name, "questions": {"q": {"type": "choice", "criteria": {"a": "a"}, "label": "b"}}}) + "\n")
        paths.append(path)
    with pytest.raises(TrainingError, match="label"):
        prepare_training("kev", *paths, tmp_path / "out")
