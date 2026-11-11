"""Story S39: alt-data ablation for admission. Offline: fake lookups and a temp alt store, no network."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from kaibot.evaluation.ablation import (
    MIN_HISTORY_DAYS,
    feature_value,
    parse_config,
    random_subset_totals,
    run_ablation,
)
from kaibot.ingest.alt import finra, store

ROOT = Path(__file__).resolve().parents[1]

CFG = """
seed: 7
n_random: 1000
rules:
  - name: x_low
    feature: sig.x
    op: "<="
    value: 1.0
    hypothesis: high x means a bad trade
"""


def make_trades(n=400, days=80, seed=0, effect=0.0, known_share=1.0):
    """n trades over `days` sessions; feature x ~ N(0,1); R = noise - effect * (x > 1)."""
    rng = np.random.default_rng(seed)
    sessions = pd.bdate_range("2026-05-01", periods=days)
    d = sessions[rng.integers(0, days, n)]
    entry = (d.tz_localize("America/New_York") + pd.Timedelta(hours=10) + pd.to_timedelta(np.arange(n) % 300, "s"))
    x = rng.normal(size=n)
    r = rng.normal(0.05, 1.0, n) - effect * (x > 1.0)
    known = rng.random(n) < known_share
    syms = [f"S{i % 25}" for i in range(n)]
    df = pd.DataFrame({"symbol": syms, "entry_ts": entry.tz_convert(UTC), "r": r, "pnl_usd": r * 100})
    feats = {(s, pd.Timestamp(t)): ({"sig": {"x": float(v)}} if k else {"sig": {"x": None}})
             for s, t, v, k in zip(syms, df["entry_ts"], x, known, strict=True)}
    return df, (lambda sym, ts: feats[(sym, pd.Timestamp(ts))])


def verdict(res, name="x_low"):
    return next(p for p in res["rules"] if p["name"] == name)


def test_config_sha_and_thresholds_cannot_be_loosened():
    c = parse_config(CFG)
    assert c.sha256 == hashlib.sha256(CFG.encode()).hexdigest() and c.rules[0].op == "<="
    with pytest.raises(ValueError):
        parse_config(CFG.replace("n_random: 1000", "n_random: 50"))
    with pytest.raises(ValueError):
        parse_config(CFG.replace('"<="', '"~"'))


def test_feature_value_paths():
    f = {"a": {"b": 2, "n": None, "s": "x", "nan": float("nan")}, "z": None}
    assert feature_value(f, "a.b") == 2.0
    for p in ("a.n", "a.s", "a.nan", "a.missing", "z.b", "q"):
        assert feature_value(f, p) is None


def test_random_subsets_are_vectorised_without_replacement():
    r = np.arange(10, dtype=float)
    tot = random_subset_totals(r, 10, 50, np.random.default_rng(0))
    assert np.allclose(tot, 45.0)
    tot = random_subset_totals(r, 3, 2000, np.random.default_rng(0))
    assert tot.min() >= 0 + 1 + 2 and tot.max() <= 9 + 8 + 7 and abs(tot.mean() - 13.5) < 0.3


def test_deterministic():
    df, look = make_trades(effect=0.3)
    cfg = parse_config(CFG)
    a, b = run_ablation(df, look, cfg, git_sha="abc"), run_ablation(df, look, cfg, git_sha="abc")
    assert json.dumps(a, allow_nan=False) == json.dumps(b, allow_nan=False)
    assert a["config_sha256"] == cfg.sha256 and a["git_sha"] == "abc" and a["thresholds"]["min_history_days"] == 63


def test_planted_informative_feature_is_admitted():
    df, look = make_trades(n=600, effect=1.5, seed=1)
    res = run_ablation(df, look, parse_config(CFG))
    p = verdict(res)
    assert p["verdict"] == "admit", p
    assert p["percentile"] >= 95 and p["kept_mean_r"] > res["all_trades"]["mean_r"] and p["removed"] > 0
    assert res["admitted"] == ["x_low"]


def test_noise_feature_is_rejected_and_false_admit_rate_is_about_5pct():
    df, look = make_trades(seed=3, effect=0.0)
    p = verdict(run_ablation(df, look, parse_config(CFG)))
    assert p["verdict"] == "reject" and p["percentile"] < 95
    admits = 0
    seeds = range(200)
    for s in seeds:
        df, look = make_trades(n=300, seed=1000 + s, effect=0.0)
        v = verdict(run_ablation(df, look, parse_config(CFG.replace("seed: 7", f"seed: {s}"))))["verdict"]
        assert v != "insufficient"
        admits += v == "admit"
    assert admits / len(seeds) <= 0.08, admits  # nominal 5%; binomial sd ~1.5% at 200 draws


def test_insufficient_history():
    df, look = make_trades(n=400, days=MIN_HISTORY_DAYS - 3, effect=1.5)  # strong effect, too few days
    p = verdict(run_ablation(df, look, parse_config(CFG)))
    assert p["history_days"] < 63 and p["verdict"] == "insufficient"
    df, look = make_trades(n=90, days=80, effect=1.5)  # enough days, too few known trades
    assert verdict(run_ablation(df, look, parse_config(CFG)))["verdict"] == "insufficient"
    res = run_ablation(df, lambda s, t: {}, parse_config(CFG))  # zero alt history
    p = verdict(res)
    assert p["verdict"] == "insufficient" and p["known"] == 0 and p["coverage"] == 0 and p["kept"] == 90


def test_unknown_values_are_kept_and_coverage_reported():
    df, look = make_trades(n=500, effect=1.0, known_share=0.6, seed=5)
    p = verdict(run_ablation(df, look, parse_config(CFG)))
    xs = [feature_value(look(s, t.to_pydatetime()), "sig.x") for s, t in zip(df["symbol"], df["entry_ts"],
                                                                               strict=True)]
    n_unknown = sum(x is None for x in xs)
    n_pass = sum(x is not None and x <= 1.0 for x in xs)
    assert p["known"] == 500 - n_unknown and abs(p["coverage"] - (500 - n_unknown) / 500) < 1e-4
    assert p["kept"] == n_unknown + n_pass and p["kept_known"] == n_pass


def test_lookup_errors_mean_unknown_not_crash():
    df, _ = make_trades(n=120)

    def boom(s, t):
        raise OSError("disk")

    res = run_ablation(df, boom, parse_config(CFG))
    assert res["lookup_errors"] == res["lookups"] and verdict(res)["verdict"] == "insufficient"


def test_lookup_is_called_at_decision_ts_not_later():
    df, _ = make_trades(n=50)
    df["decision_ts"] = df["entry_ts"] - pd.Timedelta(minutes=1)
    seen = []
    run_ablation(df, lambda s, t: seen.append((s, t)) or {}, parse_config(CFG))
    want = {(s, t.to_pydatetime()) for s, t in zip(df["symbol"], df["decision_ts"], strict=True)}
    assert set(seen) == want and all(t.tzinfo is not None for _, t in seen)
    bad = df.assign(decision_ts=df["entry_ts"] + pd.Timedelta(minutes=1))
    with pytest.raises(ValueError):
        run_ablation(bad, lambda s, t: {}, parse_config(CFG))


# ---------- the script, end to end on a temp alt store ----------

def _script():
    spec = importlib.util.spec_from_file_location("run_ablation", ROOT / "scripts" / "run_ablation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _finra_rows(root, symbol, days, last_short=600, last_avail=None):
    for i in range(days):
        d = date(2026, 10, 1) + timedelta(days=i)
        avail = last_avail if (i == days - 1 and last_avail) else finra.publication_ts(d)
        short = last_short if i == days - 1 else 400
        t = pa.table({"trade_date": [d], "symbol": [symbol], "short_volume": [short], "short_exempt_volume": [0],
                      "total_volume": [1000], "market": ["N"], "available_ts": pa.array([avail], store.TS),
                      "ingested_ts": pa.array([avail], store.TS)}, schema=finra.SCHEMA)
        store.append_versions(root, finra.SOURCE, d, t, finra.KEY, finra.VALUES)
    return date(2026, 10, 1) + timedelta(days=days - 1)


def test_pit_value_available_after_decision_is_not_used(tmp_path):
    mod = _script()
    decision = datetime(2026, 10, 23, 14, 0, tzinfo=UTC)
    # the 22nd row (trade date 2026-10-22, ratio 0.9) only became available AFTER the decision
    _finra_rows(tmp_path, "ACME", 22, last_short=900, last_avail=decision + timedelta(hours=1))
    look = mod.alt_lookup(tmp_path)
    with mod.cached_alt_reads():
        before = look("ACME", decision)["short_volume"]
        after = look("ACME", decision + timedelta(hours=2))["short_volume"]
    assert before["trade_date"] == "2026-10-21" and abs(before["svr"] - 0.4) < 1e-9
    assert after["trade_date"] == "2026-10-22" and abs(after["svr"] - 0.9) < 1e-9  # the cache did not break PIT


def test_script_with_zero_alt_history_reports_insufficient_and_logs_a_trial(tmp_path):
    mod = _script()
    days = pd.bdate_range("2026-06-01", periods=70)
    rows = [{"symbol": f"S{i % 7}", "date": str(days[i % 70].date()), "entry_m": 30 + i % 100, "exit_m": 200,
             "qty": 10, "entry": 100, "exit": 101, "stop": 99, "target": 102, "reason": "target",
             "pnl": float(np.sin(i)), "risk": 10.0} for i in range(150)]
    csv = tmp_path / "trades_balanced_test.csv"
    pd.DataFrame(rows).to_csv(csv, index=False)
    out = tmp_path / "out"
    res = mod.main(["--trades", str(csv), "--data", str(out)])
    assert res["trades"] == 150 and res["summary"]["insufficient"] == len(res["rules"]) > 0
    assert all(p["coverage"] == 0 and p["kept"] == 150 for p in res["rules"])
    files = list((out / "ablation").glob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["config_sha256"] == res["config_sha256"]
    trial = [json.loads(x) for x in (out / "trials.jsonl").read_text().splitlines()]
    assert len(trial) == 1 and trial[0]["kind"] == "ablation" and trial[0]["n_trials"] == 0
    assert trial[0]["config_sha256"] == res["config_sha256"]
    # replay minutes -> ET timestamps: entry_m 30 on 2026-06-01 is 10:00 ET = 14:00 UTC, decision 1 min earlier
    t = mod.normalize_trades(pd.DataFrame(rows[:1]))
    assert t["entry_ts"].iloc[0] == pd.Timestamp("2026-06-01 14:00", tz="UTC")
    assert t["decision_ts"].iloc[0] == pd.Timestamp("2026-06-01 13:59", tz="UTC") and t["r"].iloc[0] == 0.0
