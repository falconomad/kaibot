"""Story S13: SPY last-30-minute momentum at Balanced and Hunter: at most one trade a day, exit market-on-close."""

from __future__ import annotations

import numpy as np
import pandas as pd
from test_engine import make, run_day, synth_frames

from kaibot.replay.data import N_MIN, SymbolDays
from kaibot.strategies.spy_momentum import ENTRY_M, inputs_from_days

STRATS = {"conservative": ["dip_reversal"], "balanced": ["dip_reversal", "spy_late_momentum"],
          "aggressive": ["dip_reversal", "spy_late_momentum"]}


def strong_morning(frames):
    """Make SPY's last day open with a big rally into 10:00 (well above +0.5 sigma)."""
    f = frames["SPY"].copy()
    last = f["ts"].dt.date == f["ts"].dt.date.max()
    idx = np.flatnonzero(last.to_numpy())
    base = float(f.loc[idx[0] - 1, "c"])
    lift = np.r_[np.linspace(1.0, 1.02, 30), np.full(len(idx) - 30, 1.02)]
    for col in ("o", "h", "l", "c", "vw"):
        f.loc[idx, col] = base * lift * (f.loc[idx, col].to_numpy() / f.loc[idx, col].to_numpy()[0])
    frames["SPY"] = f
    return frames


def no_dips(eng):
    eng.market.candidates_at = lambda m: pd.DataFrame()
    eng.market.regime_at = lambda m: (False, 18.0)


def test_inputs_are_point_in_time():
    rng = np.random.default_rng(0)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.0005, (80, N_MIN)), axis=1))
    sd = SymbolDays("SPY", np.arange(80).astype("datetime64[D]"), c, c, c, c, np.ones_like(c), c)
    r1, s1 = inputs_from_days(sd, 79)
    c2 = c.copy()
    c2[79, 30:] *= 1.5  # after 10:00 today: must not matter
    r2, s2 = inputs_from_days(SymbolDays("SPY", sd.dates, c2, c2, c2, c2, sd.v, c2), 79)
    assert r1 == r2 and s1 == s2 and s1 > 0
    assert abs(r1 - (c[79, 29] / c[78, -1] - 1)) < 1e-12


def test_buys_spy_at_1530_once_and_exits_market_on_close(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, strong_morning(synth_frames()), start_level="balanced")
    eng.strategies = STRATS
    no_dips(eng)
    run_day(eng, broker, clock, open_utc)
    trades = [t for t in store.tables.get("trade", []) if t["strategy"] == "spy_late_momentum"]
    assert len(trades) == 1
    t = trades[0]
    assert t["symbol"] == "SPY" and t["exit_reason"] == "moc"
    sig = [s for s in store.tables["signal"] if s["strategy"] == "spy_late_momentum"]
    assert len(sig) == 1 and sig[0]["ts"] >= open_utc + pd.Timedelta(minutes=ENTRY_M + 1)
    moc = [o for o in broker.orders.values() if o.get("time_in_force") == "cls"]
    assert len(moc) == 1 and moc[0]["status"] == "filled"
    assert broker.pos == {}


def test_not_at_steady(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, strong_morning(synth_frames()), start_level="conservative")
    eng.strategies = STRATS
    no_dips(eng)
    run_day(eng, broker, clock, open_utc)
    assert not [t for t in store.tables.get("trade", []) if t["strategy"] == "spy_late_momentum"]


def test_at_most_one_decision_and_threshold_respected(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames(seed=3), start_level="aggressive")
    eng.strategies = STRATS
    no_dips(eng)
    eng.market.symbol_days_orig = eng.market.symbol_days
    run_day(eng, broker, clock, open_utc)
    sigs = [s for s in store.tables.get("signal", []) if s["strategy"] == "spy_late_momentum"]
    assert len(sigs) <= 1  # at most one decision a day, logged with its z whether or not it trades
    trades = [t for t in store.tables.get("trade", []) if t["strategy"] == "spy_late_momentum"]
    if sigs and sigs[0]["features"]["z"] <= 0.5:
        assert not trades


def test_moc_rejected_falls_back_to_the_1555_flatten(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, strong_morning(synth_frames()), start_level="balanced")
    eng.strategies = STRATS
    no_dips(eng)

    def reject(*a, **k):
        raise RuntimeError("422 market-on-close cutoff")

    broker.submit_moc_sell = reject
    run_day(eng, broker, clock, open_utc)
    t = [t for t in store.tables["trade"] if t["strategy"] == "spy_late_momentum"][0]
    assert t["exit_reason"] == "time" and broker.pos == {}


def test_regime_off_vetoes_momentum(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, strong_morning(synth_frames()), start_level="balanced")
    eng.strategies = STRATS
    eng.market.candidates_at = lambda m: pd.DataFrame()
    eng.market.regime_at = lambda m: (True, 40.0)  # crisis
    run_day(eng, broker, clock, open_utc)
    sig = [s for s in store.tables["signal"] if s["strategy"] == "spy_late_momentum"]
    assert len(sig) == 1 and sig[0]["rules_veto"] == "regime off"
    assert not [t for t in store.tables.get("trade", []) if t["strategy"] == "spy_late_momentum"]


def test_profiles_wire_momentum_to_balanced_and_hunter():
    from kaibot.risk.profiles import load_strategies

    st = load_strategies()
    assert "spy_late_momentum" not in st["conservative"]
    assert "spy_late_momentum" in st["balanced"] and "spy_late_momentum" in st["aggressive"]
