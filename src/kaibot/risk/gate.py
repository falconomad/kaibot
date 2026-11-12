"""Deterministic risk gate. Pure functions: snapshot in, verdict out.

No LLM output can bypass or alter a verdict. Sizing is computed here, never by an agent.
Policy numbers come from the aggressiveness profile (config/profiles.yaml).

Book-level checks (issue #119, decision 15 sections 3 and 6): a `Snapshot` may carry the book's gross, overnight and
sector exposure and its gap-scenario loss, checked against `BookLimits` (their own hard ceilings,
`BOOK_HARD_CEILINGS`). A snapshot without them (every intraday snapshot today) gets exactly the verdict it got
before; a proposal for a multi-day sleeve (`overnight=True`) without them is refused (`book_known`).
`BookLimits` is a separate policy so that `RiskPolicy`, its hash and every trial digest built from it stay
byte-identical for the intraday path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields, replace
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


@dataclass(frozen=True)
class BookLimits:
    """Book-level limits for multi-day sleeves (all % of equity unless noted). Level values are PROVISIONAL
    (decision 15 section 6: confirmed by the stakeholder with the first paper start)."""

    atr_risk_pct: float = 0.1  # k: a 1-ATR(20) move costs this % of equity per position (shares = E x k / ATR)
    max_name_pct: float = 10.0  # one symbol, all sleeves together
    max_gross_pct: float = 100.0  # everything held (intraday included)
    max_overnight_pct: float = 100.0  # multi-day sleeves held over the close
    max_sector_pct: float = 30.0
    max_names: int = 20  # distinct symbols in the multi-day book
    min_cash_pct: float = 2.0  # a FLOOR: cash kept aside (gross may not exceed 100 - this)
    max_gap_loss_pct: float = 12.0  # loss of the overnight book in the gap scenario (gap_loss_ps)


# Ceilings for book limits; no profile or owner setting may loosen them (min_cash_pct is a floor).
BOOK_HARD_CEILINGS = BookLimits(atr_risk_pct=0.25, max_name_pct=HARD_CEILINGS.max_position_pct, max_gross_pct=100.0,
                                max_overnight_pct=100.0, max_sector_pct=40.0, max_names=30, min_cash_pct=1.0,
                                max_gap_loss_pct=15.0)
BOOK_FLOORS = ("min_cash_pct",)

# The gap scenario: every overnight position opens this far below its last price; a resting stop does not help
# (a stop gapped through fills at the open, decision 15 section 5).
GAP_SCENARIO_PCT = 10.0
GAP_SCENARIO_ATR = 3.0


def gap_loss_ps(px: float, atr: float | None) -> float:
    """$ lost per share in the gap scenario: the larger of a 10% gap and a 3-ATR(20) gap."""
    return max(px * GAP_SCENARIO_PCT / 100, GAP_SCENARIO_ATR * (atr or 0.0))


def validate_book(lim: BookLimits, ceil: BookLimits = BOOK_HARD_CEILINGS) -> None:
    for f in fields(BookLimits):
        v, c = getattr(lim, f.name), getattr(ceil, f.name)
        if not isinstance(v, int | float) or isinstance(v, bool) or not math.isfinite(v) or v < 0:
            raise ValueError(f"book limit {f.name}={v!r} must be a number >= 0")
        if f.name in BOOK_FLOORS:
            if v < c or v > 100:
                raise ValueError(f"book limit loosens hard floor: {f.name}={v} < {c}")
        elif v > c:
            raise ValueError(f"book limit loosens hard ceiling: {f.name}={v} > {c}")


def tighten_book(level: BookLimits, owner: dict | None) -> BookLimits:
    """The owner's book settings on top of the level's: each may only tighten (lower a cap, raise the cash
    buffer). Anything looser, unknown or of the wrong type is refused (ValueError), never clamped."""
    if not owner:
        return level
    names = {f.name: f for f in fields(BookLimits)}
    changes = {}
    for k, v in owner.items():
        if k not in names:
            raise ValueError(f"unknown book setting {k!r}")
        if isinstance(v, bool) or not isinstance(v, int | float) or (k == "max_names" and v != int(v)):
            raise ValueError(f"book setting {k}={v!r} has the wrong type")
        changes[k] = int(v) if k == "max_names" else float(v)
    out = replace(level, **changes)
    validate_book(out, level)
    validate_book(out)
    return out


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
    # Book-level fields (#119). None = not known; checked only when given, required when `overnight`.
    gross_exposure: float | None = None  # $ of everything held
    overnight_exposure: float | None = None  # $ of multi-day sleeves
    sector_exposure: float | None = None  # $ held in this symbol's sector
    gap_loss: float | None = None  # $ the overnight book loses in the gap scenario
    overnight: bool = False  # the proposal is for a multi-day sleeve (held over the close)


@dataclass(frozen=True)
class Proposal:
    symbol: str
    side: Side
    entry: float
    stop: float
    target: float | None = None
    atr: float | None = None  # ATR(20), for the gap scenario of a multi-day proposal


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


def _book_caps(p: Proposal, s: Snapshot, book: BookLimits | None) -> list[tuple[str, str, float, float, float]]:
    """(rule, candidate, current $, per-share $, limit %) for every book check that applies to this buy."""
    if book is None or p.side is not Side.BUY:
        return []
    out = []
    if s.gross_exposure is not None:
        out.append(("gross_pct", "gross_headroom", s.gross_exposure, p.entry,
                    min(book.max_gross_pct, 100 - book.min_cash_pct)))
    if s.sector_exposure is not None:
        out.append(("sector_pct", "sector_headroom", s.sector_exposure, p.entry, book.max_sector_pct))
    if s.overnight:
        if s.overnight_exposure is not None:
            out.append(("overnight_pct", "overnight_headroom", s.overnight_exposure, p.entry,
                        book.max_overnight_pct))
        if s.gap_loss is not None:
            out.append(("gap_loss_pct", "gap_headroom", s.gap_loss, gap_loss_ps(p.entry, p.atr),
                        book.max_gap_loss_pct))
    return out


def _book_known(s: Snapshot, book: BookLimits | None) -> bool:
    return book is not None and None not in (s.gross_exposure, s.overnight_exposure, s.sector_exposure, s.gap_loss)


def size_position(p: Proposal, s: Snapshot, pol: RiskPolicy,
                  book: BookLimits | None = None) -> tuple[int, str, dict[str, int]]:
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
    for _, cand, cur, per_share, lim in _book_caps(p, s, book):
        cands[cand] = math.floor(max(s.equity * lim / 100 - cur, 0) / per_share) if per_share > 0 else 0
    binding = min(cands, key=cands.get)
    return max(cands[binding], 0), binding, cands


def evaluate(p: Proposal, s: Snapshot, pol: RiskPolicy, book: BookLimits | None = None) -> Verdict:
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
    if s.overnight:  # a multi-day sleeve is only sized against a known book (fail closed)
        hard("book_known", _book_known(s, book), _book_known(s, book), True)

    qty, binding, _ = size_position(p, s, pol, book) if stop_ok else (0, "invalid_stop", {})
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
    for rule, _, cur, per_share, lim in _book_caps(p, s, book):
        val = (cur + qty * per_share) / s.equity * 100
        state = State.FAIL if val > lim + 1e-9 else (State.WARN if val >= pol.warn_fraction * lim else State.PASS)
        checks.append(Check(rule, state, round(val, 3), round(lim, 6)))

    approved = not any(c.state is State.FAIL for c in checks)
    return Verdict(approved, qty if approved else 0, binding, round(max_loss, 2) if approved else 0.0, checks)
