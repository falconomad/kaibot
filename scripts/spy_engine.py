"""Gate B go/no-go run of the SPY noise-band rule through the ENGINE with SimBroker + SimClock (issue #202; decision
15 sections 5, 7 and 10; docs/prereg/spy_noise_band.md, unchanged). Research only: reads data/, never touches a
broker account, writes only under data/reports and data/trials.

python scripts/spy_engine.py --end 2026-11-06 [--start 2016-01-01] [--test-start 2017-05-01] [--level balanced]
    [--control-runs 500] [--data data]
python scripts/spy_engine.py --has --end ...       exit 0 if a finished report exists for the same window and runs
python scripts/spy_engine.py --summary             the one page again, written to data/reports/experiments_spy_engine.md

What runs. The frozen rule (`strategies.noise_band`, the prereg's construction on the harness's own input functions)
is handed to one unchanged `Engine` per run, built by `engine.runner.simulation` on `SimBroker` and `SimClock` over
SPY's stored minute bars (data/bars_1m/symbol=SPY), with the live engine's macro calendar (event blackouts), the VIX
cache (data/inputs/fred_vixcls.json: the size halving above 25; without it VIX is unknown and nothing is halved),
the level's risk policy and the local journal in a temporary file. Every decision, order, fill, halt and size comes
from `Engine.tick`, the risk gate and the order path, minute by minute, as on paper. Runs, each ONE continuous
$100k account over the test window (2017-05-01 to --end):

- `spy_noise_band` on cost model v2 (the go/no-go cost of decision 15) and at the prereg's 0.5 and 1 bp a side;
- the five pre-declared neighbours (prereg section 3) on cost model v2 (Gate B mark 6) and lb10 / lb20 / lb30 at
  0.5 bp (prereg mark 5).

What is judged.
- Decision 15 Gate B (`evaluation.gate_v2.evaluate_gate_b`, config/gate_v2.yaml): on the v2 run's daily
  mark-to-market returns against SPY total return (daily store; price-only SPY when it is missing, which cannot
  pass). Mark 3 is the random-timing control of section 10 for SPY-only rules (`replay.spy_noise_band
  .random_timing_control`: the engine's own round trips, their holding durations, entries at the rule's check
  times, >= 500 seeded runs), priced at the engine run's measured all-in cost per side; the lower of the engine
  run's percentile and that of its trips replayed through the control counts. Mark 4 charges the run's own daily
  costs a second time. Mark 5: SPY is one instrument, there is no stock list to be survivorship-biased, so the run
  itself is the point-in-time run (marks 1-4 must hold on it). Mark 6: each neighbour's v2 run passes mark 1.
- The pre-registration's marks 1-5 (and amendment 1's mark 3) as written, at 0.5 bp a side (mark 2 at 1 bp), from
  the engine's round trips, and the same marks once more on cost model v2.

Size. The risk gate sizes every entry; through `HARD_CEILINGS` no position can exceed 20% of equity (15% at
Balanced), so the engine cannot hold the prereg's "whole account in SPY". Per-trip numbers (bp, t) do not depend on
size and compare directly with the harness; the account's CAGR and drawdown are those of the account the engine
would actually run (about 15% invested when long) and are judged as such. The report shows both, side by side with
the harness's numbers, and reconciles the round trips one by one (which harness trips the engine did not make, and
why: event blackout, the pause after losses, a stop).

Runtime: the engine ticks every minute of every session (about 940,000 ticks a run). Measured 13 Nov 2026 on one
shared cloud CPU on synthetic SPY bars: about 1.0 s of CPU per session, so about 40 minutes per engine run and 7 to 8
hours for the 11 runs one after another. `--jobs` (default half the CPUs) runs them side by side, one process each
(about 0.5 GB of memory and up to 0.5 GB of temporary journal per process): on a Mac with 8 to 10 cores, expect
roughly 1 to 1.5 hours in all (an estimate from that measurement, not measured on the Mac), plus about two minutes
for the two random-timing controls. Ctrl-C loses the run in progress; run it again (nothing is half-written).

Trials: every run is a report of a configuration the harness has already counted (n_trials 0, decision 15
section 10); one row each in data/trials.jsonl with the pre-registration's and config/gate_v2.yaml's sha256.
Writes data/reports/spy_engine_<level>_<ts>.json (+ trades and daily CSVs in the folder of the same name).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from kaibot.engine.events import load_calendar  # noqa: E402
from kaibot.engine.journal import Journal, JournalStore  # noqa: E402
from kaibot.engine.runner import run_sessions, simulation  # noqa: E402
from kaibot.evaluation import gate_v2 as gv  # noqa: E402
from kaibot.evaluation import prereg as prereg_mod  # noqa: E402
from kaibot.evaluation import trials as trials_mod  # noqa: E402
from kaibot.replay import spy_noise_band as nb  # noqa: E402
from kaibot.replay.data import ET, N_MIN, SymbolDays, from_frame  # noqa: E402
from kaibot.replay.sim_costs import adv_table, costs_for  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402
from kaibot.strategies import registry  # noqa: E402

RULE = "spy_noise_band"
SET = "spy_engine"
SUMMARY = "experiments_spy_engine.md"
CALENDAR = ROOT / "config" / "macro_calendar.yaml"
STATUS = ("engine + SimBroker + SimClock (decision 15): the frozen rule as an engine strategy, every order through the "
          "risk gate and SimBroker, cost model v2 for the go/no-go")
# (version, scenario): the go/no-go grid; the version is a key of registry.research_intraday()
BASE_RUNS = (("spy_noise_band", "v2"), ("spy_noise_band", "0.5"), ("spy_noise_band", "1"))
NEIGHBOUR_RUNS = tuple((sp.name, "v2") for sp in nb.NEIGHBOURS) + tuple(
    (f"spy_noise_band~{k}", "0.5") for k in nb.LOOKBACK_NEIGHBOURS)
# The research harness's numbers at 0.5 bp a side as quoted on 13 Nov 2026 (issue #202), shown only when no harness
# report (scripts/spy_noise_band.py) is on this machine.
HARNESS_QUOTED = {"net_bp_per_trip": 2.48, "t_net": 2.12, "cagr_pct": 2.87, "max_drawdown_pct": 5.35,
                  "source": "quoted in issue #202 (harness run of 13 Nov 2026 at 0.5 bp a side)"}


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- data


def load_frame(data: Path, symbol: str, start: str, end: str) -> pd.DataFrame:
    """SPY's stored minute bars (regular hours) in the engine's frame format (ts UTC, o, h, l, c, v, vw)."""
    files = sorted((data / "bars_1m" / f"symbol={symbol}").glob("*.parquet"))
    files = [f for f in files if str(np.datetime64(start, "M")) <= f.stem <= str(np.datetime64(end, "M"))] or files
    if not files:
        raise SystemExit(f"no {symbol} minute bars under {data / 'bars_1m'}: run ./scripts/fetch_history.sh first")
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    et = df["ts"].dt.tz_convert(ET)
    mins = et.dt.hour * 60 + et.dt.minute - (9 * 60 + 30)
    day = et.dt.date
    keep = (mins >= 0) & (mins < N_MIN) & (day >= date.fromisoformat(start)) & (day <= date.fromisoformat(end))
    df = df[keep]
    if "vw" not in df:
        df = df.assign(vw=df["c"])
    df = df[["ts", "o", "h", "l", "c", "v", "vw"]].drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    for k in ("o", "h", "l", "c", "v", "vw"):
        df[k] = df[k].astype(float)
    return df


def calendar_of(sd: SymbolDays) -> dict[str, tuple[str, str]]:
    """{session: (open, close)} in ET: 16:00, or the early close read from the bars (prereg choice 3)."""
    cm = nb.close_minute(sd)
    out = {}
    for d, c in zip(sd.dates, cm, strict=True):
        close = 9 * 60 + 30 + int(c) + 1
        out[str(d)] = ("09:30", f"{close // 60:02d}:{close % 60:02d}")
    return out


def load_vix(data: Path) -> tuple[pd.Series, str]:
    f = data / "inputs" / "fred_vixcls.json"
    if not f.is_file():
        return pd.Series(dtype=float), "no VIX cache (data/inputs/fred_vixcls.json): VIX unknown, no size halving"
    from kaibot.replay.config_replay import load_vix as lv

    return lv(data), f"VIX previous close from {f.relative_to(data.parent) if f.is_relative_to(data.parent) else f}"


def spy_benchmark(data: Path, dates: np.ndarray, sd: SymbolDays, i0: int, i1: int) -> tuple[pd.Series, str, dict]:
    """SPY total return per session from the daily store (`walk_forward_multiday.spy_total_return_from_store`),
    else price-only SPY from the minute bars (which cannot pass Gate B: `final_requires.benchmark`)."""
    idx = pd.DatetimeIndex(pd.to_datetime(dates.astype(str)))
    d, price = nb.spy_buy_and_hold(sd, i0, i1)
    price_s = pd.Series(price, index=pd.DatetimeIndex(pd.to_datetime(d.astype(str))))
    try:
        import walk_forward_multiday as wfm

        tr, note = wfm.spy_total_return_from_store(data, "SPY", dates)
    except Exception as e:  # noqa: BLE001 - a broken store must not stop the run; it is reported
        tr, note = None, f"daily store unreadable: {e}"
    if tr is not None and np.isfinite(tr).all():
        return pd.Series(tr, index=idx), "spy_total_return", {"note": note, "price_only": price_s}
    return price_s.reindex(idx).fillna(0.0), "spy_price_only", {"note": note, "price_only": price_s}


# ---------------------------------------------------------------- one engine run


def engine_run(frame: pd.DataFrame, cal: dict, days: list[str], version: str, scenario: str, *, level: str,
               vix: pd.Series, events: dict, covers, adv: dict, cash: float = 100_000.0,
               progress=None) -> dict:
    """One continuous account of `version` under `scenario` through the engine. -> trips, daily rows, engine
    rows (trades, halts, errors)."""
    strat = registry.research_intraday()[version]
    costs = costs_for(scenario, adv)
    profiles = load_profiles()
    start = pd.Timestamp(f"{days[0]} 09:00", tz=ET).tz_convert(UTC).to_pydatetime() - timedelta(minutes=30)
    fomc = {np.datetime64(d, "D") for d, evs in events.items() if any(e == "fomc_statement_1400" for e, _ in evs)}
    with tempfile.TemporaryDirectory() as tmp:
        store = JournalStore(Journal(Path(tmp) / "journal.sqlite"))
        # the same journal and the same fold as on paper; only the per-entry fsync of the live engine (a crash on
        # the Mac must not lose an entry) is off: a research run that crashes is simply run again
        store.journal.db.execute("pragma synchronous=off")
        sim = simulation({"SPY": frame}, {}, "SPY", store=store, policies=profiles, start=start,
                         cache_dir=Path(tmp) / "history", vix=vix, fomc=fomc, costs=costs, cash=cash,
                         calendar=cal, strategies={lv: [] for lv in profiles}, events=events,
                         calendar_until=covers)
        costs.clock = sim.clock
        eng = sim.engine
        eng.shadow_only = ()  # no shadow rule runs beside it
        eng.live_strategies = lambda: (strat,)  # the research rule alone (registry.live never returns it)
        store.insert("command", {"kind": "new_run", "payload": {"amount_usd": cash, "level": level},
                                 "idempotency_key": f"{SET}-{version}-{scenario}", "applied_at": None})
        eng.startup()
        eng.apply_commands()
        daily, errors, ticks = [], [], 0
        t0 = time.time()
        for k, day in enumerate(days):
            n_fills = len(costs.log)
            before = _equity(sim.broker)
            rl = run_sessions(eng, sim.broker, sim.clock, [day], strict=False)
            ticks += rl.ticks
            errors += [f"{t.isoformat()} {e}" for t, e in rl.errors]
            after = _equity(sim.broker)
            # the per-minute `ledger` rows stay in the journal file; the engine never reads them back, so the
            # in-memory copy of the store is dropped each night (ten years of minutes would not fit in memory)
            store.tables.pop("ledger", None)
            fills = costs.log[n_fills:]
            cost = sum(abs(f["price"] - f["mid"]) * f["qty"] + f.get("fee", 0.0) for f in fills)
            daily.append({"date": day, "start": before, "end": after, "pnl": after - before,
                          "cost": cost, "invested": bool(fills),
                          "traded": sum(f["price"] * f["qty"] for f in fills)})
            if progress and (k + 1) % 250 == 0:
                progress(f"    {version} | {scenario}: {k + 1} of {len(days)} sessions, {time.time() - t0:.0f}s")
        t = store.tables
        trades = [r for r in t.get("trade", []) if r["strategy"] == version]
        halts = [r.get("kind") for r in t.get("halt", [])]
        signals = [r for r in t.get("signal", []) if r["strategy"] == version]
        gate = t.get("gate_run", [])
        store.journal.close()
    return {"version": version, "scenario": scenario, "daily": daily, "fills": list(costs.log),
            "trades": trades, "halts": dict(Counter(halts)), "signals": len(signals),
            "gate_refused": sum(1 for g in gate if not g.get("approved")), "errors": errors, "ticks": ticks,
            "runtime_s": round(time.time() - t0, 1)}


def _job(x: dict) -> dict:
    """One engine run in its own process (`--jobs`): loads the inputs itself, so nothing large is pickled."""
    data = Path(x["data"])
    frame = load_frame(data, "SPY", x["start"], x["end"])
    sd = from_frame("SPY", frame)
    days = [str(d) for d in sd.dates if x["test_start"] <= str(d) <= x["end"]]
    vix, _ = load_vix(data)
    events, covers = load_calendar(CALENDAR)
    log(f"   start {x['version']} | {x['scenario']} ({len(days)} sessions)")
    return engine_run(frame, calendar_of(sd), days, x["version"], x["scenario"], level=x["level"], vix=vix,
                      events=events, covers=covers, adv={"SPY": adv_table(frame)}, cash=x["equity"], progress=log)


def _equity(broker) -> float:
    return float(broker.account()["equity"])


# ---------------------------------------------------------------- round trips from the venue's fills


def trips_of(run: dict, sd: SymbolDays) -> list[dict]:
    """Pair the venue's fills into round trips (one position at a time, whole shares, no partial fills) and
    attach the engine's exit reason. Gross = exit mid / entry mid - 1; net after spread, impact and fees."""
    fills = run["fills"]
    reasons = [r.get("exit_reason") for r in sorted(run["trades"], key=lambda r: str(r["entry_ts"]))
               if r.get("exit_px") is not None]
    row = {str(d): i for i, d in enumerate(sd.dates)}
    out, buy = [], None
    for f in fills:
        if f["side"] == "buy":
            if buy is not None:
                raise ValueError("two buys in a row: the noise band holds one position at a time")
            buy = f
            continue
        if buy is None or abs(f["qty"] - buy["qty"]) > 1e-9:
            raise ValueError(f"a sale of {f['qty']} shares does not close the open buy {buy}")
        tb, ts = _et(buy["t"]), _et(f["t"])
        mb = _minute(tb)
        ms = _minute(ts)
        close = f["venue"] == "close"
        spent = buy["price"] * buy["qty"]
        out.append({"date": str(tb.date()), "i_entry": row[str(tb.date())], "entry_m": mb, "decide_m": mb - 1,
                    "exit_date": str(ts.date()), "i_exit": row[str(ts.date())], "exit_m": ms, "close_exit": close,
                    "qty": buy["qty"], "entry_mid": buy["mid"], "entry": buy["price"], "exit_mid": f["mid"],
                    "exit": f["price"], "fee": f.get("fee", 0.0), "gross_ret": f["mid"] / buy["mid"] - 1,
                    "net_ret": (f["price"] * f["qty"] - f.get("fee", 0.0)) / spent - 1,
                    "cost_bp_per_side": ((buy["price"] - buy["mid"]) * buy["qty"] + (f["mid"] - f["price"]) * f["qty"]
                                         + f.get("fee", 0.0)) / (buy["mid"] * buy["qty"] + f["mid"] * f["qty"]) * 1e4,
                    "reason": "close" if close else None})
        buy = None
    for t, r in zip(out, reasons, strict=False):
        t["reason"] = "close" if t["close_exit"] else (r or "unknown")
    return out


def _et(t: datetime) -> datetime:
    return t.astimezone(ZoneInfo(ET))


def _minute(t: datetime) -> int:
    """Session minute of the bar a fill traded in: the venue fills a bar at its start, the SimClock reads the
    bar's start when SimBroker.advance trades it."""
    return int((t.hour * 60 + t.minute) - (9 * 60 + 30))


def as_harness_trips(trips: list[dict], sd: SymbolDays) -> list[nb.Trip]:
    """The engine's round trips in the harness's shape, for the random-timing control (same durations)."""
    cm = nb.close_minute(sd)
    out = []
    for t in trips:
        if t["close_exit"]:
            out.append(nb.Trip(t["i_entry"], t["decide_m"], t["entry_m"], t["entry_mid"], t["i_exit"],
                               int(cm[t["i_exit"]]), t["exit_mid"], "close"))
        else:
            out.append(nb.Trip(t["i_entry"], t["decide_m"], t["entry_m"], t["entry_mid"], t["i_exit"],
                               t["exit_m"], t["exit_mid"], t["reason"] or "band_vwap"))
    return out


def run_stats(run: dict, sd: SymbolDays) -> dict:
    trips = trips_of(run, sd)
    daily = pd.DataFrame(run["daily"])
    rets = (daily["pnl"] / daily["start"]).to_numpy()
    dates = np.array(daily["date"], dtype="datetime64[D]")
    st = nb.series_stats(dates, rets)
    st.update(nb.trip_stats(trips))
    exposure = [t["entry"] * t["qty"] / s for t, s in zip(
        trips, [float(daily.loc[daily["date"] == t["date"], "start"].iloc[0]) for t in trips], strict=True)]
    st.update({"label": f"{run['version']}|{run['scenario']}", "spec": run["version"], "cost": run["scenario"],
               "sessions": len(daily), "days_invested_pct": round(float(daily["invested"].mean()) * 100, 1),
               "exit_reasons": dict(Counter(t["reason"] for t in trips)),
               "mean_cost_bp_per_side": round(float(np.mean([t["cost_bp_per_side"] for t in trips])), 3)
               if trips else None,
               "mean_position_pct": round(float(np.mean(exposure)) * 100, 2) if exposure else None,
               "max_position_pct": round(float(np.max(exposure)) * 100, 2) if exposure else None,
               "final_equity": round(float(daily["end"].iloc[-1]), 2), "halts": run["halts"],
               "signals": run["signals"], "gate_refused": run["gate_refused"], "errors": len(run["errors"]),
               "ticks": run["ticks"], "runtime_s": run["runtime_s"],
               "window_2022_23_pct": nb.window_return(dates, rets)})
    gross = np.array([t["gross_ret"] for t in trips])
    st["break_even"] = nb.break_even(gross)
    return st


def reconcile(trips: list[dict], harness: list[nb.Trip], events: dict, sd: SymbolDays) -> dict:
    """Round trip by round trip against the harness's cost-free signals: same (session, entry minute)."""
    eng = {(t["i_entry"], t["entry_m"]) for t in trips}
    har = {(t.i_entry, t.entry_m): t for t in harness}
    missing = [k for k in har if k not in eng]
    why = Counter()
    for i, m in missing:
        day = sd.dates[i].astype(object)
        evs = events.get(day, ())
        if any(em - 30 <= m - 1 <= em + 30 for _, em in evs):
            why["event blackout (+-30 min of a scheduled release)"] += 1
        elif any(k[0] == i and k[1] < m for k in eng) and not any(k[0] == i and k[1] == m for k in eng):
            why["other (the pause after losses, an earlier stop or exit in the session)"] += 1
        else:
            why["other (the pause after losses, a halt, or a different earlier trip)"] += 1
    same_exit = sum(1 for t in trips if (k := (t["i_entry"], t["entry_m"])) in har
                    and (har[k].exit_m == t["exit_m"] or (t["close_exit"] and har[k].reason == "close")))
    return {"harness_trips": len(har), "engine_trips": len(eng), "both": len(eng & set(har)),
            "same_entry_and_exit": same_exit, "harness_only": len(missing), "engine_only": len(eng - set(har)),
            "harness_only_why": dict(why)}


# ---------------------------------------------------------------- the marks


def timing_control(sd: SymbolDays, version: str, trips: list[dict], i0: int, i1: int, cost_bp: float, runs: int,
                   seed: int, own_sharpe: float) -> dict | None:
    """Decision 15 section 10 for SPY-only rules: the engine's round trips against `runs` random-timing accounts
    (the harness's control routine, the same durations, the rule's check times), at `cost_bp` a side."""
    if runs < 1 or not trips:
        return None
    spec = registry.research_intraday()[version].spec
    c = nb.random_timing_control(sd, spec, as_harness_trips(trips, sd), i0, i1, round(cost_bp, 4), runs=runs,
                                 seed=seed)
    sh = np.array(c["per_run"]["sharpe"])
    own_pct = float((sh < own_sharpe).mean() * 100)
    return {**{k: v for k, v in c.items() if k != "per_run"}, "percentile_simulated": c["percentile"],
            "percentile": own_pct, "engine_sharpe": round(own_sharpe, 4), "cost_bp_per_side": round(cost_bp, 4),
            "per_run": c["per_run"]}


def daily_series(run: dict, key: str = "ret") -> pd.Series:
    d = pd.DataFrame(run["daily"])
    idx = pd.DatetimeIndex(pd.to_datetime(d["date"]))
    if key == "cost":
        return pd.Series((d["cost"] / d["start"]).to_numpy(), index=idx)
    if key == "traded":
        return pd.Series((d["traded"] / d["start"]).to_numpy(), index=idx)
    return pd.Series((d["pnl"] / d["start"]).to_numpy(), index=idx)


def first_run_at(trials_file: Path) -> datetime | None:
    rows, _ = trials_mod.read_jsonl(trials_file)
    ts = [r.get("ts") for r in rows if r.get("strategy") == RULE and r.get("ts")]
    return min((datetime.fromisoformat(t) for t in ts), default=None)


def prereg_marks(rows: dict[str, dict], nbr_rows: dict[str, dict], base: str, mark2: str) -> list[dict]:
    """The pre-registration's marks 1-5 (section 5) on the engine's rows: `base` the scenario of marks 1, 3-5,
    `mark2` that of mark 2."""
    by_cost = {str(nb.BASE_COST): rows[base], str(nb.MARK2_COST): rows[mark2]}
    return nb.marks_v1(by_cost, nbr_rows)


# ---------------------------------------------------------------- the run


def harness_report(data: Path, level: str) -> dict | None:
    import spy_noise_band as harness

    hit = harness.find_report(data / "reports", level, RULE)
    if hit is None:
        return None
    f, rep = hit
    rows = {r["cost"]: r for r in rep["rows"] if r["spec"] == RULE}
    nbr = {r["spec"].split("~")[1]: r for r in rep["rows"] if r["kind"] == "neighbour"}
    return {"file": f.name, "run_at": rep.get("run_at"), "rows": rows, "neighbours": nbr,
            "timing_control": rep.get("timing_control"), "marks": rep.get("marks")}


def run(a: argparse.Namespace) -> dict:
    t_all = time.time()
    data = Path(a.data)
    try:
        sha = prereg_mod.require(RULE, ROOT)
    except prereg_mod.PreregError as e:
        raise SystemExit(f"no run: {e}") from e
    pre = prereg_mod.load(RULE, ROOT)
    gate = gv.GateV2.load(ROOT)
    frame = load_frame(data, "SPY", a.start, a.end)
    sd = from_frame("SPY", frame)
    days = [str(d) for d in sd.dates if a.test_start <= str(d) <= a.end]
    if not days:
        raise SystemExit(f"no SPY session between {a.test_start} and {a.end}")
    i0, i1 = int(np.searchsorted(sd.dates, np.datetime64(days[0]))), int(np.searchsorted(sd.dates,
                                                                                        np.datetime64(days[-1])))
    vix, vix_note = load_vix(data)
    events, covers = load_calendar(CALENDAR)
    log(f"SPY: {len(sd.dates)} sessions loaded ({sd.dates[0]} to {sd.dates[-1]}), test window {days[0]} to "
        f"{days[-1]} ({len(days)} sessions); {vix_note}; prereg sha256 {sha[:12]}; gate_v2 sha256 {gate.sha[:12]}")
    grid = [*BASE_RUNS, *(() if a.no_neighbours else NEIGHBOUR_RUNS)]
    jobs = max(1, min(a.jobs, len(grid)))
    log(f"{len(grid)} engine runs, {jobs} at a time")
    inputs = {"data": str(data), "start": a.start, "end": a.end, "test_start": a.test_start, "level": a.level,
              "equity": a.equity}
    runs, stats = {}, {}
    if jobs == 1:
        done = (_job({**inputs, "version": v, "scenario": c}) for v, c in grid)
    else:
        pool = ProcessPoolExecutor(max_workers=jobs)
        done = pool.map(_job, [{**inputs, "version": v, "scenario": c} for v, c in grid])
    for r in done:
        version, scenario = r["version"], r["scenario"]
        runs[(version, scenario)] = r
        stats[(version, scenario)] = s = run_stats(r, sd)
        log(f"== {version} | {scenario}: {s['trades']} round trips, net {s['net_bp_per_trip']} bp/trip (t "
            f"{s['t_net']}), CAGR {s['cagr_pct']}%, max DD {s['max_drawdown_pct']}%, {s['errors']} engine errors, "
            f"{r['runtime_s']:.0f}s")
    if jobs > 1:
        pool.shutdown()
    base_v2 = stats[(RULE, "v2")]
    trips_v2 = trips_of(runs[(RULE, "v2")], sd)
    trips_05 = trips_of(runs[(RULE, "0.5")], sd)

    # decision 15 Gate B on cost model v2
    spy, bench_kind, bench_info = spy_benchmark(data, np.array(days, dtype="datetime64[D]"), sd, i0, i1)
    log(f"== random-timing control ({a.control_runs} runs) on the engine's round trips")
    ctl_v2 = timing_control(sd, RULE, trips_v2, i0, i1, base_v2["mean_cost_bp_per_side"] or 0.0, a.control_runs,
                            a.control_seed, _sharpe(daily_series(runs[(RULE, "v2")])))
    ctl_05 = timing_control(sd, RULE, trips_05, i0, i1, nb.BASE_COST, a.control_runs, a.control_seed,
                            _sharpe(daily_series(runs[(RULE, "0.5")])))
    neighbours = {v.split("~")[1]: daily_series(runs[(v, s)]) for v, s in runs if s == "v2" and "~" in v}
    rep_gate = gv.evaluate_gate_b(
        gate, rule=RULE,
        run=gv.Run(net=daily_series(runs[(RULE, "v2")]), spy=spy, costs=daily_series(runs[(RULE, "v2")], "cost"),
                   random=None if ctl_v2 is None else {k: v for k, v in ctl_v2.items() if k != "per_run"},
                   universe="point_in_time", traded=daily_series(runs[(RULE, "v2")], "traded")),
        neighbours=neighbours if not a.no_neighbours else None, prereg=pre,
        source={"engine": "simbroker", "cost_model": "v2", "data_repairs": True, "benchmark": bench_kind},
        registry=trials_mod.load(trials_file=data / "trials.jsonl"), first_run_at=first_run_at(data / "trials.jsonl"))

    # the pre-registration's marks, as written (0.5 / 1 bp) and on cost model v2
    nbr_05 = {v.split("~")[1]: stats[(v, s)] for v, s in stats if s == "0.5" and "~" in v}
    nbr_v2 = {v.split("~")[1]: stats[(v, s)] for v, s in stats if s == "v2" and "~" in v}
    rows = {s: stats[(RULE, s)] for s in ("v2", "0.5", "1")}
    marks_written = prereg_marks(rows, nbr_05, "0.5", "1")
    marks_v2 = prereg_marks(rows, {k: v for k, v in nbr_v2.items() if k in nb.LOOKBACK_NEIGHBOURS}, "v2", "v2")
    for ms, ctl in ((marks_written, ctl_05), (marks_v2, ctl_v2)):
        m3 = gv.mark_3(gate, None if ctl is None else {k: v for k, v in ctl.items() if k != "per_run"})
        ms.append({"mark": "3 (amendment 1)", "text": "net Sharpe at or above the 90th percentile of >= 500 "
                   "random-timing accounts", "verdict": {True: "pass", False: "fail", None: "cannot be judged"}[
                       m3["met"]], "detail": _ctl_detail(ctl, m3)})
    harness = harness_report(data, a.level)
    rec = reconcile(trips_05, nb.signals(sd, nb.BASE, i0, i1), events, sd)
    return {"set": SET, "rule": RULE, "status": STATUS, "level": a.level, "start": a.start, "end": a.end,
            "test_start": a.test_start, "equity": a.equity, "control_runs": a.control_runs,
            "control_seed": a.control_seed, "neighbours": not a.no_neighbours,
            "window": [days[0], days[-1]], "sessions": len(days),
            "prereg": {"file": f"docs/prereg/{RULE}.md", "sha256": sha}, "gate_v2_sha256": gate.sha,
            "vix": vix_note, "benchmark": {"kind": bench_kind, "note": bench_info["note"],
                                           **nb.series_stats(spy.index.to_numpy(), spy.to_numpy())},
            "rows": [stats[k] for k in stats], "gate_b": rep_gate, "prereg_marks_as_written": marks_written,
            "prereg_marks_on_v2": marks_v2,
            "timing_control": {"v2": _no_runs(ctl_v2), "0.5": _no_runs(ctl_05)}, "reconcile_0.5bp": rec,
            "harness": harness, "harness_quoted": HARNESS_QUOTED, "runs": runs, "runtime_s": 0.0,
            "_t0": t_all}


def _sharpe(s: pd.Series) -> float:
    r = s.to_numpy(dtype=float)
    sd = r.std(ddof=1) if r.size > 1 else 0.0
    return float(r.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0


def _no_runs(c: dict | None) -> dict | None:
    return None if c is None else {k: v for k, v in c.items() if k != "per_run"}


def _ctl_detail(c: dict | None, m3: dict) -> str:
    if c is None:
        return m3["why"] or "not run"
    return (f"engine run at the {c['percentile']:.1f}th percentile, its trips replayed through the control at the "
            f"{c['percentile_simulated']:.1f}th (lower counts) of {c['runs']} random-timing runs at "
            f"{c['cost_bp_per_side']:g} bp a side (random median Sharpe {c['random_median']:.2f}, 90th "
            f"{c['random_p90']:.2f}; engine {c['engine_sharpe']:.2f})"
            + ("" if c["matched"] else f", NOT matched: {c['unfilled']} trips could not be placed")
            + (f"; {m3['why']}" if m3["why"] else ""))


def write(rep: dict, data: Path) -> Path:
    code_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    run_at = datetime.now(UTC)
    tag = f"spy_engine_{rep['level']}_{run_at.strftime('%Y%m%dT%H%M%S')}"
    out = data / "reports"
    (out / tag).mkdir(parents=True, exist_ok=True)
    (data / "trials").mkdir(parents=True, exist_ok=True)
    runs = rep.pop("runs")
    t0 = rep.pop("_t0")
    rows = []
    for (version, scenario), r in runs.items():
        safe = f"{version}_{scenario}".replace("~", "-")
        pd.DataFrame(r["daily"]).to_csv(out / tag / f"daily_{safe}.csv", index=False)
        fills = pd.DataFrame(r["fills"])
        fills.to_csv(out / tag / f"fills_{safe}.csv", index=False)
        st = next(s for s in rep["rows"] if s["label"] == f"{version}|{scenario}")
        st["files"] = {"daily": f"{tag}/daily_{safe}.csv", "fills": f"{tag}/fills_{safe}.csv"}
        if r["errors"]:
            (out / tag / f"errors_{safe}.txt").write_text("\n".join(r["errors"]) + "\n")
        tid = f"{tag}_{safe}"
        d = pd.DataFrame(r["daily"])
        pd.DataFrame({"date": d["date"], "ret": d["pnl"] / d["start"]}).to_csv(data / "trials" / f"{tid}.csv",
                                                                               index=False)
        rows.append({"ts": run_at.isoformat(timespec="seconds"), "trial_id": tid, "strategy": RULE,
                     "variant": SET, "label": f"spy noise band engine {version} {scenario}", "level": rep["level"],
                     "code_sha": code_sha, "experiment": SET, "status": "engine_simbroker",
                     "cost_model": "v2" if scenario.startswith("v2") else f"{scenario} bp",
                     "prereg_sha256": rep["prereg"]["sha256"], "gate_v2_sha256": rep["gate_v2_sha256"],
                     "n_trials": 0,  # a report of a configuration the harness already counted (section 10)
                     "params": {**registry.research_intraday()[version].spec.params(), "cost": scenario},
                     "window": rep["window"],
                     "metrics": {k: st[k] for k in ("trades", "net_bp_per_trip", "t_net", "return_pct", "cagr_pct",
                                                    "sharpe", "max_drawdown_pct")}})
    for c in ("v2", "0.5"):
        ctl = rep["timing_control"].get(c)
        if ctl is not None:
            ctl["file"] = f"{tag}/control_timing_{c}.json"
    rep.update(run_at=run_at.isoformat(timespec="seconds"), code_sha=code_sha,
               runtime_s=round(time.time() - t0, 1))
    for r in rows:
        trials_mod.append(data / "trials.jsonl", r)
    f = out / f"{tag}.json"
    f.write_text(json.dumps(rep, indent=1, default=str))  # last: marks the run finished
    return f


# ---------------------------------------------------------------- the page


def _fmt(v) -> str:
    return "" if v is None else str(v)


def page(rep: dict, name: str = "") -> str:
    g = rep["gate_b"]
    rows = {r["label"]: r for r in rep["rows"]}
    v2, b05, b1 = rows[f"{RULE}|v2"], rows[f"{RULE}|0.5"], rows[f"{RULE}|1"]
    h = rep.get("harness")
    hq = rep["harness_quoted"]
    h05 = (h or {}).get("rows", {}).get("0.5 bp")
    h1 = (h or {}).get("rows", {}).get("1 bp")
    hv1 = (h or {}).get("rows", {}).get("model v1")
    hsrc = f"harness report `{h['file']}`" if h else hq["source"]
    out = [f"# Gate B go/no-go: `{RULE}` through the engine with SimBroker (issue #202)", "",
           f"**{rep['status']}.** Out-of-sample {rep['window'][0]} to {rep['window'][1]} ({rep['sessions']} "
           f"sessions), level {rep['level']}, one continuous ${rep['equity']:,.0f} account per row. "
           f"Pre-registration `{rep['prereg']['file']}` sha256 `{rep['prereg']['sha256'][:12]}` (unchanged), "
           f"config/gate_v2.yaml sha256 `{rep['gate_v2_sha256'][:12]}`. {rep['vix']}.", "",
           f"## Decision 15 Gate B on cost model v2: **{g['verdict'].upper()}**", ""]
    if g["provisional_reasons"]:
        out += ["Cannot pass, whatever the marks say:", *[f"- {r}" for r in g["provisional_reasons"]], ""]
    labels = {"mark_1": "1. net positive overall and in >= 6 of 10 calendar years (section 10 part-years)",
              "mark_2": "2. CAGR >= SPY - 2 pts with DD <= 0.75 x SPY's, or >= SPY + 1 pt with <= 1.1 x",
              "mark_3": "3. net Sharpe >= 90th percentile of >= 500 random-timing runs (section 10)",
              "mark_4": "4. marks 1-2 at 2x costs", "mark_5": "5. point-in-time (SPY: the run itself, marks 1-4)",
              "mark_6": "6. every pre-declared neighbour passes mark 1 (v2)"}
    out += ["| Mark | Result | Why |", "|---|---|---|"]
    for k, lab in labels.items():
        m = g["marks"][k]
        out.append(f"| {lab} | {gv._state(m['met'])} | {m['why'] or '-'} |")
    st = g["statistics"]
    out += ["", f"Rule CAGR {gv._pct(st['rule']['cagr'])}, max drawdown {gv._pct(st['rule']['max_drawdown'])}, "
            f"Sharpe {st['rule']['sharpe']['sharpe']:.2f}; benchmark {rep['benchmark']['kind']} CAGR "
            f"{gv._pct(st['spy']['cagr'])}, max drawdown {gv._pct(st['spy']['max_drawdown'])}.", ""]
    out += [f"**Size (read before the marks):** this is the account the risk gate allows, not the prereg's 1x "
            f"account: {v2['mean_position_pct']}% of equity on average when long (at most "
            f"{v2['max_position_pct']}%; the level's per-position cap, at most 20% under `HARD_CEILINGS`). "
            "Marks 2 and 4 (CAGR and drawdown against SPY) judge this partly invested account, so a fail there "
            "says nothing about the rule at 1x; mark 1 per year and per-trip numbers do not depend on size "
            "beyond rounding to whole shares.", ""]
    out += ["## The pre-registration's marks", "",
            "As written (0.5 bp a side; mark 2 at 1 bp), from the engine's round trips:", ""]
    out += [f"- Mark {m['mark']} ({m['text']}): **{m['verdict']}**. {m['detail']}." for m in
            rep["prereg_marks_as_written"]]
    out += ["", "The same marks with cost model v2 in place of the fixed scenarios (mark 2 on v2 too):", ""]
    out += [f"- Mark {m['mark']} ({m['text']}): **{m['verdict']}**. {m['detail']}." for m in
            rep["prereg_marks_on_v2"]]
    out += ["", "## Engine against the research harness", "",
            "| | Harness 0.5 bp | Engine 0.5 bp | Harness 1 bp | Engine 1 bp | Harness model v1 | Engine v2 |",
            "|---|---|---|---|---|---|---|"]

    def cell(r, k):
        return "" if r is None else _fmt(r.get(k))

    hq05 = h05 or hq
    for name_, k in (("Round trips", "trades"), ("Net bp / trip", "net_bp_per_trip"), ("t", "t_net"),
                     ("Gross bp / trip", "gross_bp_per_trip"), ("Hit rate", "hit_rate"),
                     ("Total return %", "return_pct"), ("CAGR %", "cagr_pct"), ("Sharpe", "sharpe"),
                     ("Max drawdown %", "max_drawdown_pct"), ("Positive years", "positive_years"),
                     ("Days invested %", "days_invested_pct")):
        out.append(f"| {name_} | {cell(hq05, k)} | {cell(b05, k)} | {cell(h1, k)} | {cell(b1, k)} | "
                   f"{cell(hv1, k)} | {cell(v2, k)} |")
    out.append(f"| Mean position % of equity | 100 (1x) | {b05['mean_position_pct']} | 100 | "
               f"{b1['mean_position_pct']} | 100 | {v2['mean_position_pct']} |")
    out.append(f"| All-in cost bp a side (measured) | 0.5 | {b05['mean_cost_bp_per_side']} | 1 | "
               f"{b1['mean_cost_bp_per_side']} | model v1 | {v2['mean_cost_bp_per_side']} |")
    out += ["", f"Harness source: {hsrc}. Per-trip numbers do not depend on size; the engine's account is the one "
            "the risk gate allows (per-position cap of the level, VIX halving, whole shares), so its CAGR and "
            "drawdown are about that share of the harness's 1x account.", ""]
    rec = rep["reconcile_0.5bp"]
    out += [f"Round trips at 0.5 bp, one by one against the harness's signals: {rec['both']} in both (same entry "
            f"minute; {rec['same_entry_and_exit']} also with the same exit), {rec['harness_only']} only in the "
            f"harness ({', '.join(f'{k}: {v}' for k, v in rec['harness_only_why'].items()) or 'none'}), "
            f"{rec['engine_only']} only in the engine.", ""]
    out += ["## Every engine run", "",
            "| Run | Round trips | Net bp / trip | t | Gross bp / trip | Cost bp a side | Total return % | CAGR % | "
            "Sharpe | Max DD % | Positive years | Exits | Halts | Engine errors | Runtime s |",
            "|" + "---|" * 15]
    for r in rep["rows"]:
        out.append(f"| {r['label']} | {r['trades']} | {r['net_bp_per_trip']} | {r['t_net']} | "
                   f"{r['gross_bp_per_trip']} | {r['mean_cost_bp_per_side']} | {r['return_pct']} | {r['cagr_pct']} | "
                   f"{r['sharpe']} | {r['max_drawdown_pct']} | {r['positive_years']} of {r['n_years']} | "
                   f"{r['exit_reasons']} | {r['halts'] or '-'} | {r['errors']} | {r['runtime_s']} |")
    out += ["", f"Report `{name}`; code `{rep.get('code_sha')}`; runtime {rep.get('runtime_s')} s. Trial rows: "
            "n_trials 0 (reports of configurations the harness counted).", ""]
    return "\n".join(out)


def find_report(reports: Path, level: str, **same) -> tuple[Path, dict] | None:
    for f in sorted(reports.glob(f"spy_engine_{level}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rep = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if rep.get("set") != SET or "gate_b" not in rep:
            continue
        if any(want is not None and rep.get(k) != want for k, want in same.items()):
            continue
        return f, rep
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--start", default="2016-01-01", help="first bar read (the history before the test)")
    ap.add_argument("--test-start", default="2017-05-01", help="first test session (decision 15: 2017-05 onward)")
    ap.add_argument("--end", help="last session (inclusive)")
    ap.add_argument("--level", default="balanced")
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--control-runs", type=int, default=nb.CONTROL_RUNS)
    ap.add_argument("--control-seed", type=int, default=nb.CONTROL_SEED)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2),
                    help="engine runs at the same time, one process each (default: half the CPUs)")
    ap.add_argument("--no-neighbours", action="store_true", help="skip the eight neighbour runs (mark 6 unjudged)")
    ap.add_argument("--has", action="store_true", help="exit 0 if a finished report exists")
    ap.add_argument("--summary", action="store_true", help="print the newest report's page and write it")
    a = ap.parse_args(argv)
    data = Path(a.data)
    if a.has:
        hit = find_report(data / "reports", a.level, start=a.start, end=a.end, test_start=a.test_start,
                          control_runs=a.control_runs, neighbours=not a.no_neighbours)
        return 0 if hit else 1
    if a.summary:
        hit = find_report(data / "reports", a.level)
        if hit is None:
            print("no spy_engine report yet: run ./scripts/run_experiments.sh spy_engine")
            return 1
        text = page(hit[1], hit[0].name)
        (data / "reports" / SUMMARY).write_text(text)
        print(text)
        return 0
    if not a.end:
        ap.error("--end is required for a run")
    rep = run(a)
    f = write(rep, data)
    text = page(json.loads(f.read_text()), f.name)
    (data / "reports" / SUMMARY).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
