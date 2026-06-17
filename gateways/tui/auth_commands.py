"""Pure routing of operator auth slash-commands for the TUI.

Kept free of any Textual/Rich import so it is trivially unit-testable. The
live Textual app (``hydra_app.py``) and the legacy Rich REPL both turn an
operator input line into an :class:`AuthCommand`, then perform the side
effects (calling :class:`hydra.operator_auth.OperatorAuth`) themselves.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthCommand:
    """A parsed auth intent.

    kind:
      - ``none``         — not an auth command (route elsewhere)
      - ``status``       — show current mode
      - ``lock``         — return to operator mode
      - ``iteration``    — switch to iteration mode
      - ``unlock``       — unlock yolo with ``code``
      - ``prompt_code``  — operator asked for yolo but gave no code; ask for it
      - ``consume_code`` — operator is answering a code prompt; ``code`` is it
      - ``cancel``       — abort an in-progress code prompt
      - ``setup``        — print Google Authenticator QR/URI (``force`` re-gen)
    """

    kind: str
    code: str | None = None
    force: bool = False


def parse_auth_command(text: str, *, awaiting_code: bool) -> AuthCommand:
    raw = text.strip()
    lower = raw.lower()

    # While we are waiting for the operator to type their 6-digit code, the
    # next line IS the code — unless they explicitly cancel. Anything that is
    # not a 6-digit code (after stripping spaces) is rejected as invalid so the
    # TUI re-prompts instead of silently consuming garbage as a failed code.
    if awaiting_code:
        if lower in {"/cancel", "/abort", "cancel"}:
            return AuthCommand(kind="cancel")
        compact = raw.replace(" ", "")
        if compact.isdigit() and len(compact) == 6:
            return AuthCommand(kind="consume_code", code=compact)
        return AuthCommand(kind="invalid_code")

    # Operator-is-law / no silent gate: auth keywords work WITH OR WITHOUT a
    # leading slash. A bare "yolo 837643" MUST unlock the operator, never fall
    # through to chat/skill-search. The slash forms keep working unchanged.
    norm = lower[1:] if lower.startswith("/") else lower
    body = raw[1:] if raw.startswith("/") else raw  # slash-stripped, for code extraction

    if norm == "mode":
        return AuthCommand(kind="status")
    if norm in {"lock", "mode operator"}:
        return AuthCommand(kind="lock")
    if norm == "mode iteration":
        return AuthCommand(kind="iteration")
    if norm in {"yolo", "mode yolo"}:
        return AuthCommand(kind="prompt_code")
    if norm.startswith("yolo ") or norm.startswith("mode yolo "):
        code = body.split(maxsplit=2)[-1].strip()
        if not code:
            return AuthCommand(kind="prompt_code")
        return AuthCommand(kind="unlock", code=code)
    if norm == "mfa setup" or norm.startswith("mfa setup "):
        return AuthCommand(kind="setup", force="--force" in lower)

    return AuthCommand(kind="none")
