"""Temporary hosted gate runner; never part of the release tree."""
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from radon.complexity import cc_visit
from coverage import Coverage, CoverageData

RELEASE_TREE = "b2531f1da6bb01aff2814a622e2a95d8d9cba643"
BASE_TREE = "d18e377cba26d61107f2de1f2b548c89f241335a"
SOURCE = ["hydra/__main__.py", "gateways/tui/elite.py"]
SEALS = {
    "hydra/test_cli_output_stream_restoration.py": "b694f967de1872de8de562cb94cbc131c14a1516f1f037d1e79ba6fd711fd96f",
    "hydra/test_cli_preserves_output_error_policy.py": "6608f5e39983f47bfba620990cdf0cf16b6afd858e870923ba3baed76aca672f",
    "hydra/test_cli_redirected_output_encoding.py": "5509e133468753905ec1b7e7339eb4a2ffb423863b22870b5b3a35c770d3eb71",
    "hydra/test_cli_windows_surrogateescape.py": "e8e0f438fc3b6ece5c0b2127c734d5364cb3f165113c29f0f872c484508fbf17",
}
PROFILE = "hydra/test_public_standalone_profile_env_dir.py"
PROFILE_SEAL = "0d4b02e8026fa926ff7f50bc6656a1afa73917df1bbe59d50610a90be88f063f"
PLUGIN = r'''
import json
import os
from pathlib import Path
import subprocess
_original = subprocess.Popen
_records = []
def pytest_configure(config):
    serialized = os.environ.get("COVERAGE_PROCESS_CONFIG")
    if not serialized:
        raise RuntimeError("coverage subprocess configuration missing")
    class CoveredProcess(_original):
        def __init__(self, args, *positional, **kwargs):
            explicit = kwargs.get("env") is not None
            if explicit:
                kwargs["env"] = {**kwargs["env"], "COVERAGE_PROCESS_CONFIG": serialized}
            super().__init__(args, *positional, **kwargs)
            _records.append({"pid": self.pid, "argv": [str(a) for a in args] if isinstance(args, (list, tuple)) else [str(args)],
                             "cwd": str(kwargs.get("cwd") or os.getcwd()), "explicit_env": explicit,
                             "configuration_injected": explicit})
    subprocess.Popen = CoveredProcess
def pytest_unconfigure(config):
    subprocess.Popen = _original
    Path(os.environ["RECOVERY_CHILD_LOG"]).write_text(json.dumps(_records, indent=2))
'''

def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

def seals(root):
    actual = {p: sha(root / p) for p in SEALS}
    assert actual == SEALS, actual
    assert sha(root / PROFILE) == PROFILE_SEAL
    return actual

def source_hashes(root):
    return {p: sha(root / p) for p in SOURCE}

def junit(path):
    document = ET.parse(path)
    cases = document.findall(".//testcase")
    return {
        "tests": len(cases),
        "failures": [c.attrib["name"] for c in cases if c.find("failure") is not None],
        "failure_types": {c.attrib["name"]: c.find("failure").attrib.get("type", "")
                          for c in cases if c.find("failure") is not None},
        "failure_messages": {c.attrib["name"]: c.find("failure").attrib.get("message", "")
                             for c in cases if c.find("failure") is not None},
        "failure_tracebacks": {c.attrib["name"]: c.find("failure").text or ""
                               for c in cases if c.find("failure") is not None},
        "errors": [c.attrib["name"] for c in cases if c.find("error") is not None],
        "skips": [c.attrib["name"] for c in cases if c.find("skipped") is not None],
    }

def run(command, root, log, env, timeout=120):
    start = time.monotonic()
    with log.open("wb") as stream:
        result = subprocess.run(command, cwd=root, env=env, stdout=stream,
                                stderr=subprocess.STDOUT, timeout=timeout)
    return {"returncode": result.returncode, "seconds": time.monotonic() - start,
            "command": command}

def environment(root):
    return {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root)}

def measure(label, root, output):
    target = output / label
    target.mkdir()
    config = target / "coverage.ini"
    plugin = target / "recovery_child_coverage.py"
    plugin.write_text(PLUGIN)
    config.write_text("[run]\nbranch = True\nparallel = True\npatch = subprocess\n"
                      "include =\n" + "".join("    " + str(root / p) + "\n" for p in SOURCE))
    env = environment(root)
    env.update({"PYTHONPATH": os.pathsep.join([str(target), str(root)]),
                "COVERAGE_RCFILE": str(config), "COVERAGE_FILE": str(target / ".coverage"),
                "RECOVERY_CHILD_LOG": str(target / "children.json")})
    command = [sys.executable, "-m", "coverage", "run", "-m", "pytest", "-q",
               "--tb=short", "-p", "recovery_child_coverage",
               "--junitxml=" + str(target / "tests.xml"), *SEALS, PROFILE]
    receipt = run(command, root, target / "tests.log", env)
    receipt["junit"] = junit(target / "tests.xml")
    receipt["source_hashes"] = source_hashes(root)
    receipt["seals"] = seals(root)
    raw = target / "raw-data"
    raw.mkdir()
    data = list(target.glob(".coverage.*"))
    assert len(data) > 1, "child coverage files missing"
    for path in data:
        shutil.copy2(path, raw / path.name)
    children = json.loads((target / "children.json").read_text())
    assert children and any(c["configuration_injected"] for c in children)
    main_path = (root / SOURCE[0]).resolve()
    main_node = functions(main_path.read_text())["main"]
    body_lines = set(range(main_node.body[0].lineno, main_node.end_lineno + 1))
    relevant = [c for c in children if any(c["argv"][i:i + 2] == ["-m", "hydra"]
                                         for i in range(len(c["argv"]) - 1))]
    assert relevant, "no actual Hydra CLI subprocess recorded"
    child_provenance = []
    for child in relevant:
        matching = [p for p in raw.iterdir() if
                    re.search(r"(?:^|\.)(?:pid)?" + str(child["pid"]) + r"(?:\.|$)", p.name)]
        assert matching, "Hydra child has no raw coverage file: " + str(child["pid"])
        executed = set()
        for data_path in matching:
            child_data = CoverageData(basename=str(data_path))
            child_data.read()
            for filename in child_data.measured_files():
                if Path(filename).resolve() == main_path:
                    executed.update(child_data.lines(filename) or [])
        hits = sorted(executed & body_lines)
        assert hits, "Hydra child did not execute main body: " + str(child["pid"])
        child_provenance.append({**child, "raw_files": [p.name for p in matching],
                                 "executed_main_body_lines": hits})
    receipt.update({"child_processes": children, "hydra_child_provenance": child_provenance,
                    "raw_data_files": len(data)})
    subprocess.run([sys.executable, "-m", "coverage", "combine", str(target)],
                   cwd=root, env=env, check=True)
    subprocess.run([sys.executable, "-m", "coverage", "json", "-o", str(target / "coverage.json")],
                   cwd=root, env=env, check=True)
    save(target / "measurement.json", receipt)
    print(json.dumps({"measurement": label, "receipt": receipt}), flush=True)
    return receipt

def functions(text):
    found = {}
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found[node.name + "." + method.name] = method
    return found

def complexities(text):
    found = {}
    for block in cc_visit(text):
        if hasattr(block, "methods"):
            for method in block.methods:
                found[block.name + "." + method.name] = method.complexity
        elif getattr(block, "classname", None):
            found[block.classname + "." + block.name] = block.complexity
        else:
            found[block.name] = block.complexity
    return found

def quality(root, output, label):
    analyzer = Coverage(data_file=str(output / label / ".coverage"),
                        config_file=str(output / label / "coverage.ini"))
    analyzer.load()
    found = {}
    for relative in SOURCE:
        path = root / relative
        text = path.read_text()
        _filename, statement_lines, excluded_lines, missing_lines, _formatted = analyzer.analysis2(str(path))
        cc = complexities(text)
        statements = set(statement_lines)
        executed = statements - set(missing_lines)
        for name, node in functions(text).items():
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            own = statements & set(range(start, node.end_lineno + 1))
            coverage = len(own & executed) / len(own) if own else 0.0
            complexity = cc[name]
            found[relative + ":" + name] = {
                "cc": complexity, "coverage": coverage,
                "crap": complexity ** 2 * (1 - coverage) ** 3 + complexity,
                "statements": sorted(own), "executed": sorted(own & executed),
                "excluded_lines": sorted(set(excluded_lines) & set(range(start, node.end_lineno + 1))),
                "ast": ast.dump(node, include_attributes=False),
            }
    return found

def compare_quality(baseline, candidate, output):
    before = quality(baseline, output, "baseline")
    after = quality(candidate, output, "candidate")
    changed = []
    failures = []
    for key, row in after.items():
        old = before.get(key)
        if old and row["ast"] == old["ast"]:
            continue
        result = {"function": key, "before": old, "after": row,
                  "classification": "inherited_extraction" if key.endswith("EliteTUI._print_terminal_welcome")
                                    else ("existing_changed" if old else "new_helper")}
        if old:
            result["gate"] = "PASS" if row["cc"] <= old["cc"] and row["crap"] <= old["crap"] + 1e-9 else "WORSENED"
        else:
            result["gate"] = "PASS" if row["cc"] <= 5 and row["crap"] < 30 else "AUTHORED_DEBT"
        for metric in (result.get("before"), result["after"]):
            if metric:
                metric["ast_sha256"] = hashlib.sha256(metric.pop("ast").encode()).hexdigest()
        changed.append(result)
        if result["gate"] != "PASS":
            failures.append(key)
    old_terminal = functions((baseline / SOURCE[1]).read_text())["EliteTUI._print_welcome"]
    new_terminal = functions((candidate / SOURCE[1]).read_text())["EliteTUI._print_terminal_welcome"]
    new_terminal.name = old_terminal.name
    assert ast.dump(old_terminal, include_attributes=False) == ast.dump(new_terminal, include_attributes=False)
    result = {"formula": "CC^2 * (1 - statement_coverage)^3 + CC",
              "baseline_tree": BASE_TREE, "candidate_tree": RELEASE_TREE,
              "terminal_body_ast_identical": True, "changed_functions": changed,
              "failures": failures, "status": "PASS" if not failures else "HOLD"}
    save(output / "numeric-quality.json", result)
    print(json.dumps({"numeric_quality": result}), flush=True)
    return result

def mutant(number, candidate, output):
    target = output / ("mutant-" + str(number))
    shutil.copytree(candidate, target, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.egg-info"))
    receipt_dir = output / ("mutation-" + str(number))
    receipt_dir.mkdir()
    path = target / SOURCE[0]
    original = path.read_bytes()
    original_hashes = source_hashes(target)
    text = original.decode()
    changes = {
        1: ("@_cli_output_scope()\ndef main(", "def main("),
        2: ('    "strict": "backslashreplace",', '    "replace": "backslashreplace",\n    "xmlcharrefreplace": "backslashreplace",\n    "strict": "backslashreplace",'),
        3: ("                restore.callback(stream.reconfigure, errors=previous_errors)\n", ""),
    }
    old, new = changes[number]
    assert text.count(old) == 1
    text = text.replace(old, new)
    ast.parse(text)
    path.write_text(text)
    seals(target)
    env = environment(target)
    command = [sys.executable, "-B", "-m", "pytest", "-q", "--tb=short",
               "--junitxml=" + str(receipt_dir / "mutant.xml"), *SEALS]
    try:
        killed = run(command, target, receipt_dir / "mutant.log", env, 65)
        cases = junit(receipt_dir / "mutant.xml")
        prefixes = {
            1: ["test_redirected_", "test_chat_with_windows_default_output_policy"],
            2: ["test_ask_respects_existing_cp1252_policy"],
            3: ["test_dispatch_restores_each_stream_after_return_or_exception"],
        }
        evidence = {name: cases["failure_messages"][name] + "\n" + cases["failure_tracebacks"][name]
                    for name in cases["failures"]}
        infrastructure = ("TimeoutExpired", "ModuleNotFoundError", "ImportError",
                          "FileNotFoundError", "SyntaxError", "INTERNALERROR", "PermissionError")
        assertion_failure = (killed["returncode"] == 1 and cases["tests"] == 16
                             and not cases["errors"] and not cases["skips"]
                             and not any(kind in text for text in evidence.values() for kind in infrastructure)
                             and all(any(name.startswith(prefix) and
                                         (re.search(r"(?m)^E\s+(?:AssertionError\b|assert\b)", cases["failure_tracebacks"][name]) or
                                          re.match(r"(?:AssertionError\b|assert\b)", cases["failure_messages"][name]))
                                         for name in cases["failures"])
                                     for prefix in prefixes[number]))
        killed["junit"] = cases
    finally:
        path.write_bytes(original)
        assert source_hashes(target) == original_hashes
        seals(target)
    restored_command = [sys.executable, "-B", "-m", "pytest", "-q", "--tb=short",
                        "--junitxml=" + str(receipt_dir / "restored.xml"), *SEALS]
    restored = run(restored_command, target, receipt_dir / "restored.log", env, 65)
    restored_cases = junit(receipt_dir / "restored.xml")
    assert restored["returncode"] == 0 and restored_cases["tests"] == 16
    assert not restored_cases["failures"] and not restored_cases["errors"] and not restored_cases["skips"]
    result = {"mutant": number, "killed_by_contract": assertion_failure, "mutated": killed,
              "restored": restored, "restored_junit": restored_cases,
              "source_restored": source_hashes(target) == original_hashes, "seals": seals(target)}
    save(receipt_dir / "result.json", result)
    print(json.dumps({"mutation_result": result}), flush=True)
    shutil.rmtree(target)
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidate, baseline, output = args.candidate.resolve(), args.baseline.resolve(), args.output.resolve()
    output.mkdir(parents=True)
    assert git(candidate, "rev-parse", "HEAD^{tree}") == RELEASE_TREE
    assert git(baseline, "rev-parse", "HEAD^{tree}") == BASE_TREE
    assert not git(candidate, "status", "--porcelain", "--untracked-files=no")
    initial = source_hashes(candidate)
    baseline_initial = source_hashes(baseline)
    seals(candidate)
    for path in SEALS:
        shutil.copy2(candidate / path, baseline / path)
    seals(baseline)
    save(output / "identity.json", {"candidate_commit": git(candidate, "rev-parse", "HEAD"),
         "candidate_tree": RELEASE_TREE, "baseline_commit": git(baseline, "rev-parse", "HEAD"),
         "baseline_tree": BASE_TREE, "candidate_source_hashes": initial,
         "baseline_source_hashes": source_hashes(baseline), "seals": SEALS,
         "runner_sha256": sha(Path(__file__)), "python": sys.version,
         "measurement_tools": {name: __import__("importlib.metadata", fromlist=["version"]).version(name)
                               for name in ("coverage", "radon", "pytest")},
         "karpathy": "Already completed unchanged engine KEEP: 9/16 to16/16 on exact reviewed tree; not rerun here."})
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(measure, label, root, output) for label, root in
                   [("baseline", baseline), ("candidate", candidate)]]
        measurements = [f.result() for f in futures]
    baseline_result, candidate_result = measurements
    assert baseline_result["returncode"] == 1
    assert baseline_result["source_hashes"] == baseline_initial
    assert baseline_result["junit"]["tests"] == 21
    expected_red = {
        "test_dispatch_restores_each_stream_after_return_or_exception[False]",
        "test_dispatch_restores_each_stream_after_return_or_exception[True]",
        "test_redirected_chat_welcomes_lists_unicode_skill_and_exits[cp1252]",
        "test_redirected_ask_prints_unicode_reply_in_selected_encoding[cp1252]",
        "test_chat_with_windows_default_output_policy_completes_exit",
        "test_ask_output_bytes_under_windows_policies[ordinary-unicode]",
        "test_ask_output_bytes_under_windows_policies[mixed-raw-and-unicode]",
    }
    assert set(baseline_result["junit"]["failures"]) == expected_red
    assert not baseline_result["junit"]["errors"] and not baseline_result["junit"]["skips"]
    assert candidate_result["returncode"] == 0
    assert candidate_result["junit"]["tests"] == 21
    assert not candidate_result["junit"]["failures"] and not candidate_result["junit"]["errors"] and not candidate_result["junit"]["skips"]
    numeric = compare_quality(baseline, candidate, output)
    assert numeric["status"] == "PASS", numeric["failures"]
    # The unchanged fixed16-case suite completed GREEN before the outage.
    # Record a bounded ETA before any mutation, retaining original measured timing.
    plan = {"count": 3, "fixed_cases": 16, "previous_green_seconds": 24.96754687000066,
            "estimated_serial_seconds": 149.80528122000396, "parallel_workers": 3,
            "per_command_timeout_seconds": 65, "maximum_stage_seconds": 150,
            "source_tree": RELEASE_TREE, "seals": SEALS}
    save(output / "mutation-plan.json", plan)
    print(json.dumps({"mutation_plan_before_execution": plan}), flush=True)
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda n: mutant(n, candidate, output), [1, 2, 3]))
    save(output / "mutation-results.json", {"results": results, "seconds": time.monotonic() - start})
    assert time.monotonic() - start <= 150
    assert all(r["killed_by_contract"] and r["source_restored"] for r in results)
    assert source_hashes(candidate) == initial
    seals(candidate)
    save(output / "final.json", {"status": "PASS", "tree": RELEASE_TREE,
         "numeric_quality": "PASS", "mutants_killed": 3, "restored_controls": 3,
         "candidate_source_hashes_unchanged": True, "seals": SEALS})
    print("PASS: exact-tree numeric quality, three contract-killed mutants, restored controls", flush=True)

if __name__ == "__main__":
    main()
