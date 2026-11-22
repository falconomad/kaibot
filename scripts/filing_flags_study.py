#!/usr/bin/env python3
"""The 8-K filing-flags event study (story S101, #375), exactly as pre-registered in
docs/prereg/filing_flags_study.md. Read-only: no order, no setting, no simulator.

    .venv/bin/python scripts/filing_flags_study.py --start 2017-05-01

Reads data/filings/filings_8k.parquet (./scripts/fetch_earnings_dates.sh --all-items, S100), data/daily
(./scripts/fetch_daily.sh) and the committed point-in-time S&P 500. Writes data/reports/filing_flags_study.md and
.json, and one trial row per cell (twelve) in data/trials.jsonl; a cell that already has a row is reported again
and never logged twice. It refuses to run if the pre-registration is not committed (kaibot.evaluation.prereg).
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kaibot.evaluation import holdout  # noqa: E402
from kaibot.evaluation import prereg as prereg_mod  # noqa: E402
from kaibot.evaluation import trials as trials_mod  # noqa: E402
from kaibot.features import filing_flag as ff  # noqa: E402

STUDY = "filing_flags_study"
WINDOWS = {"w5": 5, "w20": 20}
MIN_OBS = 30
CLEAR_T = 3.5
YEARS = tuple(range(2017, 2027))
SAME_SIGN_YEARS = 6


# ---------------------------------------------------------------- pure parts
def events(table: pd.DataFrame, cfg: dict, start: date, member) -> tuple[pd.DataFrame, dict]:
    """One row per (group, company, month of day 0): the first 8-K of that month in the group. `member(company,
    day)` -> bool (point-in-time S&P 500). Returns (events, counts of what was left out)."""
    t = table[table["form"].astype(str) == "8-K"].copy()
    left = {"amendments": int(len(table) - len(t))}
    t["day0"] = pd.to_datetime(t["session"]).dt.date
    t = t[t["day0"].notna() & (t["day0"] >= start)]
    t["accepted_ts"] = pd.to_datetime(t["accepted_ts"], utc=True)
    t["items_t"] = t["items"].map(ff.split_items)
    excl = set(cfg.get("exclude_items") or ())
    with_202 = t["items_t"].map(lambda its: bool(set(its) & excl))
    left["with_2_02"] = int(with_202.sum())
    t = t[~with_202]
    rows = []
    for g, members in cfg["groups"].items():
        sel = t[t["items_t"].map(lambda its, m=set(members): bool(set(its) & m))]
        sel = sel.sort_values(["company", "accepted_ts"])
        keep = pd.Series([bool(member(c, d)) for c, d in zip(sel["company"], sel["day0"], strict=True)],
                         index=sel.index, dtype=bool)
        sel = sel.loc[keep]
        sel = sel.assign(month=[d.replace(day=1) for d in sel["day0"]])
        first = sel.drop_duplicates(["company", "month"], keep="first")
        left[f"{g}_not_member_or_repeat"] = int(len(t[t["items_t"].map(lambda its, m=set(members): bool(set(its) & m))])
                                                - len(first))
        rows += [{"group": g, "company": c, "symbol": s, "day0": d, "month": m}
                 for c, s, d, m in zip(first["company"], first["symbol"], first["day0"], first["month"], strict=True)]
    return pd.DataFrame(rows, columns=["group", "company", "symbol", "day0", "month"]), left


def window_end(c: np.ndarray, i0: int, h: int) -> int | None:
    """The row of the last close inside (day 0, day +h]; None if day 0 has no close or none follows it."""
    if i0 < 0 or i0 >= len(c) or not np.isfinite(c[i0]):
        return None
    fin = np.nonzero(np.isfinite(c[i0 + 1:i0 + h + 1]))[0]
    return None if len(fin) == 0 else int(i0 + 1 + fin[-1])


def window_return(c: np.ndarray, i0: int, h: int, end: int | None = None) -> float | None:
    """Close of day 0 -> close of day +h (or the last close inside the window). None if day 0 or after is empty.
    With `end`, the return to that row instead (SPY over the same sessions as the stock, as pre-registered)."""
    e = window_end(c, i0, h) if end is None else end
    if e is None or i0 < 0 or i0 >= len(c) or e >= len(c) or not (np.isfinite(c[i0]) and np.isfinite(c[e])):
        return None
    return float(c[e] / c[i0] - 1.0)


def abnormal_return(stock: np.ndarray, spy: np.ndarray, i0: int, h: int) -> float | None:
    """The stock's window return minus SPY's over the same sessions (the stock's last close inside the window)."""
    e = window_end(stock, i0, h)
    if e is None:
        return None
    rs, rm = window_return(stock, i0, h, e), window_return(spy, i0, h, e)
    return None if rs is None or rm is None else rs - rm


def t_stat(x: np.ndarray) -> float | None:
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return None
    sd = float(np.std(x, ddof=1))
    return float(np.mean(x) / (sd / math.sqrt(len(x)))) if sd > 0 else None


def judge(obs: pd.DataFrame) -> dict:
    """One cell: obs has columns ar (abnormal return), month (date), year (int)."""
    x = obs["ar"].to_numpy(dtype=float)
    n = int(np.isfinite(x).sum())
    mean = float(np.nanmean(x)) if n else None
    t_obs = t_stat(x)
    monthly = obs.groupby("month")["ar"].mean().to_numpy(dtype=float)
    t_month = t_stat(monthly)
    ts = [v for v in (t_obs, t_month) if v is not None]
    t = min(ts, key=abs) if len(ts) == 2 else None
    by_year = obs.groupby("year")["ar"].mean().to_dict()
    sign = (mean > 0) - (mean < 0) if mean is not None else 0
    same = sum(1 for y in YEARS if y in by_year and sign != 0 and ((by_year[y] > 0) - (by_year[y] < 0)) == sign)
    passes = bool(n >= MIN_OBS and t is not None and abs(t) >= CLEAR_T and same >= SAME_SIGN_YEARS)
    return {"n": n, "mean": mean, "t_obs": t_obs, "t_month": t_month, "t": t, "months": int(len(monthly)),
            "same_sign_years": same, "by_year": {int(k): float(v) for k, v in sorted(by_year.items())},
            "counts": passes, "negative": bool(mean is not None and mean < 0)}


def verdict(cells: dict[tuple[str, str], dict]) -> dict[str, int]:
    """Groups that count -> N sessions (20 if w20 counts, else 5). Only negative cells can count."""
    out = {}
    for g in sorted({g for g, _ in cells}):
        ok = [w for w in ("w20", "w5") if cells.get((g, w), {}).get("counts") and cells[(g, w)]["negative"]]
        if ok:
            out[g] = WINDOWS[ok[0]]
    return out


# ---------------------------------------------------------------- I/O
def code_sha() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _f(x, nd=4) -> str:
    return "-" if x is None else f"{x:.{nd}f}"


def markdown(rep: dict) -> str:
    L = [f"# 8-K filing flags: the pre-registered study ({rep['run_at'][:10]})", "",
         f"- Pre-registration `docs/prereg/{STUDY}.md`, sha256 `{rep['prereg_sha256']}`; code `{rep['code_sha']}`.",
         f"- Day 0 from {rep['window'][0]} to {rep['window'][1]}; filings file newest acceptance "
         f"{rep['file_last_accepted']}.",
         f"- Left out: {json.dumps(rep['left_out'])}.", "",
         "| Group | Window | Obs | Mean abnormal return | t (obs) | t (months) | t used | Same sign (of 10 years) "
         "| Counts | Trial row |", "|---|---|---|---|---|---|---|---|---|---|"]
    for c in rep["cells"]:
        s = c["stats"]
        L.append(f"| `{c['group']}` | {c['window']} | {s['n']} | {_f(s['mean'])} | {_f(s['t_obs'], 2)} | "
                 f"{_f(s['t_month'], 2)} | {_f(s['t'], 2)} | {s['same_sign_years']} | "
                 f"{'**yes**' if s['counts'] else 'no'} | {c['trial']} |")
    v = rep["counted_groups"]
    L += ["", "Groups that count (negative only; the flag's sessions): "
          + (", ".join(f"`{g}` ({n})" for g, n in v.items()) if v else "**none: the idea is dropped**") + ".",
          "", "Bar: at least 30 observations, |t| >= 3.5 on the smaller of the two t, the same sign in at least 6 of "
              "the years 2017-2026. Nothing here changes an order."]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2017-05-01")
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    holdout.add_argument(ap)
    a = ap.parse_args(argv)
    # the study reads the daily store to its newest bar (it has no --end): that is inside the hold-out
    holdout.enforce(script=__file__, end=None, rule=STUDY, opening=a.open_holdout, data=a.data)
    data, start = Path(a.data), date.fromisoformat(a.start)
    sha = prereg_mod.require(STUDY, ROOT)
    fpath = data / "filings" / "filings_8k.parquet"
    if not fpath.exists():
        sys.exit(f"no {fpath}: run ./scripts/fetch_earnings_dates.sh --all-items first (docs/ops/filings_8k.md)")
    from kaibot.ingest.daily_store import DailyStore
    from kaibot.ingest.sp500_pit import Membership

    table = pd.read_parquet(fpath)
    cfg = ff.load_config()
    ms = Membership.load()
    members_on = functools.lru_cache(maxsize=None)(ms.companies_on)
    ev, left = events(table, cfg, start, lambda c, d: c in members_on(d))
    store = DailyStore(data / "daily")
    keys = store.keys()
    km = ms.key_map(keys)
    names = sorted({keys[j] for j in km.values()} | {"SPY"})
    panel = store.panel(names, (start - timedelta(days=10)).isoformat(), None).adjusted("split")
    col = {s: j for j, s in enumerate(panel.symbols)}
    comp_col = {c: col[keys[j]] for c, j in km.items() if keys[j] in col}
    days = [pd.Timestamp(d).date() for d in panel.dates]
    row = {d: i for i, d in enumerate(days)}
    spy = panel.c[:, col["SPY"]]
    last_full = days[-1 - max(WINDOWS.values())] if len(days) > max(WINDOWS.values()) else days[0]
    ev = ev[ev["day0"] <= last_full]
    cells, out_cells = {}, []
    trials_file = data / "trials.jsonl"
    done = {r.get("trial_id") for r in trials_mod.read_jsonl(trials_file)[0] if r.get("strategy") == STUDY}
    run_at = datetime.now(UTC).isoformat(timespec="seconds")
    dropped = 0
    for g in cfg["groups"]:
        for w, h in WINDOWS.items():
            obs = []
            for _, e in ev[ev["group"] == g].iterrows():
                i0, j = row.get(e["day0"]), comp_col.get(e["company"])
                if i0 is None or j is None:
                    dropped += 1
                    continue
                ar = abnormal_return(panel.c[:, j], spy, i0, h)
                if ar is None:
                    dropped += 1
                    continue
                obs.append({"ar": ar, "month": e["month"], "year": e["day0"].year})
            frame = pd.DataFrame(obs, columns=["ar", "month", "year"])
            st = judge(frame) if len(frame) else judge(pd.DataFrame({"ar": [], "month": [], "year": []}))
            cells[(g, w)] = st
            tid = f"{STUDY}_{g}_{w}"
            rec = {"group": g, "window": w, "stats": st}
            if tid in done:
                rec["trial"] = "already logged: not logged again"
            else:
                trials_mod.append(trials_file, {
                    "ts": run_at, "trial_id": tid, "strategy": STUDY, "variant": "event_study",
                    "label": f"8-K {g} {w}", "status": "study", "n_trials": 1, "prereg_sha256": sha,
                    "code_sha": code_sha(), "params": {"group": g, "items": cfg["groups"][g], "window": h},
                    "window": [str(start), str(last_full)],
                    "metrics": {k: v for k, v in st.items() if k != "by_year"}})
                rec["trial"] = "logged"
            out_cells.append(rec)
    left["observations_dropped_no_close_or_no_store_key_per_window"] = dropped
    acc = pd.to_datetime(table["accepted_ts"], utc=True)
    rep = {"run_at": run_at, "prereg_sha256": sha, "code_sha": code_sha(), "window": [str(start), str(last_full)],
           "file_last_accepted": str(acc.max()) if len(acc) else None, "left_out": left, "cells": out_cells,
           "counted_groups": verdict(cells)}
    out = data / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / "filing_flags_study.json").write_text(json.dumps(rep, indent=1, default=str))
    (out / "filing_flags_study.md").write_text(markdown(rep))
    print(markdown(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
