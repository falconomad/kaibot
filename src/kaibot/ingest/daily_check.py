"""Checks on the daily bar store (story S84, issue #124). No network, nothing is changed in the store's data.

1. Minute history against the daily bars (`compare_minutes`). For a sample of symbols, every session of
   data/bars_1m is turned into one bar the way the multi-day harness does it (first minute's open, last minute's
   close, high / low over 09:30-15:59 ET, volume summed) and compared with Alpaca's daily bar, put on the minute
   history's basis (split-adjusted). Neither side is "fixed": the differences are the finding.
   - Open and close differ by design: the minute bars stop at 15:59, the daily close is the last trade of the day,
     normally the closing-auction print just after 16:00; a daily open is the first eligible trade of the day.
   - Daily volume is larger: it includes the closing auction and extended hours.
   - A close more than 5% apart is not an auction effect but a different split basis (minute files downloaded
     before a later split, or a split missing from the table). Those days are counted and listed separately.
2. The corporate-actions table against Alpaca's own adjusted bars (`check_adjustment`). The fetch stores Alpaca's
   `adjustment=all` close and volume for every key. Their ratio to the raw bars is the factor Alpaca applies;
   it is compared, step by step, with the factor the table gives. A step Alpaca has and the table has not is a
   missing action (or a spin-off, which the table does not adjust for); the other way round is a row Alpaca does
   not apply. Keys with such steps get the verdict `mismatch` in check/adjustment.csv, and
   `DailyPanel.adjusted(source="auto")` then uses Alpaca's factor for them.

Outputs under <data>/daily/check: minute_vs_daily.csv (every compared session), minute_vs_daily_summary.csv,
adjustment.csv (one verdict per key), adjustment_steps.csv (every step that differs), summary.txt.
"""

from __future__ import annotations

import os
import random
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
import pandas as pd

from kaibot.ingest.daily_bars import ADJUSTING_TYPES
from kaibot.ingest.daily_store import SCALE_TOL, DailyStore, _columns, table_factors

ET = "America/New_York"
OPEN_MIN = 9 * 60 + 30
N_MIN = 390  # 09:30-15:59 ET
EXIT_M = 385  # the 15:55 bar, the last one a decision at the close can have seen
DIFF_BP = 0.5  # a price counts as different beyond half a basis point
SAMPLE = ("SPY", "XLC", "XLK", "AAPL", "NVDA", "TSLA", "AMZN", "GOOGL", "CMG", "AVGO", "WMT", "GE", "XOM", "KO",
          "T", "META", "ELV", "RTX", "DOW", "DD")  # splits, big dividends, spin-offs, renamed and reused tickers
ABS_TOL = 0.0015  # a step in the factor ratio below 0.15% is rounding or a dividend-formula difference
REL_TOL = 0.25  # ... and so is one below a quarter of the action's own size (large special dividends)
NOISE_K = 6.0
NOISY = 0.005  # rounding noise that hides steps of 0.5%: the key cannot be verified
SPLIT_TOL = 0.01
VERDICT_COLUMNS = ["key", "verdict", "sessions", "steps", "worst_step_pct", "split_basis_days", "noise_pct",
                   "first_step"]
STEP_COLUMNS = ["key", "date", "class", "alpaca_factor", "table_factor"]


# ---------------------------------------------------------------- minute history -> one bar per session


def minute_daily(data_dir: str | Path, symbol: str, start=None, end=None) -> pd.DataFrame | None:
    """Daily bars of `symbol` from data/bars_1m, on the minute history's basis (split-adjusted as of its
    download): date, o, h, l, c, v, c1555, v_first, v_last. None without minute files."""
    files = sorted((Path(data_dir) / "bars_1m" / f"symbol={symbol}").glob("*.parquet"))
    if start is not None:
        files = [f for f in files if f.stem >= str(np.datetime64(start, "M"))]
    if end is not None:
        files = [f for f in files if f.stem <= str(np.datetime64(end, "M"))]
    if not files:
        return None
    df = pd.concat([pd.read_parquet(f, columns=["ts", "o", "h", "l", "c", "v"]) for f in files], ignore_index=True)
    et = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(ET)
    df["m"] = et.dt.hour * 60 + et.dt.minute - OPEN_MIN
    df["date"] = et.dt.tz_localize(None).dt.normalize()
    df = df[(df["m"] >= 0) & (df["m"] < N_MIN)].sort_values(["date", "m"])
    if start is not None:
        df = df[df["date"] >= pd.Timestamp(np.datetime64(start, "D"))]
    if end is not None:
        df = df[df["date"] <= pd.Timestamp(np.datetime64(end, "D"))]
    if not len(df):
        return None
    g = df.groupby("date", sort=True)
    out = pd.DataFrame({"o": g["o"].first(), "h": g["h"].max(), "l": g["l"].min(), "c": g["c"].last(),
                        "v": g["v"].sum(), "v_first": g["v"].first(), "v_last": g["v"].last()})
    out["c1555"] = df[df["m"] <= EXIT_M].groupby("date", sort=True)["c"].last().reindex(out.index)
    out = out.reset_index()
    out["date"] = out["date"].astype("datetime64[ns]")
    out.insert(0, "symbol", symbol)
    return out[["symbol", "date", "o", "h", "l", "c", "v", "c1555", "v_first", "v_last"]]


def compare_minutes(store: DailyStore, data_dir: str | Path, keys: Sequence[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(every compared session, one summary row per symbol). Keys without minute files are left out."""
    days, rows = [], []
    for key in keys:
        if "@" in key:
            continue  # the minute history holds today's company under each ticker
        m = minute_daily(data_dir, key)
        if m is None:
            continue
        p = store.panel([key], minute_share=None).adjusted("split")
        d = pd.DataFrame({"date": p.dates.astype("datetime64[ns]"), **{k: getattr(p, k)[:, 0] for k in "ohlcv"}})
        j = m.merge(d, on="date", how="outer", suffixes=("_m", "_d"), indicator=True)
        both = j[j["_merge"] == "both"]
        one = pd.DataFrame({"symbol": key, "date": both["date"].dt.strftime("%Y-%m-%d")})
        for k in "ohlc":
            one[f"{k}_bp"] = ((both[f"{k}_m"] / both[f"{k}_d"] - 1.0) * 1e4).round(3)
        one["v_ratio"] = (both["v_m"] / both["v_d"]).round(4)
        one["other_basis"] = (one["c_bp"].abs() > SCALE_TOL * 1e4)
        days.append(one)
        ok = one[~one["other_basis"]]
        row = {"symbol": key, "sessions": len(one), "only_in_minutes": int((j["_merge"] == "left_only").sum()),
               "only_in_daily": int((j["_merge"] == "right_only").sum()), "other_basis": int(one["other_basis"].sum())}
        for k in "ohlc":
            a = ok[f"{k}_bp"].abs()
            row.update({f"{k}_differs_pct": round(float((a > DIFF_BP).mean() * 100), 2) if len(a) else np.nan,
                        f"{k}_median_bp": round(float(a.median()), 3) if len(a) else np.nan,
                        f"{k}_p95_bp": round(float(a.quantile(0.95)), 3) if len(a) else np.nan,
                        f"{k}_max_bp": round(float(a.max()), 3) if len(a) else np.nan})
        r = ok["v_ratio"]
        row.update({"v_ratio_median": round(float(r.median()), 4) if len(r) else np.nan,
                    "v_ratio_p05": round(float(r.quantile(0.05)), 4) if len(r) else np.nan,
                    "v_ratio_p95": round(float(r.quantile(0.95)), 4) if len(r) else np.nan})
        rows.append(row)
    return (pd.concat(days, ignore_index=True) if days else pd.DataFrame()), pd.DataFrame(rows)


# ---------------------------------------------------------------- table against Alpaca's adjusted bars


def check_adjustment(store: DailyStore, keys: Sequence[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(one verdict per key, every step where the table and Alpaca's adjusted bars differ).

    verdict  ok            every step of Alpaca's factor is in the table, within tolerance
             mismatch      at least one step differs (see the steps frame), or the split basis differs
             noisy         Alpaca's adjusted prices are too coarse here (rounding) to verify steps of 0.5%
             no_reference  the fetch stored no adjusted series for this key
    step class  spin_off           the table lists a spin-off on this date (information only, not applied)
                dropped_duplicate  the table has a row there that was flagged as a repeat and left out
                factor_differs     the table has an action there and its size differs beyond tolerance
                missing_in_table   Alpaca adjusts on this date, the table has no action
    Each step row gives the factor each side applies to the prices before that date (1.0: none).
    """
    keys = list(keys) if keys is not None else store.keys()
    acts = _columns(store.root / "corporate_actions.parquet", ["key", "type", "ex_date", "dup"])
    info: dict[str, list[tuple]] = {}
    for key, kind, ex, dup in zip(acts["key"], acts["type"], acts["ex_date"], acts["dup"], strict=True):
        info.setdefault(key, []).append((ex, kind, bool(dup)))
    verdicts, steps = [], []
    for key in keys:
        ref = store.reference(key)
        if ref is None:
            verdicts.append({"key": key, "verdict": "no_reference"})
            continue
        p = store.panel([key], minute_share=None)
        has = np.isfinite(p.c[:, 0])
        dates = p.dates[has]
        c, v = p.c[has, 0], p.v[has, 0]
        ref_day = ref["date"].to_numpy().astype("datetime64[D]")
        pos = np.searchsorted(dates, ref_day)
        hit = (pos < len(dates)) & (dates[np.minimum(pos, len(dates) - 1)] == ref_day)
        rc, rv = np.full(len(dates), np.nan), np.full(len(dates), np.nan)
        rc[pos[hit]], rv[pos[hit]] = ref["c"].to_numpy()[hit], ref["v"].to_numpy()[hit]
        both = np.isfinite(rc) & (rc > 0) & (c > 0)
        if both.sum() < 2:
            verdicts.append({"key": key, "verdict": "no_reference"})
            continue
        split, div = p.split[has, 0], p.div[has, 0]
        t_split, t_total, _ = table_factors(c[:, None], split[:, None], div[:, None])
        idx = np.flatnonzero(both)
        alpaca, table = np.log(rc[idx] / c[idx]), np.log(t_total[idx, 0])
        d = np.diff(alpaca) - np.diff(table)  # d[n]: what Alpaca applies between bar idx[n] and idx[n + 1], minus us
        acted = np.diff(table) != 0  # the table has an action on that step
        # the dividend formula may differ a little, in proportion to the dividend; a split leaves no such room
        cash_size = np.abs(np.diff(np.log(t_total[idx, 0] / t_split[idx, 0])))
        noise = float(1.4826 * np.median(np.abs(d[~acted] - np.median(d[~acted])))) if (~acted).any() else 0.0
        # Alpaca's adjusted prices may be rounded (to the cent, say): that moves each ratio by at most half a step
        scaled = rc[idx] != c[idx]
        rounding = np.where(scaled, 0.5 * _grid(rc[idx][scaled]) / rc[idx], 0.0)
        tol = (np.maximum(max(ABS_TOL, 0.0 if rounding.any() else NOISE_K * noise), REL_TOL * cash_size)
               + rounding[:-1] + rounding[1:])
        big = np.flatnonzero(np.abs(d) > tol)
        landing = _landings(dates, info.get(key, []))
        for n in big:
            kinds = {k for r in range(idx[n] + 1, idx[n + 1] + 1) for k in landing.get(r, ())}
            cls = ("spin_off" if "spin_off" in kinds else "dropped_duplicate" if "dup" in kinds
                   else "factor_differs" if acted[n] else "missing_in_table")
            steps.append({"key": key, "date": str(dates[idx[n + 1]]), "class": cls,
                          "alpaca_factor": round(float(np.exp(-np.diff(alpaca)[n])), 6),
                          "table_factor": round(float(np.exp(-np.diff(table)[n])), 6)})
        with np.errstate(divide="ignore", invalid="ignore"):
            sr = np.where((rv > 0) & (v > 0), v / rv / t_split[:, 0], np.nan)
        split_off = int((np.abs(sr - 1.0) > SPLIT_TOL).sum())
        verdict = ("mismatch" if len(big) or split_off else "noisy" if float(np.median(tol)) > NOISY else "ok")
        verdicts.append({"key": key, "verdict": verdict, "sessions": int(both.sum()), "steps": int(len(big)),
                         "worst_step_pct": round(float(np.abs(d[big]).max() * 100), 3) if len(big) else 0.0,
                         "split_basis_days": split_off, "noise_pct": round(noise * 100, 4),
                         "first_step": str(dates[idx[big[0] + 1]]) if len(big) else ""})
    return (pd.DataFrame(verdicts, columns=VERDICT_COLUMNS), pd.DataFrame(steps, columns=STEP_COLUMNS))


def _grid(x: np.ndarray) -> float:
    """The price step a series is rounded to (1, 0.1, 0.01, ...); 0.0 when it carries more than six decimals."""
    for decimals in range(7):
        y = x * 10.0 ** decimals
        if np.all(np.abs(y - np.round(y)) < 1e-4):
            return 10.0 ** -decimals
    return 0.0


def _landings(dates: np.ndarray, rows: Sequence[tuple]) -> dict[int, set[str]]:
    """Bar row each informational row lands on (first bar on or after its date): {row: {"spin_off", "dup"}}."""
    out: dict[int, set[str]] = {}
    for ex, kind, dup in rows:
        tag = "spin_off" if kind == "spin_off" else "dup" if dup and kind in ADJUSTING_TYPES else None
        k = int(np.searchsorted(dates, np.datetime64(ex, "D")))
        if tag and k < len(dates):
            out.setdefault(k, set()).add(tag)
    return out


# ---------------------------------------------------------------- the whole check


def pick_sample(store: DailyStore, data_dir: str | Path, sample: Sequence[str] = SAMPLE, extra: int = 10,
                seed: int = 124) -> list[str]:
    """The fixed sample plus `extra` other keys drawn with a fixed seed, kept where minute files exist."""
    stored = store.keys()
    have = [k for k in stored if "@" not in k and (Path(data_dir) / "bars_1m" / f"symbol={k}").is_dir()]
    picked = [k for k in sample if k in have]
    rest = [k for k in have if k not in picked]
    random.Random(seed).shuffle(rest)
    return picked + sorted(rest[:extra])


def _save(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)


def run_check(data_dir: str | Path, sample: Sequence[str] | None = None, extra: int = 10, seed: int = 124,
              log: Callable[[str], None] = print) -> dict:
    """Run both checks, write the files under <data>/daily/check and a summary in plain words."""
    data_dir = Path(data_dir)
    store = DailyStore(data_dir / "daily")
    out = store.root / "check"
    lines: list[str] = []

    def say(s: str) -> None:
        lines.append(s)
        log(s)

    if not (store.root / "corporate_actions.parquet").exists():
        say("No corporate-actions table yet, and both checks need it: run ./scripts/fetch_daily.sh until the "
            "corporate actions are fetched.")
        return {}
    keys = list(sample) if sample else pick_sample(store, data_dir, extra=extra, seed=seed)
    days, per_symbol = compare_minutes(store, data_dir, keys)
    _save(days, out / "minute_vs_daily.csv")
    _save(per_symbol, out / "minute_vs_daily_summary.csv")
    res: dict = {"minute_symbols": len(per_symbol), "minute_sessions": len(days)}
    if len(days):
        ok = days[~days["other_basis"]]
        say(f"Minute history against daily bars: {len(per_symbol)} symbols, {len(ok)} sessions compared "
            f"({', '.join(per_symbol['symbol'])}).")
        for k, name in (("o", "open"), ("c", "close"), ("h", "high"), ("l", "low")):
            a = ok[f"{k}_bp"].abs()
            res[f"{k}_differs_pct"] = round(float((a > DIFF_BP).mean() * 100), 2)
            say(f"  {name:<5} differs on {res[f'{k}_differs_pct']:.1f}% of sessions: median {a.median():.2f} bp, "
                f"95th percentile {a.quantile(0.95):.2f} bp, largest {a.max():.1f} bp")
        res["v_ratio_median"] = round(float(ok["v_ratio"].median()), 4)
        say(f"  volume: the 09:30-15:59 minutes hold {res['v_ratio_median'] * 100:.1f}% of the daily volume "
            f"(median; 5th-95th percentile {ok['v_ratio'].quantile(0.05) * 100:.1f}%-"
            f"{ok['v_ratio'].quantile(0.95) * 100:.1f}%)")
        res["other_basis"] = int(days["other_basis"].sum())
        if res["other_basis"]:
            bad = days[days["other_basis"]].groupby("symbol")["date"].agg(["min", "max", "count"])
            say(f"  {res['other_basis']} sessions are on another split basis (close more than 5% apart): "
                + "; ".join(f"{s} {r['count']} sessions {r['min']}..{r['max']}" for s, r in bad.iterrows()))
        missing = int(per_symbol["only_in_minutes"].sum()), int(per_symbol["only_in_daily"].sum())
        res["only_in_minutes"], res["only_in_daily"] = missing
        say(f"  sessions only in the minute history: {missing[0]}; only in the daily bars: {missing[1]}")
    else:
        say("Minute history against daily bars: nothing to compare (no minute files for the sample under "
            f"{data_dir / 'bars_1m'}).")

    verdicts, steps = check_adjustment(store)
    _save(verdicts, out / "adjustment.csv")
    _save(steps, out / "adjustment_steps.csv")
    counts = verdicts["verdict"].value_counts().to_dict() if len(verdicts) else {}
    res["adjustment"] = counts
    say(f"Corporate-actions table against Alpaca's adjusted bars: {len(verdicts)} keys: "
        + ", ".join(f"{n} {v}" for v, n in sorted(counts.items())) + ".")
    if len(steps):
        by_class = steps["class"].value_counts().to_dict()
        res["adjustment_steps"] = by_class
        say("  steps that differ: " + ", ".join(f"{n} {c}" for c, n in sorted(by_class.items()))
            + f" (in {steps['key'].nunique()} keys; adjustment_steps.csv). "
            "DailyPanel.adjusted(source='auto') uses Alpaca's factor for these keys.")
    unsafe = verdicts[verdicts["split_basis_days"] > 0]["key"].tolist() if len(verdicts) else []
    res["split_unknown"] = unsafe
    if unsafe:
        say(f"  WARNING: for {len(unsafe)} keys the table and Alpaca's bars disagree about a split "
            f"({', '.join(unsafe[:20])}" + (", ..." if len(unsafe) > 20 else "")
            + "): their share counts cannot be carried through that date.")
    res.update(_benchmark_lines(store, say))
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    return res


def _benchmark_lines(store: DailyStore, say: Callable[[str], None]) -> dict:
    """Two facts the benchmark depends on: SPY's dividends per year, and where XLC starts."""
    res: dict = {}
    keys = set(store.keys())
    if "SPY" in keys:
        tr = store.total_return("SPY")
        paid = tr[tr["dividend"] > 0]
        per_year = paid.groupby(paid["date"].dt.year).size()
        first = tr["date"].iloc[0]
        years = range(first.year + (first.day_of_year > 15), tr["date"].iloc[-1].year)  # full calendar years only
        thin = [y for y in years if per_year.get(y, 0) < 4]
        growth = float(tr["index"].iloc[-1])
        price = float((tr["close"] * tr["split"] / tr["close"].shift(1)).fillna(1.0).prod())
        n_years = max((tr["date"].iloc[-1] - tr["date"].iloc[0]).days / 365.25, 1e-9)
        res["spy_dividends"] = int(len(paid))
        res["spy_dividend_points_per_year"] = round(float((growth ** (1 / n_years) - price ** (1 / n_years)) * 100), 2)
        say(f"SPY total return {tr['date'].iloc[0]:%Y-%m-%d}..{tr['date'].iloc[-1]:%Y-%m-%d}: {len(paid)} dividends; "
            f"price x{price:.3f}, with dividends x{growth:.3f} "
            f"(dividends add {res['spy_dividend_points_per_year']:.2f} points a year)."
            + (f" WARNING: fewer than 4 dividends in {', '.join(map(str, thin))}: the table is incomplete there."
               if thin else ""))
        res["spy_thin_years"] = thin
    if "XLC" in keys:
        first = store.meta("XLC").get("first", "")
        res["xlc_first"] = first
        say(f"XLC's first bar is {first}: before it, DailyPanel.spliced_close('XLC', 'SPY') continues it with SPY.")
    return res
