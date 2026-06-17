"""Live Telegram Bot API helpers.

The token is read from the process environment by default and is never returned
in reports or included in operator-facing errors.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from gateways.telegram.gate import ActionRequest, Decision, Gate
from hydra.workbench_approvals import ApprovalError, decide_request
# SEAM CUT: hydra.workbench_runs is stripped; update_run_after_approval guarded below.
try:
    from hydra.workbench_runs import RunError, update_run_after_approval as _update_run_after_approval
    _HAS_WORKBENCH_RUNS = True
except ImportError:
    _HAS_WORKBENCH_RUNS = False
    class RunError(Exception): ...  # type: ignore[no-redef]
    def _update_run_after_approval(*_a, **_kw):  # type: ignore[misc]
        raise RunError("workbench_runs not available in this build")


BOT_API_BASE = "https://api.telegram.org"
TOKEN_ENV_VAR = "TELEGRAM_BOT_TOKEN"
CHAT_ID_ENV_VAR = "TELEGRAM_CHAT_ID"
HYDRA_TOKEN_ENV_VAR = "HYDRA_TELEGRAM_BOT_TOKEN"
HYDRA_CHAT_ID_ENV_VAR = "HYDRA_TELEGRAM_CHAT_ID"
# The group chat id. Dedicated var so the group guard never keys off the
# operator's DM/approval target. Falls back to HYDRA_TELEGRAM_CHAT_ID for older setups.
HYDRA_GROUP_CHAT_ID_ENV_VAR = "HYDRA_GROUP_CHAT_ID"
# The operator's PRIVATE DM chat id — where permission/approval asks go and missions
# are run from (operator rule: "ask for permission in its own chat, NOT the
# group; the group is just agents talking/streaming"). Dedicated, optional var; if
# unset we fall back to the private TELEGRAM_CHAT_ID, never the group chat.
HYDRA_OPERATOR_DM_CHAT_ID_ENV_VAR = "HYDRA_OPERATOR_DM_CHAT_ID"
# This bot's @username, so the group guard can recognize @mentions and replies to it.
HYDRA_BOT_USERNAME_ENV_VAR = "HYDRA_TELEGRAM_BOT_USERNAME"
POLL_STATE_SCHEMA = "hydra.telegram.poll_state.v1"
TELEGRAM_ENV_FILES = (
    ".env.telegram",
    ".env",
    "~/.hydraAgent/workspace/.env.telegram",
    "~/.hydraAgent/workspace/.env",
)


class TelegramBotApiError(Exception):
    """Operator-facing Telegram Bot API failure."""


def token_from_env(env: dict[str, str] | None = None) -> str:
    source = telegram_env(process_env=env)
    token = source.get(HYDRA_TOKEN_ENV_VAR, "").strip()
    if not token:
        raise TelegramBotApiError(f"{HYDRA_TOKEN_ENV_VAR} is not set")
    return token


def chat_id_from_env(env: dict[str, str] | None = None) -> str:
    source = telegram_env(process_env=env)
    chat_id = source.get(HYDRA_CHAT_ID_ENV_VAR, "").strip()
    if not chat_id:
        raise TelegramBotApiError(f"{HYDRA_CHAT_ID_ENV_VAR} is not set")
    return chat_id


def operator_dm_chat_id_from_env(env: dict[str, str] | None = None) -> str:
    """Resolve the operator's PRIVATE DM chat id for permission/approval asks.

    Policy: an agent asks for permission and runs missions in its INDIVIDUAL chat —
    never the group chat (the group is only agents talking/streaming).
    Resolution order, skipping the group chat id: HYDRA_OPERATOR_DM_CHAT_ID (dedicated)
    -> TELEGRAM_CHAT_ID (the private bot chat) -> HYDRA_TELEGRAM_CHAT_ID. We refuse to
    return the group chat id so an approval can never land in the group.
    """
    source = telegram_env(process_env=env)
    group = source.get(HYDRA_GROUP_CHAT_ID_ENV_VAR, "").strip()
    for var in (HYDRA_OPERATOR_DM_CHAT_ID_ENV_VAR, CHAT_ID_ENV_VAR, HYDRA_CHAT_ID_ENV_VAR):
        cid = source.get(var, "").strip()
        if cid and cid != group:
            return cid
    raise TelegramBotApiError(
        "no operator DM chat id for approvals — set HYDRA_OPERATOR_DM_CHAT_ID (or "
        "TELEGRAM_CHAT_ID) to your PRIVATE chat with the bot, not the group chat"
    )


def telegram_env(
    *,
    repo_root: str | Path | None = None,
    process_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return process env plus ignored local Telegram env files.

    Process env wins. Local files are only a fallback so a new terminal can run
    Hydra without a manual `source .env` step. Values are never printed here.
    """
    source = dict(os.environ if process_env is None else process_env)
    loaded: dict[str, str] = {}
    base = Path(repo_root).expanduser() if repo_root is not None else Path.cwd()
    candidates = [
        base / ".env.telegram",
        base / ".env",
        Path("~/.hydraAgent/workspace/.env.telegram").expanduser(),
        Path("~/.hydraAgent/workspace/.env").expanduser(),
    ]
    for path in candidates:
        loaded.update(_parse_env_file(path))
    loaded.update(source)
    return loaded


def check_bot_health(
    *,
    token: str | None = None,
    timeout: float = 20,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    resolved_token = token.strip() if token is not None else token_from_env()
    if not resolved_token:
        raise TelegramBotApiError(f"{HYDRA_TOKEN_ENV_VAR} is not set")
    payload = _get_json(f"{BOT_API_BASE}/bot{resolved_token}/getMe", timeout=timeout, urlopen=urlopen)
    if payload.get("ok") is not True:
        raise TelegramBotApiError(_api_error_message(payload))
    result = payload.get("result")
    if not isinstance(result, dict):
        raise TelegramBotApiError("Telegram getMe response missing result")
    return {
        "schema": "hydra.telegram.health.v1",
        "ok": True,
        "id": result.get("id"),
        "username": result.get("username"),
        "first_name": result.get("first_name"),
        "is_bot": result.get("is_bot"),
        "can_join_groups": result.get("can_join_groups"),
        "can_read_all_group_messages": result.get("can_read_all_group_messages"),
        "supports_inline_queries": result.get("supports_inline_queries"),
    }


def render_health_text(report: dict[str, Any]) -> str:
    username = report.get("username") or "unknown"
    bot_id = report.get("id") or "unknown"
    is_bot = report.get("is_bot")
    return f"Telegram bot reachable: @{username} id={bot_id} is_bot={is_bot}\n"


def format_approval_message(approval: Any) -> str:
    tool_name = getattr(approval, "tool_name", "unknown")
    risk_tier = getattr(approval, "risk_tier", "unknown")
    summary = getattr(approval, "summary", "")
    arguments_preview = getattr(approval, "arguments_preview", {})
    task = _human_approval_task(tool_name, summary, arguments_preview)
    impact = _human_approval_impact(tool_name, risk_tier, arguments_preview)
    lines = ["Hydra needs your approval", ""]
    mission = _human_mission_class(risk_tier)
    if mission:
        lines.append(f"Mission: {mission}")
        lines.append("")
    lines += [
        f"Task: {task}",
        f"Impact: {impact}",
        "",
        "Tap Approve to let Hydra continue, or Deny to stop it.",
    ]
    return "\n".join(lines)


def _human_mission_class(risk_tier: Any) -> str:
    """Render a D4 mission-class risk_tier as plain English (§8 — no raw
    `mission:...` strings in the operator-facing summary). Tool-level tiers
    (T0..T3) return empty so ordinary approvals are unchanged."""
    tier = str(risk_tier or "")
    if not tier.startswith("mission:"):
        return ""
    return {
        "mission:dangerous": "Dangerous",
        "mission:destructive_or_off_lan": "Destructive or outside-the-LAN",
        "mission:huge_batch": "Large collaborative build",
    }.get(tier, tier.split(":", 1)[1].replace("_", " "))


def format_callback_response_message(callback_report: dict[str, Any]) -> str:
    decision = callback_report.get("decision")
    approval = callback_report.get("approval")
    tool_name = ""
    summary = ""
    risk_tier = ""
    arguments_preview: Any = {}
    if isinstance(approval, dict):
        tool_name = str(approval.get("tool_name") or "")
        summary = str(approval.get("summary") or "")
        risk_tier = str(approval.get("risk_tier") or "")
        arguments_preview = approval.get("arguments_preview") or {}
    task = _human_approval_task(tool_name, summary, arguments_preview)
    impact = _human_approval_impact(tool_name, risk_tier, arguments_preview)
    if decision == "approved":
        lines = [
            "✅ Accepted.",
            "",
            f"Task: {task}",
            f"Impact: {impact}",
            "",
            "Hydra is running it now.",
        ]
    elif decision == "denied":
        lines = [
            "🛑 Denied.",
            "",
            f"Task: {task}",
            "",
            "Hydra stopped that task.",
        ]
    else:
        lines = [
            "Hydra recorded the button response.",
            "",
            f"Task: {task}",
        ]
    return "\n".join(lines)


def _human_approval_task(tool_name: str, summary: str, arguments_preview: Any) -> str:
    command = ""
    path = ""
    if isinstance(arguments_preview, dict):
        raw_command = arguments_preview.get("command")
        raw_path = arguments_preview.get("path")
        if isinstance(raw_command, str):
            command = " ".join(raw_command.split())
        if isinstance(raw_path, str):
            path = raw_path.strip()
    if tool_name == "bash":
        return _human_bash_task(command, summary)
    if tool_name == "fs_write":
        if path:
            return f"write or replace the file `{_compact(path, 180)}`."
        return "write a file in the workspace."
    if tool_name == "fs_edit":
        if path:
            return f"edit the file `{_compact(path, 180)}`."
        return "edit a file in the workspace."
    if summary:
        return _sentence(_compact(_strip_approval_words(summary), 360))
    return "continue with an action that needs operator approval."


def _human_bash_task(command: str, summary: str) -> str:
    if command == "git status --short":
        return "check the current Git worktree status."
    if command == "git diff --check":
        return "check the current code changes for whitespace or patch formatting problems."
    if command.startswith("sudo systemctl restart "):
        service = command.removeprefix("sudo systemctl restart ").strip()
        return f"restart the `{_compact(service, 120)}` system service."
    if command.startswith("systemctl restart "):
        service = command.removeprefix("systemctl restart ").strip()
        return f"restart the `{_compact(service, 120)}` system service."
    if command.startswith("docker compose ") and " up" in command:
        return "start or recreate Docker Compose services."
    if command.startswith("docker compose ") and " down" in command:
        return "stop Docker Compose services."
    if summary:
        cleaned = _strip_approval_words(summary)
        if cleaned and cleaned != command:
            return _sentence(_compact(cleaned, 360))
    return "run a terminal command that is not in Hydra's automatic safe list."


def _human_approval_impact(tool_name: str, risk_tier: str, arguments_preview: Any) -> str:
    command = ""
    if isinstance(arguments_preview, dict) and isinstance(arguments_preview.get("command"), str):
        command = " ".join(arguments_preview["command"].split())
    if tool_name == "bash" and command in {"git status --short", "git diff --check"}:
        return "Read-only check. It should not change files or services."
    if tool_name in {"fs_write", "fs_edit"}:
        return "This changes files, so Hydra is waiting for your approval."
    words = set(command.split())
    if {"restart", "down", "up", "stop", "kill"} & words:
        return "This can affect running services, so Hydra is waiting for your approval."
    if risk_tier:
        return "This is outside the automatic safe lane, so Hydra is waiting for your approval."
    return "Hydra is waiting because this action needs operator approval."


def _strip_approval_words(text: str) -> str:
    cleaned = " ".join(text.strip().split())
    prefixes = (
        "Approve bash command:",
        "Approve bash command",
        "Approve command:",
        "Approve",
    )
    for prefix in prefixes:
        if cleaned.lower().startswith(prefix.lower()):
            cleaned = cleaned[len(prefix):].strip(" :-")
            break
    return cleaned


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return "continue with an action that needs operator approval."
    return text if text.endswith((".", "!", "?")) else f"{text}."


def send_message(
    *,
    text: str,
    reply_markup: dict[str, Any] | None = None,
    token: str | None = None,
    chat_id: str | None = None,
    message_thread_id: int | str | None = None,
    reply_to_message_id: int | str | None = None,
    timeout: float = 20,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    resolved_token = token.strip() if token is not None else token_from_env()
    resolved_chat_id = chat_id.strip() if chat_id is not None else chat_id_from_env()
    if not resolved_token:
        raise TelegramBotApiError(f"{HYDRA_TOKEN_ENV_VAR} is not set")
    if not resolved_chat_id:
        raise TelegramBotApiError(f"{HYDRA_CHAT_ID_ENV_VAR} is not set")
    form: dict[str, str] = {"chat_id": resolved_chat_id, "text": _telegram_safe_text(text)}
    if message_thread_id is not None:
        form["message_thread_id"] = str(message_thread_id)
    if reply_to_message_id is not None:
        form["reply_to_message_id"] = str(reply_to_message_id)
    if reply_markup is not None:
        form["reply_markup"] = json.dumps(reply_markup, separators=(",", ":"))
    payload = _post_json(
        f"{BOT_API_BASE}/bot{resolved_token}/sendMessage",
        form,
        method="sendMessage",
        timeout=timeout,
        urlopen=urlopen,
    )
    if payload.get("ok") is not True:
        raise TelegramBotApiError(_api_error_message(payload, method="sendMessage"))
    result = payload.get("result")
    if not isinstance(result, dict):
        raise TelegramBotApiError("Telegram sendMessage response missing result")
    chat = result.get("chat")
    chat_id_value = chat.get("id") if isinstance(chat, dict) else None
    return {
        "schema": "hydra.telegram.send_message.v1",
        "ok": True,
        "message_id": result.get("message_id"),
        "chat_id": chat_id_value,
    }


def send_chat_action(
    *,
    chat_id: str | None = None,
    action: str = "typing",
    token: str | None = None,
    timeout: float = 10,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Show the "typing…" indicator in the chat (Telegram sendChatAction).

    Best-effort: the operator sees Hydra is THINKING/working while a turn runs. Telegram
    shows the action for ~5s, so the listener re-sends it while a turn is in flight.
    """
    resolved_token = token.strip() if token is not None else token_from_env()
    resolved_chat_id = chat_id.strip() if chat_id is not None else chat_id_from_env()
    if not resolved_token or not resolved_chat_id:
        raise TelegramBotApiError("token or chat_id is not set for sendChatAction")
    payload = _post_json(
        f"{BOT_API_BASE}/bot{resolved_token}/sendChatAction",
        {"chat_id": resolved_chat_id, "action": action},
        method="sendChatAction",
        timeout=timeout,
        urlopen=urlopen,
    )
    return {"schema": "hydra.telegram.chat_action.v1", "ok": payload.get("ok") is True, "action": action}


def send_proof(
    *,
    text: str,
    token: str | None = None,
    chat_id: str | None = None,
    timeout: float = 20,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    normalized_text = text.strip()
    if not normalized_text:
        raise TelegramBotApiError("proof text must not be empty")
    delivery = send_message(
        text=normalized_text,
        token=token,
        chat_id=chat_id,
        timeout=timeout,
        urlopen=urlopen,
    )
    return {
        "schema": "hydra.telegram.proof_message.v1",
        "ok": True,
        "text": normalized_text,
        "message": delivery,
    }


def notify_approval(
    approval: Any,
    *,
    token: str | None = None,
    chat_id: str | None = None,
    timeout: float = 20,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    now: Any | None = None,
) -> dict[str, Any]:
    request_id = getattr(approval, "request_id", None)

    # Respect rest (operator rule): 1am-6am is quiet time. A routine approval
    # ping is HELD until 6am (the request stays queued; the operator just isn't
    # woken). A dangerous-mission gate is an emergency and breaks through. Only
    # applied when the caller passes a clock (`now`), so it's deterministic;
    # production passes datetime.now(), tests stay clock-free.
    if now is not None:
        from hydra.quiet_hours import classify_notification, HOLD

        risk_tier = str(getattr(approval, "risk_tier", "") or "")
        is_emergency = "dangerous" in risk_tier or bool(getattr(approval, "emergency", False))
        quiet_decision = classify_notification(is_emergency=is_emergency, now=now)
        if quiet_decision.action == HOLD:
            return {
                "schema": "hydra.telegram.approval_notification.v1",
                "ok": True,
                "held": True,
                "reason": quiet_decision.reason,
                "request_id": request_id,
                "run_id": getattr(approval, "run_id", None),
            }

    message = format_approval_message(approval)
    # Permission/approval asks go to the operator's PRIVATE DM, never the group chat
    # (operator rule). If the caller didn't pin a chat, resolve the DM —
    # this is the fix for approvals landing in the group because HYDRA_TELEGRAM_CHAT_ID
    # was the group chat id.
    target_chat_id = chat_id if chat_id is not None else operator_dm_chat_id_from_env()
    delivery = send_message(
        text=message,
        reply_markup=approval_reply_markup(str(request_id)) if request_id else None,
        token=token,
        chat_id=target_chat_id,
        timeout=timeout,
        urlopen=urlopen,
    )
    return {
        "schema": "hydra.telegram.approval_notification.v1",
        "ok": True,
        "request_id": getattr(approval, "request_id", None),
        "run_id": getattr(approval, "run_id", None),
        "message": delivery,
    }


def maybe_notify_auth_extension(
    *,
    token: str | None = None,
    chat_id: str | None = None,
    timeout: float = 20,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    auth: Any | None = None,
) -> dict[str, Any]:
    from hydra.operator_auth import OperatorAuth, OperatorAuthError

    resolved_auth = auth if auth is not None else OperatorAuth()
    try:
        poll = resolved_auth.prepare_extension_poll()
    except OperatorAuthError as exc:
        return {
            "schema": "hydra.telegram.auth_extension_notification.v1",
            "ok": False,
            "sent": False,
            "reason": str(exc),
        }
    if not poll.should_send:
        return {
            "schema": "hydra.telegram.auth_extension_notification.v1",
            "ok": True,
            "sent": False,
            "reason": poll.reason,
        }
    delivery = send_message(
        text=format_auth_extension_message(minutes_remaining=poll.minutes_remaining),
        reply_markup=auth_extension_reply_markup(poll.nonce),
        token=token,
        chat_id=chat_id,
        timeout=timeout,
        urlopen=urlopen,
    )
    return {
        "schema": "hydra.telegram.auth_extension_notification.v1",
        "ok": True,
        "sent": True,
        "nonce": poll.nonce,
        "minutes_remaining": poll.minutes_remaining,
        "message": delivery,
    }


def approval_reply_markup(request_id: str) -> dict[str, Any]:
    request_id = request_id.strip()
    if not request_id:
        raise TelegramBotApiError("approval request id must not be empty")
    return {
        "inline_keyboard": [
            [
                {"text": "Approve", "callback_data": f"approve:{request_id}"},
                {"text": "Deny", "callback_data": f"deny:{request_id}"},
            ]
        ]
    }


def format_auth_extension_message(*, minutes_remaining: int) -> str:
    minute_word = "minute" if int(minutes_remaining) == 1 else "minutes"
    return (
        "Hydra yolo mode is about to time out.\n\n"
        f"About {int(minutes_remaining)} {minute_word} remaining.\n\n"
        "Tap Extend to keep local and network authority open for one more hour."
    )


def auth_extension_reply_markup(nonce: str) -> dict[str, Any]:
    nonce = nonce.strip()
    if not nonce:
        raise TelegramBotApiError("auth extension nonce must not be empty")
    return {
        "inline_keyboard": [
            [
                {"text": "Extend yolo 1 hour", "callback_data": f"auth_extend:{nonce}"},
            ]
        ]
    }


def parse_auth_extension_callback(callback_data: str) -> str:
    if not isinstance(callback_data, str):
        raise ValueError("callback data must be a string")
    action, sep, nonce = callback_data.strip().partition(":")
    if action != "auth_extend" or sep != ":" or not nonce.strip():
        raise ValueError("callback data must be auth_extend:<nonce>")
    return nonce.strip()


def handle_auth_extension_callback(
    *,
    callback_data: str,
    auth: Any | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    from hydra.operator_auth import OperatorAuth

    nonce = parse_auth_extension_callback(callback_data)
    resolved_auth = auth if auth is not None else OperatorAuth()
    status = resolved_auth.extend_yolo(nonce, now=now)
    return {
        "schema": "hydra.telegram.auth_extension_callback.v1",
        "ok": True,
        "decision": "extended",
        "status": status.to_dict(),
    }


def format_auth_extension_callback_message(report: dict[str, Any]) -> str:
    status = report.get("status")
    seconds = None
    if isinstance(status, dict):
        seconds = status.get("expires_in_seconds")
    minutes = max(1, int((int(seconds) + 59) // 60)) if isinstance(seconds, int) else 60
    return (
        "Yolo extended.\n\n"
        f"Hydra has local and network authority for about {minutes} minutes."
    )


def render_approval_notification_text(report: dict[str, Any]) -> str:
    message = report.get("message")
    message_id = message.get("message_id") if isinstance(message, dict) else "unknown"
    return f"Telegram approval notification sent: request={report.get('request_id')} message_id={message_id}\n"


def render_proof_text(report: dict[str, Any]) -> str:
    message = report.get("message")
    message_id = message.get("message_id") if isinstance(message, dict) else "unknown"
    return f"Telegram proof sent: message_id={message_id}\n"


def parse_approval_callback(callback_data: str) -> tuple[str, str]:
    if not isinstance(callback_data, str):
        raise ValueError("callback data must be a string")
    action, sep, request_id = callback_data.strip().partition(":")
    if sep != ":" or not request_id.strip():
        raise ValueError("callback data must be approve:<request_id> or deny:<request_id>")
    if action == "approve":
        return "approved", request_id.strip()
    if action == "deny":
        return "denied", request_id.strip()
    raise ValueError("callback action must be approve or deny")


def handle_approval_callback(
    *,
    approval_path: Path,
    run_path: Path,
    callback_data: str,
    reason: str = "telegram callback",
) -> dict[str, Any]:
    decision, request_id = parse_approval_callback(callback_data)
    approval = decide_request(approval_path, request_id, decision, reason=reason)
    report: dict[str, Any] = {
        "schema": "hydra.telegram.approval_callback.v1",
        "ok": True,
        "request_id": request_id,
        "decision": decision,
        "approval": approval.to_dict(),
    }
    try:
        run = _update_run_after_approval(run_path, approval)
    except RunError as e:
        if "linked run not found" not in str(e):
            raise
    else:
        report["run"] = run.to_dict()
    return report


def answer_callback_query(
    *,
    callback_query_id: str,
    text: str,
    token: str | None = None,
    timeout: float = 20,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    resolved_token = token.strip() if token is not None else token_from_env()
    if not resolved_token:
        raise TelegramBotApiError(f"{HYDRA_TOKEN_ENV_VAR} is not set")
    payload = _post_json(
        f"{BOT_API_BASE}/bot{resolved_token}/answerCallbackQuery",
        {"callback_query_id": callback_query_id, "text": _compact(text, 180)},
        method="answerCallbackQuery",
        timeout=timeout,
        urlopen=urlopen,
    )
    if payload.get("ok") is not True:
        raise TelegramBotApiError(_api_error_message(payload, method="answerCallbackQuery"))
    return {
        "schema": "hydra.telegram.answer_callback_query.v1",
        "ok": True,
        "callback_query_id": callback_query_id,
    }


def get_updates(
    *,
    token: str | None = None,
    offset: int | None = None,
    poll_timeout: int = 0,
    timeout: float = 20,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> list[dict[str, Any]]:
    resolved_token = token.strip() if token is not None else token_from_env()
    if not resolved_token:
        raise TelegramBotApiError(f"{HYDRA_TOKEN_ENV_VAR} is not set")
    form = {
        "timeout": str(max(0, int(poll_timeout))),
        "allowed_updates": json.dumps(["callback_query", "message"]),
    }
    if offset is not None:
        form["offset"] = str(offset)
    payload = _post_json(
        f"{BOT_API_BASE}/bot{resolved_token}/getUpdates",
        form,
        method="getUpdates",
        timeout=timeout,
        urlopen=urlopen,
    )
    if payload.get("ok") is not True:
        raise TelegramBotApiError(_api_error_message(payload, method="getUpdates"))
    result = payload.get("result")
    if not isinstance(result, list):
        raise TelegramBotApiError("Telegram getUpdates response missing result list")
    return [item for item in result if isinstance(item, dict)]


def poll_once_approval_callbacks(
    *,
    approval_path: Path,
    run_path: Path,
    token: str | None = None,
    chat_id: str | None = None,
    offset: int | None = None,
    poll_timeout: int = 0,
    timeout: float = 20,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    operator_state: dict[str, Any] | None = None,
    agent_runner: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    try:
        auth_extension_report = maybe_notify_auth_extension(
            token=token,
            chat_id=chat_id,
            timeout=timeout,
            urlopen=urlopen,
        )
    except TelegramBotApiError as exc:
        auth_extension_report = {
            "schema": "hydra.telegram.auth_extension_notification.v1",
            "ok": False,
            "sent": False,
            "reason": f"send_message failed: {exc}",
        }
    updates = get_updates(
        token=token,
        offset=offset,
        poll_timeout=poll_timeout,
        timeout=timeout,
        urlopen=urlopen,
    )
    processed: list[dict[str, Any]] = []
    ignored_count = 0
    max_update_id: int | None = None
    operator_state = operator_state if operator_state is not None else {}
    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            max_update_id = update_id if max_update_id is None else max(max_update_id, update_id)
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            report = _process_callback_update(
                callback,
                approval_path=approval_path,
                run_path=run_path,
                token=token,
                chat_id=chat_id,
                timeout=timeout,
                urlopen=urlopen,
            )
            if report is None:
                ignored_count += 1
                continue
            processed.append(report)
            continue
        report = _process_operator_message_update(
            update,
            token=token,
            timeout=timeout,
            urlopen=urlopen,
            operator_state=operator_state,
            agent_runner=agent_runner,
        )
        if report is None:
            ignored_count += 1
            continue
        processed.append(report)
    return {
        "schema": "hydra.telegram.poll_once.v1",
        "ok": True,
        "processed_count": len(processed),
        "ignored_count": ignored_count,
        "next_offset": None if max_update_id is None else max_update_id + 1,
        "processed": processed,
        "auth_extension": auth_extension_report,
    }


def _process_callback_update(
    callback: dict[str, Any],
    *,
    approval_path: Path,
    run_path: Path,
    token: str | None,
    chat_id: str | None,
    timeout: float,
    urlopen: Callable[..., Any],
) -> dict[str, Any] | None:
    originating_chat_id: str | None = None
    message = callback.get("message")
    if isinstance(message, dict):
        chat = message.get("chat")
        if isinstance(chat, dict) and chat.get("id") is not None:
            originating_chat_id = str(chat["id"])
    callback_id = callback.get("id")
    callback_data = callback.get("data")
    if not isinstance(callback_id, str) or not isinstance(callback_data, str):
        return None
    if callback_data.startswith("auth_extend:"):
        try:
            report = handle_auth_extension_callback(callback_data=callback_data)
        except Exception:
            return None
        report["callback_ack"] = answer_callback_query(
            token=token,
            callback_query_id=callback_id,
            text="Hydra extended yolo mode",
            timeout=timeout,
            urlopen=urlopen,
        )
        try:
            report["callback_response"] = send_message(
                token=token,
                chat_id=originating_chat_id or chat_id,
                text=format_auth_extension_callback_message(report),
                timeout=timeout,
                urlopen=urlopen,
            )
        except TelegramBotApiError as exc:
            report["callback_response"] = {"ok": False, "error": f"send_message failed: {exc}"}
        report["offset_action"] = "advance"
        return report
    try:
        report = handle_approval_callback(
            approval_path=approval_path,
            run_path=run_path,
            callback_data=callback_data,
        )
    except (ApprovalError, RunError, ValueError) as exc:
        # NEVER silently drop a button press (operator: "I tap and see nothing").
        # Log the failure AND answer the callback so the operator gets a toast,
        # then surface it as a processed update (advances the offset) so the same
        # broken callback isn't retried forever.
        import logging

        logging.getLogger(__name__).warning(
            "approval callback failed: data=%r error=%s", callback_data, exc
        )
        error_report: dict[str, Any] = {
            "schema": "hydra.telegram.approval_callback.v1",
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "callback_data": callback_data,
        }
        try:
            error_report["callback_ack"] = answer_callback_query(
                token=token,
                callback_query_id=callback_id,
                text="Hydra couldn't apply that button — it may already be decided or expired.",
                timeout=timeout,
                urlopen=urlopen,
            )
        except TelegramBotApiError as ack_exc:
            error_report["callback_ack"] = {"ok": False, "error": f"answer_callback_query failed: {ack_exc}"}
        error_report["offset_action"] = "advance"
        return error_report
    # The button must clearly REACT when pushed (operator: it should say "Accepted").
    # This text shows as the Telegram toast on the button press.
    _decision = str(report.get("decision", "")).lower()
    if _decision in {"approve", "approved", "accept", "accepted", "yes"}:
        _ack_text = "✅ Accepted — Hydra is running it now."
    elif _decision in {"deny", "denied", "no", "reject", "rejected"}:
        _ack_text = "🛑 Denied — Hydra stopped it."
    else:
        _ack_text = f"Recorded: {report.get('decision')}"
    report["callback_ack"] = answer_callback_query(
        token=token,
        callback_query_id=callback_id,
        text=_ack_text,
        timeout=timeout,
        urlopen=urlopen,
    )
    try:
        report["callback_response"] = send_message(
            token=token,
            chat_id=originating_chat_id or chat_id,
            text=format_callback_response_message(report),
            timeout=timeout,
            urlopen=urlopen,
        )
    except TelegramBotApiError as exc:
        report["callback_response"] = {"ok": False, "error": f"send_message failed: {exc}"}
    if isinstance(callback.get("id"), str):
        report["offset_action"] = "advance"
    return report


def _resolve_group_chat_id() -> str:
    """The group chat id. Prefer the dedicated HYDRA_GROUP_CHAT_ID; fall
    back to HYDRA_TELEGRAM_CHAT_ID for older setups that overload that var as the group.
    The fallback is safe because the group guard only ever applies to GROUP chats (see
    _process_operator_message_update), so it can never silence a private operator DM."""
    group = os.environ.get(HYDRA_GROUP_CHAT_ID_ENV_VAR, "").strip()
    if group:
        return group
    return os.environ.get(HYDRA_CHAT_ID_ENV_VAR, "").strip()


def _group_message_addresses_bot(message: dict[str, Any], text: str) -> bool:
    """In a group chat, Hydra only answers when actually addressed: a slash command,
    an @mention of this bot, or a reply to this bot. Plain group chatter is left to
    the conversation orchestrator, so Hydra doesn't spam the room."""
    if text.lstrip().startswith("/"):
        return True
    username = os.environ.get(HYDRA_BOT_USERNAME_ENV_VAR, "").strip().lstrip("@")
    if username and ("@" + username) in text:
        return True
    reply = message.get("reply_to_message")
    if isinstance(reply, dict):
        rfrom = reply.get("from") or {}
        if rfrom.get("is_bot") and username and rfrom.get("username") == username:
            return True
    return False


def _forward_group_message_to_fabric(update: dict[str, Any], chat_id: Any) -> None:
    """Best-effort mirror of a group-chat message onto the local fabric board
    (POST /ask). Reads the group chat id from HYDRA_GROUP_CHAT_ID and the fabric base
    from HYDRA_FABRIC_BASE. Silent on any failure."""
    try:
        from gateways.telegram import group_relay

        group_raw = _resolve_group_chat_id()
        if not group_raw:
            return
        try:
            group_id = int(group_raw)
        except ValueError:
            return
        if chat_id is None or int(chat_id) != group_id:
            return
        fabric_base = os.environ.get("HYDRA_FABRIC_BASE")
        if not fabric_base:
            return  # fabric not configured; no-op

        def _post(url: str, body: dict[str, Any]) -> Any:
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 (LAN fabric)
                return resp.status

        group_relay.forward_group_to_fabric(update, group_id, fabric_base, _post)
    except Exception:  # noqa: BLE001 — relay is best-effort, never breaks the listener
        return


def _process_operator_message_update(
    update: dict[str, Any],
    *,
    token: str | None,
    timeout: float,
    urlopen: Callable[..., Any],
    operator_state: dict[str, Any],
    agent_runner: Callable[[str], Any] | None,
) -> dict[str, Any] | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    sender = message.get("from")
    if isinstance(sender, dict) and sender.get("is_bot") is True:
        return None
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("id") is None:
        return None
    originating_chat_id = str(chat["id"])

    # Group bridge: if this came from the group chat, mirror it onto the shared
    # fabric board so peer agents see the group chat too. Best-effort
    # — never let a relay hiccup break the operator listener.
    _forward_group_message_to_fabric(update, chat.get("id"))

    # REGRESSION FIX: don't treat group chat chatter as operator commands.
    # The guard ONLY applies to group/supergroup chats — a private operator DM (always
    # chat type "private") can never be muted, no matter how the group id is configured.
    # In the group, Hydra only answers when actually addressed (command, @mention, or
    # reply to it); other chats and DMs are unchanged, so it never spams the group.
    chat_type = chat.get("type")
    if chat_type in ("group", "supergroup"):
        group_id = _resolve_group_chat_id()
        if group_id and str(chat.get("id")) == group_id and not _group_message_addresses_bot(message, text):
            return None

    from gateways.telegram.operator import OperatorContext, handle_operator_message, is_likely_work_message

    sender_username = sender.get("username") if isinstance(sender, dict) else None
    sender_first_name = sender.get("first_name") if isinstance(sender, dict) else None
    message_id = message.get("message_id")
    message_thread_id = message.get("message_thread_id")
    update_id = update.get("update_id")
    report_preface: dict[str, Any] | None = None
    if is_likely_work_message(text):
        try:
            report_preface = send_message(
                token=token,
                chat_id=originating_chat_id,
                text="On it. I'll inspect the repo, use the right tools, and verify before I hand it back.",
                message_thread_id=message_thread_id if isinstance(message_thread_id, int) else None,
                reply_to_message_id=message_id if isinstance(message_id, int) else None,
                timeout=timeout,
                urlopen=urlopen,
            )
        except TelegramBotApiError as exc:
            report_preface = {"ok": False, "error": f"preface send failed: {exc}"}

    # TYPING INDICATOR (operator: "it should say typing while the agent is thinking").
    # Telegram shows "typing…" for ~5s, so a daemon thread re-sends it every few seconds
    # until the turn returns. Best-effort + daemon, so it can never block or break a turn.
    import threading

    _typing_stop = threading.Event()

    def _keep_typing() -> None:
        while not _typing_stop.is_set():
            try:
                send_chat_action(
                    chat_id=originating_chat_id, action="typing",
                    token=token, timeout=timeout, urlopen=urlopen,
                )
            except Exception:
                pass
            _typing_stop.wait(4.0)

    _typer = threading.Thread(target=_keep_typing, name="hydra-telegram-typing", daemon=True)
    _typer.start()
    try:
        reply = handle_operator_message(
            text,
            gate=Gate(_RejectingTelegramTransport()),
            state=operator_state,
            agent_runner=agent_runner,
            context=OperatorContext(
                chat_id=originating_chat_id,
                message_id=message_id if isinstance(message_id, int) else None,
                message_thread_id=message_thread_id if isinstance(message_thread_id, int) else None,
                username=sender_username if isinstance(sender_username, str) else None,
                first_name=sender_first_name if isinstance(sender_first_name, str) else None,
                chat_type=str(chat.get("type")) if chat.get("type") is not None else None,
            ),
        )
    finally:
        _typing_stop.set()
    report: dict[str, Any] = {
        "schema": "hydra.telegram.operator_message.v1",
        "ok": True,
        "status": reply.status,
        "text": reply.text,
        "data": reply.data,
        "chat_id": originating_chat_id,
        "message_id": message_id,
        "update_id": update_id,
        "message_thread_id": message_thread_id,
    }
    if report_preface is not None:
        report["preface"] = report_preface
    try:
        report["reply"] = send_message(
            token=token,
            chat_id=originating_chat_id,
            text=reply.text,
            message_thread_id=message_thread_id if isinstance(message_thread_id, int) else None,
            reply_to_message_id=message_id if isinstance(message_id, int) else None,
            timeout=timeout,
            urlopen=urlopen,
        )
        report["offset_action"] = "advance"
    except TelegramBotApiError as exc:
        report["reply"] = {"ok": False, "error": f"send_message failed: {exc}"}
        report["offset_action"] = "dead_letter" if isinstance(update_id, int) else "failed"
    return report


class _RejectingTelegramTransport:
    def prompt(self, request: ActionRequest) -> Decision:
        return Decision.REJECTED


def load_poll_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": POLL_STATE_SCHEMA, "next_offset": None}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise TelegramBotApiError(f"invalid Telegram poll state JSON: {e}") from e
    if not isinstance(raw, dict):
        raise TelegramBotApiError("Telegram poll state must be an object")
    if raw.get("schema") != POLL_STATE_SCHEMA:
        raise TelegramBotApiError("unsupported Telegram poll state schema")
    next_offset = raw.get("next_offset")
    if next_offset is not None and not isinstance(next_offset, int):
        raise TelegramBotApiError("Telegram poll state next_offset must be an integer or null")
    return {"schema": POLL_STATE_SCHEMA, "next_offset": next_offset}


def save_poll_state(path: Path, *, next_offset: int | None) -> dict[str, Any]:
    if next_offset is not None and not isinstance(next_offset, int):
        raise TelegramBotApiError("Telegram poll state next_offset must be an integer or null")
    state = {"schema": POLL_STATE_SCHEMA, "next_offset": next_offset}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return state


def _get_json(url: str, *, timeout: float, urlopen: Callable[..., Any]) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except Exception as e:
        raise TelegramBotApiError(f"Telegram getMe request failed: {type(e).__name__}") from e
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise TelegramBotApiError("Telegram getMe response was not valid JSON") from e
    if not isinstance(payload, dict):
        raise TelegramBotApiError("Telegram getMe response must be an object")
    return payload


def _post_json(
    url: str,
    form: dict[str, str],
    *,
    method: str,
    timeout: float,
    urlopen: Callable[..., Any],
) -> dict[str, Any]:
    data = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as e:
        # Surface the HTTP status AND the Telegram description so failures are
        # never invisible (previously this logged only "HTTPError"). The most
        # common cause here is 409 Conflict from a second getUpdates consumer on
        # the same bot. NOTE: we deliberately log status + description only --
        # never the request URL, because the URL embeds the bot token.
        status = getattr(e, "code", None)
        description = ""
        try:
            body = json.loads(e.read().decode("utf-8"))
            if isinstance(body, dict) and isinstance(body.get("description"), str):
                description = body["description"].strip()
        except Exception:
            description = ""
        detail = f"HTTP {status}" if status is not None else "HTTPError"
        if description:
            detail = f"{detail}: {description}"
        raise TelegramBotApiError(f"Telegram {method} request failed: {detail}") from e
    except Exception as e:
        raise TelegramBotApiError(f"Telegram {method} request failed: {type(e).__name__}") from e
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise TelegramBotApiError(f"Telegram {method} response was not valid JSON") from e
    if not isinstance(payload, dict):
        raise TelegramBotApiError(f"Telegram {method} response must be an object")
    return payload


def _telegram_safe_text(text: str, *, limit: int = 4096) -> str:
    if len(text) <= limit:
        return text
    suffix = "\n\n[truncated for Telegram]"
    return text[: max(0, limit - len(suffix))].rstrip() + suffix


def _api_error_message(payload: dict[str, Any], *, method: str = "getMe") -> str:
    description = payload.get("description")
    if isinstance(description, str) and description.strip():
        return f"Telegram {method} failed: {description.strip()}"
    return f"Telegram {method} failed"


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in {
            HYDRA_TOKEN_ENV_VAR,
            HYDRA_CHAT_ID_ENV_VAR,
            TOKEN_ENV_VAR,
            CHAT_ID_ENV_VAR,
            HYDRA_GROUP_CHAT_ID_ENV_VAR,
            HYDRA_BOT_USERNAME_ENV_VAR,
        }:
            out[key] = value
    return out


def _compact(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)] + "..."
