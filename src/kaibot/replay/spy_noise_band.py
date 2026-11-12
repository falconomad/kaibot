"""SPY noise-band intraday momentum, long only, 1x, and its overnight carry (issue #128, decision 15 rules 3-4).

Pre-registered in docs/prereg/spy_noise_band.md (committed before this code). A small SPY-only research routine,
not a general simulator: the rule produces cost-free round trips from the minute bars, then ONE continuous
account prices them under a cost scenario with the cost functions of `kaibot.replay.sim`. PROVISIONAL by
decision 15: go/no-go numbers come only from the engine with `SimBroker`.

Rule (Zarattini, Aziz and Barbon 2024): sigma(m) = mean over the previous 14 sessions of |close(m)/open - 1|;
upper band UB(m) = max(today's open, yesterday's close) x (1 + sigma(m)); checks at HH:00 and HH:30 from 10:00;
when flat, long if the 1-minute close > UB(m); when long, sell if the close < max(UB(m), session VWAP(m)); else
flat at the close (carry version: sold at the next session's open). Every decision fills at the next bar's open.
A decision at minute m reads only bars <= m of today and earlier sessions (see `tests/test_spy_noise_band.py`).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from kaibot.broker.shadow_costs import sell_fees
from kaibot.evaluation.metrics import max_drawdown, sharpe
from kaibot.replay.data import N_MIN, SymbolDays
from kaibot.replay.sim import buy_px, sell_px

FIRST_CHECK_M = 29  # the 09:59 bar closes at 10:00: decide there, fill at the 10:00 bar's open
LAST_CHECK_M = 359  # 15:30 (close of the 15:29 bar), the live last-30-minutes rule's decision minute
CHECK_1530_M = 359
EXIT_1555_M = 385  # neighbour exit1555: sell at the open of the 15:55 bar
EARLY_CLOSE_BEFORE_M = 300  # a session whose last bar is before 14:30 is an early-close session (prereg choice 3)
WINDOW_2022 = (np.datetime64("2022-06-01"), np.datetime64("2023-07-31"))  # NightShares 500's losing window
COSTS: tuple[float | str, ...] = (0.5, 1.0, 2.0, "v1")  # bp a side, all-in; "v1" = cost model v1
BASE_COST = 0.5
MARK2_COST = 1.0


@dataclass(frozen=True)
class Spec:
    name: str
    lookback: int = 14
    min_obs: int = 12
    check_every: int = 30
    last_check_m: int = LAST_CHECK_M
    exit_m: int | None = None  # None: the session's close; else sell at that bar's open
    carry: bool = False

    def checks(self) -> list[int]:
        return list(range(FIRST_CHECK_M, self.last_check_m + 1, self.check_every))

    def params(self) -> dict:
        return {"lookback": self.lookback, "min_obs": self.min_obs, "check_every_min": self.check_every,
                "last_check": _clock(self.last_check_m), "exit": "close" if self.exit_m is None else
                f"open of the {_clock(self.exit_m)} bar", "carry_to_next_open": self.carry}


def _clock(m: int) -> str:
    t = 9 * 60 + 30 + m + 1  # the close of bar m
    return f"{t // 60}:{t % 60:02d}"


BASE = Spec("spy_noise_band")
VERSIONS = {"spy_noise_band": BASE, "spy_noise_band_carry": replace(BASE, name="spy_noise_band_carry", carry=True)}
NEIGHBOURS = (
    replace(BASE, name="spy_noise_band~lb10", lookback=10, min_obs=9),
    replace(BASE, name="spy_noise_band~lb20", lookback=20, min_obs=17),
    replace(BASE, name="spy_noise_band~lb30", lookback=30, min_obs=26),
    replace(BASE, name="spy_noise_band~check15", check_every=15, last_check_m=374),
    replace(BASE, name="spy_noise_band~exit1555", exit_m=EXIT_1555_M),
)
LOOKBACK_NEIGHBOURS = ("lb10", "lb20", "lb30")


# ---------------------------------------------------------------- inputs (point-in-time)


def close_minute(spy: SymbolDays) -> np.ndarray:
    """Each session's last bar: 389, or the last minute with volume on an early-close session (prereg choice 3).
    The exchange calendar is known in advance, so this is not look-ahead."""
    has = spy.v > 0
    last = np.where(has.any(axis=1), N_MIN - 1 - np.argmax(has[:, ::-1], axis=1), N_MIN - 1)
    return np.where(last < EARLY_CLOSE_BEFORE_M, last, N_MIN - 1).astype(int)


def session_open(spy: SymbolDays) -> np.ndarray:
    """Open of the session's first bar (prereg choice 1)."""
    o = spy.o
    fin = np.isfinite(o)
    first = np.argmax(fin, axis=1)
    out = o[np.arange(len(o)), first]
    return np.where(fin.any(axis=1), out, np.nan)


def sigma_table(spy: SymbolDays, lookback: int, min_obs: int, close_m: np.ndarray | None = None) -> np.ndarray:
    """(sessions, 390): mean of |close(m)/open - 1| over the previous `lookback` sessions (row i uses rows < i
    only); minutes after an early close have no value; at least `min_obs` values are needed."""
    cm = close_minute(spy) if close_m is None else close_m
    move = np.abs(spy.c / session_open(spy)[:, None] - 1)
    move[np.arange(N_MIN)[None, :] > cm[:, None]] = np.nan
    return pd.DataFrame(move).rolling(lookback, min_periods=min_obs).mean().shift(1).to_numpy()


def vwap_table(spy: SymbolDays) -> np.ndarray:
    """Session VWAP through each minute (bar VWAP x volume over volume; the close before any volume)."""
    v = np.nan_to_num(spy.v)
    pv = np.cumsum(np.nan_to_num(spy.vw * v), axis=1)
    cv = np.cumsum(v, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cv > 0, pv / np.where(cv > 0, cv, 1.0), spy.c)


# ---------------------------------------------------------------- the rule: cost-free round trips


@dataclass(frozen=True)
class Trip:
    i_entry: int  # session row of the entry
    decide_m: int  # check minute of the entry decision (fill at decide_m + 1)
    entry_m: int
    entry_mid: float
    i_exit: int
    exit_m: int  # minute of the exit fill (0 = the next session's open for a carry)
    exit_mid: float
    reason: str  # "band_vwap" | "close" | "exit1555" | "next_open"


def signals(spy: SymbolDays, spec: Spec, i0: int, i1: int,
            decisions: list[tuple[int, int, str]] | None = None) -> list[Trip]:
    """The rule's round trips for entry sessions i0..i1 (cost-free mid prices). `decisions`, when given, collects
    every check decision as (session, minute, "buy" | "sell") for the look-ahead test."""
    cm = close_minute(spy)
    op = session_open(spy)
    sig = sigma_table(spy, spec.lookback, spec.min_obs, cm)
    vwap = vwap_table(spy)
    c, o = spy.c, spy.o
    n = len(spy.dates)
    trips: list[Trip] = []
    for i in range(max(i0, 1), min(i1, n - 1) + 1):
        ref = max(op[i], c[i - 1, cm[i - 1]])
        if not math.isfinite(ref):
            continue
        long_at: tuple[int, float] | None = None
        for m in spec.checks():
            if m + 1 > cm[i]:
                break
            px = c[i, m]
            ub = ref * (1 + sig[i, m])
            if long_at is not None:
                stop = max(ub, vwap[i, m]) if math.isfinite(ub) else vwap[i, m]
                if px < stop:
                    if decisions is not None:
                        decisions.append((i, m, "sell"))
                    trips.append(Trip(i, *long_at, i, m + 1, float(o[i, m + 1]), "band_vwap"))
                    long_at = None
            elif math.isfinite(ub) and px > ub:
                if decisions is not None:
                    decisions.append((i, m, "buy"))
                long_at = (m, m + 1, float(o[i, m + 1]))
        if long_at is None:
            continue
        if spec.carry and i + 1 < n and math.isfinite(op[i + 1]):
            trips.append(Trip(i, *long_at, i + 1, 0, float(op[i + 1]), "next_open"))
        elif spec.exit_m is not None and spec.exit_m <= cm[i] and spec.exit_m > long_at[1]:
            trips.append(Trip(i, *long_at, i, spec.exit_m, float(o[i, spec.exit_m]), "exit1555"))
        else:
            trips.append(Trip(i, *long_at, i, int(cm[i]), float(c[i, cm[i]]), "close"))
    return trips


# ---------------------------------------------------------------- one continuous account under a cost scenario


@dataclass
class Book:
    equity0: float
    equity: float
    trades: list[dict] = field(default_factory=list)
    daily: list[dict] = field(default_factory=list)


def minute_sigma(spy: SymbolDays) -> np.ndarray:
    """Per session: std of the PREVIOUS session's 1-minute log returns (cost model v1 input, prereg choice 7)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.diff(np.log(spy.c), axis=1)
    s = np.nanstd(np.where(np.isfinite(r), r, np.nan), axis=1)
    return np.r_[np.nan, s[:-1]]


def _buy(cost: float | str, mid: float, cash: float, sigma: float, vol: float) -> tuple[float, float]:
    """-> (shares, price per share); all the cash goes in (1x, fractional shares)."""
    if cost == "v1":
        sg = sigma if math.isfinite(sigma) else 0.0
        px = buy_px(mid, cash / mid, sg, vol)
        px = buy_px(mid, cash / px, sg, vol)
        return cash / px, px
    px = mid * (1 + float(cost) / 1e4)
    return cash / px, px


def _sell(cost: float | str, mid: float, qty: float, sigma: float, vol: float) -> tuple[float, float]:
    """-> (proceeds after fees, price per share)."""
    if cost == "v1":
        px = sell_px(mid, qty, sigma if math.isfinite(sigma) else 0.0, vol)
        return qty * px - sell_fees(qty, px), px
    px = mid * (1 - float(cost) / 1e4)
    return qty * px, px


def run_account(spy: SymbolDays, trips: Sequence[Trip], i0: int, i1: int, cost: float | str,
                equity0: float = 100_000.0) -> Book:
    """Daily mark-to-market account over sessions i0..i1. A carry still open after i1 stays marked at the close."""
    cm = close_minute(spy)
    sg = minute_sigma(spy) if cost == "v1" else np.full(len(spy.dates), np.nan)
    by_day: dict[int, list[Trip]] = {}
    for t in trips:
        by_day.setdefault(t.i_entry, []).append(t)
    book = Book(equity0, equity0)
    cash = equity0
    carried: tuple[Trip, float, float] | None = None  # (trip, shares, cash spent)
    for i in range(i0, i1 + 1):
        start = book.equity
        invested = False
        carry_in = carried is not None
        if carried is not None:
            t, q, spent = carried
            proceeds, px = _sell(cost, t.exit_mid, q, sg[i], float(spy.v[i, 0]))
            cash = proceeds
            book.trades[-1].update(exit=px, net_ret=proceeds / spent - 1)
            carried, invested = None, True
        for t in sorted(by_day.get(i, ()), key=lambda x: x.entry_m):
            invested = True
            spent = cash
            q, bpx = _buy(cost, t.entry_mid, cash, sg[i], float(spy.v[i, t.entry_m]))
            row = {"date": str(spy.dates[i]), "decide": _clock(t.decide_m), "decide_m": t.decide_m,
                   "entry_m": t.entry_m, "entry": bpx, "exit_date": str(spy.dates[t.i_exit]), "exit_m": t.exit_m,
                   "exit": math.nan, "reason": t.reason, "qty": q, "gross_ret": t.exit_mid / t.entry_mid - 1,
                   "net_ret": math.nan}
            book.trades.append(row)
            if t.i_exit == i:
                proceeds, px = _sell(cost, t.exit_mid, q, sg[i], float(spy.v[i, t.exit_m]))
                row.update(exit=px, net_ret=proceeds / spent - 1)
                cash = proceeds
            else:
                carried, cash = (t, q, spent), 0.0
        mark = cash + (carried[1] * float(spy.c[i, cm[i]]) if carried is not None else 0.0)
        book.equity = mark
        book.daily.append({"date": spy.dates[i], "start": start, "end": mark, "pnl": mark - start,
                           "invested": invested, "carry_in": carry_in, "carry_out": carried is not None})
    if carried is not None:  # still open at the end of the window: not a finished round trip
        book.trades.pop()
    return book


# ---------------------------------------------------------------- statistics


def series_stats(dates: np.ndarray, rets: np.ndarray) -> dict:
    """Total return, CAGR, annualised daily Sharpe (252 sessions, no risk-free rate) with its 50% haircut, maximum
    drawdown and calendar-year returns of a daily mark-to-market return series (the columns of the other reports)."""
    rets = np.asarray(rets, dtype=float)
    eq = np.cumprod(1 + rets) if len(rets) else np.array([1.0])
    total = float(eq[-1] - 1)
    cagr = (1 + total) ** (252 / len(rets)) - 1 if len(rets) and total > -1 else -1.0
    sh = round(sharpe(rets) * math.sqrt(252), 2) if len(rets) > 1 else 0.0
    years = np.asarray(dates, dtype="datetime64[Y]").astype(str)
    yr = (1 + pd.Series(rets)).groupby(years).prod() - 1
    yr = {str(y): round(float(v) * 100, 2) for y, v in yr.items()}
    return {"return_pct": round(total * 100, 2), "cagr_pct": round(cagr * 100, 2), "sharpe": sh,
            "sharpe_haircut": round(sh / 2, 2), "max_drawdown_pct": round(max_drawdown(np.r_[1.0, eq]) * 100, 2),
            "years": yr, "positive_years": int(sum(v > 0 for v in yr.values())), "n_years": len(yr)}


def _t(x: np.ndarray) -> float | None:
    x = np.asarray(x, dtype=float)
    if len(x) < 2 or np.std(x, ddof=1) == 0:
        return None
    return round(float(np.mean(x) / (np.std(x, ddof=1) / math.sqrt(len(x)))), 2)


def welch_t(a: np.ndarray, b: np.ndarray) -> float | None:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if len(a) < 2 or len(b) < 2:
        return None
    se = math.sqrt(np.var(a, ddof=1) / len(a) + np.var(b, ddof=1) / len(b))
    return round(float((a.mean() - b.mean()) / se), 2) if se > 0 else None


def window_return(dates: np.ndarray, rets: np.ndarray, lo=WINDOW_2022[0], hi=WINDOW_2022[1]) -> float | None:
    d = np.asarray(dates, dtype="datetime64[D]")
    k = (d >= lo) & (d <= hi)
    return round(float(np.prod(1 + np.asarray(rets)[k]) - 1) * 100, 2) if k.any() else None


def trip_stats(trades: Iterable[dict]) -> dict:
    tr = list(trades)
    net = np.array([t["net_ret"] for t in tr], dtype=float) * 1e4
    gross = np.array([t["gross_ret"] for t in tr], dtype=float) * 1e4
    return {"trades": len(tr), "net_bp_per_trip": round(float(net.mean()), 2) if len(tr) else None,
            "t_net": _t(net), "gross_bp_per_trip": round(float(gross.mean()), 2) if len(tr) else None,
            "hit_rate": round(float((net > 0).mean()), 3) if len(tr) else None,
            "sum_net_pct": round(float(np.prod(1 + net / 1e4) - 1) * 100, 2) if len(tr) else None}


def break_even(gross_rets: np.ndarray) -> dict:
    """Cost per side (bp) at which (a) the mean net per round trip and (b) the account's total return reach zero.
    (a) is half the mean gross; (b) solves prod(1 + g) x ((1 - c) / (1 + c))^n = 1 for a fixed bp cost c."""
    g = np.asarray(gross_rets, dtype=float)
    if not len(g):
        return {"per_trip_bp": None, "account_bp": None}
    log_p = float(np.sum(np.log1p(g)))
    k = math.exp(-log_p / len(g))
    acct = (1 - k) / (1 + k) * 1e4
    return {"per_trip_bp": round(float(g.mean()) * 1e4 / 2, 2), "account_bp": round(acct, 2) if log_p > 0 else None}


def account_stats(book: Book) -> dict:
    dates = np.array([r["date"] for r in book.daily], dtype="datetime64[D]")
    rets = np.array([r["pnl"] / r["start"] for r in book.daily], dtype=float)
    out = series_stats(dates, rets)
    out.update(trip_stats(book.trades))
    reasons: dict[str, int] = {}
    for t in book.trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    out.update({
        "sessions": len(book.daily),
        "days_invested_pct": round(float(np.mean([r["invested"] for r in book.daily])) * 100, 1)
        if book.daily else None,
        "carry_nights": int(sum(r["carry_out"] for r in book.daily)),
        "window_2022_23_pct": window_return(dates, rets), "exit_reasons": reasons,
        "final_equity": round(book.equity, 2)})
    return out


def overnight_returns(spy: SymbolDays, i0: int, i1: int) -> tuple[np.ndarray, np.ndarray]:
    """(dates, SPY close -> next open return) for each session i0..i1 that has a next session."""
    cm, op = close_minute(spy), session_open(spy)
    idx = np.arange(i0, min(i1, len(spy.dates) - 2) + 1)
    return spy.dates[idx], op[idx + 1] / spy.c[idx, cm[idx]] - 1


def carry_nights(spy: SymbolDays, carry_trips: Sequence[Trip], i0: int, i1: int) -> dict:
    """Carry nights (the rule long at the close) against all other nights: SPY close -> next open, gross."""
    dates, r = overnight_returns(spy, i0, i1)
    rows = {t.i_entry for t in carry_trips if t.reason == "next_open"}
    flag = np.array([i in rows for i in range(i0, i0 + len(r))], dtype=bool)
    ok = np.isfinite(r)
    a, b = r[flag & ok] * 1e4, r[~flag & ok] * 1e4
    d = np.asarray(dates, dtype="datetime64[D]")
    win = flag & ok & (d >= WINDOW_2022[0]) & (d <= WINDOW_2022[1])
    return {"carry_nights": int(len(a)), "other_nights": int(len(b)),
            "carry_mean_bp": round(float(a.mean()), 2) if len(a) else None, "carry_t": _t(a),
            "other_mean_bp": round(float(b.mean()), 2) if len(b) else None, "other_t": _t(b),
            "diff_bp": round(float(a.mean() - b.mean()), 2) if len(a) and len(b) else None,
            "welch_t": welch_t(a, b),
            "carry_nights_2022_23": int(win.sum()),
            "carry_nights_2022_23_pct": round(float(np.prod(1 + r[win]) - 1) * 100, 2) if win.any() else None}


def spy_buy_and_hold(spy: SymbolDays, i0: int, i1: int) -> tuple[np.ndarray, np.ndarray]:
    """Price-only close-to-close returns of SPY over sessions i0..i1 (the first from the previous close)."""
    cm = close_minute(spy)
    closes = spy.c[np.arange(len(spy.dates)), cm]
    lo = max(i0, 1)
    rets = closes[lo:i1 + 1] / closes[lo - 1:i1] - 1
    return spy.dates[lo:i1 + 1], np.nan_to_num(rets)


# ---------------------------------------------------------------- pass marks (prereg section 5) and section 6


def _mark(n: int, text: str, ok: bool | None, detail: str) -> dict:
    return {"mark": n, "text": text, "verdict": "cannot be judged" if ok is None else ("pass" if ok else "fail"),
            "detail": detail}


def marks_v1(by_cost: dict, neighbours: dict) -> list[dict]:
    b, m2 = by_cost[str(BASE_COST)], by_cost[str(MARK2_COST)]
    nb, t = b["net_bp_per_trip"], b["t_net"]
    years = b.get("years", {})
    pos = sum(v > 0 for y, v in years.items() if "2017" <= y <= "2026")
    lbs = {k: neighbours.get(k) for k in LOOKBACK_NEIGHBOURS}
    lb_ok = None if any(v is None for v in lbs.values()) else all(
        (v["net_bp_per_trip"] or -1) >= 0.75 and v["return_pct"] > 0 for v in lbs.values())
    return [
        _mark(1, "at least +1.5 bp net per round trip with t >= 2 (0.5 bp a side)",
              None if nb is None else (nb >= 1.5 and (t or 0) >= 2), f"{nb} bp, t {t}, {b['trades']} round trips"),
        _mark(2, "total net return positive at 1 bp a side", m2["return_pct"] > 0, f"{m2['return_pct']}%"),
        _mark(3, "at least 7 of the 10 calendar years 2017-2026 positive", pos >= 7,
              f"{pos} of {sum('2017' <= y <= '2026' for y in years)}"),
        _mark(4, "maximum drawdown under 15%", b["max_drawdown_pct"] < 15, f"{b['max_drawdown_pct']}%"),
        _mark(5, "lb10, lb20, lb30 each >= +0.75 bp net per round trip and a positive total return", lb_ok,
              "; ".join(f"{k} {v['net_bp_per_trip']} bp, {v['return_pct']}%" if v else f"{k} not run"
                        for k, v in lbs.items())),
    ]


def marks_carry(nights: dict) -> list[dict]:
    wt, w = nights["welch_t"], nights["carry_nights_2022_23_pct"]
    return [
        _mark(6, "carry nights beat other nights with t >= 2", None if wt is None else wt >= 2,
              f"{nights['carry_mean_bp']} bp vs {nights['other_mean_bp']} bp a night, Welch t {wt} "
              f"({nights['carry_nights']} carry nights)"),
        _mark(7, "carry nights positive from June 2022 to July 2023", None if w is None else w > 0,
              f"{w}% over {nights['carry_nights_2022_23']} carry nights"),
    ]


def live_trade_statement(v1_marks: Sequence[dict], at_base: dict, at_1bp: dict) -> dict:
    """Prereg section 6, mechanically: absorb / neither yet / retire. `at_base` and `at_1bp` are trip_stats of the
    version 1 round trips entered at the 15:30 check."""
    if all(m["verdict"] == "pass" for m in v1_marks):
        verdict, why = "absorb", "the noise-band rule passes marks 1-5; its 15:30 check covers the live trade"
    elif (at_base["net_bp_per_trip"] is not None and at_base["net_bp_per_trip"] >= 1.5 and (at_base["t_net"] or 0)
          >= 2 and (at_1bp["sum_net_pct"] or 0) > 0):
        verdict, why = "neither yet", ("the rule fails, but its 15:30 entries pass on their own: they need their "
                                       "own pre-registration before anything changes")
    else:
        verdict, why = "retire", "the rule fails and its 15:30 entries do not pass on their own"
    return {"verdict": verdict, "why": why, "check_1530_base": at_base, "check_1530_1bp": at_1bp}
