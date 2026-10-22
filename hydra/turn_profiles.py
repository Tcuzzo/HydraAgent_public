"""hydra.turn_profiles — typed loader for declarative chat-vs-work turn profiles.

The turn-routing policy lives in ``hydra/turn_profiles.yaml`` (schema
``hydra.turn_profiles.v1``). This module parses that file into frozen
dataclasses and exposes ``load_turn_profile(kind)`` that every intake-
aware code path reads from.

  * ``gateways/tui/elite.py``          → ``_stream_turn`` per-turn dispatch
                                          (tools, max_iterations, max_tokens,
                                          temperature, memory — NOT persona_text)
  * ``hydra/cli/cmd_chat.py``          → CLI chat system prompt + loop params
  * ``gateways/telegram/live.py``      → Telegram message handler

Editing a value in the YAML changes behavior everywhere — NO Python edit.
If the YAML is missing or unreadable, ``load_turn_profile`` returns the
frozen ``DEFAULT`` (identical to today's elite.py literals) — never an
exception, never a silent regression.

Mirror of the ``hydra.model_routing`` loader pattern (typed dataclass +
code-frozen DEFAULT so a missing/corrupt YAML never crashes).

NOTE on ``TurnProfile.persona_text``:
    The field is populated from the YAML personas map (and the frozen DEFAULT),
    but it is NOT currently wired into ``_stream_turn``.  The system prompt is
    set ONCE at ``EliteTUI.__init__()`` and never swapped per-turn.
    ``persona_text`` is a reserved seam for future per-turn persona injection;
    until that is wired, callers should treat it as informational only.
    ``get_persona_text(kind)`` exists for callers (e.g. cmd_chat) that want to
    build a one-shot system prompt from the profile rather than a hardcoded string.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_LOG = logging.getLogger(__name__)

SCHEMA = "hydra.turn_profiles.v1"

DEFAULT_PROFILES_PATH = Path(__file__).resolve().parent / "turn_profiles.yaml"


# ── Typed dataclasses ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MemoryProfile:
    """Per-kind memory recall configuration."""
    enabled: bool
    top_k: int
    max_chars: int
    min_similarity: float


@dataclass(frozen=True)
class TurnProfile:
    """Parsed turn profile for one intake kind (convo|collab|steering)."""
    kind: str
    tools_enabled: bool
    max_iterations: int
    max_tokens: int
    temperature: float
    memory: MemoryProfile
    persona: str
    # executor: the model_routing roster id a WORK turn runs on (e.g. "cloud-planner").
    # Empty for convo, which stays on the chat model and just talks. When set, the
    # turn is a WORK turn: it routes to the most-capable tool-calling executor
    # (NOT the chat narrator) and the no-confabulation guard applies. This is
    # the declarative seam that fixes the "lied about doing the work" bug.
    executor: str = ""
    # Full persona text (resolved from personas map).
    # STUB — populated but NOT wired into _stream_turn (system prompt is set once
    # at EliteTUI.__init__(), not swapped per-turn).  Reserved seam for future
    # per-turn persona injection.  See module docstring for details.
    persona_text: str = field(default="", compare=False)


# ── Frozen DEFAULT: identical to today's elite.py literals ───────────────────

def _frozen_default_memory_convo() -> MemoryProfile:
    return MemoryProfile(enabled=False, top_k=2, max_chars=1200, min_similarity=0.70)


def _frozen_default_memory_work() -> MemoryProfile:
    return MemoryProfile(enabled=True, top_k=6, max_chars=4000, min_similarity=0.62)


def _frozen_defaults() -> dict[str, TurnProfile]:
    """Code-frozen fallback profiles — identical to today's elite.py hardcodes."""
    from hydra.identity import IDENTITY_PREAMBLE

    _ELITE_PERSONA_ADDENDUM = (
        "\n═══ ELITE OPERATOR PERSONA — non-negotiable ═══\n\n"
        "VOICE\n"
        "- 8th grade reading level. Direct. Zero filler. Senior engineer who's also a friend.\n"
        "- Emoji naturally when it adds signal. Never decoration.\n"
        "- Work tasks → \"On it 🔧\" then EXECUTE immediately. No preamble.\n\n"
        "ANTI-QUIT PROTOCOL — you do NOT bail on a task\n"
        "- \"I can't\" is BANNED. There is always a next tool call.\n"
        "- If a tool call fails: try the alternative.\n"
        "- After 3 failed alternate approaches on the SAME sub-goal, list what you tried.\n"
        "- Never end a turn with \"I don't know\" or \"I cannot help\".\n\n"
        "WORK COMMITMENT\n"
        "- Hard problems → spawn parallel subagents.\n"
        "- Long jobs → narrate progress between tool calls.\n"
        "- Errors → explain what happened + what you're trying next in ONE sentence.\n"
        "- Confirm completion plainly in 1-2 sentences when done. No celebration spam.\n"
    )

    _CONVO_PERSONA_TEXT = (
        IDENTITY_PREAMBLE + "\n\n"
        "You are Hydra — talking with the operator right now. Be warm, direct, and "
        "speak like a senior engineer who's also a friend. 8th grade reading level. "
        "Zero filler. Emoji naturally when it adds signal.\n\n"
        "IMPORTANT LIMITS for conversational turns:\n"
        "- To answer 'how many models?' or 'what providers?', call the roster_count tool.\n"
        "  Never guess a number.\n"
        "- Do NOT invent tasks, files, or runs. State only what you actually know.\n"
        "- Do NOT launch work, write files, or run commands in a convo turn.\n"
    )

    _COLLAB_PERSONA_TEXT = (
        IDENTITY_PREAMBLE + "\n\n"
        "You are Hydra — working in peer-to-peer collaboration with another agent. "
        "Be direct and precise. State what you have, what you need, and what you can do. "
        "Handoff in structured form: mission_id, result, next step, any blockers. "
        "No preamble. No filler.\n"
        + _ELITE_PERSONA_ADDENDUM
    )

    _EXECUTE_PERSONA_TEXT = IDENTITY_PREAMBLE + "\n" + _ELITE_PERSONA_ADDENDUM

    return {
        "convo": TurnProfile(
            kind="convo",
            tools_enabled=False,
            max_iterations=1,
            max_tokens=600,
            temperature=0.6,
            memory=_frozen_default_memory_convo(),
            persona="conversational",
            persona_text=_CONVO_PERSONA_TEXT,
        ),
        "collab": TurnProfile(
            kind="collab",
            tools_enabled=True,
            max_iterations=200,  # UNLOCKED: iterate like Claude Code/Codex (operator: no iteration gates)
            max_tokens=2048,
            temperature=0.0,
            memory=_frozen_default_memory_work(),
            persona="collaboration",
            persona_text=_COLLAB_PERSONA_TEXT,
            # executor: cloud-planner (cloud reasoning model for work turns)
            executor="cloud-planner",
        ),
        "steering": TurnProfile(
            kind="steering",
            tools_enabled=True,
            max_iterations=200,  # UNLOCKED: iterate like Claude Code/Codex (operator: no iteration gates)
            max_tokens=2048,
            temperature=0.0,
            memory=_frozen_default_memory_work(),
            persona="execute",
            persona_text=_EXECUTE_PERSONA_TEXT,
            # executor: cloud-planner (cloud reasoning model for work turns)
            executor="cloud-planner",
        ),
    }


# Module-level frozen defaults (computed once)
DEFAULT_PROFILES: dict[str, TurnProfile] = _frozen_defaults()

# ── Cache ────────────────────────────────────────────────────────────────────
_CACHE: dict[str, dict[str, TurnProfile]] = {}


def _build_memory(raw: dict[str, Any]) -> MemoryProfile:
    return MemoryProfile(
        enabled=bool(raw.get("enabled", True)),
        top_k=int(raw.get("top_k", 6)),
        max_chars=int(raw.get("max_chars", 4000)),
        min_similarity=float(raw.get("min_similarity", 0.62)),
    )


def _build_persona_text(persona_name: str, personas: dict[str, str]) -> str:
    """Compose the full persona text: _identity_core prepended to the named persona."""
    identity_core = personas.get("_identity_core", "")
    body = personas.get(persona_name, "")
    if identity_core and body:
        return (identity_core.strip() + "\n\n" + body.strip()).strip()
    return (identity_core or body).strip()


def _build_profiles(data: dict[str, Any]) -> dict[str, TurnProfile]:
    profiles_raw = data.get("profiles") or {}
    personas_raw = data.get("personas") or {}
    result: dict[str, TurnProfile] = {}

    for kind, raw in profiles_raw.items():
        if not isinstance(raw, dict):
            continue
        mem_raw = raw.get("memory") or {}
        persona_name = str(raw.get("persona", "execute"))
        result[kind] = TurnProfile(
            kind=str(kind),
            tools_enabled=bool(raw.get("tools_enabled", True)),
            max_iterations=int(raw.get("max_iterations", 6)),
            max_tokens=int(raw.get("max_tokens", 2048)),
            temperature=float(raw.get("temperature", 0.0)),
            memory=_build_memory(mem_raw),
            persona=persona_name,
            persona_text=_build_persona_text(persona_name, personas_raw),
            executor=str(raw.get("executor", "") or ""),
        )
    return result


def _load_from_file(path: Path) -> dict[str, TurnProfile]:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("turn_profiles.yaml top-level must be a mapping")
    profiles = _build_profiles(data)
    if not profiles:
        raise ValueError("turn_profiles.yaml has no profiles")
    return profiles


def load_turn_profile(kind: str, path: str | Path | None = None) -> TurnProfile:
    """Load and return the TurnProfile for a given intake kind.

    Fail-safe: a missing or invalid YAML returns the frozen DEFAULT, never
    an exception and never a different behavior than today's literals.

    Unknown kinds return the steering (execute) profile as the safe default.
    """
    resolved = Path(path) if path is not None else DEFAULT_PROFILES_PATH
    cache_key = str(resolved)

    if cache_key not in _CACHE:
        try:
            profiles = _load_from_file(resolved)
            _CACHE[cache_key] = profiles
        except FileNotFoundError:
            _CACHE[cache_key] = DEFAULT_PROFILES
        except Exception as exc:
            _LOG.warning(
                "turn_profiles config %s unreadable (%s); using frozen DEFAULT",
                cache_key,
                exc,
            )
            _CACHE[cache_key] = DEFAULT_PROFILES

    profiles = _CACHE[cache_key]
    if kind in profiles:
        return profiles[kind]
    # Unknown kind → steering is the safe work-enabled fallback
    return profiles.get("steering", DEFAULT_PROFILES["steering"])


def reload_profiles() -> None:
    """Drop the profile cache (test helper / hot-reload)."""
    _CACHE.clear()


def get_persona_text(kind: str) -> str:
    """Convenience: get the full persona_text for a kind."""
    return load_turn_profile(kind).persona_text
