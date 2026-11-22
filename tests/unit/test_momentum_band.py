"""M11 ticket 2.9 (#460): `trend_momentum_band`, docs/prereg/trend_momentum_band.md with Amendment 1. The ten years
are on the owner's Mac; the engine runs here are on the committed fixture."""

from __future__ import annotations

import json
import math
import sys
from datetime import date, timedelta

import numpy as np
import pytest

from kaibot.engine.config import ROOT
from kaibot.strategies.base import Event
from kaibot.strategies.momentum import DailyContext, is_weekly_decision, ranking, week_number
from kaibot.strategies.momentum_band import NEIGHBOURS, TrendMomentumBand

sys.path.insert(0, str(ROOT / "scripts"))
import trend_momentum_band as script  # noqa: E402


@pytest.fixture(scope="module")
def fx():
    from kaibot.engine import daily_report as dr
    from kaibot.engine.daily import DailyData

    m, info = dr.load_fixture(dr.FIXTURE)
    data = DailyData.from_market(m)
    i0 = int(np.searchsorted(data.dates, np.datetime64(info["window"][0])))
    return data, i0, len(data.dates) - 1


def test_the_frozen_settings_and_neighbours():
    r = TrendMomentumBand()
    assert (r.n_tranches, r.enter_top, r.exit_top, r.market_filter, r.reg_window, r.max_names, r.atr_risk) == (
        4, 0.10, 0.20, True, 90, 30, 0.001)
    assert r.per_tranche == 8 and r.rule == "trend_momentum_band"
    assert set(NEIGHBOURS) == {"noband", "exit30", "onetranche", "twotranches", "nofilter"}
    assert all(n.tranches == {} for n in r.neighbours())
    with pytest.raises(ValueError):
        TrendMomentumBand(n_tranches=3)


def test_each_tranche_decides_in_its_own_slot_of_four_weeks():
    """Amendment 1, item 1: tranche t of n in the slot t x 4 / n; every tranche once every four weeks."""
    days = [date(2024, 1, 1) + timedelta(days=k) for k in range(7 * 16)]
    days = [d for d in days if d.weekday() < 5]
    for n, slots in ((4, {0, 1, 2, 3}), (2, {0, 2}), (1, {0})):
        r = TrendMomentumBand(n_tranches=n)
        seen = {}
        for prev, fill in zip(days, days[1:], strict=False):
            if r.decision_day(prev, fill):
                t = r.tranche_of(fill)
                assert week_number(fill) % 4 == t * 4 // n and is_weekly_decision(prev, fill)
                seen.setdefault(t, []).append(fill)
        assert {t * 4 // n for t in seen} == slots and len(seen) == n
        for fills in seen.values():  # once every four weeks
            assert all((b - a).days == 28 for a, b in zip(fills, fills[1:], strict=False))


def decide(r, data, k, held):
    fill = data.day(k + 1)
    return r.on_event(DailyContext(Event.AFTER_CLOSE, data.day(k), fill, data.view(k), dict(held)))


def test_a_tranche_enters_in_the_top_10_percent_and_holds_to_the_top_20(fx):
    data, i0, i1 = fx
    r = TrendMomentumBand(market_filter=False)
    ks = [k for k in range(i0, i1) if r.decision_day(data.day(k), data.day(k + 1))]
    t = decide(r, data, ks[0], {})
    v = data.view(ks[0])
    score, ok, _ = r.signals(v)
    order = ranking(score, ok)
    top10 = {v.symbols[j] for j in order[:math.ceil(0.10 * len(order))]}
    tr = t.features["tranche"]
    assert set(r.tranches[tr]) <= top10 and len(r.tranches[tr]) <= 8
    assert all(w == pytest.approx(1 / 4) for w in t.weights.values())  # one tranche holds each so far
    # later decisions of the same tranche keep a name while it is in the top 20%, never buy one below the top 10%
    same = [k for k in ks if r.tranche_of(data.day(k + 1)) == tr][1:3]
    for k in same:
        before = list(r.tranches[tr])
        decide(r, data, k, {})
        v = data.view(k)
        score, ok, sell_if = r.signals(v)
        order = ranking(score, ok)
        top20 = {v.symbols[j] for j in order[:math.ceil(0.20 * len(order))]}
        top10 = {v.symbols[j] for j in order[:math.ceil(0.10 * len(order))]}
        for s in r.tranches[tr]:
            assert s in top20 and (s in before or s in top10)


def test_weights_count_the_tranches_and_names_dropped_by_every_tranche_are_sold(fx):
    data, i0, i1 = fx
    r = TrendMomentumBand(market_filter=False)
    r.tranches = {0: ["P01", "P02"], 1: ["P01"], 2: [], 3: []}
    k = next(k for k in range(i0, i1) if r.decision_day(data.day(k), data.day(k + 1))
             and r.tranche_of(data.day(k + 1)) == 2)
    t = decide(r, data, k, {"P01": 10.0, "P02": 5.0, "P99": 1.0})
    assert t.weights["P01"] == pytest.approx(2 / 4) and t.weights["P02"] == pytest.approx(1 / 4)
    assert t.features["sell"] == {"P99": "rank"} and t.resize is True
    assert list(t.weights)[:2] == ["P01", "P02"]  # held names first


def test_through_the_engine_with_simbroker_on_the_fixture(fx):
    from kaibot.engine.daily import run_daily
    from kaibot.risk.profiles import load_profiles

    data, i0, i1 = fx
    pol = load_profiles()["balanced"]
    run = run_daily(TrendMomentumBand(), data, pol, i0, i1)
    assert run.fills and not run.refused and not run.ledger.overdrafts and run.cash_gap < 0.01
    assert not run.guards  # the pre-registered sizing: no outer guard cut
    assert max(r["positions"] for r in run.book.daily) <= 30


def test_the_script_on_the_fixture(tmp_path):
    trials = tmp_path / "t.jsonl"
    out = script.main(["--fixture", "--data", str(tmp_path), "--out", str(tmp_path / "o"), "--trials", str(trials),
                       "--control-runs", "20"])
    rows = [json.loads(x) for x in trials.read_text().splitlines()]
    assert sum(r["n_trials"] for r in rows) == 6 and len(rows) == 11
    rep = out["gate_v3_sleeve"]
    assert rep["tier"] == "evidence_based" and rep["rule"] == "trend_momentum_band"
    assert "20 runs, 2000 needed" in rep["marks"]["s3"]["why"]
    t = out["turnover_mark"]
    assert t["max_ratio"] == pytest.approx(2 / 3) and t["trend_momentum"] > 0 and isinstance(t["met"], bool)
