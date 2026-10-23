"""Answer budget for Hydra replies: cut-off replies move to the next rung, and each rung's answer record is learned.

Ported from the Hydra research runtime (the Ask Hydra path):

* A reply that stops at its output budget (finish_reason "length") is not a final answer. When another rung waits, that
  rung answers next instead of retrying the same rung with a bigger budget (a bigger budget on the same rung measured
  66 s slower and still cut off). The last rung retries with bigger budgets, up to the cap.
* A per-rung ledger records the smallest budget each rung finished in, the largest budget it ran out of, its cut-off
  rate and its latency, separately for short and long questions. Each rung starts past the budget it last ran out of.
* Long questions skip rungs that keep cutting off long answers (they become a fallback only).
* A rung whose model is gone (HTTP 410: retired upstream) is skipped for a while instead of failing every call, and
  the next rung answers. Other 4xx errors (bad request, auth) still surface at once.
* A rung that prints its tool call as text gets that call run for real (see hydra.text_tool_calls).

Model-agnostic: rungs come from the caller's routing config and are keyed by their identity string; this module names no
model, provider or secret. The ledger is small JSON under the Hydra home directory and never holds secrets.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from hydra.llm import LlmError
from hydra.text_tool_calls import promote_text_tool_calls

LEDGER_FILE = "runtime/rung_ledger.json"
LONG_WORDS = 14
GONE_SKIP_S = 24 * 3600            # a retired rung is re-probed once a day: an upstream restore is picked up again
TRUNCATED_FINISH = frozenset({"length", "max_tokens", "max_output_tokens"})
DEFAULT_CAP = 8192
_LOCK = threading.Lock()
_ASKS = re.compile(r"\b(?:and|versus|vs\.?|compare|why|how|explain|walk me through|list|each|every|all)\b", re.I)
_GONE = re.compile(r"\bhttp\s+410\b", re.I)


class TruncatedCompletion(LlmError):
    """The reply stopped at its output budget. Not a final answer."""

    def __init__(self, message: str, response: Any = None) -> None:
        super().__init__(message)
        self.response = response


def is_long(question: str) -> bool:
    """A question this long, or with several asks in it, usually needs a long answer."""
    q = str(question or "")
    words = len(q.split())
    return words >= LONG_WORDS or (words >= 8 and len(_ASKS.findall(q)) >= 2) or q.count("?") >= 2


def is_gone_error(exc: BaseException) -> bool:
    return bool(_GONE.search(str(exc)))


def is_truncated(resp: Any) -> bool:
    """Cut off at the output budget with no tool call to run: the text is not a finished answer."""
    finish = str(getattr(resp, "finish_reason", "") or "").lower()
    return finish in TRUNCATED_FINISH and not getattr(resp, "tool_calls", None)


def hydra_home() -> Path:
    return Path(os.environ.get("HYDRA_HOME") or (Path.home() / ".hydraAgent"))


def output_cap() -> int:
    try:
        return max(256, int(os.environ.get("HYDRA_MAX_OUTPUT_TOKENS", DEFAULT_CAP)))
    except ValueError:
        return DEFAULT_CAP


class RungLedger:
    """What each rung has actually done on answers. Keyed by rung identity only."""

    def __init__(self, home: Path | str | None = None) -> None:
        self.path = Path(home or hydra_home()) / LEDGER_FILE

    def load(self) -> dict:
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            return doc if isinstance(doc, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, doc: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass  # the ledger is an optimisation: a read-only home never blocks an answer

    def record(self, identity: str, receipt: dict, *, long: bool) -> None:
        if not identity:
            return
        with _LOCK:
            doc = self.load()
            row = doc.setdefault(identity, {})
            lane = row.setdefault("long" if long else "short", {"n": 0, "cut": 0})
            lane["n"] += 1
            budget = receipt.get("max_tokens")
            if receipt.get("truncated"):
                lane["cut"] += 1
                if budget:
                    lane["cut_at"] = max(int(budget), int(lane.get("cut_at") or 0))
            elif budget:
                lane["fit"] = min(int(budget), int(lane.get("fit") or budget))
            lat = receipt.get("latency_ms")
            if lat is not None and not receipt.get("truncated"):
                prev = lane.get("latency_ms")
                lane["latency_ms"] = int(lat if prev is None else 0.6 * prev + 0.4 * lat)
            out = receipt.get("output_tokens")
            if out:
                lane["output_tokens"] = max(int(out), int(lane.get("output_tokens") or 0))
            if receipt.get("ok"):
                row.pop("gone_at", None)
            self._save(doc)

    def mark_gone(self, identity: str, error: str) -> None:
        with _LOCK:
            doc = self.load()
            row = doc.setdefault(identity, {})
            row["gone_at"] = int(time.time())
            row["gone_error"] = str(error)[:160]
            self._save(doc)

    def is_gone(self, identity: str, *, now: float | None = None) -> bool:
        at = (self.load().get(identity) or {}).get("gone_at")
        return bool(at) and ((now or time.time()) - float(at)) < GONE_SKIP_S

    def lane(self, identity: str, *, long: bool) -> dict:
        return dict(((self.load().get(identity) or {}).get("long" if long else "short")) or {})


def keeps_cutting_off(lane: dict) -> bool:
    """Measured at least twice for this answer size and cut off at least half the time."""
    n = int(lane.get("n") or 0)
    return n >= 2 and (int(lane.get("cut") or 0) / n) >= 0.5


def start_budget(lane: dict, initial: int, maximum: int) -> int:
    """First output budget for a rung: the configured start, raised past the budget it last ran out of."""
    cut, fit = lane.get("cut_at"), lane.get("fit")
    if fit and (not cut or fit > cut):
        return max(1, min(maximum, max(initial, int(fit))))
    if cut:
        return max(1, min(maximum, max(initial, int(cut) * 2)))
    return max(1, min(maximum, initial))


def _last_user_text(messages: Iterable[Any]) -> str:
    text = ""
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        if role == "user":
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
            text = content if isinstance(content, str) else text
    return text


class Rung:
    """One routing rung. ``client`` may be built lazily by ``factory`` (only when the rung is reached)."""

    def __init__(self, provider: str, model: str, client: Any = None,
                 factory: Callable[[], Any] | None = None) -> None:
        self.provider, self.model = provider, model
        self.identity = f"{provider}/{model}"
        self._client, self._factory = client, factory

    def client(self) -> Any:
        if self._client is None and self._factory is not None:
            self._client = self._factory()
        return self._client


class AnswerBudgetClient:
    """A ``.chat()`` client over ordered rungs that never hands back a cut-off reply while another rung or a bigger
    budget can finish it. Every rung move is logged (no silent downgrade)."""

    def __init__(self, rungs: list[Rung], *, question: str | None = None, ledger: RungLedger | None = None,
                 cap: int | None = None, truncation_next_rung: bool = True, log: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not rungs:
            raise ValueError("AnswerBudgetClient needs at least one rung")
        self._rungs = list(rungs)
        self._question = question
        self.ledger = ledger or RungLedger()
        self.cap = cap or output_cap()
        self.truncation_next_rung = truncation_next_rung
        self._log = log or (lambda msg: print(msg, file=sys.stderr))
        self._clock = clock
        self._sticky: Rung | None = None
        self.receipts: list[dict] = []
        self.provider, self.model = rungs[0].provider, rungs[0].model

    # pass-throughs so the wrapper is a drop-in client
    def __getattr__(self, name: str) -> Any:
        return getattr(self._rungs[0].client(), name)

    def list_models(self, **kw: Any) -> Any:
        return self._rungs[0].client().list_models(**kw)

    def _order(self, long: bool) -> list[Rung]:
        rungs = [r for r in self._rungs if not self.ledger.is_gone(r.identity)] or list(self._rungs)
        if self._sticky is not None and self._sticky in rungs:
            rungs = [self._sticky] + [r for r in rungs if r is not self._sticky]
        if long:
            ok = [r for r in rungs if not keeps_cutting_off(self.ledger.lane(r.identity, long=True))]
            rungs = ok + [r for r in rungs if r not in ok]
        return rungs

    def chat(self, messages: Any, *, model: str | None = None, max_tokens: int = 1024, **kw: Any) -> Any:
        messages = list(messages)
        long = is_long(self._question if self._question is not None else _last_user_text(messages))
        initial = int(max_tokens or 1024) * (2 if long else 1)
        order = self._order(long)
        primary = self._rungs[0]
        last_cut: TruncatedCompletion | None = None
        first_err: BaseException | None = None
        for idx, rung in enumerate(order):
            is_last = idx == len(order) - 1
            lane = self.ledger.lane(rung.identity, long=long)
            first = start_budget(lane, min(initial, self.cap), self.cap)
            budgets = [first]
            while budgets[-1] * 2 <= self.cap and len(budgets) < 3:
                budgets.append(budgets[-1] * 2)
            rung_model = model if (rung is primary and model) else rung.model
            for attempt, budget in enumerate(budgets):
                try:
                    client = rung.client()
                except Exception as exc:  # noqa: BLE001 - a rung that cannot be built is skipped, never fatal
                    self._log(f"hydra rung {rung.identity} unavailable ({type(exc).__name__}); the next rung answers")
                    client = None
                if client is None:
                    break
                t0 = self._clock()
                try:
                    resp = client.chat(messages, model=rung_model, max_tokens=budget, **kw)
                except LlmError as exc:
                    if is_gone_error(exc):
                        self.ledger.mark_gone(rung.identity, str(exc))
                        self._log(f"hydra rung {rung.identity} is gone upstream ({str(exc)[:120]}); "
                                  f"the next rung answers")
                        first_err = first_err or exc
                        break
                    raise
                resp = promote_text_tool_calls(resp, kw.get("tools"), log=self._log, rung=rung.identity)
                ms = int((self._clock() - t0) * 1000)
                receipt = {"rung": rung.identity, "max_tokens": budget, "attempt": attempt + 1, "latency_ms": ms,
                           "output_tokens": getattr(resp, "completion_tokens", 0) or 0, "answer_size":
                           "long" if long else "short"}
                if is_truncated(resp):
                    receipt["truncated"] = True
                    self.ledger.record(rung.identity, receipt, long=long)
                    self.receipts.append(receipt)
                    last_cut = TruncatedCompletion(
                        f"reply from rung {rung.identity} cut off at max_tokens={budget} (finish_reason=length)", resp)
                    if self.truncation_next_rung and not is_last:
                        self._log(f"hydra reply cut off at {budget} tokens on {rung.identity}; the next rung answers")
                        break
                    if attempt + 1 < len(budgets):
                        self._log(f"hydra reply cut off at {budget} tokens on {rung.identity}; "
                                  f"retrying with {budgets[attempt + 1]}")
                        continue
                    break
                receipt["ok"] = True
                self.ledger.record(rung.identity, receipt, long=long)
                self.receipts.append(receipt)
                if rung is not primary and self._sticky is not rung:
                    self._log(f"hydra answered on rung {rung.identity} (requested {primary.identity})")
                self._sticky = rung
                self.provider, self.model = rung.provider, rung.model
                return resp
        if last_cut is not None and last_cut.response is not None:
            # every rung and budget cut off: hand back the longest real text, flagged on stderr; never invented
            self._log("hydra: every rung cut off this reply at the output cap; the answer below is incomplete")
            return last_cut.response
        raise first_err if first_err is not None else LlmError("every hydra rung failed")
