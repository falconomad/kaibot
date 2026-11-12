"""#111 Data part: quoted-spread calibration of cost model v2 from cached Databento bbo-1m minutes. No network."""

from __future__ import annotations

import ast
import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kaibot.broker.costs_v2 import V2Params
from kaibot.ingest import nyse_sessions as nyse
from kaibot.ingest import spread_calibration as sc
from kaibot.ingest.daily_store import DailyStore

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fetch_spread_calibration.py"
DAY = date(2026, 11, 10)  # a Tuesday, full session
EARLY = date(2026, 11, 27)  # day after Thanksgiving: closes 13:00 ET
V2 = V2Params()
T = sc.SpreadTable(V2.buckets, V2.half_bp_floor, V2.tick_half, V2.open_mult, V2.close_mult)


def _script():
    spec = importlib.util.spec_from_file_location("fetch_spread_calibration", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def bbo_frame(day: date, mid: float = 100.0, half_bp: float = 2.0, edge_mult: float = 1.0,
              extra_hours: bool = True) -> pd.DataFrame:
    """One bbo-1m record per minute of `day` (ts_recv = minute end), spread `half_bp` (x `edge_mult` in the first and
    last 5 minutes), plus two pre-market and two after-hours minutes that must be ignored."""
    o, c = nyse.open_utc(day), nyse.close_utc(day)
    starts = [o + timedelta(minutes=i) for i in range(int((c - o).total_seconds() // 60))]
    if extra_hours:
        starts = [o - timedelta(minutes=2), o - timedelta(minutes=1), *starts, c, c + timedelta(minutes=1)]
    rows = []
    for t in starts:
        edge = (t - o) < timedelta(minutes=5) or (c - t) <= timedelta(minutes=5)
        h = half_bp * (edge_mult if edge else 1.0) / 1e4 * mid
        rows.append({"ts_recv": t + timedelta(minutes=1), "ts_event": t, "bid_px_00": mid - h, "ask_px_00": mid + h,
                     "symbol": "X"})
    return pd.DataFrame(rows)


def write_cache(root: Path, symbol: str, df: pd.DataFrame, name: str = "a.parquet") -> None:
    d = root / "databento" / sc.DATASET / sc.SCHEMA / f"symbol={symbol}"
    d.mkdir(parents=True, exist_ok=True)
    df.assign(symbol=symbol).to_parquet(d / name, index=False)


def write_bars(daily: Path, symbol: str, days: list[date], close: float, volume) -> None:
    d = daily / "bars"
    d.mkdir(parents=True, exist_ok=True)
    n = len(days)
    v = list(volume) if isinstance(volume, list | tuple) else [volume] * n
    pq.write_table(pa.table({"date": pa.array(days, pa.date32()), "o": [close] * n, "h": [close] * n,
                             "l": [close] * n, "c": [close] * n, "v": v}), d / f"symbol={symbol}.parquet")


# ---------------------------------------------------------------- buckets


def test_bucket_labels_and_assignment_follow_v2_params():
    assert sc.bucket_labels(T) == [">=500M", "100M-500M", "50M-100M", "<50M"]
    assert sc.bucket_of(1e9, T) == ">=500M"
    assert sc.bucket_of(500e6, T) == ">=500M"
    assert sc.bucket_of(499e6, T) == "100M-500M"
    assert sc.bucket_of(75e6, T) == "50M-100M"
    assert sc.bucket_of(10e6, T) == "<50M"
    for unknown in (math.nan, 0.0, -5.0):
        assert sc.bucket_of(unknown, T) == "<50M"


def test_model_half_bp_matches_v2_and_applies_the_tick_floor():
    assert sc.model_half_bp(1e9, T) == pytest.approx(1.75)
    assert sc.model_half_bp(250e6, T, mid=100.0) == pytest.approx(3.75)
    assert sc.model_half_bp(1e9, T, mid=5.0) == pytest.approx(10.0)  # half a cent on $5 = 10 bp > 1.75 bp
    assert sc.model_half_bp(math.nan, T) == pytest.approx(V2Params().half_bp_floor)


# ---------------------------------------------------------------- measuring


def test_minute_spreads_measures_half_bp_and_windows():
    m, rejected = sc.minute_spreads(bbo_frame(DAY, half_bp=2.0, edge_mult=3.0))
    assert rejected == 0
    assert len(m) == 390  # pre-market and after-hours minutes dropped
    assert m["minute"].min() == pd.Timestamp(nyse.open_utc(DAY))
    counts = m["window"].value_counts().to_dict()
    assert counts == {"open": 5, "close": 5, "midday": 330, "other": 50}
    assert m.loc[m["window"] == "midday", "half_bp"].to_numpy() == pytest.approx(2.0)
    assert m.loc[m["window"] == "open", "half_bp"].to_numpy() == pytest.approx(6.0)
    assert m.loc[m["window"] == "close", "half_bp"].to_numpy() == pytest.approx(6.0)


def test_minute_spreads_early_close_ends_at_13_00():
    m, _ = sc.minute_spreads(bbo_frame(EARLY))
    assert len(m) == 210
    assert m["minute"].max() == pd.Timestamp(nyse.close_utc(EARLY) - timedelta(minutes=1))
    assert (m["window"] == "close").sum() == 5


def test_minute_spreads_rejects_bad_quotes_and_ignores_holidays():
    df = bbo_frame(DAY, extra_hours=False).head(40).copy()
    df.loc[0, "bid_px_00"] = np.nan  # one-sided
    df.loc[1, "ask_px_00"] = df.loc[1, "bid_px_00"]  # locked
    df.loc[2, "ask_px_00"] = df.loc[2, "bid_px_00"] - 0.01  # crossed
    df.loc[3, ["bid_px_00", "ask_px_00"]] = [50.0, 150.0]  # 50% half-spread: bad print
    df.loc[4, "bid_px_00"] = 0.0
    holiday = bbo_frame(date(2026, 11, 26), extra_hours=False).head(10)  # Thanksgiving: not a session
    m, rejected = sc.minute_spreads(pd.concat([df, holiday], ignore_index=True))
    assert rejected == 5
    assert len(m) == 35


def test_minute_spreads_reads_fixed_point_prices_and_ts_event_only():
    df = bbo_frame(DAY, mid=20.0, half_bp=5.0, extra_hours=False).head(60)
    fixed = df.drop(columns=["ts_recv"]).copy()
    for col in ("bid_px_00", "ask_px_00"):
        fixed[col] = np.round(df[col] / sc.FIXED_SCALE).astype("int64")
    fixed.loc[0, "bid_px_00"] = np.iinfo(np.int64).max  # Databento UNDEF_PRICE
    m, rejected = sc.minute_spreads(fixed)
    assert rejected == 1
    assert len(m) == 59
    assert m["mid"].to_numpy() == pytest.approx(20.0)
    assert m["half_bp"].to_numpy() == pytest.approx(5.0)


def test_minute_spreads_empty():
    m, rejected = sc.minute_spreads(pd.DataFrame())
    assert len(m) == 0 and rejected == 0


# ---------------------------------------------------------------- calibrating


def test_calibrate_verdicts_per_symbol_and_bucket(tmp_path):
    write_cache(tmp_path, "BIG", bbo_frame(DAY, half_bp=1.0, edge_mult=2.0))  # model 1.75 / 1.0 -> ok
    write_cache(tmp_path, "BIG", bbo_frame(DAY + timedelta(days=1), half_bp=1.0, edge_mult=2.0), "b.parquet")
    write_cache(tmp_path, "MID", bbo_frame(DAY, half_bp=6.0))  # model 3.75 < 6 -> under
    write_cache(tmp_path, "SML", bbo_frame(DAY, half_bp=2.0))  # model 6.5 / 2 -> conservative
    empty_dir = tmp_path / "databento" / sc.DATASET / sc.SCHEMA / "symbol=NONE"
    empty_dir.mkdir(parents=True)
    (empty_dir / "a.empty").touch()  # an .empty marker only: no data
    cal = sc.calibrate(tmp_path, T, {"BIG": 2e9, "MID": 200e6, "SML": 60e6})
    s = cal.by_symbol.set_index("symbol")
    assert list(s.index) == ["BIG", "MID", "SML"]  # NONE has no Parquet file
    assert s.loc["BIG", "sessions"] == 2
    assert s.loc["BIG", "midday_median_bp"] == pytest.approx(1.0)
    assert s.loc["BIG", "open_ratio"] == pytest.approx(2.0)
    assert s.loc["BIG", "close_ratio"] == pytest.approx(2.0)
    assert s.loc["BIG", "verdict"] == "ok"
    assert s.loc["MID", "verdict"] == "under"
    assert s.loc["MID", "model_over_measured"] == pytest.approx(3.75 / 6.0)
    assert s.loc["SML", "verdict"] == "conservative"
    b = cal.by_bucket.set_index("bucket")
    assert list(b.index) == [">=500M", "100M-500M", "50M-100M"]
    assert b.loc["100M-500M", "verdict"] == "under"
    assert b.loc[">=500M", "model_half_bp"] == pytest.approx(1.75)
    paths = cal.write(tmp_path / "out")
    assert [p.name for p in paths] == ["spreads_by_symbol.csv", "spreads_by_bucket.csv", "calibration.txt"]
    text = paths[2].read_text()
    assert "under-charges: MID" in text
    assert list(pd.read_csv(paths[0]).columns) == list(sc.SYMBOL_COLUMNS)


def test_calibrate_unknown_adv_uses_the_least_liquid_bucket_and_no_data_verdict(tmp_path):
    write_cache(tmp_path, "X", bbo_frame(DAY, half_bp=4.0))
    write_cache(tmp_path, "PRE", bbo_frame(DAY).iloc[:2])  # pre-market minutes only
    cal = sc.calibrate(tmp_path, T)
    s = cal.by_symbol.set_index("symbol")
    assert s.loc["X", "bucket"] == "<50M"
    assert s.loc["X", "model_half_bp"] == pytest.approx(10.0)
    assert s.loc["PRE", "verdict"] == "no_data"
    assert list(cal.by_bucket["bucket"]) == ["<50M"]


def test_calibrate_with_nothing_cached(tmp_path):
    cal = sc.calibrate(tmp_path, T)
    assert len(cal.by_symbol) == 0 and len(cal.by_bucket) == 0
    assert "No cached bbo-1m minutes" in cal.text()


# ---------------------------------------------------------------- ADV and the plan


def test_adv_table_is_point_in_time(tmp_path):
    days = nyse.sessions(date(2026, 9, 1), date(2026, 11, 11))
    write_bars(tmp_path, "A", days, 100.0, 2e6)  # $200M a day
    write_bars(tmp_path, "SHORT", days[-5:], 10.0, 1e6)  # too few bars: unknown
    # the window's own first bar has huge volume: it must not leak into an ADV taken before the window
    write_bars(tmp_path, "B", days, 10.0, [1e6] * (len(days) - 1) + [1e12])
    store = DailyStore(tmp_path)
    adv = sc.adv_table(store, days[-1], T).set_index("symbol")
    assert adv.loc["A", "adv_usd"] == pytest.approx(200e6)
    assert adv.loc["A", "bucket"] == "100M-500M"
    assert adv.loc["B", "adv_usd"] == pytest.approx(10e6)
    assert math.isnan(adv.loc["SHORT", "adv_usd"]) and adv.loc["SHORT", "bucket"] == "<50M"
    assert adv.loc["A", "bars"] == sc.ADV_SESSIONS


def test_plan_sample_spreads_inside_each_bucket_and_skips_unknown():
    advs = pd.DataFrame({"symbol": [f"S{i}" for i in range(10)] + ["U", "SPY"],
                         "adv_usd": [100e6 + i * 40e6 for i in range(10)] + [math.nan, 30e9], "bars": 20})
    advs["bucket"] = [sc.bucket_of(x, T) for x in advs["adv_usd"]]
    s = sc.plan_sample(advs, T, per_bucket=3, exclude=["spy"])
    mid = s[s["bucket"] == "100M-500M"]["symbol"].tolist()
    assert mid == ["S9", "S5", "S0"]  # most liquid, middle, least liquid of S0..S9
    assert "U" not in s["symbol"].tolist() and "SPY" not in s["symbol"].tolist()
    assert sc.plan_sample(advs, T, per_bucket=3).equals(sc.plan_sample(advs, T, per_bucket=3))
    with pytest.raises(ValueError):
        sc.plan_sample(advs, T, per_bucket=0)


def test_fetch_commands_are_dry_runs_over_regular_hours():
    cmds = sc.fetch_commands(["msft", "AAPL"], [DAY, EARLY])
    assert len(cmds) == 2
    assert all("--confirm" not in c and "--schema bbo-1m" in c and "--symbols AAPL,MSFT" in c for c in cmds)
    assert "--start 2026-11-10T14:30Z --end 2026-11-10T21:00Z" in cmds[0]
    assert "--end 2026-11-27T18:00Z" in cmds[1]


# ---------------------------------------------------------------- the script


def test_script_plan_and_report_offline(tmp_path, capsys):
    mod = _script()
    days = nyse.sessions(date(2026, 9, 1), date(2026, 11, 11))
    for sym, vol in (("SPY", 1e8), ("AAA", 1e7), ("BBB", 2e6), ("CCC", 6e5), ("DDD", 1e5)):
        write_bars(tmp_path / "daily", sym, days, 100.0, vol)
    assert mod.main(["--data", str(tmp_path), "plan", "--sessions", "2", "--per-bucket", "1", "--exclude", "SPY"]) == 0
    out = capsys.readouterr().out
    assert "window: 2026-11-10 .. 2026-11-11" in out
    cmds = [line for line in out.splitlines() if "scripts/fetch_databento.py" in line]
    assert len(cmds) == 2 and not any("--confirm" in c for c in cmds)
    sample = pd.read_csv(tmp_path / "databento" / "calibration" / "sample.csv")
    assert sample["symbol"].tolist() == ["AAA", "BBB", "CCC", "DDD"]  # $1B, $200M, $60M, $10M: one per bucket
    write_cache(tmp_path, "AAA", bbo_frame(DAY, half_bp=1.5))
    assert mod.main(["--data", str(tmp_path), "report"]) == 0
    assert ">=500M: model 1.75 bp, measured median 1.50" in capsys.readouterr().out
    assert (tmp_path / "databento" / "calibration" / "spreads_by_bucket.csv").exists()


def test_script_table_is_v2_and_the_table_rejects_unordered_buckets():
    assert _script().v2_table() == T
    assert T.half_bp(math.nan) == V2.half_bp_floor
    with pytest.raises(ValueError):
        sc.SpreadTable(((50e6, 6.5), (500e6, 1.75)), 10.0)
    with pytest.raises(ValueError):
        sc.SpreadTable((), 10.0)


def test_script_plan_without_store(tmp_path, capsys):
    assert _script().main(["--data", str(tmp_path), "plan"]) == 2
    assert "no daily bars" in capsys.readouterr().err


def test_module_never_imports_network_clients():
    imports = []
    for f in (Path(sc.__file__), SCRIPT):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Import):
                imports += [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
    for banned in ("urllib", "requests", "databento", "socket", "http"):
        assert not any(banned in line for line in imports), banned
    assert "costs_v2" not in Path(sc.__file__).read_text()  # live code never imports cost model v2
