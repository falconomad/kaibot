"""Issue #201: the SPY trend filter of docs/prereg/spy_trend_filter.md. Synthetic, seeded SPY bars only; no network,
no real data."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kaibot.evaluation import prereg
from kaibot.ingest.daily_bars import actions_table, bars_path, bars_table, write_table
from kaibot.ingest.daily_store import DailyPanel, DailyStore
from kaibot.replay import spy_trend as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import spy_trend as script  # noqa: E402


def sessions(start="2016-01-04", end="2026-11-06") -> np.ndarray:
    return pd.bdate_range(start, end).to_numpy(dtype="datetime64[D]")


def closes(n: int, seed: int = 7) -> np.ndarray:
    """A seeded walk with a bull run, a long bear market and a recovery, so the filter has something to do."""
    rng = np.random.default_rng(seed)
    drift = np.full(n, 0.0005)
    drift[n // 3:n // 3 + 250] = -0.0025
    drift[n // 3 + 250:n // 3 + 400] = 0.002
    return 200.0 * np.exp(np.cumsum(drift + rng.normal(0, 0.009, n)))


def div_days(dates: np.ndarray) -> list:
    """One cash dividend near the 20th of March, June, September and December."""
    out = []
    for y in range(2016, 2027):
        for m in (3, 6, 9, 12):
            t = np.datetime64(f"{y}-{m:02d}-20", "D")
            k = int(np.searchsorted(dates, t))
            if k < len(dates):
                out.append(dates[k])
    return out


@pytest.fixture(scope="module")
def market():
    dates = sessions()
    c = closes(len(dates))
    o = np.r_[c[0], c[:-1]] * 1.0003
    actions = [("SPY", d, None, 1.5) for d in div_days(dates)]
    panel = DailyPanel.make(dates, ["SPY"], o[:, None], (c * 1.01)[:, None], (c * 0.99)[:, None], c[:, None],
                            np.full((len(dates), 1), 8e7), actions=actions)
    return panel, st.SpyData.from_panel(panel)


def window(d: st.SpyData) -> tuple[int, int]:
    return d.index_from("2017-05-01"), d.index_upto("2026-11-06")


# ---------------------------------------------------------------- the pre-registration came first


def test_the_pre_registration_passes_gate_a_with_five_neighbours():
    p = prereg.load(st.RULE, ROOT, committed=False)
    assert p.status == "pre-registered" and p.neighbours == tuple(st.NEIGHBOURS)
    assert len(p.neighbours) <= prereg.MAX_NEIGHBOURS


# ---------------------------------------------------------------- the rule


def test_checks_are_the_last_session_of_each_month_and_week():
    d = sessions("2026-01-01", "2026-03-31")
    m = d[st.check_flags(d, "month")]
    assert [str(x) for x in m] == ["2026-01-30", "2026-02-27"], "the last stored session is never a check"
    w = d[st.check_flags(d, "week")]
    assert all(pd.Timestamp(x).dayofweek == 4 for x in w)


def test_the_ten_month_average_is_the_mean_of_ten_month_end_closes(market):
    _, d = market
    avg = st.trend(d, st.BASE)
    me = np.flatnonzero(st.check_flags(d.dates, "month"))
    k = me[20]
    assert avg[k] == pytest.approx(d.sig[me[11:21]].mean())
    assert np.isnan(avg[me[8]]) and np.isfinite(avg[me[9]]), "10 month-ends are needed"
    mid = me[20] - 3  # not a month-end: its close plus the 9 previous month-ends (the weekly neighbour)
    assert avg[mid] == pytest.approx((d.sig[mid] + d.sig[me[11:20]].sum()) / 10)
    s200 = st.trend(d, st.NEIGHBOURS["sma200d"])
    assert s200[k] == pytest.approx(d.sig[k - 199:k + 1].mean())


def test_the_state_changes_only_at_checks_and_strictly_above(market):
    _, d = market
    s = st.states(d, st.BASE)
    flips = np.flatnonzero(np.diff(s.astype(int))) + 1
    assert len(flips) >= 2, "the synthetic bear market must make the rule switch"
    assert st.check_flags(d.dates, "month")[flips].all()
    avg = st.trend(d, st.BASE)
    for i in flips:
        assert s[i] == (d.sig[i] > avg[i])


def test_the_band_keeps_the_state_inside_it():
    dates = sessions("2016-01-04", "2019-12-31")
    c = np.full(len(dates), 100.0)
    d = st.SpyData(dates, c, c, np.full(len(dates), 1e7), np.ones(len(dates)), np.zeros(len(dates)), c)
    assert not st.states(d, st.BASE).any(), "a close equal to the average is out"
    d.sig = c * np.where(np.arange(len(c)) > len(c) - 40, 1.005, 1.0)  # a late 0.5% rise: base in, band not
    assert st.states(d, st.BASE)[-1] and not st.states(d, st.NEIGHBOURS["band1"])[-1]


def test_the_fill_is_one_session_after_the_decision(market):
    _, d = market
    i0, i1 = window(d)
    s = st.states(d, st.BASE)
    t = st.targets(s, i0, i1)
    assert len(t) == i1 - i0 + 1 and (t == s[i0 - 1:i1]).all()
    b = st.run_account(d, t, i0, i1)
    first = b.trips[0]["entry_i"]
    assert s[first - 1] and (first == i0 or not s[first - 2]), "bought at the close after the deciding check"


# ---------------------------------------------------------------- the account


def test_always_in_at_zero_cost_is_spy_total_return(market):
    panel, d = market
    i0, i1 = window(d)
    b = st.run_account(d, np.ones(i1 - i0 + 1, bool), i0, i1, mult=0.0)
    r = st.returns(b).to_numpy()
    tr = panel.total_return("SPY")[i0:i1 + 1]
    assert r[0] == pytest.approx(0.0), "bought at the first close"
    assert np.allclose(r[1:], tr[1:], atol=1e-12), "dividends reinvested at the ex-date close = total return"
    assert b.dividends > 0 and len(b.trips) == 1 and b.trips[0]["open_at_end"]


def test_costs_are_charged_and_scale(market):
    _, d = market
    i0, i1 = window(d)
    t = st.targets(st.states(d, st.BASE), i0, i1)
    eq = {(m, x): st.run_account(d, t, i0, i1, costs=m, mult=x).equity for m in ("v1", "v2") for x in (0, 1, 2, 3)}
    for m in ("v1", "v2"):
        assert eq[(m, 0)] > eq[(m, 1)] > eq[(m, 2)] > eq[(m, 3)]
    stress = st.run_account(d, t, i0, i1, fill="stress")
    assert stress.costs > st.run_account(d, t, i0, i1).costs
    b = st.run_account(d, t, i0, i1)
    df = st.daily_frame(b)
    assert np.allclose(df["end"].iloc[:-1].to_numpy(), df["start"].iloc[1:].to_numpy()), "one continuous account"
    assert (df["exposure"] <= 1 + 1e-9).all() and (df["end"] > 0).all(), "never more than the cash"


def test_cash_returns_only_touch_cash(market):
    _, d = market
    i0, i1 = window(d)
    n = i1 - i0 + 1
    cash = np.full(len(d.dates), 0.0001)
    out = st.run_account(d, np.zeros(n, bool), i0, i1, cash_returns=cash)
    assert out.equity == pytest.approx(100_000 * 1.0001 ** n)


# ---------------------------------------------------------------- mark 3: the random-timing control


def test_random_paths_keep_the_rules_round_trips_and_durations():
    rng = np.random.default_rng(1)
    durations = [40, 300, 5, 120, 61]
    for _ in range(50):
        path, miss = st.random_target(800, durations, rng)
        assert miss == 0
        got = sorted(x for _, x in st.spells(path))
        assert got == sorted(durations), "every duration once, never merged with a neighbour"
    path, miss = st.random_target(800, [399, 399], rng)
    assert miss == 0 and sorted(x for _, x in st.spells(path)) == [399, 399], "a tight fit is always placed"
    path, miss = st.random_target(100, [60, 60], rng)
    assert miss == 2, "durations that cannot fit are counted, so the control is not matched"
    starts = [st.spells(st.random_target(50, [10], np.random.default_rng(s))[0])[0][0] for s in range(2000)]
    assert min(starts) == 0 and max(starts) == 40 and abs(np.mean(starts) - 20) < 1, "uniform over the window"


def test_the_control_is_seeded_matched_and_judged_by_gate_v2(market):
    _, d = market
    i0, i1 = window(d)
    t = st.targets(st.states(d, st.BASE), i0, i1)
    a = st.random_timing_control(d, t, i0, i1, runs=30, seed=201)
    b = st.random_timing_control(d, t, i0, i1, runs=30, seed=201)
    assert a == b and a["matched"] and a["round_trips"] == len(st.spells(t))
    assert a["sessions_in"] == int(t.sum()) and 0 <= a["percentile"] <= 100
    from kaibot.evaluation.gate_v2 import GateV2, mark_3

    m = mark_3(GateV2.load(ROOT), a)
    assert m["met"] is None and "500 needed" in m["why"], "fewer than 500 runs never passes"


# ---------------------------------------------------------------- the script, end to end on a tiny store


def write_store(root: Path, panel: DailyPanel) -> None:
    rows = [{"t": f"{pd.Timestamp(x).date()}T12:00:00Z", "o": float(panel.o[i, 0]), "h": float(panel.h[i, 0]),
             "l": float(panel.l[i, 0]), "c": float(panel.c[i, 0]), "v": float(panel.v[i, 0])}
            for i, x in enumerate(panel.dates)]
    write_table(bars_table(rows, pd.Timestamp(panel.dates[-1]).date()), bars_path(root, "SPY"), {"rows": len(rows)})
    acts = []
    for i in np.flatnonzero(panel.div[:, 0] > 0):
        ex = pd.Timestamp(panel.dates[i]).date()
        acts.append({"key": "SPY", "symbol": "SPY", "type": "cash_dividend", "ex_date": ex, "ratio": None,
                     "cash": float(panel.div[i, 0]), "special": False, "foreign": False, "process_date": ex,
                     "record_date": ex, "payable_date": ex, "cusip": "", "id": f"d{i}", "dup": False, "raw": "{}"})
    write_table(actions_table(acts), root / "corporate_actions.parquet", {})


def test_the_experiment_runs_from_the_daily_store_and_writes_one_table(market, tmp_path):
    panel, _ = market
    write_store(tmp_path / "daily", panel)
    store = DailyStore(tmp_path / "daily")
    assert np.allclose(store.panel(["SPY"]).div[:, 0], panel.div[:, 0]), "the fixture store reads back"
    args = ["--data", str(tmp_path), "--control-runs", "20"]
    assert script.main(["--has", *args]) == 1
    assert script.main(args) == 0
    assert script.main(["--has", *args]) == 0
    assert script.main(["--summary", "--data", str(tmp_path)]) == 0
    text = (tmp_path / "reports" / script.SUMMARY).read_text()
    for want in ("BENCHMARK: SPY buy-and-hold (total return", "random-timing accounts", "### Cost model v1",
                 "### Cost model v2", "Mark 3", "sma250d", "2017", "2026", "T-bill variant: skipped"):
        assert want in text, want
    assert "fails Gate B" in text or "cannot pass here" in text, "a research run never passes"
    rows = [json.loads(x) for x in (tmp_path / "trials.jsonl").read_text().splitlines()]
    assert sum(r["n_trials"] for r in rows) == 1 + len(st.NEIGHBOURS)
    sha = prereg.require(st.RULE, ROOT)
    assert {r["prereg_sha256"] for r in rows} == {sha} and {r["strategy"] for r in rows} == {st.RULE}
    rep = json.loads(sorted((tmp_path / "reports").glob("spy_trend_*.json"))[0].read_text())
    assert rep["oos_window"][0] == "2017-05-01" and rep["oos_window"][1] == "2026-11-06"
    g = rep["gate_b"]["v1"]
    assert g["marks"]["mark_5"]["numbers"]["source"] == "this run is on the point-in-time universe"
    assert set(g["marks"]["mark_6"]["numbers"]["declared"]) == set(st.NEIGHBOURS)
    assert any("research_harness" in r for r in g["provisional_reasons"])
    assert math.isfinite(rep["benchmarks"]["spy_total_return"]["cagr_pct"])
