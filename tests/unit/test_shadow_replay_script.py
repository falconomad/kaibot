"""scripts/run_shadow_replay.py end to end on synthetic Parquet data (offline): trades, baselines, trial row."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd

from kaibot.replay.data import ET

ROOT = Path(__file__).resolve().parents[2]


def write_bars(data: Path, sym: str, days, seed: int, in_play_every: int = 0):
    rng = np.random.default_rng(seed)
    rows, p = [], 100.0
    for n, d in enumerate(days):
        ts = pd.Timestamp(f"{d.date()} 09:30", tz=ET).tz_convert("UTC") + pd.to_timedelta(np.arange(390), unit="m")
        r = rng.normal(0, 0.0008, 390)
        v = np.full(390, 2e5)
        if in_play_every and n > 20 and n % in_play_every == 0:
            r[:5], r[5:40], r[40] = 0.001, 0.0, 0.004  # up opening candle, then a break at 10:10
            v[:5] *= 4
        c = p * np.exp(np.cumsum(r))
        o = np.r_[p, c[:-1]]
        rows.append(pd.DataFrame({"ts": ts, "o": o, "h": np.maximum(o, c) + 0.01, "l": np.minimum(o, c) - 0.01,
                                  "c": c, "v": v, "vw": c}))
        p = c[-1]
    f = data / "bars_1m" / f"symbol={sym}"
    f.mkdir(parents=True)
    pd.concat(rows).to_parquet(f / "all.parquet")


def test_shadow_replay_script_runs_offline_and_logs_a_trial(tmp_path):
    days = pd.bdate_range("2026-01-02", periods=45)
    for k, s in enumerate(("AAA", "BBB", "SPY")):
        write_bars(tmp_path, s, days, k, in_play_every=3 if s == "AAA" else 0)
    nd = tmp_path / "news" / "symbol=AAA"
    nd.mkdir(parents=True)
    pd.DataFrame({"created_at": pd.to_datetime(["2026-02-10 12:00"], utc=True)}).to_parquet(nd / "all.parquet")
    spec = importlib.util.spec_from_file_location("rsr", ROOT / "scripts" / "run_shadow_replay.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for strat in ("orb", "news_continuation"):
        rep = mod.main(["--strategy", strat, "--data", str(tmp_path), "--symbols", "AAA,BBB", "--start", "2026-02-01",
                        "--runs", "5", "--label", "test"])
        assert rep["strategy"] in ("orb_stocks_in_play", "news_continuation") and rep["mode"] == "shadow"
    assert rep["sessions"] > 0
    trials = [json.loads(x) for x in (tmp_path / "trials.jsonl").read_text().splitlines()]
    assert [t["strategy"] for t in trials] == ["orb_stocks_in_play", "news_continuation"]
    orb_rep = json.loads(sorted((tmp_path / "reports").glob("shadow_replay_balanced_orb_*.json"))[0].read_text())
    assert orb_rep["trades"] > 0 and orb_rep["random_runs"] == 5 and orb_rep["spy_return_pct"] is not None
