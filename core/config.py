"""Validating config loader. Thin function over YAML + JSON Schema.

Maturity: SCAFFOLDED. Will be promoted to PROVEN by the §10.3 loop running
the §10.2 planted-defect eval against this module.
"""
from __future__ import annotations

from pathlib import Path

import jsonschema
import yaml
from jsonschema.exceptions import best_match


class ConfigError(Exception):
    """Raised when a config file fails to load or validate.

    Attributes:
        path: the offending file
        message: human-readable explanation; first line is the operator-facing
            sentence, the rest (if any) is internal detail.
    """

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def load(config_path: str | Path, schema_path: str | Path) -> dict:
    """Load `config_path`, validate against `schema_path`, return the dict.

    Raises ConfigError on missing file, malformed YAML, empty document, or
    schema violation. Never returns a partially-valid object — the loader
    is the boundary; downstream code may assume the dict matches the schema.
    """
    config_path = Path(config_path)
    schema_path = Path(schema_path)

    if not config_path.is_file():
        raise ConfigError(config_path, "config file not found")
    if not schema_path.is_file():
        raise ConfigError(schema_path, "schema file not found")

    try:
        data = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(config_path, f"invalid YAML: {e}") from e
    if data is None:
        raise ConfigError(config_path, "config is empty")
    if not isinstance(data, dict):
        raise ConfigError(
            config_path,
            f"config root must be a mapping, got {type(data).__name__}",
        )

    schema = yaml.safe_load(schema_path.read_text())
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        e = _best_operator_error(errors, data)
        loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
        raise ConfigError(
            config_path, f"schema violation at {loc}: {e.message}"
        ) from e

    return data


def _best_operator_error(
    errors: list[jsonschema.ValidationError],
    data: dict | None = None,
) -> jsonschema.ValidationError:
    """Pick a useful field-named error.

    `oneOf` schemas often surface a generic root error while the real
    actionable cause lives in `error.context`. Prefer the nested error whose
    message names a missing/additional field; fall back to jsonschema's
    normal best_match heuristic.
    """
    candidates: list[jsonschema.ValidationError] = []
    stack = list(errors)
    while stack:
        err = stack.pop(0)
        candidates.append(err)
        stack.extend(err.context)
    branch = None
    if isinstance(data, dict):
        if "time_budget_seconds" in data:
            branch = 0
        elif "budget" in data or "instruction" in data or "verdict_path" in data:
            branch = 1
    if branch is not None:
        branch_candidates = [
            err
            for err in candidates
            if len(err.absolute_schema_path) >= 2
            and err.absolute_schema_path[0] == "oneOf"
            and err.absolute_schema_path[1] == branch
        ]
        if branch_candidates:
            candidates = branch_candidates
    for validator in ("additionalProperties", "type", "enum", "required"):
        for err in candidates:
            if err.validator == validator:
                return err
    return best_match(errors)
