#!/usr/bin/env python3
"""Backfill and measure the market-event alarm (story S103, #377), as pre-registered in
docs/prereg/market_alarm_study.md. Read-only: it places no order and changes no setting.

    .venv/bin/python scripts/market_alarm_report.py --start 2016-01-01             # the study (once)
    .venv/bin/python scripts/market_alarm_report.py --start 2016-01-01 --flags-only  # flags and shares only
    .venv/bin/python scripts/market_alarm_report.py --rankings-only                 # refresh data/alarm/top10.csv

Steps, in the pre-registered order:

1. the ten largest companies per month from data/daily (raw close x volume, 60 sessions, point-in-time S&P 500
   members, one ticker per company) -> data/alarm/top10.csv (the live alarm reads it too);
2. the flags of every session as of 09:00 ET (`kaibot.features.market_alarm.backfill`) -> data/alarm/backfill.csv;
3. the share of sessions per flag and tier; if `major` covers more than 10%, the megacap threshold of `major` is
   raised until it does not, BEFORE anything below is read;
4. the check of 19 Nov 2026 (ANT: `mega_listing`, tier `major`);
5. (not with --flags-only) the eight comparisons: M1 = |SPY close-to-close return|, M2 = the daily return of the
   newest engine + SimBroker point-in-time `trend_momentum` run in data/reports; OLS on a flag dummy, Newey-West
   (5 lags) t; one trial row each in data/trials.jsonl. A comparison that already has a row is reported again
   but never logged twice;
6. `mega_listing` as a list of cases with SPY's move.

Writes data/reports/market_alarm_backfill.md (and .json). Needs data/daily (./scripts/fetch_daily.sh) and
data/earnings/announcements.parquet (./scripts/fetch_earnings_dates.sh); without them the flags that need them
are unknown and their comparisons are not run.
"""

from __future__ import annotations

import argparse
import json
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
from kaibot.features import market_alarm as ma  # noqa: E402
from kaibot.ingest import nyse_sessions as nyse  # noqa: E402

STUDY = "market_alarm_study"
NW_LAGS = 5
CLEAR_T = 3.0
MAJOR_MAX_SHARE = 0.10
RANK_SESSIONS, RANK_MIN = 60, 40
CHECK_DAY = date(2026, 11, 19)


# ---------------------------------------------------------------- statistics
def newey_west_diff(y: np.ndarray, flag: np.ndarray, lags: int = NW_LAGS) -> dict:
    """OLS y = a + b * flag; b with a Newey-West (Bartlett) standard error. NaN rows of y are dropped."""
    ok = np.isfinite(y)
    y, f = y[ok].astype(float), flag[ok].astype(float)
    n, k = len(y), int(f.sum())
    out = {"n": int(n), "flagged": k, "mean_flagged": float(y[f == 1].mean()) if k else None,
           "mean_other": float(y[f == 0].mean()) if n - k else None, "b": None, "se": None, "t": None}
    if k < 2 or n - k < 2:
        return out
    x = np.column_stack([np.ones(n), f])
    xtx_inv = np.linalg.inv(x.T @ x)
    beta = xtx_inv @ x.T @ y
    u = y - x @ beta
    xu = x * u[:, None]
    s = xu.T @ xu
    for lag in range(1, min(lags, n - 1) + 1):
        w = 1.0 - lag / (lags + 1.0)
        g = xu[lag:].T @ xu[:-lag]
        s += w * (g + g.T)
    var = xtx_inv @ s @ xtx_inv
    se = float(np.sqrt(var[1, 1]))
    out.update(b=float(beta[1]), se=se, t=float(beta[1] / se) if se > 0 else None)
    return out


# ---------------------------------------------------------------- the ten largest
def rankings_from_panel(dates: np.ndarray, symbols: list[str], close: np.ndarray, volume: np.ndarray,
                        member: np.ndarray, company: dict[int, str], start: date, end: date) -> pd.DataFrame:
    """At the last session of each month: the ten member companies with the highest mean raw dollar volume over
    their last RANK_SESSIONS sessions with a bar (at least RANK_MIN). Pure arithmetic on the arrays given."""
    dv = close * volume
    days = [pd.Timestamp(d).date() for d in dates]
    rows = []
    month_ends = [i for i in range(len(days)) if i + 1 == len(days) or days[i + 1].month != days[i].month]
    for i in month_ends:
        d = days[i]
        nxt = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
        if nxt < start.replace(day=1) or nxt > end:
            continue
        lo = max(0, i - RANK_SESSIONS + 1)
        win = dv[lo:i + 1]
        cnt = np.isfinite(win).sum(axis=0)
        with np.errstate(invalid="ignore"):
            mean = np.where(cnt >= RANK_MIN, np.nansum(win, axis=0) / np.maximum(cnt, 1), np.nan)
        best: dict[str, tuple[float, int]] = {}
        for j, c in company.items():
            if member[i, j] and np.isfinite(mean[j]) and (c not in best or mean[j] > best[c][0]):
                best[c] = (float(mean[j]), j)
        top = sorted(best.items(), key=lambda kv: (-kv[1][0], kv[0]))[:10]
        avail = nyse.close_utc(d) + ma.TOP10_LAG
        for r, (c, (v, j)) in enumerate(top, start=1):
            rows.append({"month": nxt.isoformat(), "available_ts": avail.isoformat(), "rank": r, "company": c,
                         "symbol": symbols[j].split("@")[0], "ranked_on": d.isoformat(), "dollar_volume_60": v})
    return pd.DataFrame(rows, columns=["month", "available_ts", "rank", "company", "symbol", "ranked_on",
                                       "dollar_volume_60"])


def build_rankings(data: Path, start: date, end: date) -> pd.DataFrame:
    from kaibot.ingest.daily_store import DailyStore
    from kaibot.ingest.sp500_pit import Membership

    store = DailyStore(data / "daily")
    keys = store.keys()
    ms = Membership.load()
    km = ms.key_map(keys)
    cols = sorted(set(km.values()))
    names = [keys[j] for j in cols]
    company = {names.index(keys[j]): c for c, j in km.items()}
    lo = (start.replace(day=1) - timedelta(days=130)).isoformat()
    panel = store.panel(names, lo, end.isoformat())
    member = ms.mask(panel.dates, panel.symbols)
    return rankings_from_panel(panel.dates, panel.symbols, panel.c, panel.v, member, company, start, end)


# ---------------------------------------------------------------- outcomes
def spy_abs_returns(data: Path, start: date, end: date) -> pd.Series:
    from kaibot.ingest.daily_store import DailyStore

    panel = DailyStore(data / "daily").panel(["SPY"], (start - timedelta(days=10)).isoformat(), end.isoformat())
    adj = panel.adjusted("split")
    c = pd.Series(adj.c[:, 0], index=[pd.Timestamp(d).date() for d in adj.dates])
    return c.pct_change()


def newest_engine_run(data: Path) -> Path | None:
    """The base account of the newest engine + SimBroker point-in-time run of trend_momentum (pre-registered)."""
    reports = data / "reports"
    runs = sorted((p for p in reports.glob("engine_*_pit_*") if p.is_dir()), key=lambda p: p.name)
    for run in reversed(runs):
        js = reports / f"{run.name}.json"
        try:
            rep = json.loads(js.read_text())
        except (OSError, ValueError):
            continue
        if rep.get("strategy") not in (None, "trend_momentum") and "trend_momentum" not in run.name:
            continue
        base = rep.get("base_label")
        if base:
            f = run / f"daily_{base.replace('|', '_').replace('~', '-')}.csv"
            if f.exists():
                return f
    return None


def account_returns(path: Path) -> pd.Series:
    t = pd.read_csv(path)
    if "ret" in t.columns:
        r = t["ret"]
    elif {"pnl", "start"} <= set(t.columns):
        r = t["pnl"] / t["start"]
    else:
        r = t["equity"].pct_change()
    return pd.Series(r.to_numpy(dtype=float), index=[date.fromisoformat(str(d)[:10]) for d in t["date"]])


# ---------------------------------------------------------------- the report
def shares(rows: list[dict]) -> dict:
    n = len(rows)
    out = {"sessions": n, "flags": {}, "tiers": {}}
    for f in ma.FLAGS:
        vals = [r["flags"][f] for r in rows]
        out["flags"][f] = {"flagged": sum(v is True for v in vals), "unknown": sum(v is None for v in vals),
                           "share": sum(v is True for v in vals) / n if n else None}
    for t in ("major", "notice", "none"):
        k = sum(r["tier"] == t for r in rows)
        out["tiers"][t] = {"sessions": k, "share": k / n if n else None}
    return out


def tighten(sessions: list[date], inputs: ma.Inputs) -> tuple[int, list[dict], list[dict]]:
    """(threshold, rows, steps): raise the megacap part of `major` until major <= 10% (pre-registered)."""
    thr, steps = ma.MEGACAP_MAJOR, []
    rows = ma.backfill(sessions, inputs, thr)
    while True:
        sh = shares(rows)["tiers"]["major"]["share"] or 0.0
        steps.append({"megacap_major": thr, "major_share": sh})
        if sh <= MAJOR_MAX_SHARE or thr >= 10:
            return thr, rows, steps
        thr += 1
        rows = ma.backfill(sessions, inputs, thr)


def logged_ids(path: Path) -> set[str]:
    rows, _ = trials_mod.read_jsonl(path)
    return {r.get("trial_id") for r in rows if r.get("strategy") == STUDY}


def code_sha() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _pct(x) -> str:
    return "-" if x is None else f"{100 * x:.1f}%"


def _num(x, nd=3) -> str:
    return "-" if x is None else f"{x:.{nd}f}"


def markdown(rep: dict) -> str:
    L = [f"# Market-event alarm: backfill and the eight comparisons ({rep['run_at'][:10]})", "",
         f"- Pre-registration `docs/prereg/{STUDY}.md`, sha256 `{rep['prereg_sha256']}`; code `{rep['code_sha']}`.",
         f"- Sessions {rep['window'][0]} to {rep['window'][1]}: {rep['shares']['sessions']}. "
         f"Missing inputs: {', '.join(rep['missing']) or 'none'}.", "",
         "## Share of sessions flagged", "", "| Flag | Sessions | Share | Unknown |", "|---|---|---|---|"]
    for f, v in rep["shares"]["flags"].items():
        L.append(f"| `{f}` | {v['flagged']} | {_pct(v['share'])} | {v['unknown']} |")
    L += ["", "| Tier | Sessions | Share |", "|---|---|---|"]
    L += [f"| {t} | {v['sessions']} | {_pct(v['share'])} |" for t, v in rep["shares"]["tiers"].items()]
    L += ["", "Tightening of `major` (pre-registered, before any comparison): " + "; ".join(
        f"megacaps >= {s['megacap_major']}: {_pct(s['major_share'])}" for s in rep["tightening"])
          + f". Used: **{rep['megacap_major']}**.", ""]
    c = rep["check"]
    L += [f"Check {CHECK_DAY}: tier **{c['tier']}**, flags "
          + ", ".join(k for k, v in c["flags"].items() if v) + f"; mega listing {c['details']['mega_listing']}.", ""]
    if rep.get("comparisons") is None:
        L += ["Comparisons not run (`--flags-only`)."]
    else:
        L += ["## The eight comparisons", "", "| Flag | Measure | Sessions | Flagged | Mean flagged | Mean other | "
              "Difference | NW t | Clear (abs t >= 3) | Trial row |", "|---|---|---|---|---|---|---|---|---|---|"]
        for r in rep["comparisons"]:
            if r.get("not_run"):
                L.append(f"| `{r['flag']}` | {r['measure']} | not run: {r['not_run']} | | | | | | | |")
                continue
            s = r["stats"]
            L.append(f"| `{r['flag']}` | {r['measure']} | {s['n']} | {s['flagged']} | {_num(s['mean_flagged'], 5)} | "
                     f"{_num(s['mean_other'], 5)} | {_num(s['b'], 5)} | {_num(s['t'], 2)} | "
                     f"{'yes' if s['t'] is not None and abs(s['t']) >= CLEAR_T else 'no'} | {r['trial']} |")
        L += ["", f"M1 = absolute SPY close-to-close return. M2 = daily return of `{rep.get('m2_file') or '-'}`."]
    L += ["", "## Mega listings (not tested)", "", "| Session | What | SPY close-to-close |", "|---|---|---|"]
    L += [f"| {m['session']} | {m['what']} | {_pct(m['spy'])} |" for m in rep["mega_listings"]] or ["| - | none | |"]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default=None, help="last session (default: the last SPY bar in data/daily, or today)")
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--flags-only", action="store_true", help="flags and shares only; reads no outcome")
    ap.add_argument("--rankings-only", action="store_true", help="refresh data/alarm/top10.csv and stop")
    holdout.add_argument(ap)
    a = ap.parse_args(argv)
    data = Path(a.data)
    start = date.fromisoformat(a.start)
    have_daily = (data / "daily").exists()
    spy = None
    if a.end:
        end = date.fromisoformat(a.end)
    elif have_daily and not a.flags_only:
        spy = spy_abs_returns(data, start, date.today())
        end = max(spy.index)
    else:
        end = date.today() - timedelta(days=1)
    if not (a.flags_only or a.rankings_only):
        # the comparisons read outcomes (SPY's move, the rule's returns): a research read. The flags and the
        # monthly rankings read no outcome, and the live alarm needs the rankings, so those two modes stay open
        holdout.enforce(script=__file__, end=end, rule=STUDY, opening=a.open_holdout, data=data)
    (data / "alarm").mkdir(parents=True, exist_ok=True)
    if have_daily:
        rk = build_rankings(data, start, end + timedelta(days=31))
        rk.to_csv(data / ma.TOP10_FILE, index=False)
        print(f"rankings: {rk['month'].nunique()} months -> {data / ma.TOP10_FILE}")
    else:
        print("no data/daily: no rankings, megacap_report is unknown", file=sys.stderr)
    if a.rankings_only:
        return 0
    sha = prereg_mod.require(STUDY, ROOT)
    inputs = ma.load_inputs(data)
    sessions = nyse.sessions(start, end)
    thr, rows, steps = tighten(sessions, inputs)
    sh = shares(rows)
    pd.DataFrame([{"session": r["session"], **{f: r["flags"][f] for f in ma.FLAGS}, "tier": r["tier"],
                   "n_megacaps": r["n_megacaps"], "megacaps": ",".join(r["details"]["megacaps_reporting"]),
                   "index": ",".join(r["details"]["index"]), "mega_listing": ",".join(r["details"]["mega_listing"])}
                  for r in rows]).to_csv(data / "alarm" / "backfill.csv", index=False)
    check = ma.alarm(CHECK_DAY, ma.as_of_for(CHECK_DAY), inputs, thr)
    run_at = datetime.now(UTC)
    rep = {"run_at": run_at.isoformat(timespec="seconds"), "prereg_sha256": sha, "code_sha": code_sha(),
           "window": [str(sessions[0]), str(sessions[-1])], "missing": list(inputs.missing), "shares": sh,
           "tightening": steps, "megacap_major": thr, "check": check, "comparisons": None, "mega_listings": []}
    if not a.flags_only:
        if spy is None and have_daily:
            spy = spy_abs_returns(data, start, end)
        m2_file = newest_engine_run(data)
        m2 = account_returns(m2_file) if m2_file else None
        rep["m2_file"] = str(m2_file.relative_to(data)) if m2_file else None
        trials_file = data / "trials.jsonl"
        done = logged_ids(trials_file)
        comps = []
        by_day = {date.fromisoformat(r["session"]): r for r in rows}
        for flag in ma.TESTED_FLAGS:
            for measure, series in (("M1", None if spy is None else spy.abs()), ("M2", m2)):
                rec = {"flag": flag, "measure": measure, "trial": "-"}
                if series is None:
                    rec["not_run"] = "no data/daily" if measure == "M1" else "no engine + SimBroker pit run"
                    comps.append(rec)
                    continue
                days = [d for d in series.index if d in by_day and np.isfinite(series[d])]
                flags = [by_day[d]["flags"][flag] for d in days]
                if any(v is None for v in flags):
                    rec["not_run"] = f"{flag} unknown on {sum(v is None for v in flags)} sessions"
                    comps.append(rec)
                    continue
                y = np.array([series[d] for d in days], dtype=float)
                st = newey_west_diff(y, np.array(flags, dtype=float))
                rec["stats"] = st
                tid = f"{STUDY}_{flag}_{measure}"
                if tid in done:
                    rec["trial"] = "already logged: not logged again"
                else:
                    trials_mod.append(trials_file, {
                        "ts": rep["run_at"], "trial_id": tid, "strategy": STUDY, "variant": "event_study",
                        "label": f"market alarm {flag} {measure}", "status": "study", "n_trials": 1,
                        "prereg_sha256": sha, "code_sha": rep["code_sha"],
                        "params": {"flag": flag, "measure": measure, "nw_lags": NW_LAGS, "megacap_major": thr},
                        "window": [str(days[0]), str(days[-1])], "metrics": st})
                    rec["trial"] = "logged"
                comps.append(rec)
        rep["comparisons"] = comps
        for r in rows:
            if r["flags"]["mega_listing"]:
                d = date.fromisoformat(r["session"])
                rep["mega_listings"].append({"session": r["session"], "what": ", ".join(r["details"]["mega_listing"]),
                                             "spy": None if spy is None or d not in spy.index else float(spy[d])})
    out = data / "reports"
    out.mkdir(parents=True, exist_ok=True)
    name = "market_alarm_flags" if a.flags_only else "market_alarm_backfill"
    (out / f"{name}.json").write_text(json.dumps(rep, indent=1, default=str))
    (out / f"{name}.md").write_text(markdown(rep))
    print(markdown(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
