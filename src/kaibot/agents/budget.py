"""Claude spend control. Every call reserves its WORST-CASE cost first, then settles the actual cost.

PRD v2: above $3/day -> rules-only vetoes (room disabled); $150/month alert; $190/month -> no new entries.
The budget stop disables the LLM only, never the quant, gate or broker paths.
Prices are per million tokens and must be kept in config (checked against anthropic.com pricing).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import StrEnum


class BudgetState(StrEnum):
    OK = "ok"
    RULES_ONLY = "rules_only"  # daily soft cap reached: the room is skipped, rules-only veto applies
    NO_ENTRIES = "no_entries"  # monthly hard stop: the engine opens nothing new, still manages exits


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float


@dataclass
class BudgetGateway:
    prices: dict[str, ModelPrice]
    daily_soft_usd: float = 3.0
    monthly_alert_usd: float = 150.0
    monthly_hard_usd: float = 190.0
    spent_today: float = 0.0
    spent_month: float = 0.0
    reserved: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def worst_case(self, model: str, input_tokens: int, max_tokens: int) -> float:
        p = self.prices[model]
        return input_tokens / 1e6 * p.input_per_mtok + max_tokens / 1e6 * p.output_per_mtok

    def state(self) -> BudgetState:
        if self.spent_month + self.reserved >= self.monthly_hard_usd:
            return BudgetState.NO_ENTRIES
        if self.spent_today + self.reserved >= self.daily_soft_usd:
            return BudgetState.RULES_ONLY
        return BudgetState.OK

    @property
    def alert(self) -> bool:
        return self.spent_month >= self.monthly_alert_usd

    def reserve(self, model: str, input_tokens: int, max_tokens: int) -> float | None:
        """Return the reserved amount, or None if the call must not be made (fail closed)."""
        cost = self.worst_case(model, input_tokens, max_tokens)
        with self._lock:
            if (self.spent_today + self.reserved + cost > self.daily_soft_usd
                    or self.spent_month + self.reserved + cost > self.monthly_hard_usd):
                return None
            self.reserved += cost
            return cost

    def settle(self, reserved: float, actual: float) -> None:
        with self._lock:
            self.reserved = max(0.0, self.reserved - reserved)
            self.spent_today += actual
            self.spent_month += actual

    def new_day(self) -> None:
        with self._lock:
            self.spent_today = 0.0
