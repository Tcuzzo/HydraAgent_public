"""Bounded local fine-tuning with sealed inputs and before/after evaluation.

Public source: Cactus Compute Needle (Apache-2.0) and Jared Palmer Kev
(Apache-2.0). Hydra owns input validation, provenance, process deadlines and
evaluation receipts. This workflow never uploads data or promotes a checkpoint.
"""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from hydra.atomic_write import atomic_write_bytes
from hydra.decision_contract import validate_questions
from hydra.llm import LlmError
from hydra.local_model_worker import model_cache_dir
from hydra.proc import kill_tree, popen_portable

MAX_DATA_BYTES = 32 * 1024 * 1024
MAX_LOG_BYTES = 32 * 1024 * 1024


class TrainingError(ValueError):
    pass


def _write(path, value):
    atomic_write_bytes(Path(path), (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode())


def _read_bytes(path):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_DATA_BYTES + 1)
    if len(data) > MAX_DATA_BYTES:
        raise TrainingError("dataset exceeds 32 MiB")
    return data


def _read(path):
    data = _read_bytes(path)
    try:
        rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    except (ValueError, UnicodeError) as exc:
        raise TrainingError(f"dataset is not UTF-8 JSONL: {Path(path).name}") from exc
    if not 1 <= len(rows) <= 10000 or any(not isinstance(row, dict) for row in rows):
        raise TrainingError("dataset must contain 1..10000 JSON objects")
    return data, rows


def _repository_root(path):
    # Sandboxes can contain empty .git markers. Only Git itself can distinguish
    # a real checkout or worktree from a placeholder directory.
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        result = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True, timeout=5, check=False)
        return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _validate_rows(backend, rows):
    fingerprints = set()
    for row in rows:
        if backend == "kev":
            if "state" not in row:
                raise TrainingError("Kev training row is missing state")
            try:
                validate_questions(row.get("questions"))
            except LlmError as exc:
                raise TrainingError(str(exc)) from exc
            for question in row["questions"].values():
                label, kind = question.get("label"), question["type"]
                valid = (isinstance(label, str) and label in question["criteria"]) if kind == "choice" else (
                    isinstance(label, bool) if kind == "noul" else
                    isinstance(label, int) and not isinstance(label, bool) and 0 <= label < len(question["criteria"]))
                if not valid:
                    raise TrainingError("Kev training label must belong to its typed question")
            key = row["state"]
        else:
            from hydra.needle_client import _schemas
            from jsonschema import Draft202012Validator, ValidationError
            if not isinstance(row.get("query"), str) or not row["query"].strip() or not isinstance(row.get("answers"), list):
                raise TrainingError("Needle rows require a query and exact answers list")
            try:
                schemas = _schemas(row.get("tools"))
                for call in row["answers"]:
                    if not isinstance(call, dict) or call.get("name") not in schemas or not isinstance(call.get("arguments"), dict):
                        raise TrainingError("Needle label names an unknown tool or invalid arguments")
                    Draft202012Validator(schemas[call["name"]]).validate(call["arguments"])
            except (LlmError, ValidationError) as exc:
                raise TrainingError(f"invalid Needle training label/schema: {exc}") from exc
            key = row["query"].strip()
        try:
            canonical = json.dumps(key, sort_keys=True, ensure_ascii=False, allow_nan=False)
            json.dumps(row, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise TrainingError("dataset must contain finite JSON values") from exc
        fingerprints.add(hashlib.sha256(canonical.encode()).hexdigest())
    return fingerprints


def prepare_training(backend, train_file, eval_file, out, *, epochs=1, timeout=1800.0, device="cpu", model=None):
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 86400:
        raise TrainingError("training timeout must be finite, positive and at most 86400 seconds")
    if backend not in {"needle", "kev"}:
        raise TrainingError("training backend must be needle or kev")
    if isinstance(epochs, bool) or not isinstance(epochs, int) or not 1 <= epochs <= 20:
        raise TrainingError("epochs must be an integer in 1..20")
    if device not in {"cpu", "cuda", "mps"}:
        raise TrainingError("choose an explicit cpu, cuda, or mps training device")
    if backend == "needle" and (device != "cpu" or model not in {None, "needle3"}):
        raise TrainingError("this Needle training recipe uses official needle3 on CPU; use its SDK for another recipe")
    train_file, eval_file, out = [Path(p).expanduser().resolve() for p in (train_file, eval_file, out)]
    if train_file == eval_file:
        raise TrainingError("train/evaluation leakage: files must be distinct")
    roots = [Path(__file__).resolve().parent.parent]
    for candidate in (Path.cwd(), train_file.parent, eval_file.parent, out.parent):
        root = _repository_root(candidate)
        if root is not None:
            roots.append(root)
    if any(out.is_relative_to(root) or root.is_relative_to(out) for root in roots):
        raise TrainingError("training output must be outside source repositories and the installed runtime")
    if out.exists():
        raise TrainingError("training output must be a new directory")
    train_bytes, train_rows = _read(train_file)
    eval_bytes, eval_rows = _read(eval_file)
    if _validate_rows(backend, train_rows) & _validate_rows(backend, eval_rows):
        raise TrainingError("train/evaluation leakage: a source state or query appears in both splits")
    # When the source-grounded corpus is available, check its stronger file-group
    # partition and hashes as well as exact query/state duplication.
    provenance_files = {}
    grouped = False
    if train_file.parent == eval_file.parent and (train_file.parent / "manifest.json").is_file():
        manifest_path = train_file.parent / "manifest.json"
        manifest_bytes = _read_bytes(manifest_path)
        manifest = json.loads(manifest_bytes)
        if not isinstance(manifest, dict) or not isinstance(manifest.get("dataset_sha256"), dict):
            raise TrainingError("corpus manifest must contain dataset checksums")
        provenance_files[manifest_path.name] = {"path": str(manifest_path), "sha256": hashlib.sha256(manifest_bytes).hexdigest()}
        for path, data in ((train_file, train_bytes), (eval_file, eval_bytes)):
            expected = manifest["dataset_sha256"].get(path.name)
            if hashlib.sha256(data).hexdigest() != expected:
                raise TrainingError("dataset changed after its provenance manifest was created")
        provenance = train_file.parent / "provenance.jsonl"
        if provenance.is_file():
            provenance_bytes, records = _read(provenance)
            provenance_files[provenance.name] = {"path": str(provenance), "sha256": hashlib.sha256(provenance_bytes).hexdigest()}
            groups = {name: {r["group"] for r in records if r.get("dataset") == name} for name in (train_file.name, eval_file.name)}
            if not all(groups.values()):
                raise TrainingError("provenance must describe both dataset splits")
            if groups[train_file.name] & groups[eval_file.name]:
                raise TrainingError("train/evaluation leakage: provenance file groups overlap")
            grouped = True
    return {"schema": "hydra.local_training.v1", "backend": backend, "train_file": str(train_file),
            "eval_file": str(eval_file), "out": str(out), "train_sha256": hashlib.sha256(train_bytes).hexdigest(),
            "eval_sha256": hashlib.sha256(eval_bytes).hexdigest(), "train_rows": len(train_rows), "eval_rows": len(eval_rows),
            "epochs": epochs, "timeout": timeout, "device": device,
            "provenance_files": provenance_files, "split_validation": "exact_inputs_and_file_groups" if grouped else "exact_inputs",
            "model": model or ("needle3" if backend == "needle" else "jaredpalmer/kev-0.8b@v1.0"),
            "promote": False, "remote_training_calls": 0,
            "warnings": (["Local Needle tuning drops the untrained confidence head; candidate confidence is null, not a calibrated success probability."]
                         if backend == "needle" else []),
            "limitations": ["Evaluation applies only to these labeled tasks", "Calibration is not fitted on the evaluation split",
                            "Use a separate untouched test set after selecting among multiple candidates"]}


def _evaluate_needle(rows, weights=None):
    import needle
    records, agent, previous = [], None, None
    for row in rows:
        signature = json.dumps(row["tools"], sort_keys=True)
        if previous != signature:
            if agent is not None:
                agent.close()
            options = {"tools": row["tools"], "auto_date": False}
            if weights:
                options["weights"] = weights
            agent, previous = needle.Needle(**options), signature
        agent.reset()
        raw = agent.complete(row["query"], max_new_tokens=256)
        validation = raw.get("validation") or {}
        exact = (raw.get("success") is True and not validation.get("ungrounded") and not validation.get("negation")
                 and raw.get("function_calls") == row["answers"])
        records.append({"expected": row["answers"], "actual": raw, "exact": exact})
    if agent is not None:
        agent.close()
    return {"rows": len(records), "correct": sum(r["exact"] for r in records),
            "accuracy": sum(r["exact"] for r in records) / len(records), "predictions": records}


def _evaluate_kev(rows, checkpoint, device):
    from kev.api import SystemOneRequest, to_record, to_answers
    from kev.checkpoint import Checkpoint
    from kev.model import admit
    tokenizer, model = Checkpoint(checkpoint).load(device)
    records, correct, count, brier = [], 0, 0, 0.0
    for row in rows:
        rec, meta = to_record(SystemOneRequest(state=row["state"], questions=row["questions"]))
        encoded = admit(model, tokenizer, rec, truncate=False)
        probabilities = [p.tolist() for p in model.probs(encoded)]
        answers = to_answers(probabilities, meta)
        for (name, question), ps, item in zip(row["questions"].items(), probabilities, meta):
            label = question["label"]
            target = str(label).lower() if question["type"] == "noul" else str(label)
            index = item["keys"].index(target)
            correct += int(max(range(len(ps)), key=ps.__getitem__) == index)
            count += 1
            brier += sum((p - int(i == index)) ** 2 for i, p in enumerate(ps))
        records.append({"answers": answers, "expected": {k: q["label"] for k, q in row["questions"].items()}})
    return {"rows": len(rows), "questions": count, "correct": correct,
            "accuracy": correct / count, "brier": brier / count, "predictions": records}


def _worker(plan):
    from hydra.local_resources import cpu_budget
    plan["resource_budget"] = cpu_budget(default_fraction=1.0 if plan["backend"] == "needle" else 0.15)
    out = Path(plan["out"])
    _, evaluation = _read(out / "evaluation.jsonl")
    if plan["backend"] == "needle":
        from needle.agent import fetch
        from needle.model.finetune import finetune_local
        try:
            from needle.model.build import build_main
        except ModuleNotFoundError as exc:
            # PyPI 3.2.1 keeps build here; upstream main moved it to model.build
            # without changing its version. Do not mask missing dependencies.
            if exc.name != "needle.model.build":
                raise
            from needle.model.finetune import build_main
        cache = model_cache_dir()
        fetch.cache_dir = lambda generation=3: str(cache / "needle" / f"v{generation}" / fetch.engine_version(generation))
        base = _evaluate_needle(evaluation)
        _write(out / "baseline.json", base)
        checkpoint = fetch.fetch_checkpoint("needle3.safetensors", str(cache / "needle" / "checkpoints"), generation=3)
        # The SDK slices examples at max_len. Refuse that silent label loss
        # before fitting; this recipe has a deliberate 512-token CPU budget.
        from needle.model.finetune import render_example
        from needle.model.tokenizer import get_tokenizer
        tokenizer = get_tokenizer()
        _, training = _read(out / "train.jsonl")
        lengths = [2 + sum(len(tokenizer.encode(text)) for text in render_example(row)) for row in training]
        if max(lengths) > 512:
            raise TrainingError("Needle training example exceeds this recipe's 512-token limit; shorten it instead of truncating labels")
        plan["max_training_tokens"] = max(lengths)
        adapter, target = out / "adapter.safetensors", out / "candidate.cact"
        finetune_local(argparse.Namespace(checkpoint=checkpoint, jsonl_path=str(out / "train.jsonl"), generate=0,
            epochs=plan["epochs"], batch_size=1, lr=1e-4, lora_rank=8, lora_alpha=16,
            max_len=512, val_split=0.0, seed=42, checkpoint_dir=str(out), out=str(adapter), score=False))
        build_main(argparse.Namespace(checkpoint=checkpoint, lora=str(adapter), out=str(target), layers=20, platform=None, upload=False))
        candidate = _evaluate_needle(evaluation, str(target))
    else:
        from kev.checkpoint import Checkpoint
        from kev.data import materialize
        from kev.model import fits, load_tokenizer, training_context
        base = _evaluate_kev(evaluation, plan["model"], plan["device"])
        _write(out / "baseline.json", base)
        meta = Checkpoint(plan["model"]).meta
        if meta.weights != "lora" or meta.lora <= 0:
            raise TrainingError("this Kev recipe requires an existing LoRA checkpoint")
        tokenizer = load_tokenizer(meta.base, revision=meta.base_revision)
        _, training = _read(out / "train.jsonl")
        if any(not fits(materialize(row), tokenizer, **training_context()) for row in training):
            raise TrainingError("Kev training example exceeds its context; shorten it instead of silently dropping a row")
        target = out / "candidate"
        command = [sys.executable, "-m", "kev.train", "--data", str(out / "train.jsonl"),
            "--base", meta.base, "--init_from", plan["model"], "--epochs", str(plan["epochs"]),
            "--lr", "2e-5", "--batch", "1", "--accum", "8", "--dtype", "fp32", "--device", plan["device"],
            "--lora", str(meta.lora), "--head_dim", str(meta.head_dim), "--seed", "42", "--out", str(target),
            "--option_isolation", str(int(meta.option_isolation)), "--special_embeddings", str(int(meta.special_embeddings)),
            "--weights_dtype", meta.weights_dtype, "--p_none", "0", "--p_none_distract", "0", "--p_distract", "0"]
        if meta.base_revision:
            command.extend(["--base_revision", meta.base_revision])
        subprocess.run(command, check=True)  # outer process group owns the deadline
        candidate = _evaluate_kev(evaluation, str(target), plan["device"])
    _write(out / "candidate-evaluation.json", candidate)
    packages = {}
    for package in ("hydraagent", "cactus-needle", "kev", "torch", "transformers", "jax", "peft"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            pass
    _write(out / "report.json", {**plan, "status": "completed", "checkpoint": str(target), "packages": packages,
        "baseline_accuracy": base["accuracy"], "candidate_accuracy": candidate["accuracy"],
        "accuracy_delta": candidate["accuracy"] - base["accuracy"], "calibration_fitted": False,
        "improvement_observed": candidate["accuracy"] > base["accuracy"], "promoted": False})


def run_training(plan):
    out = Path(plan["out"])
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    for field, destination in (("train", "train.jsonl"), ("eval", "evaluation.jsonl")):
        data, _ = _read(plan[f"{field}_file"])
        if hashlib.sha256(data).hexdigest() != plan[f"{field}_sha256"]:
            raise TrainingError("training inputs changed after validation")
        atomic_write_bytes(out / destination, data, overwrite=False)
    for name, item in plan.get("provenance_files", {}).items():
        if name not in {"manifest.json", "provenance.jsonl"}:
            raise TrainingError("unexpected provenance filename")
        data = _read_bytes(item["path"])
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise TrainingError("provenance changed after validation")
        atomic_write_bytes(out / name, data, overwrite=False)
    _write(out / "plan.json", plan)
    _write(out / "report.json", {**plan, "status": "running", "promoted": False})
    env = {**os.environ, "NEEDLE_TELEMETRY": "0", "DO_NOT_TRACK": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
           "HF_HUB_DISABLE_PROGRESS_BARS": "1", "USE_TF": "0", "JAX_PLATFORMS": "cpu"}
    env.setdefault("HYDRA_MODEL_CACHE", str(model_cache_dir()))
    env.setdefault("HF_HOME", str(model_cache_dir() / "huggingface"))
    process = None
    try:
        with (out / "training.log").open("wb") as log:
            process = popen_portable([sys.executable, "-m", "hydra.local_training", "--worker", str(out / "plan.json")],
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                cwd=str(Path(__file__).resolve().parent.parent))
            deadline = time.monotonic() + plan["timeout"]
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise TrainingError(f"training exceeded {plan['timeout']}s; partial checkpoint/logs preserved")
                if (out / "training.log").stat().st_size > MAX_LOG_BYTES:
                    raise TrainingError("training exceeded its 32 MiB log budget")
                time.sleep(0.1)
            if process.returncode:
                raise TrainingError(f"training worker exited {process.returncode}; inspect {out / 'training.log'}")
        report = json.loads((out / "report.json").read_text())
        if report.get("status") != "completed":
            raise TrainingError("training worker exited without a completed evaluation receipt")
        return report
    except BaseException as exc:
        if process is not None and process.poll() is None:
            kill_tree(process)
            process.wait(timeout=10)
        _write(out / "report.json", {**plan, "status": "failed", "error": str(exc), "promoted": False})
        raise


def register_training_arguments(parser):
    """Share one argument contract between `hydra train` and the module CLI."""
    parser.add_argument("--backend", choices=("needle", "kev"))
    parser.add_argument("--train", type=Path)
    parser.add_argument("--eval", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--device", choices=("cpu", "cuda", "mps"), default="cpu")
    parser.add_argument("--model")
    parser.add_argument("--plan-only", action="store_true")
    return parser


def run_from_args(args):
    """Execute a parsed CLI request, print its JSON receipt, return an exit code."""
    if not all((args.backend, args.train, args.eval, args.out)):
        raise TrainingError("--backend, --train, --eval and --out are required")
    plan = prepare_training(args.backend, args.train, args.eval, args.out,
        epochs=args.epochs, timeout=args.timeout, device=args.device, model=args.model)
    result = plan if args.plan_only else run_training(plan)
    print(json.dumps(result, indent=2))
    return 0


def main(argv=None):
    parser = register_training_arguments(argparse.ArgumentParser(description=__doc__))
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.worker:
            _worker(json.loads(args.worker.read_text()))
            return 0
        return run_from_args(args)
    except (OSError, ValueError, LlmError) as exc:
        parser.exit(1, f"local training: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
