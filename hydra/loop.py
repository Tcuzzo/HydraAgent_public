"""hydra.loop — the LLM-driven agent loop.

The piece that makes Hydra an actual agent. Composes:
  - §10.20 / §10.22 / §10.23 / §10.24 LLM client (any OpenAI-compat
    host — local Ollama or cloud OpenAI-compatible provider).
  - The function-calling surface proven in §10.22.
  - The skill protocol (fs_read, fs_write, http_fetch, git_diff, ...)
    plus any operator-supplied callable wrapped as a `Tool`.

Mechanic:
  1. Send the conversation + tool schemas to the LLM.
  2. If the response has `tool_calls`, dispatch each via the bound
     `Tool.invoke`, capture the result (or the error), append it as a
     `role: "tool"` message, loop.
  3. If the response has only content (no tool_calls), return — the
     model has signaled natural completion.
  4. Hard cap at `max_iterations` so a model stuck in a tool-call loop
     can't burn unbounded LLM budget.

Tool dispatch failures (tool not in registry, callable raised, callable
returned a non-serializable object) become JSON-encoded `{"error": ...}`
messages fed back to the model — the model can recover or give up
gracefully, the loop never crashes.

Maturity: SCAFFOLDED. Promoted by §10.25.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT))

from hydra.llm import ChatMessage, LlmError, OllamaClient, ToolCall  # noqa: E402
from hydra.inter_agent import current_trace_id, new_trace_id, use_trace_id  # noqa: E402
from hydra.tool_bridge import extract_bridged_tool_call, scrub_public_fake_output_preview  # noqa: E402


class LoopError(Exception):
    """A loop-level configuration failure. Individual tool-call
    failures don't raise — they become tool-result messages the model
    can react to."""


@dataclass
class Tool:
    """One callable the agent can invoke during the loop.

    `parameters` is a JSON Schema describing the arguments. The
    operator pre-binds any context (worktree root, allow-list, etc.)
    via closure so the LLM only sees the args it should care about.

    `invoke(**arguments)` is called with the parsed argument dict.
    Returning a dict is the common case (skills do this); strings and
    other JSON-serializable values also work. Anything else is JSON-
    wrapped as `{"result": <repr>}` before being shown to the model.
    """

    name: str
    description: str
    parameters: dict
    invoke: Callable[..., Any]

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class LoopStep:
    """One observable event in the agent's trajectory — either an
    assistant turn or a single tool-call dispatch result."""

    step: int
    kind: str  # "assistant" | "tool_result"
    role: str  # "assistant" | "tool"
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_arguments: dict | None = None
    tool_error: str | None = None
    tool_duration_ms: int | None = None
    trace_id: str | None = None


@dataclass
class LoopResult:
    steps: list[LoopStep]
    final_response: str
    iterations: int
    tool_calls_made: int
    halted_reason: str  # "natural" | "max_iterations"
    messages: list[dict] = field(repr=False)
    
    # Worker evidence validation: track real tool activity vs heartbeat-only
    has_real_tool_activity: bool = False
    heartbeat_only: bool = False
    fake_final_scrubbed: bool = False
    trace_id: str = ""

    # S6 — life-support fallback. When a provider chat call fails during an
    # autonomous mission, the loop halts with halted_reason='provider_fallback'
    # rather than crashing, and these carry the operator-facing detail.
    fallback_engaged: bool = False
    fallback_error_class: str | None = None
    substitution: dict | None = None
    fallback_checkpoint_path: str | None = None
    operator_message: str | None = None

    # Phantom tool call recovery: set True when the provider returned HTTP 400
    # "tool call validation failed" for a tool that wasn't in the request (e.g.
    # model hallucinated skill_route on a convo turn with tools=[]).  The loop
    # retried plain-text and the operator got a real answer.
    phantom_tool_recovery: bool = False


def _is_phantom_tool_call_error(exc: LlmError) -> bool:
    """Return True when the provider HTTP 400 is the whole OpenAI-compat
    *tool/function-call emission failure* class — i.e. the model tried to emit
    a tool or function call and the provider rejected the request because the
    call was invalid, unparseable, or referenced a tool not in the request.

    Two known variants:

    VARIANT 1 — tool not in request.tools:
        HTTP 400 ... {"error":{"message":"tool call validation failed: attempted
        to call tool skill_route which was not in request.tools", ...}}

    VARIANT 2 — unparseable function-call body (error code = "tool_use_failed"):
        HTTP 400 ... {"error":{"message":"Failed to call a function. Please
        adjust your prompt. See 'failed_generation' for more details.",
        "code":"tool_use_failed","failed_generation":"<function=skill_route ...>"}}

    Both are recovered by retrying with tools=None and a plain-text instruction.
    The detector returns False for non-tool 400s (model_not_found,
    context_length_exceeded, auth errors, etc.) so those still surface.
    """
    msg = str(exc).lower()
    if "400" not in msg:
        return False
    # Variant 1: validation failure referencing the tool registry
    if "tool call validation failed" in msg and "not in request.tools" in msg:
        return True
    # Variant 2: provider error code / message for a malformed function-call body
    if "tool_use_failed" in msg:
        return True
    if "failed to call a function" in msg:
        return True
    # Variant 2b: failed_generation containing an XML-style function call
    if "failed_generation" in msg and "<function=" in msg:
        return True
    return False


def _serialize_tool_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, (dict, list, int, float, bool)) or result is None:
        return json.dumps(result)
    return json.dumps({"result": repr(result)})


class AgentLoop:
    """LLM ↔ tool-dispatcher loop. One instance binds to one LLM
    client + model + system prompt; `.run()` is reusable."""

    def __init__(
        self,
        client: OllamaClient,
        *,
        model: str,
        system_prompt: str | None = None,
    ) -> None:
        if not model:
            raise LoopError("AgentLoop: model must be a non-empty string")
        self.client = client
        self.model = model
        self.system_prompt = system_prompt

    def run(
        self,
        user_prompt: str,
        tools: list[Tool] | None = None,
        *,
        max_iterations: int = 15,  # Increased from 8: complex missions need room for data collection + synthesis
        max_tokens: int = 1024,
        temperature: float = 0.0,
        timeout: float = 120.0,
        initial_messages: list[dict] | None = None,
        on_step: Callable[[LoopStep], None] | None = None,
        autonomous: bool = False,
        mission_id: str | None = None,
        repo_root: "str | Path | None" = None,
        requested_provider: str = "cloud",
        local_client_factory: "Callable[[], tuple[Any, str]] | None" = None,
    ) -> LoopResult:
        if max_iterations < 1:
            raise LoopError(
                f"max_iterations must be >= 1, got {max_iterations}"
            )

        tools = list(tools or [])
        tool_by_name: dict[str, Tool] = {t.name: t for t in tools}
        tool_schemas = [t.to_openai_schema() for t in tools] if tools else None

        run_trace_id = current_trace_id() or new_trace_id()
        messages: list[dict] = [dict(m) for m in initial_messages or []]
        if not messages and self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        # L2 fix: strip any prior trace-context messages before appending the
        # fresh one so feeding result.messages back never grows the count past 1.
        _TRACE_CTX_MARKER = "Hydra inter-agent trace context"
        messages = [
            m for m in messages
            if not (
                m.get("role") == "system"
                and _TRACE_CTX_MARKER in m.get("content", "")
            )
        ]
        messages.append(
            {
                "role": "system",
                "content": (
                    "Hydra inter-agent trace context\n"
                    f"trace_id={run_trace_id}\n"
                    "Use this exact trace_id for subagent messages, tool handoffs, "
                    "events, reviews, escalations, and final evidence."
                ),
            }
        )
        messages.append({"role": "user", "content": user_prompt})

        steps: list[LoopStep] = []
        tool_calls_made = 0
        final_response = ""
        halted_reason = "max_iterations"
        last_iteration = 0
        phantom_tool_recovery = False

        def record_step(step: LoopStep) -> None:
            steps.append(step)
            if on_step is not None:
                on_step(step)

        for iteration in range(1, max_iterations + 1):
            last_iteration = iteration
            
            # AUTO-SYNTHESIS TRIGGER: when genuinely near the cap (2 before the
            # end), nudge the model to deliver findings instead of running out of
            # room mid-collection. This scales with max_iterations: cap 6 fires at
            # 4, cap 15 fires at 13, cap 200 fires at 198. Short loops (<=3) are
            # intentionally unaffected.
            synthesis_iteration = max(1, max_iterations - 2)
            if max_iterations >= 4 and iteration == synthesis_iteration and tool_calls_made > 0:
                # Check if we've been collecting data without delivering conclusions
                assistant_responses = [s for s in steps if s.kind == "assistant" and s.content]
                has_substantive_conclusion = any(
                    len(s.content) > 300 or any(kw in s.content.lower() for kw in ["conclusion", "finding", "recommendation", "fix", "broken", "issue"])
                    for s in assistant_responses
                )
                if not has_substantive_conclusion:
                    # Near the iteration ceiling — deliver findings before running out.
                    # L9 fix: strip any prior synthesis nudge so exactly one is ever
                    # present (prevents accumulation when messages are fed back).
                    _SYNTH_MARKER = "SYNTHESIS REQUIRED"
                    messages = [
                        m for m in messages
                        if not (
                            m.get("role") == "system"
                            and _SYNTH_MARKER in m.get("content", "")
                        )
                    ]
                    synthesis_prompt = {
                        "role": "system",
                        "content": (
                            "SYNTHESIS REQUIRED: you are near the iteration limit. "
                            "STOP collecting more information. Now deliver your final report: "
                            "1) KEY FINDINGS — what did you discover? "
                            "2) ROOT CAUSES — what's broken/wrong and why? "
                            "3) ACTIONABLE RECOMMENDATIONS — specific steps to fix/improve. "
                            "Be direct, conclusive, and actionable. This is your final deliverable."
                        )
                    }
                    messages.append(synthesis_prompt)
                    record_step(LoopStep(step=iteration, kind="system", role="system", content=synthesis_prompt["content"]))
            
            try:
                resp = self.client.chat(
                    messages,
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                    tools=tool_schemas,
                )
            except LlmError as exc:
                # Phantom / tool-call-emission failure recovery.
                #
                # Cloud providers return HTTP 400 with an error class
                # that signals the model tried to emit a tool/function call but
                # the call was invalid.  There are two known variants:
                #
                #   VARIANT 1 — tool not in request.tools (stale system prompt
                #     primed the model to call skill_route but tools=[] were sent)
                #   VARIANT 2 — unparseable <function=...> body; error code
                #     "tool_use_failed" — can happen whether or not tools were sent
                #
                # In BOTH cases the right response is to retry once with tools=None
                # and a plain-text instruction so the user still gets an answer.
                # A chat turn must NEVER crash on a tool_use_failed 400.
                if _is_phantom_tool_call_error(exc):
                    # Build a recovery message list: same context, but add a
                    # clear instruction to skip any tool call and answer directly.
                    recovery_messages = [
                        m for m in messages
                        if not (
                            isinstance(m.get("content"), str)
                            and "_phantom_recovery_" in m.get("content", "")
                        )
                    ]
                    recovery_messages.append({
                        "role": "system",
                        "content": (
                            "_phantom_recovery_ "
                            "IMPORTANT: Answer the user's question directly in plain text. "
                            "Do NOT call any tools or functions — there are no tools available "
                            "in this turn. Respond conversationally."
                        ),
                    })
                    try:
                        resp = self.client.chat(
                            recovery_messages,
                            model=self.model,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            timeout=timeout,
                            tools=None,
                        )
                        phantom_tool_recovery = True
                        # Strip any tool calls from the recovery response so
                        # the loop doesn't try to dispatch them.
                        resp = type(resp)(
                            content=resp.content or "Got it.",
                            model=resp.model,
                            finish_reason=resp.finish_reason,
                            prompt_tokens=resp.prompt_tokens,
                            completion_tokens=resp.completion_tokens,
                            raw=resp.raw,
                            tool_calls=[],
                        )
                    except LlmError:
                        # Recovery also failed — return a safe fallback text so
                        # the user still gets something instead of a crash.
                        phantom_tool_recovery = True
                        final_response = "I ran into an issue reaching the provider. Try again."
                        halted_reason = "phantom_tool_recovery"
                        break
                    # Phantom recovery succeeded — `resp` is set; fall through
                    # to the normal tool_calls dispatch below with tool_calls=[].
                else:
                    # Non-phantom LlmError. S6 — life-support fallback for
                    # autonomous missions; interactive chat re-raises so the
                    # operator still sees the failure (§13).
                    if not autonomous:
                        raise
                    from hydra.emergency_fallback import engage_life_support_fallback

                    fb = engage_life_support_fallback(
                        error=exc,
                        requested_provider=requested_provider,
                        mission_id=mission_id or run_trace_id,
                        repo_root=repo_root or REPO_ROOT,
                        checkpoint_state={
                            "messages": [dict(m) for m in messages],
                            "iteration": iteration,
                            "model": self.model,
                            "requested_provider": requested_provider,
                        },
                        local_client_factory=local_client_factory,
                    )
                    # Switch this loop to the local life-support client/model so a
                    # caller that retries .run() (or resume) continues on local.
                    self.client = fb["client"]
                    self.model = fb["used_model"]
                    return LoopResult(
                        steps=steps,
                        final_response=fb["operator_message"],
                        iterations=iteration,
                        tool_calls_made=tool_calls_made,
                        halted_reason="provider_fallback",
                        messages=messages,
                        trace_id=run_trace_id,
                        fallback_engaged=True,
                        fallback_error_class=fb["error_class"],
                        substitution=fb["substitution"],
                        fallback_checkpoint_path=fb["checkpoint_path"],
                        operator_message=fb["operator_message"],
                    )

            tool_calls = list(resp.tool_calls)
            if not tool_calls and tools:
                bridged = extract_bridged_tool_call(resp.content)
                if bridged is not None:
                    tool_calls = [
                        ToolCall(
                            id=f"bridged-{iteration}-1",
                            name=bridged.name,
                            arguments_raw=bridged.arguments_raw,
                            arguments=bridged.arguments,
                        )
                    ]

            # Record the assistant turn.
            assistant_msg: dict = {"role": "assistant", "content": resp.content}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": tc.arguments_raw,
                        },
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)
            record_step(
                LoopStep(
                    step=iteration,
                    kind="assistant",
                    role="assistant",
                    content=resp.content,
                    trace_id=run_trace_id,
                )
            )

            if not tool_calls:
                final_response = resp.content
                halted_reason = "natural"
                break

            # Dispatch each tool call from this assistant turn.
            for tc in tool_calls:
                tool_calls_made += 1
                tool = tool_by_name.get(tc.name)
                if tool is None:
                    err = f"unknown tool: {tc.name!r}"
                    payload = json.dumps({"error": err})
                    record_step(
                        LoopStep(
                            step=iteration,
                            kind="tool_result",
                            role="tool",
                            content=payload,
                            tool_name=tc.name,
                            tool_call_id=tc.id,
                            tool_arguments=tc.arguments,
                            tool_error=err,
                            tool_duration_ms=0,
                            trace_id=run_trace_id,
                        )
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": payload,
                        }
                    )
                    continue

                tool_started = time.time()
                try:
                    with use_trace_id(run_trace_id):
                        result = tool.invoke(**tc.arguments)
                except Exception as e:  # noqa: BLE001
                    err = f"{type(e).__name__}: {e}"
                    payload = json.dumps({"error": err})
                    record_step(
                        LoopStep(
                            step=iteration,
                            kind="tool_result",
                            role="tool",
                            content=payload,
                            tool_name=tc.name,
                            tool_call_id=tc.id,
                            tool_arguments=tc.arguments,
                            tool_error=err,
                            tool_duration_ms=int((time.time() - tool_started) * 1000),
                            trace_id=run_trace_id,
                        )
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": payload,
                        }
                    )
                    continue

                tool_duration_ms = int((time.time() - tool_started) * 1000)
                payload = _serialize_tool_result(result)
                record_step(
                    LoopStep(
                        step=iteration,
                        kind="tool_result",
                        role="tool",
                        content=payload,
                        tool_name=tc.name,
                        tool_call_id=tc.id,
                        tool_arguments=tc.arguments,
                        tool_duration_ms=tool_duration_ms,
                        trace_id=run_trace_id,
                    )
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": payload,
                    }
                )

        if halted_reason == "max_iterations":
            # final_response = last assistant content we saw, if any.
            for s in reversed(steps):
                if s.kind == "assistant":
                    final_response = s.content
                    break
            if tool_calls_made > 0 and not (final_response or "").strip():
                _FINAL_SYNTH_MARKER = "HYDRA FINAL SYNTHESIS REQUIRED"
                messages = [
                    m for m in messages
                    if not (
                        m.get("role") == "system"
                        and _FINAL_SYNTH_MARKER in m.get("content", "")
                    )
                ]
                final_synthesis_prompt = {
                    "role": "system",
                    "content": (
                        "HYDRA FINAL SYNTHESIS REQUIRED: You are out of iterations. "
                        "Summarize what you found and give your best answer now, "
                        "no tool calls."
                    ),
                }
                messages.append(final_synthesis_prompt)
                record_step(
                    LoopStep(
                        step=last_iteration + 1,
                        kind="system",
                        role="system",
                        content=final_synthesis_prompt["content"],
                        trace_id=run_trace_id,
                    )
                )
                try:
                    synthesis_resp = self.client.chat(
                        messages,
                        model=self.model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        timeout=timeout,
                        tools=None,
                    )
                    final_response = (synthesis_resp.content or "").strip()
                except LlmError:
                    final_response = ""

                if not final_response:
                    tool_summaries: list[str] = []
                    for step in [s for s in steps if s.kind == "tool_result"][-3:]:
                        content = (step.content or "").strip().replace("\n", " ")
                        if len(content) > 240:
                            content = content[:237] + "..."
                        tool_summaries.append(
                            f"{step.tool_name or 'tool'}: {content or '(empty result)'}"
                        )
                    if tool_summaries:
                        final_response = (
                            f"Hydra reached max_iterations after {tool_calls_made} "
                            "tool call(s), but the model did not produce a final "
                            "answer. Last tool results: "
                            + "; ".join(tool_summaries)
                        )
                    else:
                        final_response = (
                            f"Hydra reached max_iterations after {tool_calls_made} "
                            "tool call(s), but the model did not produce a final answer."
                        )

                messages.append({"role": "assistant", "content": final_response})
                record_step(
                    LoopStep(
                        step=last_iteration + 1,
                        kind="assistant",
                        role="assistant",
                        content=final_response,
                        trace_id=run_trace_id,
                    )
                )
        
        # Worker evidence validation: distinguish real tool activity from heartbeat-only
        has_real_tool_activity = any(
            s.kind == "tool_result" and not s.tool_error
            for s in steps
        )
        heartbeat_only = (len(steps) > 0 and tool_calls_made == 0)
        
        # Detect fake final output after real tools (scrub pattern from pi-heads)
        fake_final_scrubbed = False
        if has_real_tool_activity and final_response:
            scrubbed = scrub_public_fake_output_preview(final_response)
            fake_final_scrubbed = scrubbed != final_response
            final_response = scrubbed

        if phantom_tool_recovery and halted_reason not in ("natural", "phantom_tool_recovery"):
            halted_reason = "phantom_tool_recovery"

        return LoopResult(
            steps=steps,
            final_response=final_response,
            iterations=last_iteration,
            tool_calls_made=tool_calls_made,
            halted_reason=halted_reason,
            messages=messages,
            has_real_tool_activity=has_real_tool_activity,
            heartbeat_only=heartbeat_only,
            fake_final_scrubbed=fake_final_scrubbed,
            trace_id=run_trace_id,
            phantom_tool_recovery=phantom_tool_recovery,
        )
