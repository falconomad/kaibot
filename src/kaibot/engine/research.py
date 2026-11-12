"""One research command for every registered strategy (issue #116): the unchanged `Engine` with `SimBroker` and
`SimClock` through the one runner (engine/runner.py), with only the chosen strategy switched on.

    python -m kaibot.engine.research --strategy dip_reversal
    python -m kaibot.engine.research --strategy orb_stocks_in_play --level aggressive --out /tmp/orb.json
    python -m kaibot.engine.research --list

Bars: a directory in the engine-spine format (`bars.parquet`: symbol, ts, o/h/l/c in cents, v, vw in 1/100 cent;
`meta.json`: sessions, test_days, sector_of, market, fomc, vix_prev_close, news). The default is the committed
fixture tests/fixtures/engine_spine. A live strategy trades alone at the level; a shadow strategy runs alone in
shadow mode next to no live rule (its signals and day-end counterfactuals are in the summary). Multi-day
strategies need the ledger and the order manager (S77-S78) and are refused for now.

Numbers from here are research until a pre-registration exists (docs/prereg/), and read `pnl_venue`: the engine's
reported P&L counts SimBroker's costs twice (review of #136, D4). The engine runs on the local journal
(engine/journal.py, S77) in a temporary file, so the run is the same fold over the same entries as on paper, with
the ledger's mark-to-market close per session in the summary. `--corporate-actions` books dividends and splits
from the daily store (data/daily, docs/ops/daily_bars.md) for symbols held at an open. Nothing is written anywhere
but `--out`; no trial row is logged by this command.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from datetime import UTC, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from kaibot.engine.config import ROOT
from kaibot.engine.journal import Journal, JournalStore
from kaibot.engine.runner import simulation
from kaibot.replay.data import ET
from kaibot.risk.profiles import load_profiles
from kaibot.strategies import registry
from kaibot.strategies.base import Holding, Mode

FIXTURE = ROOT / "tests" / "fixtures" / "engine_spine"


def load_bars(directory: Path) -> dict:
    raw = pd.read_parquet(directory / "bars.parquet")
    meta = json.loads((directory / "meta.json").read_text())
    frames = {}
    for sym, g in raw.groupby("symbol", sort=True):
        f = pd.DataFrame({"ts": pd.to_datetime(g["ts"], utc=True)})
        for k in ("o", "h", "l", "c"):
            f[k] = g[k].to_numpy(dtype=float) / 100.0
        f["v"] = g["v"].to_numpy(dtype=float)
        f["vw"] = g["vw"].to_numpy(dtype=float) / 10_000.0
        frames[str(sym)] = f.sort_values("ts").reset_index(drop=True)
    vix = pd.Series({pd.Timestamp(d): float(v) for d, v in meta["vix_prev_close"].items()}).sort_index()
    news = {s: np.sort(np.array([np.datetime64(t.replace("Z", ""), "s") for t in ts], dtype="datetime64[s]"))
            for s, ts in meta.get("news", {}).items()}
    return {"frames": frames, "sector_of": dict(meta["sector_of"]), "market": meta["market"],
            "fomc": {np.datetime64(d, "D") for d in meta.get("fomc", [])}, "vix": vix, "news": news,
            "days": list(meta.get("test_days") or meta["sessions"])}


def run(strategy: str, *, level: str = "balanced", bars: Path = FIXTURE, days: list[str] | None = None,
        cash: float = 100_000.0, corporate_actions=None) -> dict:
    strat = registry.get(strategy)
    if strat is None:
        raise SystemExit(f"unknown strategy {strategy!r}; registered: {', '.join(registry.names())}")
    if strat.holding is Holding.MULTI_DAY:
        raise SystemExit(f"{strategy} holds for days: the engine trades target lists only after S77-S78")
    data = load_bars(Path(bars))
    days = days or data["days"]
    live = [strategy] if strat.mode is Mode.LIVE else []
    start = pd.Timestamp(f"{days[0]} 09:00", tz=ET).tz_convert(UTC).to_pydatetime() - timedelta(minutes=30)
    with tempfile.TemporaryDirectory() as tmp:
        store = JournalStore(Journal(Path(tmp) / "journal.sqlite"))
        sim = simulation(data["frames"], data["sector_of"], data["market"], store=store, policies=load_profiles(),
                         start=start, cache_dir=Path(tmp), vix=data["vix"], fomc=data["fomc"], news=data["news"],
                         cash=cash, strategies={lv: live for lv in load_profiles()},
                         corporate_actions=corporate_actions)
        sim.engine.shadow_only = (strategy,) if strat.mode is Mode.SHADOW else ()
        store.insert("command", {"kind": "new_run", "payload": {"amount_usd": cash, "level": level},
                                 "idempotency_key": "research", "applied_at": None})
        sim.engine.startup()
        sim.engine.apply_commands()
        log = sim.run(days, strict=False)
        out = summary(strategy, level, days, store, log)
        store.journal.close()
    return out


def summary(strategy: str, level: str, days: list[str], store: JournalStore, log) -> dict:
    t = store.tables
    trades = [r for r in t.get("trade", []) if r["strategy"] == strategy]
    signals = [r for r in t.get("signal", []) if r["strategy"] == strategy]
    cf = [(r.get("features") or {}).get("counterfactual") for r in signals if r["mode"] == "shadow"]
    cf = [c for c in cf if c]
    venue = [float((r.get("features") or {}).get("pnl_venue") or 0) for r in trades if r.get("exit_ts")]
    return {
        "strategy": strategy, "level": level, "sessions": days, "ticks": log.ticks,
        "errors": [f"{ts.isoformat()} {e}" for ts, e in log.errors],
        "signals": len(signals), "vetoes": dict(Counter(r["rules_veto"] for r in signals if r["rules_veto"])),
        "trades": len(trades), "exit_reasons": dict(Counter(r.get("exit_reason") for r in trades)),
        "pnl_venue_usd": round(sum(venue), 2),
        "shadow_counterfactual": {"n": len(cf), "pnl_usd": round(sum(c["pnl_usd"] for c in cf), 2)} if cf else None,
        "halts": [r["kind"] for r in t.get("halt", [])],
        "equity": [{k: r[k] for k in ("session", "start_equity", "close_equity", "close_equity_venue", "cash",
                                      "invested", "dividends")} for r in t.get("ledger_day", [])],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--strategy")
    ap.add_argument("--level", default="balanced", choices=sorted(load_profiles()))
    ap.add_argument("--bars", type=Path, default=FIXTURE)
    ap.add_argument("--days", nargs="*")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--corporate-actions", type=Path, metavar="DAILY_DIR",
                    help="book dividends and splits from this daily store (e.g. data/daily)")
    ap.add_argument("--list", action="store_true", help="list the registered strategies")
    a = ap.parse_args(argv)
    if a.list or not a.strategy:
        for s in registry.STRATEGIES:
            print(f"{s.name:22} {s.mode:7} {s.holding:10} {'retired' if s.name in registry.RETIRED else ''}")
        return 0
    ca = None
    if a.corporate_actions:
        from kaibot.replay.corporate_actions import daily_store_actions

        ca = daily_store_actions(a.corporate_actions)
    rep = run(a.strategy, level=a.level, bars=a.bars, days=a.days, corporate_actions=ca)
    text = json.dumps(rep, indent=1, sort_keys=True, default=str)
    if a.out:
        a.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
