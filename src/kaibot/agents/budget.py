"""Claude spend control. Every call reserves its WORST-CASE cost first, then settles the actual cost.

#309 (sized to the stakeholder's $200 of API credit; were $3 / $150 / $190): above $1/day -> rules-only vetoes (room
disabled); $20/month alert; $25/month -> no new entries; $150 in all (lifetime) -> no call ever again, and the same
no-entries state. The budget stop disables the LLM only, never the quant, gate or broker paths.
This is the in-memory model of the caps (offline evals). The live ledger is Postgres (`agents/gateway.Gateway`:
`kaibot.budget_day` holds one row per ET day, so its sum is the lifetime total).
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
    daily_soft_usd: float = 1.0
    monthly_alert_usd: float = 20.0
    monthly_hard_usd: float = 25.0
    lifetime_hard_usd: float = 150.0
    spent_today: float = 0.0
    spent_month: float = 0.0
    spent_lifetime: float = 0.0  # every settled dollar, all months (never reset)
    reserved: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def worst_case(self, model: str, input_tokens: int, max_tokens: int) -> float:
        p = self.prices[model]
        return input_tokens / 1e6 * p.input_per_mtok + max_tokens / 1e6 * p.output_per_mtok

    def __post_init__(self):
        self.spent_lifetime = max(self.spent_lifetime, self.spent_month)  # a month's spend is part of the total

    def state(self) -> BudgetState:
        if (self.spent_month + self.reserved >= self.monthly_hard_usd
                or self.spent_lifetime + self.reserved >= self.lifetime_hard_usd):
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
                    or self.spent_month + self.reserved + cost > self.monthly_hard_usd
                    or self.spent_lifetime + self.reserved + cost > self.lifetime_hard_usd):
                return None
            self.reserved += cost
            return cost

    def settle(self, reserved: float, actual: float) -> None:
        with self._lock:
            self.reserved = max(0.0, self.reserved - reserved)
            self.spent_today += actual
            self.spent_month += actual
            self.spent_lifetime += actual

    def new_day(self) -> None:
        with self._lock:
            self.spent_today = 0.0

    def new_month(self) -> None:
        """The month's spend starts again; the lifetime total never does."""
        with self._lock:
            self.spent_today = 0.0
            self.spent_month = 0.0
