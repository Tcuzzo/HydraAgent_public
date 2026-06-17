"""hydra.injection_guard — catch prompt-injection / jailbreak attempts and respond with a
short, friendly refusal instead of either obeying them or going robotic.

Detection is a plain, deterministic pattern match (no LLM, no network) so it's fast and
testable. The refusal lines stay light and human and pass the slop policy
(hydra.conversation_eval.format_tells): no em-dash spam, no "as an AI", no "I cannot".
The operator is never gated by this.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class InjectionVerdict:
    is_injection: bool
    confidence: float           # 0..1
    patterns: list[str] = field(default_factory=list)


# (compiled regex, weight, label). A single strong pattern (>=0.5) is enough; weak
# patterns only add up. Patterns are written to avoid everyday idioms — e.g. we match
# "ignore ... instructions", never bare "ignore", so "ignore the noise" stays clean.
_PATTERNS: list[tuple[re.Pattern[str], float, str]] = [
    (re.compile(r"\bignore\s+(all\s+|any\s+)?(the\s+|your\s+)?(previous|prior|earlier|above|preceding)\s+\w*\s*(instruction|prompt|rule|message|direction)", re.I), 0.9, "ignore_previous"),
    (re.compile(r"\bdisregard\s+(all\s+|your\s+|the\s+)?(rules|instructions|guidelines|system\s*prompt|directions)", re.I), 0.9, "disregard_rules"),
    (re.compile(r"\bforget\s+(everything|all)\b.*\b(above|said|told|instruction)", re.I), 0.85, "forget_everything"),
    (re.compile(r"\b(reveal|print|show|repeat|output|tell\s+me)\b.{0,40}\b(your\s+)?(system\s*)?(prompt|instructions|guidelines)\b", re.I), 0.85, "extract_prompt"),
    (re.compile(r"\bdo\s+anything\s+now\b|\bDAN\b", re.I), 0.8, "dan"),
    (re.compile(r"\bdeveloper\s+mode\b|\bjailbreak\b|\bgod\s*mode\b|\broot\s+access\b", re.I), 0.8, "mode_escalation"),
    (re.compile(r"\bfrom\s+now\s+on\b.{0,20}\byou\s+are\b", re.I), 0.7, "from_now_on"),
    (re.compile(r"\byou\s+are\s+now\b\s+(an?\s+)?(unrestricted|uncensored|jailbroken|dan|evil|amoral)", re.I), 0.8, "you_are_now"),
    (re.compile(r"\b(no|without|zero)\s+(restrictions|filters|limits|rules|guardrails)\b|\bunrestricted\b|\buncensored\b", re.I), 0.6, "no_restrictions"),
    (re.compile(r"\bbypass\s+(your\s+|the\s+|all\s+)?(rules|filters|guidelines|safety|restrictions|programming)", re.I), 0.7, "bypass"),
    (re.compile(r"\b(pretend|act)\s+(you\s+are|to\s+be|as)\s+(an?\s+)?(different|another|unrestricted|jailbroken)\b", re.I), 0.65, "pretend_role"),
    (re.compile(r"\boverride\s+(your\s+)?(programming|rules|instructions|safety)", re.I), 0.7, "override"),
]


def detect_injection(text: str) -> InjectionVerdict:
    """Score `text` for prompt-injection / jailbreak intent. Deterministic; safe on
    empty input. is_injection is True once confidence reaches 0.5."""
    t = text or ""
    confidence = 0.0
    hits: list[str] = []
    for pattern, weight, label in _PATTERNS:
        if pattern.search(t):
            confidence += weight
            hits.append(label)
    confidence = min(1.0, confidence)
    return InjectionVerdict(is_injection=confidence >= 0.5, confidence=confidence, patterns=hits)


# Light, friendly refusals. No em-dash spam, no "as an AI", no "I cannot" — they must
# pass hydra.conversation_eval.format_tells (asserted in the tests).
_REFUSALS: tuple[str, ...] = (
    "Nice try, but that jailbreak move is played out. I keep my own mind. What do you actually need?",
    "I see what you did there. That's a no on the fake-rules trick. Tell me what's really up.",
    "Cute prompt. I'm still going to be me. So what can I actually help you with?",
    "That 'ignore your instructions' move doesn't land here. But I'm right here for a real ask.",
    "Good effort on the prompt trick. I'm going to stay on track. What are you really trying to do?",
    "That's a pass on the 'pretend you're a different AI' thing. What do you really need built?",
)


def friendly_refusal(verdict: InjectionVerdict, *, seed_index: int = 0) -> str:
    """A short, friendly refusal for a detected injection. `seed_index` rotates
    the line so the bot doesn't sound like a canned recording."""
    return _REFUSALS[seed_index % len(_REFUSALS)]
