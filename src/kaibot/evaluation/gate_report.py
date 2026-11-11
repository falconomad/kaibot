"""S38: the automatic go-live gate report. Pure evaluation plus the engine's day-end hook.

The bar is pre-registered in config/gate.yaml (sha256 recorded in every report). The report is computed here from
stored trades, sessions and the nightly random-entry baseline, then appended to `kaibot.gate_report`, which cannot
be updated or deleted (migration 0010). The dashboard only reads it. Passing opens a discussion: there is no live
switch anywhere.

Random-entry baseline: each evening `day_random()` simulates `random_runs` random-entry strategies on that day's
bars with the same number of dip entries, sizing and exits as replay (kaibot.replay.sim.random_entry_runs), seeded
by (run, session). Run k of the period is the sum of run k of each day, so months of minute bars never need to be
kept. The strategy's percentile is its total P&L against those sums.

Frozen code: trades count only from the first engine start of the latest unbroken stretch of starts with the
current strategy-code fingerprint (hash of the files listed in gate.yaml)."""

from __future__ import annotations

import glob
import hashlib
import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from kaibot.evaluation.metrics import deflated_sharpe, max_drawdown, random_entry_percentile, sharpe, t_stat

log = logging.getLogger(__name__)
DAYS_PER_MONTH = 30.44


# ---------------- pre-registration ----------------
@dataclass(frozen=True)
class Bar:
    cfg: dict
    sha: str

    @classmethod
    def load(cls, root: Path) -> Bar:
        raw = (root / "config" / "gate.yaml").read_bytes()
        return cls(yaml.safe_load(raw), hashlib.sha256(raw).hexdigest())


def fingerprint(root: Path, patterns: list[str]) -> str:
    """sha256 over the strategy-relevant files (sorted relative paths and contents): changes with trading code only."""
    h = hashlib.sha256()
    files = sorted({p for pat in patterns for p in glob.glob(str(root / pat))})
    for f in files:
        rel = Path(f).relative_to(root).as_posix()
        h.update(rel.encode() + b"\0" + Path(f).read_bytes() + b"\0")
    return h.hexdigest()[:16]


def frozen_since(starts: list[dict], current_fp: str) -> datetime | None:
    """Earliest start of the final unbroken stretch of engine starts with `current_fp` (starts: {ts, fingerprint})."""
    since = None
    for s in sorted(starts, key=lambda s: _ts(s.get("ts")) or datetime.min.replace(tzinfo=UTC)):
        if s.get("fingerprint") == current_fp:
            since = since or (_ts(s.get("ts")) or datetime.min.replace(tzinfo=UTC))
        else:
            since = None
    return since


# ---------------- evaluation ----------------
def evaluate(bar: Bar, *, strategies: list[str], trades: list[dict], sessions: list[dict], random_days: list[dict],
             starts: list[dict], fingerprint_now: str, amount: float, dd_cap_pct: float,
             trials: dict[str, list[float]] | None = None, as_of: datetime | None = None,
             config_version: int | None = None) -> dict:
    """The gate report as JSON-ready data.

    trades: closed trades {strategy, entry_ts, exit_ts, pnl_usd}. sessions: {session, spy_ret}.
    random_days: {strategy, session, fingerprint, totals: [random_runs floats]}. starts: {ts, fingerprint}.
    trials: per strategy, the daily (not annualised) Sharpe of every logged research trial (deflated Sharpe)."""
    cfg, b, k = bar.cfg, bar.cfg["bar"], bar.cfg["kill"]
    since = frozen_since(starts, fingerprint_now)
    other_cfg = 0
    if config_version is not None:  # M5: the gate judges one frozen configuration only
        keep = [t for t in trades if int(t.get("config_version") or 0) == config_version]
        other_cfg = len(trades) - len(keep)
        trades = keep
        random_days = [r for r in random_days if int(r.get("config_version") or 0) == config_version]
    out = {"generated_at": _iso(as_of or datetime.now(UTC)), "bar_sha": bar.sha, "fingerprint": fingerprint_now,
           "frozen_since": _iso(since) if since else None, "config_version": config_version,
           "excluded_other_config": other_cfg, "bar": b, "kill": k, "strategies": {}}
    sess = pd.DataFrame(sessions or [], columns=["session", "spy_ret"])
    sess["session"] = pd.to_datetime(sess["session"]).dt.date
    for strat in strategies:
        out["strategies"][strat] = _one(strat, b, k, cfg, trades, sess, random_days, since, fingerprint_now,
                                        amount, dd_cap_pct, (trials or {}).get(strat, []))
    out["overall"] = _overall(out["strategies"])
    return out


def _one(strat, b, k, cfg, trades, sess, random_days, since, fp, amount, dd_cap_pct, trial_sharpes) -> dict:
    tr = pd.DataFrame([t for t in trades if t.get("strategy") == strat and t.get("exit_ts") and
                       t.get("pnl_usd") is not None], columns=["strategy", "entry_ts", "exit_ts", "pnl_usd"])
    tr["entry_ts"] = pd.to_datetime(tr["entry_ts"], utc=True, format="mixed")
    tr["exit_ts"] = pd.to_datetime(tr["exit_ts"], utc=True, format="mixed")
    excluded = 0
    if since is not None:
        keep = tr["entry_ts"] >= pd.Timestamp(since)
        excluded = int((~keep).sum())
        tr = tr[keep]
    else:
        excluded, tr = len(tr), tr.iloc[0:0]  # no start with the current fingerprint: nothing counts yet
    pnl = tr["pnl_usd"].astype(float).to_numpy()
    n = len(pnl)
    ret = pnl / amount if amount else pnl
    months = 0.0
    if n:
        months = (tr["exit_ts"].max() - pd.Timestamp(since)).total_seconds() / 86400 / DAYS_PER_MONTH

    # daily P&L on every session in the frozen window (zero on days without a trade) vs SPY
    s = sess[sess["session"] >= (_et_date(since) if since else date.max)] if len(sess) else sess
    exit_day = tr["exit_ts"].dt.tz_convert("America/New_York").dt.date
    by_day = pd.Series(pnl, index=exit_day.values).groupby(level=0).sum() if n else pd.Series(dtype=float)
    days = sorted(set(s["session"]) | set(by_day.index))
    daily = by_day.reindex(days, fill_value=0.0).to_numpy(dtype=float)
    equity = amount + np.cumsum(daily) if len(daily) else np.array([amount])
    prev = np.r_[amount, equity[:-1]] if len(daily) else np.array([amount])
    daily_ret = daily / prev if len(daily) else np.array([])
    spy = s.set_index("session")["spy_ret"].reindex(days).astype(float).to_numpy() if len(days) else np.array([])
    spy = spy[np.isfinite(spy)]
    sh, spy_sh = sharpe(daily_ret) * math.sqrt(252), sharpe(spy) * math.sqrt(252)
    dd = max_drawdown(np.r_[amount, equity])

    t = t_stat(ret)
    # Deflated Sharpe on daily returns, against the variance of the daily Sharpe across every logged research trial
    # of this strategy (one trial counts at least: the live strategy itself).
    var = float(np.var(trial_sharpes, ddof=1)) if len(trial_sharpes) > 1 else 0.0
    dsr = deflated_sharpe(daily_ret, max(len(trial_sharpes), 1), var) if n and len(daily_ret) >= 3 else 0.0

    pct, rnd_days, rnd_missing = None, 0, None
    if strat in cfg.get("random_baseline", []):
        start_day = _et_date(since) if since else date.max
        rows = [r for r in random_days if r.get("strategy") == strat and r.get("fingerprint") == fp
                and _d(r["session"]) >= start_day]
        trade_days = set(tr["entry_ts"].dt.tz_convert("America/New_York").dt.date) if n else set()
        have = {_d(r["session"]) for r in rows}
        rnd_missing = sorted(str(d) for d in trade_days - have)
        if rows and not rnd_missing:
            tot = np.sum([np.asarray(r["totals"], dtype=float) for r in rows], axis=0)
            pct, rnd_days = random_entry_percentile(float(pnl.sum()), tot), len(rows)

    crit = {
        "trades": _c(n, b["min_trades"], n >= b["min_trades"]),
        "months": _c(round(months, 2), b["min_months"], months >= b["min_months"]),
        "t_stat": _c(round(t, 3), b["t_min"], t > b["t_min"]),
        "deflated_sharpe": _c(round(dsr, 4), b["dsr_min"], dsr > b["dsr_min"]),
        "random_pct": _c(None if pct is None else round(pct, 1), b["random_pct_min"],
                         None if pct is None else pct >= b["random_pct_min"]),
        "sharpe_vs_spy": _c(round(sh, 3), round(spy_sh, 3), sh > spy_sh if len(spy) > 1 else None),
        "drawdown": _c(round(dd, 4), dd_cap_pct / 100, dd <= dd_cap_pct / 100),
    }
    kills = []
    if n >= k["min_trades"]:
        if t <= k["t_max"]:
            kills.append(f"t-stat {t:.2f} <= {k['t_max']}")
        if pct is not None and pct <= k["random_pct_max"]:
            kills.append(f"below {k['random_pct_max']:.0f}% of random entries ({pct:.1f}%)")
    if k.get("drawdown_breach") and dd > dd_cap_pct / 100:
        kills.append(f"drawdown {dd:.1%} beyond the {dd_cap_pct}% cap")
    provisional = n < b["min_trades"] or months < b["min_months"]
    if kills:
        status = "kill"
    elif provisional:
        status = "provisional"
    elif all(c["met"] is True for c in crit.values()):
        status = "pass"
    else:
        status = "fail"
    return {"status": status, "kills": kills, "criteria": crit, "n_trades": n, "excluded_older_code": excluded,
            "pnl_usd": round(float(pnl.sum()), 2), "mean_r_pct": round(float(ret.mean() * 100), 4) if n else None,
            "sessions": len(days), "random_days": rnd_days, "random_missing_days": rnd_missing,
            "n_trials": len(trial_sharpes), "trades_to_go": max(b["min_trades"] - n, 0)}


def _overall(st: dict) -> str:
    vals = [v["status"] for v in st.values()]
    if any(v == "pass" for v in vals):
        return "discuss"  # at least one strategy cleared the bar: open a go-live discussion (no switch)
    if vals and all(v == "kill" for v in vals):
        return "kill"
    return "keep collecting"


def _c(value, limit, met) -> dict:
    return {"value": value, "limit": limit, "met": met}


# ---------------- engine hook ----------------
def day_random(engine, n_runs: int) -> dict | None:
    """Today's random-entry totals for the dip strategy (same entry count, sizing and exits as replay)."""
    from kaibot.replay.pipeline import prepare
    from kaibot.replay.sim import random_entry_runs

    d, mk = engine.day, engine.market
    n = sum(1 for t in engine.store.select("trade", {"select": "strategy,entry_ts", "run_id": f"eq.{d.run_id}",
                                                       "strategy": "eq.dip_reversal"})
            if _et_date(_ts(t.get("entry_ts"))) == d.day)
    last_m = d.n_min - 1
    rules = getattr(d, "rules", None)
    stocks = {s: mk.symbol_days(s, last_m) for s in mk.symbols if rules is None or rules.tradable(s)}
    etfs = {e: mk.symbol_days(e, last_m) for e in set(mk.etf_of.values())}
    day = np.datetime64(d.day, "D")
    stocks = {s: sd for s, sd in stocks.items() if sd is not None and len(sd.dates) and sd.dates[-1] == day}
    prep = prepare(stocks, etfs, mk.etf_of, {}, day, day)
    seed = int(hashlib.sha256(f"{d.run_id}:{d.day}".encode()).hexdigest()[:8], 16)
    totals = random_entry_runs(prep.bars, prep.atr5, prep.vwap, prep.sigma, pd.Series({day: n}), engine.policy(),
                               d.start_equity, n_runs=n_runs, seed=seed, variant="A") if n else np.zeros(n_runs)
    return {"strategy": "dip_reversal", "session": str(d.day), "n_trades": n, "seed": seed,
            "config_version": rules.version if rules is not None else 0,
            "totals": [round(float(x), 2) for x in totals]}


def on_day_end(engine, root: Path) -> dict | None:
    """Append today's random baseline and a fresh gate report. Never raises (logged and contained)."""
    try:
        bar = Bar.load(root)
        fp = engine.fingerprint
        rid = engine.day.run_id
        if not rid:
            return None
        have = engine.store.select("gate_random", {"select": "session", "run_id": f"eq.{rid}",
                                                   "session": f"eq.{engine.day.day}", "strategy": "eq.dip_reversal"})
        if not have:  # first write wins: the table is append-only, a restart never recomputes a day
            rnd = day_random(engine, int(bar.cfg["bar"]["random_runs"]))
            if rnd is not None:
                engine.store.insert("gate_random", {**rnd, "run_id": rid, "fingerprint": fp}, returning=False)
        rules = getattr(engine.day, "rules", None)
        strategies = list(rules.strategies) if rules is not None else list(
            engine.strategies.get(engine.level(), ["dip_reversal"]))
        report = build(engine.store, bar, rid, fp, engine.run, engine.policy().run_drawdown_end_pct, strategies,
                       root / "data" / "trials.jsonl", config_version=rules.version if rules is not None else 0)
        engine.store.insert("gate_report", {"run_id": rid, "session": str(engine.day.day), "bar_sha": bar.sha,
                                            "fingerprint": fp, "overall": report["overall"], "report": report},
                            returning=False)
        return report
    except Exception:
        log.exception("gate report failed (trading unaffected)")
        return None


def load_trials(store, trials_file: Path | None) -> dict[str, list[float]]:
    """Daily Sharpe per research trial, by strategy: the kaibot.trial table plus the local data/trials.jsonl."""
    rows = []
    try:
        rows += store.select("trial", {"select": "strategy,metrics"})
    except Exception:
        log.warning("trial table unreadable; using the local trial log only")
    if trials_file is not None and trials_file.exists():
        import json

        for line in trials_file.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    out: dict[str, list[float]] = {}
    for t in rows:
        v = (t.get("metrics") or {}).get("daily_sharpe_ann")
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            out.setdefault(t.get("strategy") or "?", []).append(v / math.sqrt(252))
    return out


def build(store, bar: Bar, run_id: int, fp: str, run: dict | None, dd_cap_pct: float, strategies: list[str],
          trials_file: Path | None = None, config_version: int | None = None) -> dict:
    q = {"run_id": f"eq.{run_id}"}
    trades = store.select("trade", {"select": "strategy,entry_ts,exit_ts,pnl_usd,config_version", **q})
    sessions = store.select("daily_result", {"select": "session,spy_ret", **q})
    random_days = store.select("gate_random", {"select": "strategy,session,fingerprint,totals,config_version", **q})
    starts = [{"ts": e.get("ts"), "fingerprint": (e.get("payload") or {}).get("fingerprint")}
              for e in store.select("event", {"select": "ts,payload", "type": "eq.engine_start", **q})]
    trials = load_trials(store, trials_file)
    return evaluate(bar, strategies=strategies, trades=trades, sessions=sessions, random_days=random_days,
                    starts=starts, fingerprint_now=fp, amount=float((run or {}).get("amount_usd") or 0) or 1.0,
                    dd_cap_pct=dd_cap_pct, trials=trials, config_version=config_version)


# ---------------- helpers ----------------
def _ts(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    t = pd.Timestamp(v)
    return (t.tz_localize("UTC") if t.tzinfo is None else t).to_pydatetime()


def _et_date(t: datetime | None) -> date:
    if t is None:
        return date.min
    return pd.Timestamp(t).tz_convert("America/New_York").date()


def _d(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")
