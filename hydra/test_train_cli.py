"""Training plans are available through the normal installed command surface."""
import json

from hydra.test_local_intelligence_cli import cli


def dataset(path, query):
    row = {"query": query, "tools": [{"name": "read_file", "description": "Read a path",
           "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}],
           "answers": [{"name": "read_file", "arguments": {"path": "README.md"}}]}
    path.write_text(json.dumps(row) + "\n")


def test_normal_cli_plans_training_without_installing_models_or_writing_output(tmp_path):
    training, evaluation, output = (tmp_path / name for name in ("train.jsonl", "eval.jsonl", "candidate"))
    dataset(training, "Read README.md")
    dataset(evaluation, "Please inspect README.md")
    result = cli(tmp_path, "train", "--backend", "needle", "--train", str(training), "--eval", str(evaluation),
                 "--out", str(output), "--plan-only")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["train_rows"] == report["eval_rows"] == 1
    assert report["promote"] is False
    assert not output.exists()


def test_normal_cli_refuses_train_evaluation_overlap(tmp_path):
    training, evaluation = tmp_path / "train.jsonl", tmp_path / "eval.jsonl"
    dataset(training, "Read README.md")
    dataset(evaluation, "Read README.md")
    result = cli(tmp_path, "train", "--backend", "needle", "--train", str(training), "--eval", str(evaluation),
                 "--out", str(tmp_path / "candidate"), "--plan-only")
    assert result.returncode != 0
    assert "leakage" in result.stderr
