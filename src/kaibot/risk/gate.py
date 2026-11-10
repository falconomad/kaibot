"""Deterministic risk gate. Pure functions: snapshot in, verdict out.

No LLM output can bypass or alter a verdict. Sizing is computed here, never by an agent.
Policy numbers come from the aggressiveness profile (config/profiles.yaml).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class State(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class RiskPolicy:
    risk_per_trade_pct: float  # e.g. 0.5 => 0.5% of equity at risk between entry and stop
    max_position_pct: float  # max single-name exposure as % of equity
    max_open_risk_pct: float  # sum of open (entry-stop) risk
    max_daily_loss_pct: float  # halts NEW entries for the day when breached
    max_positions: int
    max_entries_per_day: int = 20
    run_drawdown_end_pct: float = 8.0  # run ends when drawdown from the run's peak reaches this
    max_quote_age_s: float = 5.0
    warn_fraction: float = 0.8  # warn when post-trade value >= 80% of a limit
    allow_short: bool = False


# Hard ceilings: no profile may loosen these (PRD v2). Profiles are validated against them at load time.
HARD_CEILINGS = RiskPolicy(risk_per_trade_pct=1.0, max_position_pct=20, max_open_risk_pct=3.0,
                           max_daily_loss_pct=3.0, max_positions=8, max_entries_per_day=30,
                           run_drawdown_end_pct=12.0, max_quote_age_s=5.0)


def validate_policy(pol: RiskPolicy, ceil: RiskPolicy = HARD_CEILINGS) -> None:
    for f in ("risk_per_trade_pct", "max_position_pct", "max_open_risk_pct", "max_daily_loss_pct",
              "max_positions", "max_entries_per_day", "run_drawdown_end_pct", "max_quote_age_s"):
        if getattr(pol, f) > getattr(ceil, f):
            raise ValueError(f"profile loosens hard ceiling: {f}={getattr(pol, f)} > {getattr(ceil, f)}")
    if pol.allow_short and not ceil.allow_short:
        raise ValueError("shorts are disabled in v1")


@dataclass(frozen=True)
class Snapshot:
    equity: float
    day_start_equity: float
    open_positions: int
    symbol_exposure: float  # current $ exposure in this symbol
    open_risk: float  # current $ open risk across positions
    quote_age_s: float
    kill_switch: bool
    reconciled: bool
    market_open: bool
    data_ok: bool = True
    in_entry_window: bool = True  # 09:35-15:45 ET
    event_blackout: bool = False  # FOMC/CPI +-30 min
    pump_risk: bool = False  # alt-data long-block (the one alt signal used from day 1)
    symbol_halted: bool = False  # LULD / exchange halt
    has_open_position_in_symbol: bool = False
    entries_today: int = 0
    run_peak_equity: float | None = None
    buying_power: float | None = None  # ledger-capped; never exceeds the onboarding amount


@dataclass(frozen=True)
class Proposal:
    symbol: str
    side: Side
    entry: float
    stop: float
    target: float | None = None


@dataclass
class Check:
    rule: str
    state: State
    actual: float | str
    limit: float | str


@dataclass
class Verdict:
    approved: bool
    qty: int
    binding: str
    max_loss: float
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.state is State.FAIL]


def open_risk_budget(s: Snapshot, pol: RiskPolicy) -> float:
    """$ open risk allowed: min(policy cap, room left before the daily stop)."""
    day_loss = max(0.0, s.day_start_equity - s.equity)
    room_to_stop = max(0.0, s.day_start_equity * pol.max_daily_loss_pct / 100 - day_loss)
    return min(s.equity * pol.max_open_risk_pct / 100, room_to_stop)


def size_position(p: Proposal, s: Snapshot, pol: RiskPolicy) -> tuple[int, str, dict[str, int]]:
    """Return (qty, binding_constraint, candidates). Long-only unless policy allows shorts."""
    per_share_risk = abs(p.entry - p.stop)
    if per_share_risk <= 0 or p.entry <= 0:
        return 0, "invalid_stop", {}
    cands = {
        "risk_per_trade": math.floor(s.equity * pol.risk_per_trade_pct / 100 / per_share_risk),
        "position_cap": math.floor(max(s.equity * pol.max_position_pct / 100 - s.symbol_exposure, 0) / p.entry),
        "open_risk_headroom": math.floor(max(open_risk_budget(s, pol) - s.open_risk, 0) / per_share_risk),
    }
    if s.buying_power is not None:
        cands["buying_power"] = math.floor(max(s.buying_power, 0) / p.entry)
    binding = min(cands, key=cands.get)
    return max(cands[binding], 0), binding, cands


def evaluate(p: Proposal, s: Snapshot, pol: RiskPolicy) -> Verdict:
    checks: list[Check] = []

    def hard(rule: str, ok: bool, actual, limit) -> None:
        checks.append(Check(rule, State.PASS if ok else State.FAIL, actual, limit))

    hard("kill_switch", not s.kill_switch, "on" if s.kill_switch else "off", "off")
    hard("reconciled", s.reconciled, s.reconciled, True)
    hard("market_open", s.market_open, s.market_open, True)
    hard("entry_window", s.in_entry_window, s.in_entry_window, "09:35-15:45 ET")
    hard("event_blackout", not s.event_blackout, s.event_blackout, False)
    hard("data_ok", s.data_ok, s.data_ok, True)
    hard("pump_risk", not (s.pump_risk and p.side is Side.BUY), s.pump_risk, False)
    hard("symbol_halted", not s.symbol_halted, s.symbol_halted, False)
    hard("duplicate_symbol", not s.has_open_position_in_symbol, s.has_open_position_in_symbol, False)
    hard("entries_today", s.entries_today < pol.max_entries_per_day, s.entries_today, pol.max_entries_per_day)
    peak = s.run_peak_equity or s.day_start_equity
    run_dd = max(0.0, (peak - s.equity) / peak * 100)
    hard("run_drawdown_pct", run_dd < pol.run_drawdown_end_pct, round(run_dd, 3), pol.run_drawdown_end_pct)
    hard("quote_age_s", s.quote_age_s <= pol.max_quote_age_s, s.quote_age_s, pol.max_quote_age_s)
    hard("no_short", pol.allow_short or p.side is Side.BUY or s.symbol_exposure > 0, p.side.value, "long-only")
    stop_ok = (p.stop < p.entry) if p.side is Side.BUY else (p.stop > p.entry)
    hard("stop_side", stop_ok, p.stop, f"{'<' if p.side is Side.BUY else '>'} entry")
    day_loss_pct = max(0.0, (s.day_start_equity - s.equity) / s.day_start_equity * 100)
    hard("daily_loss_pct", day_loss_pct < pol.max_daily_loss_pct, round(day_loss_pct, 3), pol.max_daily_loss_pct)
    hard("max_positions", s.open_positions < pol.max_positions, s.open_positions, pol.max_positions)

    qty, binding, _ = size_position(p, s, pol) if stop_ok else (0, "invalid_stop", {})
    hard("qty_positive", qty > 0, qty, "> 0")

    max_loss = qty * abs(p.entry - p.stop)
    pos_pct = (s.symbol_exposure + qty * p.entry) / s.equity * 100
    risk_pct = (s.open_risk + max_loss) / s.equity * 100
    budget_pct = open_risk_budget(s, pol) / s.equity * 100
    for rule, val, lim in (
        ("position_pct", pos_pct, pol.max_position_pct),
        ("open_risk_pct", risk_pct, round(budget_pct, 6)),
    ):
        state = State.FAIL if val > lim + 1e-9 else (State.WARN if val >= pol.warn_fraction * lim else State.PASS)
        checks.append(Check(rule, state, round(val, 3), lim))

    approved = not any(c.state is State.FAIL for c in checks)
    return Verdict(approved, qty if approved else 0, binding, round(max_loss, 2) if approved else 0.0, checks)
