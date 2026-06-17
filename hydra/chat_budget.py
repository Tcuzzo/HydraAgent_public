"""hydra.chat_budget — a small per-sender daily budget for *real chat*.

Policy: each sender gets a budget for conversational turns; once they pass the budget,
the agent keeps talking on the cheap LOCAL model (qwen2.5-coder) instead of spending
more cloud. The budget resets every day. Work turns are billed separately — this is
just the "don't burn money just chatting" guardrail.

Storage is a single JSON file: { key: {"day": "YYYY-MM-DD", "spent": int} }. Reads and
writes are whole-file (small data, single-operator typical), so a fresh ledger always
sees the latest total on disk.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

CHEAP_MODEL = "ollama"  # local qwen2.5-coder — the cost-free fallback for over-budget senders


def _day(now: datetime | None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d")


class BudgetLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

    def spent_today(self, key: str, *, now: datetime | None = None) -> int:
        entry = self._read().get(key)
        if not isinstance(entry, dict) or entry.get("day") != _day(now):
            return 0
        try:
            return int(entry.get("spent", 0))
        except (TypeError, ValueError):
            return 0

    def record(self, key: str, tokens: int, *, now: datetime | None = None) -> int:
        """Add `tokens` to today's spend for `key`; returns the new daily total."""
        data = self._read()
        new_total = self.spent_today(key, now=now) + max(0, int(tokens))
        data[key] = {"day": _day(now), "spent": new_total}
        self._write(data)
        return new_total

    def over_budget(self, key: str, *, limit: int, now: datetime | None = None) -> bool:
        return self.spent_today(key, now=now) >= limit

    def choose_model(
        self,
        key: str,
        *,
        limit: int,
        premium: str,
        cheap: str = CHEAP_MODEL,
        now: datetime | None = None,
    ) -> str:
        """The model to use for this sender's next chat turn: the premium model while
        they're within budget, the cheap model once they've used it up."""
        return cheap if self.over_budget(key, limit=limit, now=now) else premium
