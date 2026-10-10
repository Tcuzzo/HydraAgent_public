"""BACKS dev-mode seam — the hallucination-resistance floor for HydraAgent.

Loads the dev-mode invariants, playbook, and sniper / anti-mock-theater policies
from ``.hydraAgent/``, exposes the dev-mode doctrine that is injected into the
agent system prompt, and provides the grounding guards that make the runtime
hallucination-resistant. The guards resolve referenced symbols against the
installed artifact (real ``importlib`` + ``getattr``) and refute fabricated
claims against the real file tree. Real side-effects only — the seam whose
behavior is the proof is never mocked.

This module is intentionally dependency-light: stdlib + PyYAML only, so the
seam loads on any clean install with no model provider configured.
"""
from __future__ import annotations

import importlib
import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEV_MODE_ROOT = REPO_ROOT / ".hydraAgent"
BUNDLED_DEV_MODE_ROOT = Path(__file__).resolve().parent / "runtime_data" / "catalog"

# (contract key, relative path, top-level list key or None)
_CONTRACTS: dict[str, tuple[str, str | None]] = {
    "invariants": ("policies/backs-invariants.yaml", "invariants"),
    "sniper": ("policies/sniper-testing.yaml", "rules"),
    "anti_mock": ("policies/anti-mock-theater.yaml", "rules"),
    "playbook": ("playbooks/dev-mode-elite-build.yaml", None),
}

# Dotted tokens that are files / versions / prose, not code symbols. The guard
# must not flag a README.md reference as a hallucination.
_NON_SYMBOL_SUFFIX = re.compile(
    r"\.(md|markdown|yaml|yml|py|pyc|json|txt|rst|sh|toml|cfg|ini|so|lock|gz|png|jpg|jpeg|svg|ts|js|tsx|jsx|rs|go|c|h|cpp|hpp)$",
    re.IGNORECASE,
)
_VERSION_LIKE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*\.\d+\.\d+\b")
# A code symbol: identifier with one or more dotted attribute parts. The first
# part is a module-ish name (lowercase letters / underscores), not a number.
_SYMBOL = re.compile(r"\b([a-z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+)\b")


@dataclass(frozen=True)
class SymbolResolution:
    """Result of resolving one dotted symbol against the installed artifact."""

    symbol: str
    resolved: bool
    evidence: str


@dataclass(frozen=True)
class RefutationResult:
    """Drain-refute result for a claim that references symbols/quotes."""

    claim: str
    refuted: bool
    flagged: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data or {}


def load_dev_mode_contracts(root: str | Path | None = None) -> dict[str, Any]:
    """Load explicit contracts or the installed/source public defaults.

    Returns a dict with one entry per contract plus a ``loaded`` map and the
    resolved ``root``. A missing contract is reported as loaded=False (loud),
    never silently faked.
    """
    if root is not None:
        base = Path(root).expanduser().resolve()
    else:
        base = BUNDLED_DEV_MODE_ROOT if BUNDLED_DEV_MODE_ROOT.is_dir() else DEV_MODE_ROOT
    out: dict[str, Any] = {}
    for key, (rel, _) in _CONTRACTS.items():
        out[key] = _read_yaml(base / rel)
    out["loaded"] = {k: bool(v) for k, v in out.items()}
    out["root"] = str(base)
    return out


def _guard_names(playbook: dict[str, Any]) -> list[str]:
    guards = playbook.get("anti_hallucination_guards") or []
    names: list[str] = []
    for item in guards:
        if isinstance(item, dict):
            names.extend(item.keys())
        elif isinstance(item, str):
            names.append(item)
    return names


def build_backs_dev_mode_doctrine(root: str | Path | None = None) -> str:
    """Build the dev-mode doctrine injected into the agent system prompt.

    The doctrine is assembled from the REAL loaded YAML so a test can assert that
    an invariant title actually present in the file appears in the prompt — no
    canned constant. If the contracts are not loaded, the doctrine says so LOUD
    (no silent gate): grounding has collapsed to model priors.
    """
    contracts = load_dev_mode_contracts(root)
    loaded = contracts["loaded"]
    if not all(loaded.values()):
        missing = [k for k, ok in loaded.items() if not ok]
        return (
            "BACKS dev-mode seam — NOT LOADED (loud, not silent).\n"
            f"Missing contracts: {', '.join(missing)}.\n"
            "Grounding has collapsed to model priors — the exact mechanism that "
            "produces fabricated APIs, invented quotes, and confident nonsense. "
            "Load the seam from .hydraAgent/ before any answer ships."
        )

    inv = contracts["invariants"]
    sniper = contracts["sniper"]
    anti_mock = contracts["anti_mock"]
    playbook = contracts["playbook"]
    guard_names = _guard_names(playbook)

    lines: list[str] = [
        "BACKS dev-mode seam (hallucination-resistance floor)",
        "",
        f"Loaded from {contracts['root']}. A skill named but not invoked did not happen.",
        "Ground in these invariants BEFORE reasoning. The agent never invents; "
        "it verifies.",
        "",
        f"## Invariants — {inv.get('name', 'BACKS dev-mode invariants')}",
    ]
    for item in inv.get("invariants", []) or []:
        if not isinstance(item, dict):
            continue
        lines.append(f"- {item.get('id', '?')}: {item.get('title', '')} — {item.get('rule', '')}")

    lines.append("")
    lines.append(f"## Environment play — {playbook.get('name', 'dev-mode-elite-build')}")
    for stage in playbook.get("stages", []) or []:
        if isinstance(stage, dict):
            lines.append(f"- {stage.get('stage', '?')}: {stage.get('question', '')}")
    lines.append("Repair loop: A reproduce on live truth -> B RED contract -> "
                 "C fix the class at the seam -> D verify on the real path -> "
                 "E independent grade -> F concurrent sessions -> G land.")

    lines.append("")
    lines.append(f"## Sniper testing — {sniper.get('name', '')}")
    for rule in sniper.get("rules", []) or []:
        if isinstance(rule, dict):
            lines.append(f"- {rule.get('rule', '')}")

    lines.append("")
    lines.append(f"## Anti-mock-theater — {anti_mock.get('name', '')}")
    for rule in anti_mock.get("rules", []) or []:
        if isinstance(rule, dict):
            lines.append(f"- {rule.get('rule', '')}")

    lines.append("")
    lines.append("## Anti-hallucination guards (runtime, not prose)")
    for name in guard_names:
        lines.append(f"- {name}")
    lines.append("Run resolve_symbol_against_repo on every referenced symbol before "
                 "an answer ships. Unresolved = hallucination, blocked. Run "
                 "refute_fabricated_claim to falsify before you confirm — an organ "
                 "that can only confirm is an echo, not understanding.")

    lines.append("")
    lines.append("## Anthropolithic parser (prose is the spec, not a rough draft)")
    lines.append("Parse carrier vs payload. Ground in the human's record and repo "
                 "truth. Deduce intent. Never take a metaphor as a literal "
                 "instruction; never caricature the dialect; never invent. A thin "
                 "anchor is labelled thin — it is never filled in. See "
                 "engineering-grounding/principles/anthropolithic_engine.md.")
    return "\n".join(lines)


def resolve_symbol_against_repo(symbol: str, root: str | Path | None = None) -> SymbolResolution:
    """Resolve a dotted symbol against the installed artifact. Real importlib.

    ``symbol`` is a dotted path like ``hydra.skill_spine.route_skill_names`` or
    ``hydra.frobnicate``. The longest importable-module prefix is found with
    ``importlib.util.find_spec``; the remaining parts are walked with ``getattr``.
    Any missing module or attribute => resolved=False with evidence. Zero mocks:
    this is the real resolution the runtime uses to catch fabricated APIs.
    """
    if not symbol or "." not in symbol:
        return SymbolResolution(symbol, False, f"not a dotted symbol: {symbol!r}")
    parts = symbol.split(".")
    mod_path: str | None = None
    attr_chain: list[str] = []
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        try:
            spec = importlib.util.find_spec(candidate)
        except (ModuleNotFoundError, ValueError):
            spec = None
        if spec is not None:
            mod_path = candidate
            attr_chain = parts[i:]
            break
    if mod_path is None:
        return SymbolResolution(
            symbol, False,
            f"no importable module prefix of {symbol!r} (find_spec returned None for all prefixes)",
        )
    try:
        module = importlib.import_module(mod_path)
    except Exception as exc:  # pragma: no cover - import failures are loud evidence
        return SymbolResolution(
            symbol, False, f"import of {mod_path!r} raised {type(exc).__name__}: {exc}",
        )
    obj: Any = module
    for attr in attr_chain:
        if hasattr(obj, attr):
            obj = getattr(obj, attr)
        else:
            full = mod_path + ("." + ".".join(attr_chain[: attr_chain.index(attr)])) if attr_chain else mod_path
            return SymbolResolution(
                symbol, False, f"attribute {attr!r} not found on {full!r}",
            )
    tail = "." + ".".join(attr_chain) if attr_chain else ""
    return SymbolResolution(symbol, True, f"resolved: {mod_path}{tail}")


def extract_referenced_symbols(text: str) -> list[str]:
    """Extract dotted code symbols referenced in a claim. Filters files/versions."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _SYMBOL.finditer(text or ""):
        sym = match.group(1)
        if _NON_SYMBOL_SUFFIX.search(sym):
            continue
        if _VERSION_LIKE.match(sym):
            continue
        if sym in seen:
            continue
        seen.add(sym)
        found.append(sym)
    return found


def refute_fabricated_claim(claim: str, root: str | Path | None = None) -> RefutationResult:
    """Drain-refute a claim by resolving every referenced symbol against the repo.

    Extract each dotted symbol, resolve it with ``resolve_symbol_against_repo``,
    and flag any that do not resolve. If any symbol is unresolved, the claim is
    refuted (it contains a reference to something that does not exist in the
    installed artifact — the signature of a hallucination). This is the honesty
    proof: the guard can only confirm is an echo.
    """
    flagged: list[str] = []
    evidence: list[str] = []
    for sym in extract_referenced_symbols(claim):
        res = resolve_symbol_against_repo(sym, root)
        if not res.resolved:
            flagged.append(sym)
            evidence.append(f"{sym}: {res.evidence}")
    return RefutationResult(claim=claim or "", refuted=bool(flagged), flagged=flagged, evidence=evidence)


def dev_mode_doctor_report(root: str | Path | None = None) -> dict[str, Any]:
    """Report which dev-mode contracts loaded. For the CLI doctor and the seam test."""
    contracts = load_dev_mode_contracts(root)
    playbook = contracts["playbook"]
    return {
        "schema": "hydra.dev_mode_seam.doctor.v1",
        "status": "OK" if all(contracts["loaded"].values()) else "WARN",
        "root": contracts["root"],
        "contracts_loaded": contracts["loaded"],
        "invariant_count": len((contracts["invariants"].get("invariants") or [])),
        "playbook_stages": [
            s.get("stage") for s in (playbook.get("stages") or []) if isinstance(s, dict)
        ],
        "anti_hallucination_guards": _guard_names(playbook),
    }


__all__ = [
    "DEV_MODE_ROOT",
    "SymbolResolution",
    "RefutationResult",
    "load_dev_mode_contracts",
    "build_backs_dev_mode_doctrine",
    "resolve_symbol_against_repo",
    "extract_referenced_symbols",
    "refute_fabricated_claim",
    "dev_mode_doctor_report",
]
