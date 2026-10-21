"""Initialize the shipped local helpers and report measured readiness."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import importlib.util
import json
import math
from pathlib import Path
import sys

from hydra.atomic_write import atomic_write_bytes
from hydra.llm import ChatMessage, LlmError
from hydra.local_model_worker import model_cache_dir


KEV_SOURCE = "https://github.com/jaredpalmer/kev.git"
KEV_REVISION = "1f04cfa28e6a708485b28a9585010b9e436b776e"
DEFAULT_STATE_DIR = Path.home() / ".hydraAgent" / "local-intelligence"


def _dependency(distribution: str, module: str) -> dict:
    try:
        package = importlib.metadata.distribution(distribution)
        if importlib.util.find_spec(module) is None:
            return {"status": "missing", "distribution": distribution}
    except (importlib.metadata.PackageNotFoundError, ValueError, ImportError):
        return {"status": "missing", "distribution": distribution}
    result = {"status": "installed", "distribution": distribution, "version": package.version}
    if distribution == "kev":
        # The public PyPI name is an unrelated ORM. The complete bundle installs
        # an immutable revision of Jared Palmer's model SDK instead.
        try:
            origin = json.loads(package.read_text("direct_url.json") or "{}")
        except (ValueError, TypeError):
            origin = {}
        vcs = origin.get("vcs_info", {})
        if origin.get("url", "").rstrip("/") != KEV_SOURCE or vcs.get("commit_id") != KEV_REVISION:
            result.update(status="unverified_source", remedy="Reinstall the pinned official Hydra intelligence extra.")
        else:
            result["source_revision"] = KEV_REVISION
        if not (3, 12) <= sys.version_info[:2] < (3, 14):
            result.update(status="unsupported_python", remedy="Use Python 3.12 or 3.13 for the complete local bundle.")
    return result


def dependency_report() -> dict:
    models = {"needle": _dependency("cactus-needle", "needle"), "kev": _dependency("kev", "kev")}
    memory = _dependency("sqlite-vec", "sqlite_vec")
    installed = all(row["status"] == "installed" for row in [*models.values(), memory])
    return {"schema": "hydra.local-intelligence.v1", "python": sys.version.split()[0],
            "dependencies_ready": installed, "ready": False, "models": models,
            "memory": memory, "verification": "dependency inspection; model inference has not run"}


def _probe_needle(timeout: float) -> dict:
    from hydra.needle_client import NeedleClient, configured_needle_model
    model = configured_needle_model()
    tools = [{"type": "function", "function": {"name": "read_file", "description": "Read a file by path.",
              "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                             "required": ["path"], "additionalProperties": False}}}]
    response = NeedleClient(model=model).chat([ChatMessage("user", "Read README.md")],
                                             model=model, tools=tools, timeout=timeout)
    selections = [{"tool": call.name, "arguments": call.arguments} for call in response.tool_calls]
    if selections != [{"tool": "read_file", "arguments": {"path": "README.md"}}]:
        raise LlmError("Needle loaded but did not pass the explicit read-file initialization probe")
    return {"status": "ready", "model": model, "executed_actions": False,
            "probe": "literal file selection", "selections": selections}


def _probe_kev(timeout: float) -> dict:
    from hydra.decision_models import KevDecisionClient
    client = KevDecisionClient()
    response = client.predict(
        "The service state is ready. The health check passed.",
        {"ready": {"type": "noul", "instructions": "Is the service state ready?"}}, timeout=timeout,
    )
    return {"status": "ready", "model": client.model, "device": client.device,
            "executed_actions": False, "probe": "typed decision inference",
            "answers": response["answers"]}


def initialize(*, state_dir: Path, check_only: bool = False, timeout: float = 600.0) -> dict:
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0 or timeout > 3600:
        raise ValueError("initialization timeout must be finite and in (0, 3600] seconds per model")
    report = dependency_report()
    if check_only:
        return report
    state_dir = state_dir.expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    report["checked_at"] = datetime.now(timezone.utc).isoformat()
    report["verification"] = "real local inference; no tool proposal is executed"
    report["model_cache"] = str(model_cache_dir())
    for name, probe in (("needle", _probe_needle), ("kev", _probe_kev)):
        if report["models"][name]["status"] != "installed":
            continue
        print(f"Initializing {name} locally; first use downloads its public weights...", file=sys.stderr, flush=True)
        try:
            report["models"][name].update(probe(timeout))
        except (LlmError, OSError, ValueError, ImportError) as exc:
            report["models"][name].update(status="failed", error=str(exc))
    report["ready"] = report["dependencies_ready"] and all(
        row["status"] == "ready" for row in report["models"].values()
    )
    report["quality_certified"] = False
    report["note"] = "Readiness confirms loading and protocol behavior; evaluate task quality separately."
    atomic_write_bytes(state_dir / "status.json", (json.dumps(report, indent=2) + "\n").encode())
    return report


def register_intelligence_commands(sub) -> None:
    from hydra.local_training import register_training_arguments
    register_training_arguments(sub.add_parser("train", help="train a local Needle or Kev candidate with a separate evaluation split; never auto-promote"))
    init = sub.add_parser("init", help="initialize Needle and Kev locally and write a measured readiness report")
    init.add_argument("--check", action="store_true", help="inspect dependencies without downloading weights or claiming inference readiness")
    init.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    init.add_argument("--timeout", type=float, default=600.0, help="hard deadline per model, including downloads")
    decide = sub.add_parser("decide", help="run a typed local Kev or Laya decision")
    decide.add_argument("--backend", choices=("kev", "laya"), default="kev")
    decide.add_argument("--state", type=Path, required=True)
    decide.add_argument("--questions", type=Path, required=True)
    decide.add_argument("--model")
    decide.add_argument("--device")
    decide.add_argument("--timeout", type=float, default=120.0)


def cmd_intelligence(args: argparse.Namespace) -> int:
    if args.cmd == "train":
        from hydra.local_training import run_from_args
        try:
            return run_from_args(args)
        except (OSError, ValueError, LlmError) as exc:
            print(f"local training: {exc}", file=sys.stderr)
            return 1
    if args.cmd == "decide":
        from hydra.decision_models import main
        argv = ["--backend", args.backend, "--state", str(args.state), "--questions", str(args.questions),
                "--timeout", str(args.timeout)]
        for name in ("model", "device"):
            if getattr(args, name):
                argv.extend(["--" + name, getattr(args, name)])
        return main(argv)
    try:
        report = initialize(state_dir=args.state_dir, check_only=args.check, timeout=args.timeout)
    except (OSError, ValueError) as exc:
        print(f"local intelligence: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if (report["dependencies_ready"] if args.check else report["ready"]) else 1
