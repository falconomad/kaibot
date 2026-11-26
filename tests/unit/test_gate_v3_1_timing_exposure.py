"""Gate v3.1, ticket 6.3 (#703, decision 18 section 7): a timing sleeve's benchmark holds SPY at its average
time-weighted exposure, so an intraday rule (flat at every close) is compared with SPY, not with T-bills."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_v3 as g3
from kaibot.evaluation import results
from kaibot.replay import spy_noise_band as nb
from kaibot.replay.data import N_MIN, SymbolDays

GATE = g3.GateV3.load(ROOT)
IDX = pd.bdate_range("2017-05-01", "2024-12-31")
SPY = pd.Series(np.random.default_rng(16).normal(0.0005, 0.011, len(IDX)), index=IDX)
BILLS = pd.Series(2.0, index=pd.bdate_range("2017-01-02", "2024-12-31"))


def intraday(share=0.13, edge=0.0, seed=1) -> g3.Account:
    """An intraday SPY rule: invested `share` of each session, flat at every close."""
    own = np.random.default_rng(seed).normal(edge / 252, 0.001, len(IDX))
    return g3.Account(pd.Series(share * SPY.to_numpy() + own, index=IDX), SPY, pd.Series(0.0, index=IDX),
                      intraday_exposure=pd.Series(share, index=IDX), universe="point_in_time")


def test_the_config_names_the_time_weighted_timing_benchmark():
    assert GATE.cfg["sleeve"]["benchmark"] == {"selection": "path_matched",
                                               "timing": "average_matched_time_weighted"}


def test_an_intraday_rule_is_compared_with_spy_at_its_time_in_the_market_not_with_bills():
    acc = intraday(0.13)
    a = g3.active_return(GATE, "timing", acc, BILLS)
    assert float(a["benchmark_exposure"].mean()) == pytest.approx(0.13)
    old = g3.active_return(GATE, "timing", g3.Account(acc.net, SPY, acc.exposure), BILLS)  # close exposure only
    assert float(old["benchmark_exposure"].mean()) == 0.0
    # SPY rose: the rule's 13% of SPY beats bills but not the same 13% of SPY
    assert old["active"].mean() * 252 > 0.005 and abs(a["active"].mean() * 252) < 0.01


def test_the_session_exposure_is_the_larger_of_close_and_intraday():
    e = pd.Series(np.where(np.arange(len(IDX)) % 2, 1.0, 0.0), index=IDX)  # carried every other night
    acc = g3.Account(SPY * 0.5, SPY, e, intraday_exposure=pd.Series(0.4, index=IDX))
    a = g3.active_return(GATE, "timing", acc, None)
    assert float(a["benchmark_exposure"].mean()) == pytest.approx(0.5 * 1.0 + 0.5 * 0.4, abs=1e-3)


def test_selection_sleeves_and_rules_without_an_intraday_series_are_unchanged():
    e = pd.Series(0.6, index=IDX)
    acc = g3.Account(SPY * 0.6, SPY, e)
    with_tw = g3.Account(SPY * 0.6, SPY, e, intraday_exposure=pd.Series(0.9, index=IDX))
    for kind in ("selection",):
        assert g3.active_return(GATE, kind, with_tw, None)["benchmark"].equals(
            g3.active_return(GATE, kind, acc, None)["benchmark"])
    assert float(g3.active_return(GATE, "timing", acc, None)["benchmark_exposure"].mean()) == pytest.approx(0.6)


def test_the_sleeve_report_shows_the_exposure_the_benchmark_holds():
    rep = g3.evaluate_sleeve(GATE, rule="spy_noise_band", account=intraday(0.13), tbill=BILLS)
    st = rep["statistics"]
    assert st["mean_exposure"] == pytest.approx(0.13) and st["mean_close_exposure"] == 0.0
    assert st["mean_intraday_exposure"] == pytest.approx(0.13)
    assert "Mean exposure 13%" in g3.render_sleeve(rep)


def test_the_intraday_exposure_of_an_old_daily_file_comes_from_its_trades_file(tmp_path):
    trades = pd.DataFrame({"date": ["2024-01-02", "2024-01-02", "2024-01-03"],
                           "entry_m": [30, 300, 330], "exit_date": ["2024-01-02", "2024-01-02", "2024-01-04"],
                           "exit_m": [90, 389, 0], "reason": ["band_vwap", "close", "next_open"]})
    f = tmp_path / "trades.csv"
    trades.to_csv(f, index=False)
    days = pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-01-04"])
    tw = results.intraday_exposure(f, days)
    assert tw.tolist() == pytest.approx([(60 + 90) / 390, 60 / 390, 0.0])


def test_the_rescore_reads_it_through_the_report(tmp_path):
    """A report of scripts/spy_noise_band.py's layout: its daily file has no exposure_intraday column."""
    (tmp_path / "r").mkdir()
    pd.DataFrame({"date": ["2024-01-02", "2024-01-03"], "start": [100.0, 101.0], "pnl": [1.0, 0.0],
                  "carry_out": [False, False]}).to_csv(tmp_path / "r" / "daily_x.csv", index=False)
    pd.DataFrame({"date": ["2024-01-02"], "entry_m": [0], "exit_date": ["2024-01-02"], "exit_m": [195],
                  "reason": ["band_vwap"]}).to_csv(tmp_path / "r" / "trades_x.csv", index=False)
    rep = {"set": "spy", "rule": "spy_noise_band",
           "rows": [{"label": "spy_noise_band|0.5 bp", "spec": "spy_noise_band", "cost": "0.5 bp",
                     "files": {"daily": "r/daily_x.csv", "trades": "r/trades_x.csv"}}]}
    f = tmp_path / "spy_balanced_x_20240104T000000.json"
    f.write_text(json.dumps(rep))
    (row,) = results.rows(f, rep)
    d = results.daily(row)
    assert d["exposure"].tolist() == [0.0, 0.0] and d["exposure_intraday"].tolist() == pytest.approx([0.5, 0.0])


def _two_sessions() -> SymbolDays:
    c = np.full((2, N_MIN), 100.0)
    dates = np.array(["2024-01-02", "2024-01-03"], dtype="datetime64[D]")
    return SymbolDays("SPY", dates, c.copy(), c.copy(), c.copy(), c, np.full_like(c, 1e5), c.copy())


def test_the_replay_writes_the_intraday_exposure_of_each_session():
    sd = _two_sessions()
    trips = [nb.Trip(0, 29, 30, 100.0, 0, 90, 100.0, "band_vwap"),
             nb.Trip(0, 299, 300, 100.0, 0, 389, 100.0, "close")]
    book = nb.run_account(sd, trips, 0, 1, 0.0)
    assert book.daily[0]["exposure_intraday"] == pytest.approx((60 + 90) / 390)
    assert book.daily[1]["exposure_intraday"] == 0.0 and not book.daily[0]["carry_out"]
