"""The SPY trend filter (issue #201, pre-registered in docs/prereg/spy_trend_filter.md before this file existed).

Hold SPY when its month-end close is above its 10-month simple moving average (Faber 2007), otherwise cash; decide
at the close of the check session T, fill at the close of T+1. Research only: a small SPY-only routine in the style
of `kaibot.replay.spy_noise_band` on the daily store (`kaibot.ingest.daily_store`, raw bars with dividends). Fills
are priced by the cost models of `kaibot.broker.costs_v2` ("v1" or "v2"). Nothing in the engine imports this
module, and nothing here trades. Every result is PROVISIONAL: the go/no-go run is the engine with `SimBroker`.

Pieces, all pure functions of arrays so the tests run them on synthetic bars:

- `SpyData.from_panel`: SPY's raw closes, opens, volume, splits and dividends, the split-adjusted close for the
  signal, and the point-in-time inputs of the cost models (20-session sigma and dollar volume BEFORE each session).
- `states(data, spec)`: the rule's state after the close of every session (in / out), changed only at checks.
- `targets(states, i0, i1)`: what the account holds after the close of each session of the window: the state
  decided at the previous close (decide at T, fill at T+1).
- `run_account`: one continuous account over sessions i0..i1 for a target path (the rule's or a random one).
- `random_timing_control`: Gate B mark 3 for SPY-only rules (decision 15 section 10), as pre-registered.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from kaibot.broker.costs_v2 import CostModel, FillCtx, get_model

RULE = "spy_trend_filter"
N_MIN = 390
SIGMA_SESSIONS = 20  # prereg "Fill model": sigma and ADV from the 20 sessions before the fill
SIGMA_FALLBACK = 0.01 / math.sqrt(N_MIN)  # 1% daily when fewer than 2 returns are known (warm-up only)
CLOSE_SHARE = 0.02  # the daily store's convention for the first / last minute's volume without minute data
MIN_CASH = 1.0  # prereg choice 4: cash under $1 is not invested
CONTROL_RUNS, CONTROL_SEED = 500, 201  # prereg mark 3: >= 500 runs, run k seeded by (201, k)


@dataclass(frozen=True)
class Spec:
    """One configuration. check: "month" | "week"; trend: "months" (mean of the last n month-end closes, the
    current check counted as the latest) | "sessions" (mean of the last n session closes); band: in above
    (1 + band) x average, out below (1 - band) x average, otherwise keep (0: strictly above = in)."""

    name: str
    check: str = "month"
    trend: str = "months"
    n: int = 10
    band: float = 0.0

    def params(self) -> dict:
        return asdict(self)


BASE = Spec(RULE)
NEIGHBOURS = {  # docs/prereg/spy_trend_filter.md, "Neighbours": one setting changed each
    "sma150d": Spec(f"{RULE}~sma150d", trend="sessions", n=150),
    "sma200d": Spec(f"{RULE}~sma200d", trend="sessions", n=200),
    "sma250d": Spec(f"{RULE}~sma250d", trend="sessions", n=250),
    "weekly": Spec(f"{RULE}~weekly", check="week"),
    "band1": Spec(f"{RULE}~band1", band=0.01),
}


# ---------------------------------------------------------------- data


@dataclass
class SpyData:
    dates: np.ndarray  # datetime64[D]
    o: np.ndarray  # raw open
    c: np.ndarray  # raw close
    v: np.ndarray  # shares
    split: np.ndarray  # shares per share held, from this session's open (1.0: nothing)
    div: np.ndarray  # cash per share held at the previous close, ex this session (0.0: nothing)
    sig: np.ndarray  # split-adjusted close without dividends: the signal price
    sigma_1m: np.ndarray = field(default=None)  # 1-minute sigma known before the session (v1 input)
    adv: np.ndarray = field(default=None)  # mean dollar volume of the 20 sessions before the session (v2 input)

    def __post_init__(self) -> None:
        self.dates = np.asarray(self.dates, dtype="datetime64[D]")
        for k in ("o", "c", "v", "split", "div", "sig"):
            setattr(self, k, np.asarray(getattr(self, k), dtype=float))
        self.split = np.where(np.isfinite(self.split) & (self.split > 0), self.split, 1.0)
        self.div = np.where(np.isfinite(self.div) & (self.div > 0), self.div, 0.0)
        if self.sigma_1m is None or self.adv is None:
            self.sigma_1m, self.adv = cost_inputs(self.sig, self.c, self.v)

    @classmethod
    def from_panel(cls, panel, spy: str = "SPY") -> SpyData:
        """From a RAW `DailyPanel` of the daily store (or `DailyPanel.make` in tests)."""
        j = panel.col(spy)
        adj = panel.adjusted("split")
        return cls(panel.dates, panel.o[:, j], panel.c[:, j], panel.v[:, j], panel.split[:, j], panel.div[:, j],
                   adj.c[:, j])

    def index_from(self, d) -> int:
        return int(np.searchsorted(self.dates, np.datetime64(d, "D"), side="left"))

    def index_upto(self, d) -> int:
        return int(np.searchsorted(self.dates, np.datetime64(d, "D"), side="right")) - 1


def cost_inputs(sig: np.ndarray, c: np.ndarray, v: np.ndarray, n: int = SIGMA_SESSIONS
                ) -> tuple[np.ndarray, np.ndarray]:
    """Per session i: the 1-minute sigma (std of the daily log returns of the n sessions before i, over sqrt(390))
    and the mean dollar volume of the n sessions before i (NaN when unknown). Nothing of session i itself."""
    lr = pd.Series(np.log(sig)).diff()
    sd = lr.rolling(n, min_periods=2).std().shift(1).to_numpy() / math.sqrt(N_MIN)
    sd = np.where(np.isfinite(sd) & (sd > 0), sd, SIGMA_FALLBACK)
    adv = pd.Series(c * v).rolling(n, min_periods=1).mean().shift(1).to_numpy()
    return sd, adv


# ---------------------------------------------------------------- the rule


def check_flags(dates: np.ndarray, kind: str) -> np.ndarray:
    """True on the last stored session of each month ("month") or week ("week"); never on the last session
    (prereg choice 1: its successor is not known)."""
    d = np.asarray(dates, dtype="datetime64[D]")
    if kind == "month":
        key = d.astype("datetime64[M]").astype(int)
    elif kind == "week":
        key = ((d - np.datetime64("1970-01-05", "D")).astype(int)) // 7  # weeks starting Monday
    else:
        raise ValueError(f"check must be month or week, not {kind!r}")
    out = np.zeros(len(d), dtype=bool)
    out[:-1] = key[1:] != key[:-1]
    return out


def trend(data: SpyData, spec: Spec) -> np.ndarray:
    """The moving average each session would be compared with (NaN without enough history)."""
    s = data.sig
    n = len(s)
    if spec.trend == "sessions":
        return pd.Series(s).rolling(spec.n, min_periods=spec.n).mean().to_numpy()
    if spec.trend != "months":
        raise ValueError(f"trend must be months or sessions, not {spec.trend!r}")
    me = np.flatnonzero(check_flags(data.dates, "month") & np.isfinite(s))
    out = np.full(n, np.nan)
    for i in range(n):
        if not math.isfinite(s[i]):
            continue
        prev = me[me < i]  # completed month-ends before i; i itself is the latest "month-end" when it is one
        if len(prev) < spec.n - 1:
            continue
        out[i] = (s[i] + s[prev[-(spec.n - 1):]].sum()) / spec.n if spec.n > 1 else s[i]
    return out


def states(data: SpyData, spec: Spec) -> np.ndarray:
    """The rule's state after the close of each session (True = in). Starts out; changes only at a check with a
    finite close and average (prereg choices 2 and 3)."""
    checks = check_flags(data.dates, spec.check)
    avg = trend(data, spec)
    out = np.zeros(len(data.dates), dtype=bool)
    cur = False
    for i in range(len(out)):
        p, a = data.sig[i], avg[i]
        if checks[i] and math.isfinite(p) and math.isfinite(a):
            if spec.band <= 0:
                cur = p > a
            elif p > a * (1 + spec.band):
                cur = True
            elif p < a * (1 - spec.band):
                cur = False
        out[i] = cur
    return out


def targets(state: np.ndarray, i0: int, i1: int) -> np.ndarray:
    """Holding after the close of each session i0..i1: the state decided at the previous close (fill at T+1).
    A session without a close carries the previous target forward (choice 2)."""
    if i0 < 1:
        raise ValueError("the window needs one session of history before it (the first decision)")
    return np.asarray(state[i0 - 1:i1], dtype=bool).copy()


# ---------------------------------------------------------------- the account


@dataclass
class Book:
    equity0: float
    daily: list[dict] = field(default_factory=list)
    trips: list[dict] = field(default_factory=list)
    equity: float = 0.0
    bought: float = 0.0
    sold: float = 0.0
    costs: float = 0.0
    dividends: float = 0.0


def _fill(model: CostModel, side: str, mid: float, qty: float, sigma: float, vol: float, adv: float,
          venue: str, mult: float) -> tuple[float, float]:
    """(price per share, spread + impact $ per share) with spread and impact scaled by `mult`."""
    f = model.fill(side, FillCtx(mid, qty, sigma, max(vol, 1.0), adv, venue, mid))
    per = (f.half + f.impact) * mult
    return (mid + per if side == "buy" else mid - per), per


def run_account(data: SpyData, target: np.ndarray, i0: int, i1: int, *, costs: str = "v1", mult: float = 1.0,
                fill: str = "base", equity0: float = 100_000.0, cash_returns: np.ndarray | None = None) -> Book:
    """One continuous account over sessions i0..i1 (prereg "Frozen settings" and "Fill model").

    target[k] is what the account holds after session i0 + k: True = the whole account in SPY. Flat in cash at the
    close of i0 - 1. Each session: at the open, dividends on the shares held at the previous close are credited and
    splits applied; cash earns `cash_returns[i]` (None: 0); the trade happens at the close (fill "base") or the
    open (fill "stress": twice the spread and impact for v1, venue "open" for v2); the account is marked at the
    close. An open position at i1 is marked, not sold. `mult` scales spread and impact (0 = gross), never fees."""
    if fill not in ("base", "stress"):
        raise ValueError("fill must be base or stress")
    model = get_model(costs)
    target = np.asarray(target, dtype=bool)
    if len(target) != i1 - i0 + 1:
        raise ValueError("one target per session of the window")
    k_mult = mult * (2.0 if fill == "stress" and costs == "v1" else 1.0)
    venue = "close" if fill == "base" else "open"
    book = Book(equity0)
    cash, qty = float(equity0), 0.0
    last = data.c[i0 - 1] if math.isfinite(data.c[i0 - 1]) else math.nan
    trip = None
    for k, i in enumerate(range(i0, i1 + 1)):
        start = cash + qty * (last if math.isfinite(last) else 0.0)
        divs = qty * data.div[i]
        qty *= data.split[i]
        if cash_returns is not None and math.isfinite(cash_returns[i]):
            cash *= 1.0 + float(cash_returns[i])
        cash += divs
        book.dividends += divs
        if trip is not None:
            trip["dividends"] += divs
        mid = data.c[i] if fill == "base" else data.o[i]
        cost_today = traded = 0.0
        if math.isfinite(mid) and mid > 0:
            vol = CLOSE_SHARE * data.v[i] if math.isfinite(data.v[i]) else 1.0
            sig, adv = float(data.sigma_1m[i]), float(data.adv[i])
            if target[k] and cash >= MIN_CASH:
                px, per = _fill(model, "buy", mid, cash / mid, sig, vol, adv, venue, k_mult)
                q = cash / px
                qty += q
                cost_today += q * per
                traded += cash
                book.bought += cash
                if trip is None:
                    trip = {"entry_i": i, "entry_date": str(data.dates[i]), "entry_px": round(px, 4),
                            "invested": round(cash, 2), "exit_i": None, "exit_date": None, "exit_px": None,
                            "dividends": 0.0, "costs": 0.0}
                trip["costs"] += q * per
                cash = 0.0
            elif not target[k] and qty > 0:
                px, per = _fill(model, "sell", mid, qty, sig, vol, adv, venue, k_mult)
                fees = model.sell_fees(qty, px, mid)
                proceeds = qty * px - fees
                cost_today += qty * per + fees
                traded += qty * px
                book.sold += qty * px
                cash += proceeds
                if trip is not None:
                    trip.update(exit_i=i, exit_date=str(data.dates[i]), exit_px=round(px, 4))
                    trip["costs"] += qty * per + fees
                    trip["pnl"] = round(proceeds + trip["dividends"] - trip["invested"], 2)
                    trip["sessions"] = i - trip["entry_i"]
                    book.trips.append(trip)
                    trip = None
                qty = 0.0
        if math.isfinite(data.c[i]):
            last = data.c[i]
        end = cash + qty * (last if math.isfinite(last) else 0.0)
        book.costs += cost_today
        book.daily.append({"date": str(data.dates[i]), "start": start, "end": end, "pnl": end - start,
                           "cost": cost_today, "traded": traded, "dividends": divs,
                           "exposure": qty * last / end if end > 0 and math.isfinite(last) else 0.0})
    if trip is not None:  # open at the end: marked, not sold
        trip["open_at_end"] = True
        trip["sessions"] = i1 + 1 - trip["entry_i"]
        trip["pnl"] = round(qty * last + trip["dividends"] - trip["invested"], 2)
        book.trips.append(trip)
    book.equity = cash + qty * (last if math.isfinite(last) else 0.0)
    return book


def daily_frame(book: Book) -> pd.DataFrame:
    return pd.DataFrame(book.daily, columns=["date", "start", "end", "pnl", "cost", "traded", "dividends",
                                             "exposure"])


def returns(book: Book) -> pd.Series:
    d = daily_frame(book)
    return pd.Series((d["pnl"] / d["start"]).to_numpy(dtype=float), index=pd.to_datetime(d["date"]))


def cost_share(book: Book) -> pd.Series:
    """Each session's costs as a share of its starting equity (gate v2's `costs` series)."""
    d = daily_frame(book)
    return pd.Series((d["cost"] / d["start"]).to_numpy(dtype=float), index=pd.to_datetime(d["date"]))


# ---------------------------------------------------------------- statistics


def _sharpe(r: np.ndarray) -> float:
    r = np.asarray(r, dtype=float)
    sd = r.std(ddof=1) if r.size > 1 else 0.0
    return float(r.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0


def _max_dd(r: np.ndarray) -> float:
    eq = np.r_[1.0, np.cumprod(1 + np.asarray(r, dtype=float))]
    return float((1 - eq / np.maximum.accumulate(eq)).max())


def series_stats(dates, rets) -> dict:
    """Total return, CAGR, Sharpe (and its 50% haircut), max drawdown and calendar years of a daily series."""
    r = np.asarray(rets, dtype=float)
    total = float(np.prod(1 + r) - 1) if r.size else 0.0
    cagr = (1 + total) ** (252 / r.size) - 1 if r.size and total > -1 else -1.0
    years = pd.Series(1 + r).groupby(np.asarray(dates, dtype="datetime64[Y]").astype(str)).prod() - 1
    sh = _sharpe(r)
    return {"return_pct": round(total * 100, 2), "cagr_pct": round(cagr * 100, 2), "sharpe": round(sh, 2),
            "sharpe_haircut": round(sh / 2, 2), "max_drawdown_pct": round(_max_dd(r) * 100, 2),
            "years": {str(y): round(float(v) * 100, 2) for y, v in years.items()},
            "positive_years": int((years > 0).sum()), "n_years": int(len(years))}


def account_stats(book: Book) -> dict:
    d = daily_frame(book)
    r = (d["pnl"] / d["start"]).to_numpy(dtype=float)
    out = series_stats(d["date"].to_numpy(dtype="datetime64[D]"), r)
    yrs = len(r) / 252 if len(r) else math.nan
    avg_eq = float(d["start"].mean()) if len(d) else math.nan
    closed = [t for t in book.trips if not t.get("open_at_end")]
    out.update({
        "avg_exposure_pct": round(float(d["exposure"].mean()) * 100, 1) if len(d) else None,
        "sessions_in_pct": round(float((d["exposure"] > 0.5).mean()) * 100, 1) if len(d) else None,
        "round_trips": len(book.trips), "closed_round_trips": len(closed),
        "open_at_end": len(book.trips) - len(closed),
        "avg_hold_sessions": round(float(np.mean([t["sessions"] for t in book.trips])), 1) if book.trips else None,
        "turnover_pct_per_year": round((book.bought + book.sold) / 2 / avg_eq / yrs * 100, 1) if yrs else None,
        "cost_bp_per_year": round(book.costs / avg_eq / yrs * 1e4, 1) if yrs else None,
        "costs_paid": round(book.costs, 2), "dividends": round(book.dividends, 2),
        "net_pnl": round(book.equity - book.equity0, 2), "final_equity": round(book.equity, 2),
    })
    return out


def break_even(pnl_1x: float, pnl_0x: float) -> dict:
    """The multiple of spread and impact at which net profit reaches zero, from the 0x and 1x runs."""
    if pnl_0x <= 0:
        return {"multiple": None, "note": "loses before costs"}
    if pnl_0x <= pnl_1x:
        return {"multiple": None, "note": "costs did not lower the profit"}
    return {"multiple": round(pnl_0x / (pnl_0x - pnl_1x), 1), "note": "from the 0x and 1x runs"}


# ---------------------------------------------------------------- Gate B mark 3: the random-timing control


def spells(target: np.ndarray) -> list[tuple[int, int]]:
    """(first held offset, held sessions) of each run of True in a target path, offsets into the window. A spell
    reaching the end has its sessions counted to the end (it is the open-at-end round trip)."""
    t = np.r_[False, np.asarray(target, dtype=bool), False].astype(int)
    starts, ends = np.flatnonzero(np.diff(t) == 1), np.flatnonzero(np.diff(t) == -1)
    return [(int(a), int(b - a)) for a, b in zip(starts, ends, strict=True)]


def random_target(n: int, durations: Sequence[int], rng: np.random.Generator) -> tuple[np.ndarray, int]:
    """One random path over n sessions (prereg Amendment 1): the durations in a random order, separated by random
    gaps. Each spell occupies its d held sessions plus its sale session (index n, past the end, for a spell held to
    the end), so a sale and a buy never share a close and spells never merge. The F = n + 1 - sum(d + 1) free
    sessions are split into len(durations) + 1 gaps by a uniform random composition (stars and bars), so every
    arrangement of that order is equally likely and every duration is always placed when the rule's own spells
    fit. -> (path, durations not placed: all of them when they cannot fit)."""
    d = np.asarray(durations, dtype=int)
    path = np.zeros(n, dtype=bool)
    k = len(d)
    free = n + 1 - int((d + 1).sum())
    if k == 0:
        return path, 0
    if free < 0 or (d < 1).any():
        return path, k
    order = rng.permutation(k)
    bars = np.sort(rng.choice(free + k, size=k, replace=False))
    gaps = np.diff(np.r_[-1, bars, free + k]) - 1  # k + 1 gaps, each >= 0, summing to `free`
    pos = int(gaps[0])
    for j, g in zip(order, gaps[1:], strict=True):
        path[pos:pos + d[j]] = True
        pos += int(d[j]) + 1 + int(g)
    return path, 0


def random_timing_control(data: SpyData, target: np.ndarray, i0: int, i1: int, *, costs: str = "v1",
                          runs: int = CONTROL_RUNS, seed: int = CONTROL_SEED, equity0: float = 100_000.0) -> dict:
    """Gate B mark 3 for a SPY-only rule (decision 15 section 10; prereg "Pass marks", mark 3): the percentile of
    the rule's net Sharpe among `runs` seeded random-timing accounts with the rule's number of round trips and its
    holding durations, priced by the same account routine and costs (base fill). Ties count against the rule.
    The keys are those `gate_v2.mark_3` reads (percentile, runs, matched)."""
    if runs < 1:
        raise ValueError("runs must be at least 1")
    sp = spells(target)
    if not sp:
        raise ValueError("the rule never held SPY in the window: there is nothing to match")
    own = run_account(data, target, i0, i1, costs=costs, equity0=equity0)
    own_r = returns(own).to_numpy()
    own_sh, own_ret = _sharpe(own_r), own.equity / equity0 - 1
    durations = [d for _, d in sp]
    n = len(target)
    sh, ret = np.empty(runs), np.empty(runs)
    unplaced = 0
    for k in range(runs):
        path, miss = random_target(n, durations, np.random.default_rng(np.random.SeedSequence([seed, k])))
        unplaced += miss
        b = run_account(data, path, i0, i1, costs=costs, equity0=equity0)
        sh[k], ret[k] = _sharpe(returns(b).to_numpy()), b.equity / equity0 - 1
    q = lambda x: [round(float(v), 4) for v in np.quantile(x, [0.05, 0.5, 0.9, 0.95])]  # noqa: E731
    s_q, r_q = q(sh), [round(v * 100, 2) for v in q(ret)]
    return {
        "kind": "random_timing", "rule": RULE, "costs": costs, "runs": runs, "seed": seed,
        "round_trips": len(sp), "open_at_end": int(sp[-1][0] + sp[-1][1] == n),
        "durations_sessions": durations, "sessions_in": int(sum(durations)), "sessions": n,
        "unfilled": int(unplaced), "matched": unplaced == 0,
        "strategy_sharpe": round(own_sh, 4), "strategy_return_pct": round(own_ret * 100, 2),
        "percentile": float((sh < own_sh).mean() * 100), "return_percentile": float((ret < own_ret).mean() * 100),
        "random_p05": s_q[0], "random_median": s_q[1], "random_p90": s_q[2], "random_p95": s_q[3],
        "random_return_pct_median": r_q[1], "random_return_pct_p90": r_q[2],
        "per_run": {"sharpe": [round(float(x), 4) for x in sh], "return_pct": [round(float(x) * 100, 3) for x in ret]},
    }
