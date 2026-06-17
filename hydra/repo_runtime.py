"""Deterministic repo runtime introspection for HydraAgent.

Detects manifests, languages, package managers, test evidence, runtime scripts,
container/CI evidence, and likely verification commands for any repo path the
operator points at. Pure read-only — the target repo is never mutated. Output
is keyed by ``hydra.repo_runtime.v1`` and is deterministic for identical
filesystem state.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA = "hydra.repo_runtime.v1"
RISK_TIER = "T0"
MAX_MANIFEST_BYTES = 256 * 1024

_LANGUAGE_MANIFESTS: dict[str, list[tuple[str, str]]] = {
    "python": [
        ("pyproject.toml", "pyproject"),
        ("setup.py", "setup_py"),
        ("setup.cfg", "setup_cfg"),
        ("requirements.txt", "requirements"),
        ("Pipfile", "pipfile"),
    ],
    "javascript": [
        ("package.json", "package_json"),
    ],
    "typescript": [
        ("tsconfig.json", "tsconfig"),
    ],
    "rust": [
        ("Cargo.toml", "cargo_toml"),
    ],
    "go": [
        ("go.mod", "go_mod"),
    ],
    "ruby": [
        ("Gemfile", "gemfile"),
    ],
    "java_kotlin": [
        ("pom.xml", "maven"),
        ("build.gradle", "gradle"),
        ("build.gradle.kts", "gradle_kts"),
    ],
    "php": [
        ("composer.json", "composer"),
    ],
    "c_cpp": [
        ("CMakeLists.txt", "cmake"),
    ],
    "make": [
        ("Makefile", "makefile"),
        ("makefile", "makefile"),
    ],
}

_PACKAGE_MANAGER_LOCKFILES: list[tuple[str, str]] = [
    ("package-lock.json", "npm"),
    ("yarn.lock", "yarn"),
    ("pnpm-lock.yaml", "pnpm"),
    ("poetry.lock", "poetry"),
    ("uv.lock", "uv"),
    ("Pipfile.lock", "pipenv"),
    ("Cargo.lock", "cargo"),
    ("Gemfile.lock", "bundler"),
    ("composer.lock", "composer"),
    ("go.sum", "go_modules"),
]

_TEST_DIRS = ("tests", "test", "__tests__", "spec", "specs")
_TEST_FILES = (
    "pytest.ini",
    "tox.ini",
    "jest.config.js",
    "jest.config.ts",
    "jest.config.cjs",
    "jest.config.mjs",
    ".mocharc.json",
    ".mocharc.yml",
    "karma.conf.js",
    "vitest.config.ts",
    "vitest.config.js",
)

_MAKE_TEST_TARGETS = frozenset({"test", "tests", "check", "ci"})


@dataclass(frozen=True)
class RepoRuntimeError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def audit_repo(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    if not root.exists():
        raise RepoRuntimeError(f"repo path does not exist: {root}")
    if not root.is_dir():
        raise RepoRuntimeError(f"repo path is not a directory: {root}")

    is_git = (root / ".git").exists()
    git_state = _git_state(root) if is_git else {"status": "not_git_repo"}
    languages = _detect_languages(root)
    package_managers = _detect_package_managers(root)
    test_evidence = _detect_tests(root)
    runtime_scripts = _detect_scripts(root)
    container_evidence = _detect_containers(root)
    ci_evidence = _detect_ci(root)
    verification_commands = _suggest_verifications(
        languages, package_managers, runtime_scripts, test_evidence
    )
    rot_signals = _scan_rot(languages, test_evidence, package_managers, git_state)

    manifests = _flatten_manifests(languages)
    proof = [
        f"manifests={len(manifests)}",
        f"languages={len(languages)}",
        f"package_managers={len(package_managers)}",
        f"test_evidence={len(test_evidence)}",
        f"runtime_scripts={len(runtime_scripts)}",
        f"verification_commands={len(verification_commands)}",
        f"is_git_repo={is_git}",
        f"git_status={git_state.get('status')}",
    ]

    return {
        "schema": SCHEMA,
        "repo_root": str(root),
        "risk_tier": RISK_TIER,
        "is_git_repo": is_git,
        "git_state": git_state,
        "languages_detected": languages,
        "package_managers": package_managers,
        "manifests": manifests,
        "test_evidence": test_evidence,
        "verification_commands": verification_commands,
        "runtime_scripts": runtime_scripts,
        "container_evidence": container_evidence,
        "ci_evidence": ci_evidence,
        "rot_signals": rot_signals,
        "proof": proof,
        "policy": "read-only filesystem introspection; target repo is never mutated",
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"Hydra repo audit: {report['repo_root']}",
        f"risk_tier: {report['risk_tier']}  is_git: {report['is_git_repo']}  "
        f"git: {report['git_state'].get('status')}",
        "languages:",
    ]
    if report["languages_detected"]:
        for lang in report["languages_detected"]:
            manifests = ", ".join(m["path"] for m in lang["manifests"])
            lines.append(f"  - {lang['language']}: {manifests}")
    else:
        lines.append("  - none detected")
    lines.append("package managers:")
    if report["package_managers"]:
        for pm in report["package_managers"]:
            lines.append(f"  - {pm['name']} ({pm['lockfile']})")
    else:
        lines.append("  - none detected")
    lines.append("test evidence:")
    if report["test_evidence"]:
        for t in report["test_evidence"]:
            extra = f" ({t['entry_count']} entries)" if "entry_count" in t else ""
            lines.append(f"  - {t['path']} [{t['kind']}]{extra}")
    else:
        lines.append("  - none detected")
    lines.append("verification commands:")
    if report["verification_commands"]:
        for v in report["verification_commands"]:
            lines.append(f"  - {v['command']}  # {v['rationale']}")
    else:
        lines.append("  - none suggested")
    lines.append("runtime scripts:")
    if report["runtime_scripts"]:
        for s in report["runtime_scripts"][:20]:
            lines.append(f"  - {s['source']} -> {s['name']}: {s['command']}")
        if len(report["runtime_scripts"]) > 20:
            lines.append(f"  - ... +{len(report['runtime_scripts']) - 20} more")
    else:
        lines.append("  - none detected")
    if report["container_evidence"]:
        lines.append("containers:")
        for c in report["container_evidence"]:
            lines.append(f"  - {c['path']}")
    if report["ci_evidence"]:
        lines.append("ci:")
        for c in report["ci_evidence"]:
            lines.append(f"  - {c['path']} [{c['system']}]")
    if report["rot_signals"]:
        lines.append("rot signals:")
        for s in report["rot_signals"]:
            lines.append(f"  - {s['id']} [{s['severity']}]: {s['detail']}")
    lines.append("proof:")
    for p in report["proof"]:
        lines.append(f"  - {p}")
    return "\n".join(lines) + "\n"


def _detect_languages(root: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for lang, manifests in _LANGUAGE_MANIFESTS.items():
        found: list[dict[str, str]] = []
        seen_paths: set[str] = set()
        for filename, kind in manifests:
            path = root / filename
            if path.is_file() and filename not in seen_paths:
                found.append({"path": filename, "kind": kind})
                seen_paths.add(filename)
        if found:
            results.append({"language": lang, "manifests": found})
    return results


def _flatten_manifests(languages: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for lang in languages:
        for m in lang["manifests"]:
            out.append({"language": lang["language"], "path": m["path"], "kind": m["kind"]})
    return out


def _detect_package_managers(root: Path) -> list[dict[str, str]]:
    return [
        {"name": name, "lockfile": lockfile}
        for lockfile, name in _PACKAGE_MANAGER_LOCKFILES
        if (root / lockfile).is_file()
    ]


def _detect_tests(root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for d in _TEST_DIRS:
        path = root / d
        if path.is_dir():
            try:
                count = sum(1 for _ in path.iterdir())
            except OSError:
                count = 0
            out.append({"path": d, "kind": "directory", "entry_count": count})
    for f in _TEST_FILES:
        if (root / f).is_file():
            out.append({"path": f, "kind": "config_file"})
    # pytest config embedded in pyproject.toml counts as test evidence
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8", errors="replace")[:MAX_MANIFEST_BYTES]
        except OSError:
            text = ""
        if "[tool.pytest" in text:
            out.append({"path": "pyproject.toml", "kind": "pytest_config"})
    return out


def _detect_scripts(root: Path) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    out.extend(_parse_makefile_targets(root))
    out.extend(_parse_package_json_scripts(root))
    out.extend(_parse_pyproject_scripts(root))
    return out


def _parse_makefile_targets(root: Path) -> list[dict[str, str]]:
    for name in ("Makefile", "makefile"):
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:MAX_MANIFEST_BYTES]
        except OSError:
            return []
        seen: set[str] = set()
        targets: list[dict[str, str]] = []
        for line in text.splitlines():
            m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_-]*)\s*:(?!=)", line)
            if not m:
                continue
            target = m.group(1)
            if target.startswith("."):
                continue
            if target in seen:
                continue
            seen.add(target)
            targets.append({"source": name, "name": target, "command": f"make {target}"})
        return targets
    return []


def _parse_package_json_scripts(root: Path) -> list[dict[str, str]]:
    path = root / "package.json"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")[:MAX_MANIFEST_BYTES]
        data = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return []
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return []
    runner = _node_runner(root)
    return [
        {"source": "package.json", "name": name, "command": f"{runner} run {name}"}
        for name in sorted(scripts.keys())
        if isinstance(name, str) and name
    ]


def _parse_pyproject_scripts(root: Path) -> list[dict[str, str]]:
    path = root / "pyproject.toml"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")[:MAX_MANIFEST_BYTES]
    except OSError:
        return []
    section: str | None = None
    out: list[dict[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            continue
        if section == "[project.scripts]" and "=" in stripped:
            key = stripped.split("=", 1)[0].strip().strip('"').strip("'")
            if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", key):
                out.append({"source": "pyproject.toml", "name": key, "command": key})
    return out


def _node_runner(root: Path) -> str:
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def _suggest_verifications(
    languages: list[dict[str, Any]],
    package_managers: list[dict[str, str]],
    runtime_scripts: list[dict[str, str]],
    test_evidence: list[dict[str, Any]],
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    lang_names = {lang["language"] for lang in languages}
    pm_names = {pm["name"] for pm in package_managers}

    if "python" in lang_names:
        out.append({"command": "pytest", "rationale": "python project detected"})
    if "javascript" in lang_names or "typescript" in lang_names:
        runner = "npm"
        if "pnpm" in pm_names:
            runner = "pnpm"
        elif "yarn" in pm_names:
            runner = "yarn"
        package_json_scripts = {
            s["name"] for s in runtime_scripts if s["source"] == "package.json"
        }
        if "test" in package_json_scripts:
            out.append({
                "command": f"{runner} test",
                "rationale": "package.json scripts.test present",
            })
    if "rust" in lang_names:
        out.append({"command": "cargo test", "rationale": "Cargo.toml detected"})
    if "go" in lang_names:
        out.append({"command": "go test ./...", "rationale": "go.mod detected"})
    if "ruby" in lang_names:
        out.append({"command": "bundle exec rake test", "rationale": "Gemfile detected"})
    if "java_kotlin" in lang_names:
        if any(m["path"].startswith("build.gradle") for m in _flatten_manifests(languages)):
            out.append({"command": "./gradlew test", "rationale": "Gradle build detected"})
        if any(m["path"] == "pom.xml" for m in _flatten_manifests(languages)):
            out.append({"command": "mvn test", "rationale": "Maven pom.xml detected"})

    for script in runtime_scripts:
        if script["source"] in ("Makefile", "makefile") and script["name"].lower() in _MAKE_TEST_TARGETS:
            out.append({
                "command": script["command"],
                "rationale": f"Makefile target {script['name']}",
            })
    return out


def _detect_containers(root: Path) -> list[dict[str, str]]:
    candidates = (
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    )
    return [{"path": name} for name in candidates if (root / name).is_file()]


def _detect_ci(root: Path) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    workflows = root / ".github" / "workflows"
    if workflows.is_dir():
        for path in sorted(workflows.iterdir()):
            if path.is_file() and path.suffix.lower() in (".yml", ".yaml"):
                out.append({"path": str(path.relative_to(root)), "system": "github_actions"})
    for name, system in (
        (".gitlab-ci.yml", "gitlab_ci"),
        (".travis.yml", "travis"),
        (".circleci/config.yml", "circle_ci"),
        ("Jenkinsfile", "jenkins"),
    ):
        if (root / name).is_file():
            out.append({"path": name, "system": system})
    return out


def _scan_rot(
    languages: list[dict[str, Any]],
    test_evidence: list[dict[str, Any]],
    package_managers: list[dict[str, str]],
    git_state: dict[str, Any],
) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    if not languages:
        signals.append({
            "id": "no_recognized_manifests",
            "severity": "yellow",
            "detail": "no recognized language manifests at repo root",
        })
    elif not test_evidence:
        signals.append({
            "id": "no_test_evidence",
            "severity": "yellow",
            "detail": "project has manifests but no detectable test directory or config",
        })
    js_present = any(l["language"] in {"javascript", "typescript"} for l in languages)
    js_pm_present = any(p["name"] in {"npm", "yarn", "pnpm"} for p in package_managers)
    if js_present and not js_pm_present:
        signals.append({
            "id": "node_missing_lockfile",
            "severity": "yellow",
            "detail": "package.json detected without an npm/yarn/pnpm lockfile",
        })
    if git_state.get("status") == "dirty":
        signals.append({
            "id": "git_dirty",
            "severity": "yellow",
            "detail": f"{git_state.get('changed', 0)} changed paths in target repo",
        })
    return signals


def _git_state(root: Path) -> dict[str, Any]:
    proc = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=10,
    )
    if proc.returncode != 0:
        return {"status": "error", "error": proc.stderr.strip()[:300]}
    changed = [line for line in proc.stdout.splitlines() if line.strip()]
    return {
        "status": "dirty" if changed else "clean",
        "changed": len(changed),
    }
