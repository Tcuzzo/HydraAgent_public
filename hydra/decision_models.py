"""Local Kev and Laya typed decisions: python -m hydra.decision_models --help."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from hydra.llm import LlmError
from hydra.local_model_worker import predict_local
from hydra.decision_contract import validate_decision, validate_questions


class LayaDecisionClient:
    """Typed choice/score/noul inference; deliberately separate from chat roles."""

    def __init__(self, model: str | None = None, *, device: str | None = None):
        self.model = model or os.environ.get("HYDRA_LAYA_MODEL") or "convaiinnovations/laya"
        self.device = device or os.environ.get("HYDRA_LAYA_DEVICE") or "cpu"

    def predict(self, state, questions: dict, *, timeout: float = 120.0) -> dict:
        validate_questions(questions)
        result = predict_local({"backend": "laya", "model": self.model, "device": self.device,
                                "state": state, "questions": questions}, timeout=timeout)
        return validate_decision(result, questions)


class KevDecisionClient:
    """Jared Palmer's open local Kev; distinct from hosted Jev and local Laya.

    Install the pinned official source through Hydra's intelligence installer.
    The unrelated package named ``kev`` on PyPI is not this model SDK.
    """

    def __init__(self, model: str | None = None, *, device: str | None = None):
        self.model = model or os.environ.get("HYDRA_KEV_MODEL") or "jaredpalmer/kev-0.8b@v1.0"
        self.device = device or os.environ.get("HYDRA_KEV_DEVICE") or "cpu"

    def predict(self, state, questions: dict, *, timeout: float = 120.0) -> dict:
        validate_questions(questions)
        if self.device not in {"cpu", "cuda", "mps", "auto"}:
            raise LlmError("Kev device must be cpu, cuda, mps, or auto")
        result = predict_local({"backend": "kev", "model": self.model, "device": self.device,
                                "state": state, "questions": questions}, timeout=timeout)
        return validate_decision(result, questions)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run local Kev or Laya typed decisions; checkpoints download on first use.")
    parser.add_argument("--backend", choices=("laya", "kev"), default="laya")
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
        client_type = KevDecisionClient if args.backend == "kev" else LayaDecisionClient
        result = client_type(args.model, device=args.device).predict(state, questions, timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, LlmError) as exc:
        parser.exit(1, f"{args.backend}: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
