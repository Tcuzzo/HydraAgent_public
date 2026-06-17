"""Inter-agent message envelopes and trace context for Hydra runtime work."""
from __future__ import annotations

import contextlib
import contextvars
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


PROTOCOL_VERSION = "1.0"
DEFAULT_TTL_SECONDS = 30
DEFAULT_PRIORITY = "medium"

INTER_AGENT_ROLES = {
    "operator",
    "orchestrator",
    "planner",
    "doer",
    "auditor",
    "worker",
    "reviewer",
    "harness_builder",
    "repo_auditor",
    "capability_cartographer",
    "adversarial_reviewer",
    "tool",
    "subagent",
    "broadcast",
}
MESSAGE_TYPES = {"request", "response", "event", "command", "broadcast", "heartbeat"}
MESSAGE_STATUSES = {"pending", "in-flight", "completed", "failed", "timeout"}
PRIORITIES = {"low", "medium", "high", "critical"}

_TRACE_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "hydra_trace_id",
    default=None,
)
_CORRELATION_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "hydra_correlation_id",
    default=None,
)
_SECRET_LINE = re.compile(
    r"(?i)\b([A-Za-z0-9_.-]*(?:api[_-]?key|token|secret|password|authorization|bearer|cookie)[A-Za-z0-9_.-]*)\b"
    r"\s*[:=]\s*('[^']*'|\"[^\"]*\"|[^\s,}]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_SECRET_JSON_STRING = re.compile(
    r'("([A-Za-z0-9_.-]*(?:api[_-]?key|token|secret|password|authorization|bearer|cookie)[A-Za-z0-9_.-]*)"\s*:\s*)"([^"]*)"'
)


INTER_AGENT_PROTOCOL_PROMPT = """
INTER-AGENT COMMUNICATION PROTOCOL -- MANDATORY

Hydra agents operate through a shared orchestrator message bus plus direct
subagent handoffs. When you coordinate with another agent, preserve the
message envelope discipline:

- Every top-level operator request has one trace_id. Propagate that trace_id to
  every subagent request, tool handoff, review, result, and escalation.
- Valid message roles are: operator, orchestrator, planner, doer, auditor,
  worker, reviewer, harness_builder, repo_auditor, capability_cartographer,
  adversarial_reviewer, tool, subagent, and broadcast.
- Never send messages to an agent role that does not exist. Check the role
  manifest or tool schema first.
- Use command messages for orchestrator-to-agent work, request/response for
  direct questions, event messages for completion or escalation, broadcast only
  when all agents need the information, and heartbeat only for liveness.
- Preserve correlation_id when responding to a request. Start a new
  correlation_id only for a new sub-conversation under the same trace_id.
- Respect ttl_seconds. On timeout, retry once when useful; if it still fails,
  send a failed event to the orchestrator with the error and evidence.
- Log sent/received messages with timestamp, message id, from, to, type, status,
  and trace_id. Redact API keys, tokens, cookies, passwords, bearer strings, and
  OAuth secrets before logging or summarizing evidence.
- Never impersonate another role, never process a message addressed to a
  different role unless it is broadcast, and never bypass the destructive-action
  approval policy.

Failure to follow this protocol is a system-level bug. If you cannot comply,
escalate to the orchestrator with a failed event and the trace_id.
""".strip()


def new_trace_id() -> str:
    return str(uuid.uuid4())


def current_trace_id() -> str | None:
    return _TRACE_ID.get()


def current_correlation_id() -> str | None:
    return _CORRELATION_ID.get()


@contextlib.contextmanager
def use_trace_id(trace_id: str | None):
    token = _TRACE_ID.set(trace_id or new_trace_id())
    try:
        yield _TRACE_ID.get()
    finally:
        _TRACE_ID.reset(token)


@contextlib.contextmanager
def use_correlation_id(correlation_id: str | None):
    token = _CORRELATION_ID.set(correlation_id)
    try:
        yield _CORRELATION_ID.get()
    finally:
        _CORRELATION_ID.reset(token)


def ensure_trace_id(trace_id: str | None = None) -> str:
    active = trace_id or current_trace_id()
    if active:
        return active
    return new_trace_id()


def create_message(
    *,
    from_role: str,
    to_role: str,
    message_type: str,
    action: str,
    trace_id: str | None = None,
    correlation_id: str | None = None,
    context: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    status: str = "pending",
    error: str | None = None,
    retry_count: int = 0,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    priority: str = DEFAULT_PRIORITY,
    message_id: str | None = None,
) -> dict[str, Any]:
    """Create the canonical Hydra inter-agent envelope."""
    message = {
        "envelope": {
            "id": message_id or str(uuid.uuid4()),
            "timestamp": _utc_now(),
            "ttl_seconds": int(ttl_seconds),
            "priority": priority,
            "trace_id": ensure_trace_id(trace_id),
            "correlation_id": correlation_id,
        },
        "header": {
            "from": from_role,
            "to": to_role,
            "type": message_type,
            "protocol_version": PROTOCOL_VERSION,
        },
        "payload": {
            "action": action,
            "context": redact_value(context or {}),
            "data": redact_value(data or {}),
        },
        "meta": {
            "status": status,
            "error": redact_value(error),
            "retry_count": int(retry_count),
        },
    }
    _raise_if_invalid(message)
    return message


def validate_message(
    message: dict[str, Any],
    *,
    role_manifest: Iterable[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    for field in ("envelope", "header", "payload", "meta"):
        if field not in message:
            errors.append(f"missing top-level field: {field}")
    if errors:
        return errors

    envelope = message.get("envelope")
    header = message.get("header")
    payload = message.get("payload")
    meta = message.get("meta")
    if not isinstance(envelope, dict):
        errors.append("envelope must be an object")
        envelope = {}
    if not isinstance(header, dict):
        errors.append("header must be an object")
        header = {}
    if not isinstance(payload, dict):
        errors.append("payload must be an object")
        payload = {}
    if not isinstance(meta, dict):
        errors.append("meta must be an object")
        meta = {}

    for field in ("id", "timestamp", "ttl_seconds", "priority", "trace_id"):
        if field not in envelope or envelope.get(field) is None or envelope.get(field) == "":
            errors.append(f"missing envelope field: {field}")
    if envelope.get("priority") not in PRIORITIES:
        errors.append(f"invalid priority: {envelope.get('priority')!r}")
    try:
        if int(envelope.get("ttl_seconds", -1)) < 0:
            errors.append("ttl_seconds must be >= 0")
    except (TypeError, ValueError):
        errors.append("ttl_seconds must be an integer")

    for field in ("from", "to", "type", "protocol_version"):
        if not header.get(field):
            errors.append(f"missing header field: {field}")
    if header.get("type") not in MESSAGE_TYPES:
        errors.append(f"invalid message type: {header.get('type')!r}")
    if header.get("protocol_version") != PROTOCOL_VERSION:
        errors.append(f"invalid protocol_version: {header.get('protocol_version')!r}")

    roles = set(role_manifest or INTER_AGENT_ROLES)
    for key in ("from", "to"):
        role = header.get(key)
        if role and role not in roles:
            errors.append(f"unknown role in header.{key}: {role}")

    if not payload.get("action"):
        errors.append("missing payload field: action")
    if meta.get("status") not in MESSAGE_STATUSES:
        errors.append(f"invalid status: {meta.get('status')!r}")
    try:
        if int(meta.get("retry_count", -1)) < 0:
            errors.append("retry_count must be >= 0")
    except (TypeError, ValueError):
        errors.append("retry_count must be an integer")
    return errors


def make_subagent_prompt(
    task: str,
    *,
    envelope: dict[str, Any],
) -> str:
    return (
        "INTER-AGENT COMMAND ENVELOPE\n"
        f"{json.dumps(envelope, sort_keys=True)}\n\n"
        "Execute the payload.data.task as the worker task. Preserve the trace_id "
        "in all tool work, events, and final evidence. Return a concise final "
        "answer with what changed, evidence, blockers, and status.\n\n"
        f"WORKER TASK:\n{task}"
    )


def log_message(root: Path, message: dict[str, Any]) -> Path:
    """Append a redacted bus record under the workspace memory directory."""
    _raise_if_invalid(message)
    bus_path = Path(root).expanduser().resolve() / ".hydraAgent" / "inter_agent_bus.jsonl"
    bus_path.parent.mkdir(parents=True, exist_ok=True)
    header = message.get("header") if isinstance(message.get("header"), dict) else {}
    envelope = message.get("envelope") if isinstance(message.get("envelope"), dict) else {}
    meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
    record = {
        "timestamp": _utc_now(),
        "message_id": envelope.get("id"),
        "trace_id": envelope.get("trace_id"),
        "from": header.get("from"),
        "to": header.get("to"),
        "type": header.get("type"),
        "status": meta.get("status"),
        "message": redact_value(message),
    }
    with bus_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    return bus_path


def read_bus(
    root: Path,
    *,
    trace_id: str | None = None,
    to_role: str | None = None,
    message_type: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Read inter-agent bus records so peers can see each other's messages.

    The complement of :func:`log_message`. Records are returned oldest-first.
    Filters compose:
      - ``trace_id`` restricts to one cohort's conversation (the usual scope
        for a parallel spawn — all peers share the parent's trace_id).
      - ``to_role`` returns messages addressed to that role PLUS broadcasts,
        which is what an agent checking its own inbox wants.
      - ``message_type`` restricts to one envelope type.
      - ``limit`` keeps only the most recent N after filtering.
    """
    bus_path = Path(root).expanduser().resolve() / ".hydraAgent" / "inter_agent_bus.jsonl"
    if not bus_path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in bus_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if trace_id is not None and record.get("trace_id") != trace_id:
            continue
        if to_role is not None and record.get("to") not in {to_role, "broadcast"}:
            continue
        if message_type is not None and record.get("type") != message_type:
            continue
        records.append(record)
    if limit is not None and limit >= 0:
        records = records[-limit:]
    return records


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if _secret_key(str(key)):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = redact_value(item)
        return redacted
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def redact_text(text: str | None) -> str | None:
    if text is None:
        return None
    value = _SECRET_JSON_STRING.sub(r'\1"[redacted]"', str(text))
    value = _SECRET_LINE.sub(r"\1=[redacted]", value)
    return _BEARER.sub("Bearer [redacted]", value)


def _raise_if_invalid(message: dict[str, Any]) -> None:
    errors = validate_message(message)
    if errors:
        raise ValueError("; ".join(errors))


def _secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(
        marker in lowered
        for marker in ("api_key", "apikey", "token", "secret", "password", "authorization", "bearer", "cookie")
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
