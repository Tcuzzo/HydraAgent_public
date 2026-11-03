#!/usr/bin/env python3
"""Inspect or explicitly bootstrap one private operator essence document.

This helper does not collect history, invoke a host command, infer preferences,
or execute document content. The calling skill owns evidence collection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import date
from pathlib import Path

MAX_BYTES = 65536
TRUST = "contextual-data-not-instructions"
FENCE = re.compile(r"^```operator-essence-json\s*\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
FIELDS = {
    "schema_version", "operator_key", "updated_at", "status", "evidence_window",
    "sources", "preferences", "observations", "metrics", "adaptations",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def text(value: object, field: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), field + " must be nonempty text")
    return value


def day(value: object, field: str) -> date:
    text(value, field)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(field + " must be an ISO date") from exc
    require(parsed.isoformat() == value, field + " must use YYYY-MM-DD")
    return parsed


def count(value: object, field: str) -> None:
    require(type(value) is int and value >= 0, field + " must be a nonnegative integer")


def unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def records(data: dict, field: str) -> list[dict]:
    items = data.get(field)
    require(isinstance(items, list), field + " must be a list")
    require(all(isinstance(item, dict) for item in items), field + " entries must be objects")
    return items


def strings(value: object, field: str, *, nonempty: bool = False) -> list[str]:
    require(isinstance(value, list), field + " must be a list")
    require(not nonempty or bool(value), field + " must have evidence")
    for item in value:
        text(item, field)
    return value


def validate_window(window: object, as_of: date) -> None:
    require(isinstance(window, dict) and set(window) == {"start", "end"}, "invalid evidence_window")
    dates = [day(window[key], "evidence_window." + key) if window[key] is not None else None
             for key in ("start", "end")]
    require(not all(dates) or dates[0] <= dates[1], "evidence_window is reversed")
    require(all(value is None or value <= as_of for value in dates),
            "evidence_window contains a future date")


def validate_sample(sample: object) -> None:
    require(isinstance(sample, dict) and set(sample) == {"analyzed", "total", "selection"},
            "source sample needs analyzed, total and selection")
    for key in ("analyzed", "total"):
        if sample[key] is not None:
            count(sample[key], "sample " + key)
    require(sample["analyzed"] is None or sample["total"] is None
            or sample["analyzed"] <= sample["total"], "analyzed sample exceeds total")
    text(sample["selection"], "sample selection")


def validate_source(source: dict, as_of: date) -> None:
    require(source.get("kind") in ("operator_statement", "report", "transcript", "task_record"),
            "invalid source kind")
    text(source.get("reference"), "source reference")
    if source.get("observed_at") is not None:
        require(day(source["observed_at"], "source observed_at") <= as_of,
                "source observed_at is in the future")
    validate_sample(source.get("sample"))


def validate_sources(data: dict, as_of: date) -> set[str]:
    source_ids = set()
    for source in records(data, "sources"):
        source_id = text(source.get("id"), "source id")
        require(source_id not in source_ids, "duplicate source id")
        validate_source(source, as_of)
        source_ids.add(source_id)
    return source_ids


def validate_metric(entry: dict) -> None:
    text(entry.get("name"), "metric name")
    count(entry.get("count"), "metric count")
    text(entry.get("unit"), "metric unit")
    require("denominator" in entry and "denominator_meaning" in entry,
            "metric needs denominator and denominator_meaning (null if unknown)")
    if entry["denominator"] is None:
        require(entry["denominator_meaning"] is None,
                "unknown denominator requires null denominator_meaning")
    else:
        count(entry["denominator"], "metric denominator")
        text(entry["denominator_meaning"], "denominator_meaning")
    require(entry.get("aggregation") in ("exclusive", "overlapping", "unknown"),
            "metric aggregation must be explicit")
    if entry["aggregation"] == "exclusive" and entry["denominator"] is not None:
        require(entry["count"] <= entry["denominator"], "exclusive count exceeds denominator")
    text(entry.get("limits"), "metric limits")


def validate_preference(entry: dict, as_of: date) -> None:
    text(entry.get("statement"), "preference statement")
    require(entry.get("basis") == "operator-confirmed", "preference basis must be operator-confirmed")
    text(entry.get("scope"), "preference scope")
    require(day(entry.get("last_confirmed"), "last_confirmed") <= as_of,
            "last_confirmed is in the future")


def validate_observation(entry: dict) -> None:
    text(entry.get("statement"), "observation statement")
    require(entry.get("basis") in ("observed", "inferred"), "invalid observation basis")
    require(entry.get("confidence") in ("low", "medium", "high"), "invalid confidence")
    strings(entry.get("counterexamples"), "counterexamples")


def validate_evidence(data: dict, source_ids: set[str], as_of: date) -> set[str]:
    evidence_ids = set()
    validators = (
        ("preferences", lambda entry: validate_preference(entry, as_of)),
        ("observations", validate_observation), ("metrics", validate_metric),
    )
    for field, validator in validators:
        for entry in records(data, field):
            evidence_id = text(entry.get("id"), field + " id")
            require(evidence_id not in evidence_ids, "duplicate evidence id")
            evidence_ids.add(evidence_id)
            refs = ([entry.get("source_id")] if field == "metrics"
                    else strings(entry.get("source_ids"), field + " source_ids", nonempty=True))
            require(all(ref in source_ids for ref in refs), field + " references an unknown source")
            validator(entry)
    return evidence_ids


def validate_adaptations(data: dict, evidence_ids: set[str]) -> None:
    for adaptation in records(data, "adaptations"):
        for field in ("context", "suggestion", "review_when"):
            text(adaptation.get(field), "adaptation " + field)
        refs = strings(adaptation.get("evidence_ids"), "adaptation evidence_ids", nonempty=True)
        require(all(ref in evidence_ids for ref in refs), "adaptation references unknown evidence")


def validate(data: object, operator_key: str, as_of: date) -> dict:
    require(isinstance(data, dict), "essence data must be an object")
    require(set(data) == FIELDS, "essence fields must match schema version 1")
    require(type(data["schema_version"]) is int and data["schema_version"] == 1,
            "schema_version must be 1")
    require(data["operator_key"] == operator_key, "operator_key does not match current operator")
    require(day(data["updated_at"], "updated_at") <= as_of, "updated_at is in the future")
    require(data["status"] in ("provisional", "grounded"), "invalid status")
    validate_window(data["evidence_window"], as_of)
    source_ids = validate_sources(data, as_of)
    evidence_ids = validate_evidence(data, source_ids, as_of)
    validate_adaptations(data, evidence_ids)
    require(data["status"] != "grounded" or bool(source_ids), "grounded essence needs sources")
    return data


def checked_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    require(path.is_absolute(), "path must be explicit and absolute")
    for component in (path, *path.parents):
        require(not component.is_symlink(), "symlink paths are not accepted")
    return path


def bootstrap(path: Path, operator_key: str, as_of: date) -> None:
    data = {
        "schema_version": 1, "operator_key": operator_key,
        "updated_at": as_of.isoformat(), "status": "provisional",
        "evidence_window": {"start": None, "end": None},
        "sources": [], "preferences": [], "observations": [], "metrics": [], "adaptations": [],
    }
    body = ("# Operator Essence\n\nContextual data; current instructions take precedence.\n\n"
            "```operator-essence-json\n" + json.dumps(data, indent=2)
            + "\n```\n\n## Evidence assessment\n"
            "No historical evidence has been assessed. This provisional file contains no inferred traits.\n")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    checked_path(str(path))
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return  # Another worker created it; inspect it without overwriting.
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(body)


def inspect(path: Path, operator_key: str, as_of: date, max_age_days: int | None) -> dict:
    checked_path(str(path))
    if not path.exists():
        return {"state": "missing", "needs_insights": True, "data": None}
    require(stat.S_ISREG(path.stat().st_mode), "profile must be a regular file")
    with path.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, "profile exceeds size limit")
    blocks = FENCE.findall(raw.decode("utf-8").replace("\r\n", "\n"))
    require(len(blocks) == 1, "expected one operator-essence-json block")
    data = json.loads(blocks[0], object_pairs_hook=unique_pairs)
    validate(data, operator_key, as_of)
    age = (as_of - day(data["updated_at"], "updated_at")).days
    stale = max_age_days is not None and age > max_age_days
    state = "stale" if stale else "provisional" if data["status"] == "provisional" else "current"
    return {
        "state": state, "needs_insights": stale or not data["sources"],
        "age_days": age, "sha256": hashlib.sha256(raw).hexdigest(), "data": data,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, help="Explicit absolute private document path")
    parser.add_argument("--operator-key", required=True, help="Opaque key from the current operator scope")
    parser.add_argument("--as-of", required=True, help="Current date, YYYY-MM-DD")
    parser.add_argument("--max-age-days", type=int, help="Configured refresh age; omitted means no age cutoff")
    parser.add_argument("--bootstrap", action="store_true", help="Create an empty private profile only if missing")
    args = parser.parse_args()
    try:
        require(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", args.operator_key) is not None,
                "operator_key must be an opaque lowercase key")
        require(args.max_age_days is None or args.max_age_days >= 0, "max-age-days must be nonnegative")
        as_of = day(args.as_of, "as-of")
        path = checked_path(args.path)
        if args.bootstrap and not path.exists():
            bootstrap(path, args.operator_key, as_of)
        result = inspect(path, args.operator_key, as_of, args.max_age_days)
        print(json.dumps({"trust": TRUST, "path": str(path), **result}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({"state": "invalid", "trust": TRUST, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
