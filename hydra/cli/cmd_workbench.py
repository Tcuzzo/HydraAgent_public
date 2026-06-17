"""Workbench and operator-surface CLI command handlers (telegram subset only).

SEAM CUTS (hydra-public build):
  - hydra.workbench_api (stripped) → cmd_api removed
  - hydra.workbench_ledger (stripped) → cmd_ledger removed
  - hydra.telegram_listener_runtime → start_telegram_listener_if_configured removed
  - cmd_workbench removed (uses stripped modules)
  - Only cmd_telegram + telegram subparser registration are kept.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Callable

from gateways.telegram.live import (
    TelegramBotApiError,
    answer_callback_query,
    check_bot_health,
    format_callback_response_message,
    handle_approval_callback,
    notify_approval,
    render_approval_notification_text,
    render_health_text as render_telegram_health_text,
    render_proof_text,
    send_message,
    send_proof,
)
from hydra.workbench_approvals import (
    DEFAULT_APPROVAL_QUEUE_PATH,
    ApprovalError,
    load_records as load_approval_records,
)
# SEAM CUT: workbench_api, workbench_ledger, telegram_listener_runtime are stripped.
# ledger, api, workbench subcommands removed; only telegram subcommand is kept.


def register_workbench_commands(sub: argparse._SubParsersAction) -> None:
    # SEAM CUT: ledger/api/workbench parsers removed (stripped modules).

    p_telegram = sub.add_parser(
        "telegram",
        help="check Telegram Bot API reachability without printing tokens",
    )
    telegram_sub = p_telegram.add_subparsers(dest="telegram_cmd", required=True)
    p_telegram_health = telegram_sub.add_parser("health", help="check HYDRA_TELEGRAM_BOT_TOKEN with getMe")
    p_telegram_health.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON report",
    )
    p_telegram_notify = telegram_sub.add_parser(
        "notify-approval",
        help="send a pending approval summary to HYDRA_TELEGRAM_CHAT_ID",
    )
    p_telegram_notify.add_argument(
        "--approval-path",
        default=str(DEFAULT_APPROVAL_QUEUE_PATH),
        help=f"approval queue JSONL path (default: {DEFAULT_APPROVAL_QUEUE_PATH})",
    )
    p_telegram_notify.add_argument("--request-id", default=None, help="pending approval request id")
    p_telegram_notify.add_argument("--chat-id", default=None, help="Telegram chat id; default: HYDRA_TELEGRAM_CHAT_ID")
    p_telegram_notify.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON report",
    )
    p_telegram_proof = telegram_sub.add_parser(
        "send-proof",
        help="send an operator-facing proof/status message to HYDRA_TELEGRAM_CHAT_ID without approval callbacks",
    )
    p_telegram_proof.add_argument("--text", required=True, help="plain Telegram message body")
    p_telegram_proof.add_argument("--chat-id", default=None, help="Telegram chat id; default: HYDRA_TELEGRAM_CHAT_ID")
    p_telegram_proof.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON report",
    )
    p_telegram_callback = telegram_sub.add_parser(
        "handle-callback",
        help="apply one Telegram approval callback to local records",
    )
    p_telegram_callback.add_argument(
        "--approval-path",
        default=str(DEFAULT_APPROVAL_QUEUE_PATH),
        help=f"approval queue JSONL path (default: {DEFAULT_APPROVAL_QUEUE_PATH})",
    )
    p_telegram_callback.add_argument(
        "--run-path",
        default="evidence/workbench-runs/runs.jsonl",
        help="workbench run JSONL path (default: evidence/workbench-runs/runs.jsonl)",
    )
    p_telegram_callback.add_argument(
        "--callback-data",
        required=True,
        help="callback data in approve:<request_id> or deny:<request_id> form",
    )
    p_telegram_callback.add_argument(
        "--callback-query-id",
        default=None,
        help="optional Telegram callback query id to acknowledge",
    )
    p_telegram_callback.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON report",
    )
    p_telegram_poll = telegram_sub.add_parser(
        "poll-once",
        help="process one getUpdates batch for approval callbacks and operator messages",
    )
    p_telegram_poll.add_argument(
        "--approval-path",
        default=str(DEFAULT_APPROVAL_QUEUE_PATH),
        help=f"approval queue JSONL path (default: {DEFAULT_APPROVAL_QUEUE_PATH})",
    )
    p_telegram_poll.add_argument(
        "--run-path",
        default="evidence/workbench-runs/runs.jsonl",
        help="workbench run JSONL path (default: evidence/workbench-runs/runs.jsonl)",
    )
    p_telegram_poll.add_argument("--offset", type=int, default=None, help="Telegram update offset")
    p_telegram_poll.add_argument("--poll-timeout", type=int, default=0, help="Telegram getUpdates timeout")
    p_telegram_poll.add_argument(
        "--state-path",
        default=None,
        help="optional JSON file storing Telegram next_offset between poll-once runs",
    )
    p_telegram_poll.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON report",
    )
    p_telegram_listen = telegram_sub.add_parser(
        "listen",
        help="keep polling Telegram approval callbacks and operator messages until stopped",
    )
    p_telegram_listen.add_argument(
        "--approval-path",
        default=str(DEFAULT_APPROVAL_QUEUE_PATH),
        help=f"approval queue JSONL path (default: {DEFAULT_APPROVAL_QUEUE_PATH})",
    )
    p_telegram_listen.add_argument(
        "--run-path",
        default="evidence/workbench-runs/runs.jsonl",
        help="workbench run JSONL path (default: evidence/workbench-runs/runs.jsonl)",
    )
    p_telegram_listen.add_argument(
        "--state-path",
        default="evidence/telegram/poll_state.json",
        help="JSON file storing Telegram next_offset between polls",
    )
    p_telegram_listen.add_argument("--poll-timeout", type=int, default=10, help="Telegram getUpdates timeout")
    p_telegram_listen.add_argument("--interval", type=float, default=3.0, help="seconds between polls")


# SEAM CUT: cmd_ledger, cmd_api, cmd_workbench and helper functions removed
# (workbench_api, workbench_ledger, telegram_listener_runtime are stripped).
# Stub functions exported so __main__.py dispatch dict compiles.

def cmd_ledger(args: argparse.Namespace) -> int:  # pragma: no cover
    print("ledger command not available in this build", file=sys.stderr)
    return 1


def cmd_api(args: argparse.Namespace) -> int:  # pragma: no cover
    print("api command not available in this build", file=sys.stderr)
    return 1


def cmd_workbench(args: argparse.Namespace) -> int:  # pragma: no cover
    print("workbench command not available in this build", file=sys.stderr)
    return 1


def cmd_telegram(args: argparse.Namespace) -> int:
    if args.telegram_cmd == "health":
        try:
            report = check_bot_health(urlopen=_telegram_urlopen_for_cli("HYDRA_TELEGRAM_FAKE_GETME"))
        except TelegramBotApiError as e:
            print(f"telegram error: {e}", file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_telegram_health_text(report), end="")
        return 0
    if args.telegram_cmd == "notify-approval":
        try:
            approval = _select_approval(Path(args.approval_path), args.request_id)
            report = notify_approval(
                approval,
                chat_id=args.chat_id,
                urlopen=_telegram_urlopen_for_cli("HYDRA_TELEGRAM_FAKE_SENDMESSAGE"),
            )
        except (ApprovalError, TelegramBotApiError) as e:
            print(f"telegram error: {e}", file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_approval_notification_text(report), end="")
        return 0
    if args.telegram_cmd == "send-proof":
        try:
            report = send_proof(
                text=args.text,
                chat_id=args.chat_id,
                urlopen=_telegram_urlopen_for_cli("HYDRA_TELEGRAM_FAKE_SENDMESSAGE"),
            )
        except TelegramBotApiError as e:
            print(f"telegram error: {e}", file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_proof_text(report), end="")
        return 0
    if args.telegram_cmd == "handle-callback":
        try:
            report = handle_approval_callback(
                approval_path=Path(args.approval_path),
                run_path=Path(args.run_path),
                callback_data=args.callback_data,
            )
            if args.callback_query_id:
                report["callback_ack"] = answer_callback_query(
                    callback_query_id=args.callback_query_id,
                    text=f"Hydra recorded {report['decision']}",
                    urlopen=_telegram_urlopen_for_cli("HYDRA_TELEGRAM_FAKE_ANSWER_CALLBACK"),
                )
            report["callback_response"] = send_message(
                text=format_callback_response_message(report),
                urlopen=_telegram_urlopen_for_cli("HYDRA_TELEGRAM_FAKE_SENDMESSAGE"),
            )
        except (ApprovalError, TelegramBotApiError, ValueError) as e:
            print(f"telegram error: {e}", file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(
                f"Telegram callback handled: request={report.get('request_id')} decision={report.get('decision')}\n",
                end="",
            )
        return 0
    if args.telegram_cmd == "poll-once":
        try:
            from gateways.telegram.live import load_poll_state, poll_once_approval_callbacks, save_poll_state

            state_offset = None
            if args.state_path and args.offset is None:
                state_offset = load_poll_state(Path(args.state_path)).get("next_offset")
            effective_offset = args.offset if args.offset is not None else state_offset
            report = poll_once_approval_callbacks(
                approval_path=Path(args.approval_path),
                run_path=Path(args.run_path),
                offset=effective_offset,
                poll_timeout=args.poll_timeout,
                urlopen=_telegram_multi_urlopen_for_cli(
                    {
                        "getUpdates": "HYDRA_TELEGRAM_FAKE_GETUPDATES",
                        "answerCallbackQuery": "HYDRA_TELEGRAM_FAKE_ANSWER_CALLBACK",
                        "sendMessage": "HYDRA_TELEGRAM_FAKE_SENDMESSAGE",
                    }
                ),
            )
            report["offset"] = effective_offset
            if args.state_path and report.get("next_offset") is not None:
                save_poll_state(Path(args.state_path), next_offset=report["next_offset"])
        except (ApprovalError, TelegramBotApiError, ValueError) as e:
            print(f"telegram error: {e}", file=sys.stderr)
            return 1
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(
                f"Telegram poll-once processed {report.get('processed_count')} update(s); "
                f"next_offset={report.get('next_offset')}\n",
                end="",
            )
        return 0
    if args.telegram_cmd == "listen":
        try:
            from gateways.telegram.live import (
                load_poll_state,
                poll_once_approval_callbacks,
                save_poll_state,
                telegram_env,
            )

            # Hydrate the process env from the ignored .env.telegram files so the
            # group guard / bot-username / group-id are available no matter how the
            # listener was launched (a real terminal may not have sourced them).
            # Process env still wins (setdefault), matching telegram_env's contract.
            for _k, _v in telegram_env(repo_root=REPO_ROOT).items():
                os.environ.setdefault(_k, _v)

            # SINGLETON GUARD (one bot = one poller). Telegram allows exactly one
            # getUpdates consumer per bot; a second poller makes BOTH 409 Conflict
            # and the operator's Approve/Deny taps never arrive, silently breaking
            # the approval re-execution seam. Take a per-bot non-blocking lock; if a
            # live poller already holds it, refuse and exit cleanly. The kernel frees
            # the lock automatically when a poller dies, so an orphan never wedges a
            # fresh start.
            from hydra.file_lock import acquire_singleton_lock

            bot_username = os.environ.get("HYDRA_TELEGRAM_BOT_USERNAME", "").strip().lstrip("@") or "default"
            # Co-locate the per-bot lock with the listener's poll-state so it lives
            # in the same runtime dir (and is naturally test-isolated when callers
            # point --state-path elsewhere). acquire_singleton_lock() appends
            # ".lock", so pass the bare per-bot base (no doubled "...lock.lock").
            lock_dir = Path(args.state_path).resolve().parent
            lock_path = lock_dir / f"listener-{bot_username}"
            acquired, _lock_fd, holder_pid = acquire_singleton_lock(lock_path)
            if not acquired:
                holder = f" (held by pid {holder_pid})" if holder_pid else ""
                print(
                    f"telegram listener: another poller is already running for @{bot_username}{holder}; "
                    "refusing to start a second getUpdates consumer (one bot = one poller).",
                    file=sys.stderr,
                    flush=True,
                )
                return 1
            # Keep ``_lock_fd`` referenced for the whole process so the lock is held.
            os.environ["_HYDRA_TELEGRAM_LISTENER_HOLDS_LOCK"] = "1"

            print("Telegram listener started. Press Ctrl-C to stop.", flush=True)
            operator_state: dict[str, object] = {}
            while True:
                try:
                    state = load_poll_state(Path(args.state_path))
                    report = poll_once_approval_callbacks(
                        approval_path=Path(args.approval_path),
                        run_path=Path(args.run_path),
                        offset=state.get("next_offset"),
                        poll_timeout=args.poll_timeout,
                        operator_state=operator_state,
                    )
                    if report.get("next_offset") is not None:
                        save_poll_state(Path(args.state_path), next_offset=report["next_offset"])
                    processed = report.get("processed_count", 0)
                    if processed:
                        for item in report.get("processed", []):
                            if isinstance(item, dict):
                                print(_telegram_listener_processed_summary(item), flush=True)
                        print(f"Telegram listener processed {processed} update(s).", flush=True)
                except TelegramBotApiError as e:
                    print(f"telegram listener warning: {e}", file=sys.stderr, flush=True)
                time.sleep(max(0.1, float(args.interval)))
        except KeyboardInterrupt:
            print("\nTelegram listener stopped.")
            return 0
        except (ApprovalError, TelegramBotApiError, ValueError) as e:
            print(f"telegram error: {e}", file=sys.stderr)
            return 1
    print(f"telegram error: unsupported telegram command {args.telegram_cmd!r}", file=sys.stderr)
    return 1


def _telegram_listener_processed_summary(item: dict[str, object]) -> str:
    schema = str(item.get("schema") or "unknown")
    schema_parts = schema.split(".")
    kind = schema_parts[-2] if len(schema_parts) > 1 and schema_parts[-1].startswith("v") else schema_parts[-1]
    status = str(item.get("status") or item.get("decision") or "OK")
    context_parts: list[str] = []
    for key in ("update_id", "chat_id", "message_id"):
        value = item.get(key)
        if value is not None:
            context_parts.append(f"{key}={value}")
    data = item.get("data")
    route_kind = ""
    if isinstance(data, dict):
        raw_kind = data.get("kind")
        if raw_kind:
            route_kind = f" kind={raw_kind}"
    reply = item.get("reply") or item.get("callback_response")
    reply_state = "none"
    reply_error = ""
    if isinstance(reply, dict):
        reply_state = "sent" if reply.get("ok") is not False else "failed"
        error = reply.get("error")
        if isinstance(error, str) and error:
            reply_error = f" error={_compact_listener_field(error)}"
    context = " " + " ".join(context_parts) if context_parts else ""
    return f"Telegram listener processed {kind} status={status}{context}{route_kind} reply={reply_state}{reply_error}"


def _compact_listener_field(value: str, *, limit: int = 180) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def _select_approval(path: Path, request_id: str | None) -> object:
    records = load_approval_records(path)
    pending = [record for record in records if record.status == "pending"]
    if request_id:
        for record in pending:
            if record.request_id == request_id:
                return record
        raise ApprovalError(f"pending approval request not found: {request_id!r}")
    if not pending:
        raise ApprovalError("no pending approval requests")
    return pending[0]


def _telegram_urlopen_for_cli(env_var: str) -> Callable[..., object]:
    fake = os.environ.get(env_var)
    if not fake:
        return urllib.request.urlopen
    payload = fake.encode("utf-8")

    class _FakeResponse:
        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return payload

    def _fake_urlopen(_request: object, timeout: float = 0) -> _FakeResponse:
        return _FakeResponse()

    return _fake_urlopen


def _telegram_multi_urlopen_for_cli(routes: dict[str, str]) -> Callable[..., object]:
    payloads = {method: os.environ.get(env_var) for method, env_var in routes.items()}
    if not any(payloads.values()):
        return urllib.request.urlopen

    class _FakeResponse:
        def __init__(self, payload: str) -> None:
            self.payload = payload.encode("utf-8")

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return self.payload

    def _fake_urlopen(request: object, timeout: float = 0) -> _FakeResponse:
        url = getattr(request, "full_url", "")
        for method, payload in payloads.items():
            if payload and str(url).endswith(f"/{method}"):
                return _FakeResponse(payload)
        raise RuntimeError("missing fake Telegram response")

    return _fake_urlopen
