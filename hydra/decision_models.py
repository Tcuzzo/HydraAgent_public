"""Optional local typed decisions: python -m hydra.decision_models --help."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from hydra.llm import LlmError
from hydra.local_model_worker import predict_local


class LayaDecisionClient:
    """Typed choice/score/noul inference; deliberately separate from chat roles."""

    def __init__(self, model: str | None = None, *, device: str | None = None):
        self.model = model or os.environ.get("HYDRA_LAYA_MODEL") or "convaiinnovations/laya"
        self.device = device or os.environ.get("HYDRA_LAYA_DEVICE") or "cpu"

    def predict(self, state, questions: dict, *, timeout: float = 120.0) -> dict:
        if not isinstance(questions, dict) or not questions:
            raise LlmError("Laya requires a non-empty questions object")
        for name, question in questions.items():
            if not isinstance(question, dict) or question.get("type") not in {"choice", "score", "noul"}:
                raise LlmError(f"Laya question {name!r} must have type choice, score, or noul")
        result = predict_local({"backend": "laya", "model": self.model, "device": self.device,
                                "state": state, "questions": questions}, timeout=timeout)
        if not isinstance(result.get("answers"), dict) or set(result["answers"]) != set(questions):
            raise LlmError("Laya returned missing or unexpected answers")
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run local Laya typed decisions; checkpoints download on first use.")
    parser.add_argument("--state", required=True, type=Path, help="UTF-8 text or JSON state file")
    parser.add_argument("--questions", required=True, type=Path, help="JSON map of typed questions")
    parser.add_argument("--model", help="Hugging Face ID or local trained checkpoint directory")
    parser.add_argument("--device", default=None)
    parser.add_argument("--timeout", default=120.0, type=float)
    args = parser.parse_args(argv)
    try:
        state = args.state.read_text(encoding="utf-8")
        try:
            state = json.loads(state)
        except json.JSONDecodeError:
            pass
        questions = json.loads(args.questions.read_text(encoding="utf-8"))
        result = LayaDecisionClient(args.model, device=args.device).predict(state, questions, timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, LlmError) as exc:
        parser.exit(1, f"Laya: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
