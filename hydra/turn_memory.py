"""Small shared memory seam for the live Textual and legacy chat surfaces."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from hydra.loop import LoopResult
from hydra.llm import LlmError


def run_observed_turn(loop, *args, **kwargs) -> LoopResult:
    """Return interrupted runs through the same evidence-persistence path.

    The agent loop keeps raising provider errors for callers that implement
    provider failover. Chat and team surfaces instead retain the attached
    partial result, so already completed actions do not disappear on failure.
    """
    try:
        return loop.run(*args, **kwargs)
    except LlmError as exc:
        partial = getattr(exc, "partial_result", None)
        if not isinstance(partial, LoopResult):
            raise
        failure = exc.to_dict()
        message = (f"Provider interrupted this turn: {failure['message']}\n"
                   f"Recorded tool observations: {partial.tool_calls_made}. "
                   "The task is incomplete; review those observations before retrying.")
        return replace(partial, final_response=message, provider_error=failure,
                       messages=[*partial.messages, {"role": "assistant", "content": message}])


def recall_turn(query, *, profile, builder, memory_root, workspace_root, max_chars):
    """Return ephemeral context, public metadata, and visible degradation notices."""
    if not profile.memory.enabled or builder is None or memory_root is None:
        return "", {"status": "disabled"}, []
    budget = max(0, min(int(max_chars), profile.memory.max_chars))
    try:
        recall = builder(query, root=memory_root, workspace_root=workspace_root,
                         top_k=profile.memory.top_k, max_chars=budget,
                         min_similarity=profile.memory.min_similarity)
        data = getattr(recall, "data", {}) or {}
        metadata = {key: data[key] for key in (
            "backend", "fallback", "fallback_reason", "chunks_returned", "context_chars"
        ) if key in data}
        metadata["status"] = getattr(recall, "status", "unknown")
        warnings = []
        if data.get("fallback"):
            warnings.append(f"Memory uses {data.get('backend', 'fallback')} recall: "
                            f"{data.get('fallback_reason', 'primary backend unavailable')}")
        if metadata["status"] != "OK":
            warnings.append(f"Memory recall unavailable: {getattr(recall, 'report', metadata['status'])}")
        context = str(getattr(recall, "context", "") or "") if metadata["status"] == "OK" else ""
        return context[:budget], metadata, warnings
    except Exception as exc:
        return "", {"status": "error"}, [f"Memory recall unavailable: {exc}"]


def learn_turn(goal, result, *, enabled, workspace_root, memory_root, provider, model):
    """Record real tool observations; persistence trouble must remain visible."""
    if not enabled or memory_root is None:
        return {"status": "disabled"}, []
    try:
        from hydra.execution_learning import record_execution_lesson
        receipt = record_execution_lesson(
            repo_root=Path(workspace_root), memory_root=Path(memory_root), goal=goal,
            result=result, provider=provider, model=model,
        )
        return receipt, []
    except Exception as exc:
        return {"status": "error"}, [f"Execution memory was not saved: {exc}"]


def finish_turn(results: list[LoopResult], previous_messages: list[dict], final_text: str):
    """Keep retry evidence and tool transcript, without persisting recalled context.

    The retry receives the preceding result's messages. Its final transcript thus
    contains both attempts, while steps and counters need explicit combination.
    Only persistent system messages from the prior conversation survive; recall,
    budget, tracing, and retry instructions are scoped to the current turn.
    """
    last = results[-1]
    result = replace(last, final_response=final_text,
                     steps=[step for run in results for step in run.steps],
                     iterations=sum(run.iterations for run in results),
                     provider_calls=sum(run.provider_calls for run in results),
                     tool_calls_made=sum(run.tool_calls_made for run in results),
                     has_real_tool_activity=any(run.has_real_tool_activity for run in results))
    permanent_system = [m for m in previous_messages if m.get("role") == "system"]
    history = [dict(m) for m in last.messages
               if m.get("role") != "system" or m in permanent_system]
    # Context commands may have been accepted while the model was running.
    # Retain those current persistent instructions as well as its transcript.
    history.extend(dict(m) for m in permanent_system if m not in history)
    current_user = max((i for i, m in enumerate(history) if m.get("role") == "user"), default=-1)
    for index in range(len(history) - 1, current_user, -1) if current_user >= 0 else ():
        message = history[index]
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            message["content"] = final_text
            break
    else:
        history.append({"role": "assistant", "content": final_text})
    result.messages = history
    return result
