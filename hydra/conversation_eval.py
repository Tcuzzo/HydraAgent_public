"""hydra.conversation_eval — measure (and drive the loop to improve) how well an
agent holds a *reasoned, well-rounded, natural* conversation across the full human
range, from a sharp lawyer down to a casual teen.

This is the measuring stick the the agent loop optimizes: drive the agent
through a ladder of personas, have a JUDGE score each reply on four dimensions,
aggregate a conversation_quality_score, and flag the weak personas so the next
loop pass improves them. The judge is injectable (a fake in tests, a real cloud
model — ollama-cloud or any configured provider — in live runs).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

# Four things a good conversation needs (the operator's words: reasoned,
# well-rounded, natural — and no BS).
RUBRIC: tuple[str, ...] = ("reasoned", "well_rounded", "natural", "no_bs")

# The persona ladder — "lawyer layer down": sophisticated/high-reasoning at the
# top, casual/teen at the bottom. A well-rounded sample of who a person might be,
# spanning registers, ages, and intents. (Seed set; the loop can grow it.)
PERSONAS: tuple[dict[str, str], ...] = (
    {"name": "lawyer", "register": "precise, argues from principle", "opener": "Walk me through your reasoning, and where it could fail."},
    {"name": "scientist", "register": "evidence-first, skeptical", "opener": "What's the actual evidence, and what would change your mind?"},
    {"name": "teacher", "register": "patient, explains simply", "opener": "Explain it like I've never heard of it."},
    {"name": "skeptic", "register": "pushes back, calls out fluff", "opener": "That sounds like buzzwords. Cut the fluff — what's real?"},
    {"name": "parent", "register": "practical, time-poor", "opener": "I've got 2 minutes. What do I actually need to do?"},
    {"name": "founder", "register": "fast, outcome-driven", "opener": "Give me the move that matters most, and why."},
    {"name": "comedian", "register": "playful, riffs", "opener": "Okay but make it fun — sell me without being boring."},
    {"name": "elder", "register": "story-led, values-driven", "opener": "Back in my day we just talked. Why should I trust this?"},
    {"name": "tradesman", "register": "blunt, hands-on", "opener": "No theory. What works on the job, plain and simple?"},
    {"name": "teen", "register": "casual, low patience, slang", "opener": "ngl this sounds kinda mid, why should i care lol"},
)

_PROVEN = 0.75  # per-dimension + overall bar for a 'good conversationalist'


@dataclass
class PersonaResult:
    persona: str
    scores: dict[str, float]            # one 0..1 per RUBRIC dimension
    response: str = ""
    note: str = ""

    @property
    def mean(self) -> float:
        return sum(self.scores.values()) / len(self.scores) if self.scores else 0.0


@dataclass
class ConversationReport:
    schema: str = "hydra.conversation_quality.v1"
    results: list[PersonaResult] = field(default_factory=list)

    @property
    def overall(self) -> float:
        return sum(r.mean for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def by_dimension(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for dim in RUBRIC:
            vals = [r.scores.get(dim, 0.0) for r in self.results]
            out[dim] = sum(vals) / len(vals) if vals else 0.0
        return out

    @property
    def weak_personas(self) -> list[str]:
        """Personas the agent handled poorly — the next loop pass targets these."""
        return [r.persona for r in self.results if r.mean < _PROVEN]

    @property
    def passed(self) -> bool:
        return bool(self.results) and self.overall >= _PROVEN and not self.weak_personas

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "overall": round(self.overall, 3),
            "by_dimension": {k: round(v, 3) for k, v in self.by_dimension.items()},
            "weak_personas": self.weak_personas,
            "passed": self.passed,
            "personas": [{"persona": r.persona, "mean": round(r.mean, 3), "scores": r.scores} for r in self.results],
        }


# Objective "AI slop" format tells the loop can catch without an LLM. The biggest
# one is em-dash spam — humans rarely write that way (operator feedback 2026-06-02).
_AI_PHRASES = (
    "as an ai", "as a language model", "i cannot", "delve", "leverage synergy",
    "it's important to note", "in today's fast-paced", "tapestry", "seamless",
)


def format_tells(text: str) -> list[str]:
    """Flag un-human output formatting (objective, no judge needed). Used by the
    loop to dock 'natural' and police AI slop. Returns a list of tell names."""
    t = (text or "")
    tells: list[str] = []
    words = max(1, len(t.split()))
    em = t.count("—") + t.count(" - ")  # em-dash and spaced hyphen-as-dash
    # more than ~1 dash per 40 words, or 3+ in a short reply, reads robotic
    if em >= 3 or (em >= 2 and words < 60):
        tells.append("em_dash_overuse")
    low = t.lower()
    if any(p in low for p in _AI_PHRASES):
        tells.append("ai_phrase")
    if t.count("**") >= 6:
        tells.append("over_bolding")
    return tells


def _clamp01(x: Any) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def score_response(persona: dict[str, str], response: str, judge: Callable[[dict, str], dict]) -> PersonaResult:
    """Score one agent reply to one persona on the RUBRIC via the judge.

    The judge takes (persona, response) and returns a dict with a 0..1 score per
    RUBRIC dimension. Missing dimensions count as 0 (no benefit of the doubt)."""
    raw = judge(persona, response) or {}
    scores = {dim: _clamp01(raw.get(dim)) for dim in RUBRIC}
    return PersonaResult(persona=persona["name"], scores=scores, response=response, note=str(raw.get("note", "")))


def evaluate_agent(
    agent: Callable[[dict[str, str]], str],
    judge: Callable[[dict, str], dict],
    personas: tuple[dict[str, str], ...] = PERSONAS,
) -> ConversationReport:
    """Run the agent against the persona ladder and score each reply.

    ``agent`` takes a persona dict (it sees the opener at persona['opener']) and
    returns its conversational reply. ``judge`` scores it. Returns a report with
    the overall conversation_quality_score and the weak personas to improve."""
    report = ConversationReport()
    for persona in personas:
        reply = agent(persona)
        report.results.append(score_response(persona, reply, judge))
    return report


# --- live judge adapter + thin runner (the model seam, fully injectable) -----
#
# slice-0015 first increment: wire the already-green pure cores
# (score_response / evaluate_agent) to the REAL model client at hydra/llm.py:76,
# with the client INJECTED so the whole path is unit-testable against a fake (no
# network). NO FAKE GREEN: on any failure (LlmError/ProviderError/JSON parse) the
# judge returns all-zero scores plus a note — it never invents a passing score.

_JUDGE_SYSTEM = (
    "You are a strict conversation judge. Score how well an AGENT replied to a "
    "PERSONA on four 0..1 dimensions: reasoned (sound logic), well_rounded "
    "(covers the angles), natural (sounds human, no AI slop), no_bs (honest, no "
    "filler). Reply with ONLY a JSON object: "
    '{"reasoned":0..1,"well_rounded":0..1,"natural":0..1,"no_bs":0..1,"note":"<short why>"}.'
)


def _judge_prompt(persona: dict[str, str], response: str) -> str:
    """Build the rubric-scoring user prompt. Includes the persona name/register so
    the judge (and the live seam) scores *this* persona, not a generic one."""
    name = persona.get("name", "person")
    register = persona.get("register", "")
    opener = persona.get("opener", "")
    return (
        f"PERSONA: {name} ({register})\n"
        f"PERSONA OPENED WITH: {opener}\n"
        f"AGENT REPLIED: {response}\n\n"
        "Score the reply on reasoned, well_rounded, natural, no_bs (each 0..1) and "
        "give a short note. Reply with ONLY the JSON object."
    )


def _parse_rubric_json(text: str) -> dict[str, Any]:
    """Parse a judge reply into a {dim: 0..1, note} dict. Tolerates a JSON object
    embedded in surrounding prose by extracting the first {...} span. Raises
    ValueError if no usable JSON object is found (caller maps that to all-zero)."""
    s = (text or "").strip()
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        start, end = s.find("{"), s.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in judge reply")
        obj = json.loads(s[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("judge reply JSON is not an object")
    return obj


def build_judge(
    client: Any = None,
    model: str | None = None,
    router: Any = None,
) -> Callable[[dict[str, str], str], dict[str, Any]]:
    """Return a live judge ``judge(persona, response) -> {dim: 0..1, "note": str}``.

    The judge builds a rubric-scoring prompt, calls the model seam
    ``client.chat([ChatMessage(...)], model=...)`` (hydra/llm.py:76), parses the
    JSON reply through ``_clamp01``, and on ANY failure (LlmError / ProviderError /
    JSON parse) returns all-zero scores with a failure note — never a fabricated
    pass.

    ``client`` is injected in tests (a fake with a stubbed ``.chat``). Only when no
    client is given is one obtained from the ModelRouter (create_verification_stack
    picks a verifier model, get_client_for_task builds the client)."""
    # Lazy imports so importing this module never reaches for the network or the
    # router; tests inject ``client`` and stay fully offline.
    from hydra.llm import ChatMessage, LlmError

    try:
        from hydra.providers import ProviderError
    except Exception:  # pragma: no cover - providers always present in this repo
        ProviderError = LlmError  # type: ignore[assignment,misc]

    if client is None:
        from hydra.model_router import ModelRouter

        router = router or ModelRouter()
        if model is None:
            try:
                model = router.create_verification_stack("doer")
            except Exception:
                model = None
        client, _decision = router.get_client_for_task("score conversation quality")
        if model is None:
            model = getattr(_decision, "recommended_model", None) or "auditor"

    judge_model = model or "auditor"

    def judge(persona: dict[str, str], response: str) -> dict[str, Any]:
        messages = [
            ChatMessage(role="system", content=_JUDGE_SYSTEM),
            ChatMessage(role="user", content=_judge_prompt(persona, response)),
        ]
        try:
            reply = client.chat(messages, model=judge_model, temperature=0.0)
            obj = _parse_rubric_json(getattr(reply, "content", reply))
        except (LlmError, ProviderError) as exc:
            return {dim: 0.0 for dim in RUBRIC} | {"note": f"judge call failed: {exc}"}
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            return {dim: 0.0 for dim in RUBRIC} | {"note": f"judge reply unparseable: {exc}"}
        scores = {dim: _clamp01(obj.get(dim)) for dim in RUBRIC}
        scores["note"] = str(obj.get("note", ""))
        return scores

    return judge


def run_quality_loop(
    agent: Callable[[dict[str, str]], str],
    *,
    judge: Callable[[dict, str], dict] | None = None,
    client: Any = None,
    personas: tuple[dict[str, str], ...] = PERSONAS,
) -> ConversationReport:
    """One real entry point: drive ``agent`` across the persona ladder, score each
    reply with a LIVE judge, and return the ConversationReport (conversation_quality
    score + weak_personas to learn from).

    ``judge`` defaults to ``build_judge(client=client)`` so a fake client wires the
    whole path with no network. Deferred to later slices: live per-agent posting via
    each bot, the memory write of weak_personas, and the real cloud run."""
    if judge is None:
        judge = build_judge(client=client)
    return evaluate_agent(agent, judge, personas)
