"""Operator TOTP and timed yolo authority state.

Secrets live outside the repo. Runtime state stores timestamps and nonces only.
"""
from __future__ import annotations

import base64
import getpass
import hmac
import hashlib
import json
import os
import secrets
import socket
import struct
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SECRET_SCHEMA = "hydra.operator_auth_secret.v1"
AUTH_STATE_SCHEMA = "hydra.operator_auth_state.v1"
DEFAULT_AUTH_DIR = Path("~/.hydraAgent/workspace/auth").expanduser()
AUTH_DIR_ENV_VAR = "HYDRA_OPERATOR_AUTH_DIR"
DEFAULT_ISSUER = "HydraAgent"
DEFAULT_ACCOUNT = "operator"
DEFAULT_TOTP_PERIOD_SECONDS = 30
DEFAULT_TOTP_DIGITS = 6
DEFAULT_YOLO_TTL_SECONDS = 3600
EXTENSION_POLL_WINDOW_SECONDS = 300
EXTENSION_NONCE_TTL_SECONDS = 600
VALID_MODES = frozenset({"operator", "iteration", "yolo"})
_IS_WINDOWS = os.name == "nt"


class OperatorAuthError(Exception):
    """Raised when operator auth setup or unlock fails."""


@dataclass(frozen=True)
class AuthSetup:
    secret_base32: str
    provisioning_uri: str
    secret_path: Path
    issuer: str
    account: str


@dataclass(frozen=True)
class AuthStatus:
    mode: str
    yolo_active: bool
    local_authority: bool
    network_authority: bool
    unlocked_until: float | None
    last_operator_activity_at: float | None
    expires_in_seconds: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "hydra.operator_auth_status.v1",
            "mode": self.mode,
            "yolo_active": self.yolo_active,
            "local_authority": self.local_authority,
            "network_authority": self.network_authority,
            "unlocked_until": self.unlocked_until,
            "last_operator_activity_at": self.last_operator_activity_at,
            "expires_in_seconds": self.expires_in_seconds,
        }


@dataclass(frozen=True)
class ExtensionPoll:
    should_send: bool
    nonce: str
    minutes_remaining: int
    reason: str


class OperatorAuth:
    def __init__(
        self,
        *,
        auth_dir: str | Path | None = None,
        hostname: str | None = None,
        ttl_seconds: int = DEFAULT_YOLO_TTL_SECONDS,
    ) -> None:
        env_auth_dir = os.environ.get(AUTH_DIR_ENV_VAR)
        selected_auth_dir = auth_dir if auth_dir is not None else env_auth_dir
        self.auth_dir = Path(selected_auth_dir).expanduser().resolve() if selected_auth_dir else DEFAULT_AUTH_DIR
        self.hostname = hostname or socket.gethostname()
        self.ttl_seconds = int(ttl_seconds)

    @property
    def secret_path(self) -> Path:
        return self.auth_dir / "totp.json"

    @property
    def state_path(self) -> Path:
        return self.auth_dir / "unlock_state.json"

    def setup_totp(self, *, now: float | None = None, force: bool = False) -> AuthSetup:
        if self.secret_path.exists() and not force:
            raw = self._read_json(self.secret_path)
            secret = str(raw.get("secret_base32") or "")
            issuer = str(raw.get("issuer") or DEFAULT_ISSUER)
            account = str(raw.get("account") or self._default_account())
        else:
            secret = generate_secret_base32()
            issuer = DEFAULT_ISSUER
            account = self._default_account()
            self._write_json_private(
                self.secret_path,
                {
                    "schema": SECRET_SCHEMA,
                    "issuer": issuer,
                    "account": account,
                    "secret_base32": secret,
                    "created_at": float(time.time() if now is None else now),
                },
            )
        uri = provisioning_uri(secret, issuer=issuer, account=account)
        return AuthSetup(
            secret_base32=secret,
            provisioning_uri=uri,
            secret_path=self.secret_path,
            issuer=issuer,
            account=account,
        )

    def unlock_yolo(self, code: str, *, now: float | None = None) -> AuthStatus:
        current_time = _now(now)
        secret = self.load_secret()
        if not verify_totp(secret, code, at_time=current_time):
            raise OperatorAuthError("invalid authenticator code")
        state = self._base_state()
        state.update(
            {
                "mode": "yolo",
                "unlocked_until": current_time + self.ttl_seconds,
                "last_operator_activity_at": current_time,
                "extension_nonce": None,
                "extension_expires_at": None,
                "extension_notified_at": None,
            }
        )
        self._write_json_private(self.state_path, state)
        return self.status(now=current_time)

    def set_mode(self, mode: str, *, now: float | None = None) -> AuthStatus:
        mode = mode.strip().lower()
        if mode not in {"operator", "iteration"}:
            raise OperatorAuthError("set_mode supports operator or iteration; use unlock_yolo for yolo")
        state = self._base_state()
        state.update(
            {
                "mode": mode,
                "unlocked_until": None,
                "last_operator_activity_at": _now(now),
                "extension_nonce": None,
                "extension_expires_at": None,
                "extension_notified_at": None,
            }
        )
        self._write_json_private(self.state_path, state)
        return self.status(now=now)

    def lock(self, *, now: float | None = None) -> AuthStatus:
        return self.set_mode("operator", now=now)

    def record_operator_activity(self, *, now: float | None = None) -> AuthStatus:
        current_time = _now(now)
        state = self._load_state()
        if state.get("mode") == "yolo" and self._state_yolo_active(state, current_time):
            state["unlocked_until"] = current_time + self.ttl_seconds
        state["last_operator_activity_at"] = current_time
        self._write_json_private(self.state_path, state)
        return self.status(now=current_time)

    def prepare_extension_poll(self, *, now: float | None = None) -> ExtensionPoll:
        current_time = _now(now)
        state = self._load_state()
        remaining = self._remaining_seconds(state, current_time)
        if remaining is None:
            return ExtensionPoll(False, "", 0, "yolo is not active")
        if remaining > EXTENSION_POLL_WINDOW_SECONDS:
            return ExtensionPoll(False, "", max(1, int(remaining // 60)), "too early")
        existing_nonce = str(state.get("extension_nonce") or "")
        existing_expires_at = state.get("extension_expires_at")
        if (
            existing_nonce
            and isinstance(existing_expires_at, (int, float))
            and current_time <= float(existing_expires_at)
        ):
            return ExtensionPoll(
                False,
                existing_nonce,
                max(1, int((remaining + 59) // 60)),
                "extension poll already sent",
            )
        nonce = secrets.token_urlsafe(12)
        state["extension_nonce"] = nonce
        state["extension_expires_at"] = current_time + EXTENSION_NONCE_TTL_SECONDS
        state["extension_notified_at"] = current_time
        self._write_json_private(self.state_path, state)
        return ExtensionPoll(True, nonce, max(1, int((remaining + 59) // 60)), "expiry window")

    def extend_yolo(self, nonce: str, *, now: float | None = None) -> AuthStatus:
        current_time = _now(now)
        state = self._load_state()
        expected = str(state.get("extension_nonce") or "")
        expires_at = state.get("extension_expires_at")
        if not expected or not hmac.compare_digest(expected, str(nonce)):
            raise OperatorAuthError("invalid extension nonce")
        if not isinstance(expires_at, (int, float)) or current_time > float(expires_at):
            raise OperatorAuthError("extension nonce expired")
        state.update(
            {
                "mode": "yolo",
                "unlocked_until": current_time + self.ttl_seconds,
                "last_operator_activity_at": current_time,
                "extension_nonce": None,
                "extension_expires_at": None,
                "extension_notified_at": None,
            }
        )
        self._write_json_private(self.state_path, state)
        return self.status(now=current_time)

    def status(self, *, now: float | None = None) -> AuthStatus:
        current_time = _now(now)
        state = self._load_state()
        mode = str(state.get("mode") or "operator")
        if mode not in VALID_MODES:
            mode = "operator"
        yolo_active = mode == "yolo" and self._state_yolo_active(state, current_time)
        if mode == "yolo" and not yolo_active:
            state.update({"mode": "operator", "unlocked_until": None})
            self._write_json_private(self.state_path, state)
            mode = "operator"
        unlocked_until = state.get("unlocked_until")
        unlocked_until_float = float(unlocked_until) if isinstance(unlocked_until, (int, float)) else None
        remaining = self._remaining_seconds(state, current_time) if yolo_active else None
        last_activity = state.get("last_operator_activity_at")
        return AuthStatus(
            mode=mode,
            yolo_active=yolo_active,
            local_authority=yolo_active,
            network_authority=yolo_active,
            unlocked_until=unlocked_until_float if yolo_active else None,
            last_operator_activity_at=float(last_activity) if isinstance(last_activity, (int, float)) else None,
            expires_in_seconds=int(remaining) if remaining is not None else None,
        )

    def load_secret(self) -> str:
        if not self.secret_path.exists():
            raise OperatorAuthError("operator authenticator is not set up; run /mfa setup")
        raw = self._read_json(self.secret_path)
        if raw.get("schema") != SECRET_SCHEMA:
            raise OperatorAuthError("unsupported operator auth secret schema")
        secret = str(raw.get("secret_base32") or "").strip()
        if not secret:
            raise OperatorAuthError("operator auth secret is empty")
        return secret

    def _default_account(self) -> str:
        return f"{DEFAULT_ACCOUNT}@{self.hostname}"

    def _base_state(self) -> dict[str, Any]:
        return {
            "schema": AUTH_STATE_SCHEMA,
            "mode": "operator",
            "unlocked_until": None,
            "last_operator_activity_at": None,
            "extension_nonce": None,
            "extension_expires_at": None,
            "extension_notified_at": None,
        }

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._base_state()
        raw = self._read_json(self.state_path)
        if raw.get("schema") != AUTH_STATE_SCHEMA:
            return self._base_state()
        state = self._base_state()
        state.update(raw)
        return state

    def _state_yolo_active(self, state: dict[str, Any], now: float) -> bool:
        unlocked_until = state.get("unlocked_until")
        return isinstance(unlocked_until, (int, float)) and now <= float(unlocked_until)

    def _remaining_seconds(self, state: dict[str, Any], now: float) -> float | None:
        if not self._state_yolo_active(state, now):
            return None
        unlocked_until = state.get("unlocked_until")
        return max(0.0, float(unlocked_until) - now) if isinstance(unlocked_until, (int, float)) else None

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OperatorAuthError(f"could not read operator auth file {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise OperatorAuthError(f"operator auth file {path} must contain a JSON object")
        return raw

    def _write_json_private(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _restrict_to_owner(path.parent, mode=0o700, directory=True)
        _write_text_private(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


# -- private-file primitives ---------------------------------------------------
#
# The operator's TOTP secret must be readable by the operator's own account and
# nothing else. POSIX and Windows spell that property differently, so both
# spellings live here -- and both fail LOUDLY, because a secret we could not make
# private is a secret we must not silently write.


def _restrict_to_owner(path: Path, *, mode: int, directory: bool = False) -> None:
    """Make ``path`` accessible to the current account only.

    POSIX gets ``mode`` (0o600 / 0o700). Windows has no POSIX permission bits:
    ``os.chmod(path, 0o600)`` there only toggles the read-only attribute and
    leaves the *inherited* DACL -- which normally grants ``BUILTIN\\Users`` --
    fully intact, so a chmod'd secret stays readable by every other local
    account. ``icacls`` is Windows' own mechanism for what 0o600 means on POSIX:
    drop the inherited ACEs, then grant exactly one principal.
    """
    if not _IS_WINDOWS:
        try:
            os.chmod(path, mode)
        except OSError as exc:
            raise OperatorAuthError(
                f"could not restrict {path} to mode {mode:#o}: {exc}; refusing to "
                "leave operator auth state readable by other accounts"
            ) from exc
        return

    account = getpass.getuser()
    # (OI)(CI): a directory's restriction inherits to the files created inside it.
    grant = f"{account}:(OI)(CI)(F)" if directory else f"{account}:(F)"
    try:
        proc = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OperatorAuthError(
            f"could not restrict {path} to {account}: icacls unusable ({exc}); "
            "refusing to leave operator auth state readable by other accounts"
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise OperatorAuthError(
            f"could not restrict {path} to {account}: icacls exited "
            f"{proc.returncode}: {detail}; refusing to leave operator auth state "
            "readable by other accounts"
        )


def _write_text_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path``, private from the moment the file exists.

    The restriction is applied to an *empty* file first, so the secret bytes are
    never on disk under a permissive mode/ACL -- closing the window that the old
    write-then-chmod order left open.
    """
    if _IS_WINDOWS:
        path.write_text("", encoding="utf-8")
        _restrict_to_owner(path, mode=0o600)
        path.write_text(text, encoding="utf-8")
        return

    # O_CREAT applies `mode` only to a newly created file, so fchmod covers the
    # already-exists case. Both land before a single secret byte is written.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        handle = os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise
    with handle:
        handle.write(text)


def generate_secret_base32(byte_count: int = 20) -> str:
    return base64.b32encode(secrets.token_bytes(byte_count)).decode("ascii").rstrip("=")


def generate_totp(
    secret_base32: str,
    *,
    for_time: float | None = None,
    period: int = DEFAULT_TOTP_PERIOD_SECONDS,
    digits: int = DEFAULT_TOTP_DIGITS,
) -> str:
    counter = int(_now(for_time) // period)
    key = _decode_base32_secret(secret_base32)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % (10**digits)).zfill(digits)


def verify_totp(
    secret_base32: str,
    code: str,
    *,
    at_time: float | None = None,
    window: int = 1,
    period: int = DEFAULT_TOTP_PERIOD_SECONDS,
    digits: int = DEFAULT_TOTP_DIGITS,
) -> bool:
    normalized = "".join(ch for ch in str(code).strip() if ch.isdigit())
    if len(normalized) != digits:
        return False
    current_time = _now(at_time)
    for offset in range(-window, window + 1):
        candidate_time = current_time + (offset * period)
        candidate = generate_totp(secret_base32, for_time=candidate_time, period=period, digits=digits)
        if hmac.compare_digest(candidate, normalized):
            return True
    return False


def provisioning_uri(secret_base32: str, *, issuer: str = DEFAULT_ISSUER, account: str = DEFAULT_ACCOUNT) -> str:
    label = urllib.parse.quote(f"{issuer}:{account}", safe="")
    query = urllib.parse.urlencode(
        {
            "secret": secret_base32,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": str(DEFAULT_TOTP_DIGITS),
            "period": str(DEFAULT_TOTP_PERIOD_SECONDS),
        }
    )
    return f"otpauth://totp/{label}?{query}"


def render_qr_ascii(uri: str) -> str | None:
    try:
        import qrcode  # type: ignore[import-not-found]
    except Exception:
        return None
    from io import StringIO

    qr = qrcode.QRCode(border=1)
    qr.add_data(uri)
    qr.make(fit=True)
    out = StringIO()
    qr.print_ascii(out=out, invert=True)
    return out.getvalue()


def is_yolo_active(*, auth_dir: str | Path | None = None, now: float | None = None) -> bool:
    try:
        return OperatorAuth(auth_dir=auth_dir).status(now=now).yolo_active
    except OperatorAuthError:
        return False


def _decode_base32_secret(secret_base32: str) -> bytes:
    normalized = "".join(str(secret_base32).upper().split())
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    try:
        return base64.b32decode(normalized + padding, casefold=True)
    except Exception as exc:
        raise OperatorAuthError("invalid base32 authenticator secret") from exc


def _now(value: float | None) -> float:
    return float(time.time() if value is None else value)
