"""S14 shadow hook inside the live engine: shadow signals are written with mode="shadow", never reach the broker,
get a counterfactual P&L at day end, and an exception in shadow code never breaks Engine.tick()."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
from fakes import FakeStore
from test_engine import DAY, fake_candidate, make, run_day, synth_frames

from kaibot.engine import shadow
from kaibot.replay.data import ET


def orb_frames():
    """Symbol A is a Stock in Play on DAY: 5x opening volume, up opening candle, close above the range at 10:10."""
    f = synth_frames()
    a = f["A"]
    day = a["ts"].dt.tz_convert(ET).dt.date.astype(str) == DAY
    idx = np.flatnonzero(day.to_numpy())
    p0 = float(a["c"].iloc[idx[0] - 1])
    path = np.r_[np.linspace(0.001, 0.004, 5), np.full(35, 0.003), np.linspace(0.006, 0.012, 350)]
    c = p0 * (1 + path)
    o = np.r_[p0, c[:-1]]
    a.loc[idx, "c"], a.loc[idx, "o"] = c, o
    a.loc[idx, "h"], a.loc[idx, "l"] = np.maximum(o, c) + 0.01, np.minimum(o, c) - 0.01
    a.loc[idx, "vw"] = c
    a.loc[idx[:5], "v"] = 1e6
    return f


class Spy:
    """Stands in for the broker during shadow calls: records any access."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        self.calls.append(name)
        raise AssertionError(f"shadow touched the broker: {name}")


def test_shadow_orb_signal_is_never_sent_and_gets_a_counterfactual(tmp_path, monkeypatch):
    eng, broker, clock, store, open_utc = make(tmp_path, orb_frames())
    eng.market.candidates_at = lambda m: pd.DataFrame()  # no live candidates: any order would be shadow's
    spy = Spy()
    real_min, real_end = shadow.on_minute, shadow.on_day_end

    def guarded(fn):
        def run(e, *a):
            b, e.broker = e.broker, spy
            try:
                fn(e, *a)
            finally:
                e.broker = b

        return run

    monkeypatch.setattr(shadow, "on_minute", guarded(real_min))
    monkeypatch.setattr(shadow, "on_day_end", guarded(real_end))
    run_day(eng, broker, clock, open_utc)
    assert spy.calls == [] and broker.orders == {} and "trade" not in store.tables
    sig = [s for s in store.tables["signal"] if s["mode"] == "shadow"]
    assert [(s["strategy"], s["symbol"]) for s in sig] == [("orb_stocks_in_play", "A")]
    s = sig[0]
    f = s["features"]
    assert f["m"] == 40 and f["never_sent"] is True and f["would_be_qty"] > 0 and s["rules_veto"] is None
    assert s["stop"] < s["entry"] and s["target"] is None
    # the gate sized it at the run's level (balanced: 0.5% of $5,000 at risk, <= 15% of the pot)
    assert f["would_be_qty"] * (s["entry"] - s["stop"]) <= 25.0 + 1e-6
    assert f["would_be_qty"] * s["entry"] <= 0.15 * 5000 + 1
    cf = f["counterfactual"]
    assert cf["fill_m"] == 41 and cf["reason"] in ("stop", "time") and np.isfinite(cf["pnl_usd"])
    ev = [e for e in store.tables["event"] if e["type"] == "shadow_session_end"]
    assert len(ev) == 1 and ev[0]["payload"]["orb_stocks_in_play"]["signals"] == 1
    assert not any(e["type"] == "shadow_error" for e in store.tables["event"])


def test_shadow_exceptions_never_break_live_trading(tmp_path, monkeypatch):
    def boom(self, *a, **k):
        raise RuntimeError("shadow bug")

    monkeypatch.setattr(shadow.ShadowRunner, "minute", boom)
    monkeypatch.setattr(shadow.ShadowRunner, "finalize", boom)
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc)
    assert len(store.tables["trade"]) == 1 and store.tables["trade"][0]["exit_ts"] is not None
    assert store.tables["daily_result"][0]["trades"] == 1 and eng.day.finalized
    errs = [e for e in store.tables["event"] if e["type"] == "shadow_error"]
    assert sorted(e["payload"]["where"] for e in errs) == ["day_end", "minute"]  # once per kind per day, not 390x
    assert all("shadow bug" in e["payload"]["error"] for e in errs)


def test_shadow_setup_failure_is_contained(tmp_path, monkeypatch):
    def boom(self, engine):
        raise KeyError("history")

    monkeypatch.setattr(shadow.ShadowRunner, "__init__", boom)
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc, until=80)
    assert len(store.tables["trade"]) == 1


def test_per_minute_cost_300_symbols(tmp_path):
    """Measure the hook on 300 symbols (printed; asserted loosely so CI noise can't flake it)."""
    frames = synth_frames(n_days=75)
    eng, broker, clock, store, open_utc = make(tmp_path, frames)
    mk = eng.market
    rng = np.random.default_rng(0)
    base = frames["A"][frames["A"]["ts"] < pd.Timestamp(open_utc)].reset_index(drop=True)
    syms = [f"S{k:03d}" for k in range(300)]
    ts = pd.Timestamp(open_utc) + pd.to_timedelta(np.arange(390), unit="m")
    for s in syms:
        mk.history[s] = base.assign(c=base["c"] * (1 + rng.normal(0, 0.001)))
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.0007, 390)))
        mk.today_bars[s] = pd.DataFrame({"ts": ts, "o": c, "h": c + 0.02, "l": c - 0.02, "c": c, "v": 2e5, "vw": c})
    mk.symbols, mk.day, mk.open_utc = syms, pd.Timestamp(DAY).date(), open_utc

    class E:
        market, store, fomc = mk, FakeStore(), set()
        day = type("D", (), {"day": pd.Timestamp(DAY).date(), "start_equity": 5000.0, "n_min": 390})()
        policy = staticmethod(lambda: eng.policy())
        now = staticmethod(lambda: open_utc)
        _rid = staticmethod(lambda: 1)

    e = E()
    t0 = time.perf_counter()
    r = shadow._runner(e)
    setup_s = time.perf_counter() - t0
    for m in range(0, 120):
        r.minute(m)
    ms = np.array(r.minutes_ms)
    print(f"\nshadow setup {setup_s:.2f}s; per minute p50 {np.median(ms):.1f} ms, max {ms.max():.1f} ms (300 symbols)")
    assert np.median(ms) < 500


def test_live_panel_matches_replay_arrays_and_fast_grid_matches_from_frame(tmp_path):
    """The hook's panel at minute m equals what replay/data.from_frame gives for bars up to m (with gaps)."""
    from kaibot.engine.shadow import fast_days
    from kaibot.replay.data import from_frame

    frames = orb_frames()
    a = frames["A"]
    frames["A"] = a.drop(index=a.index[a.index % 7 == 3]).reset_index(drop=True)  # IEX-style missing minutes
    eng, broker, clock, store, open_utc = make(tmp_path, frames)
    eng.market.candidates_at = lambda m: pd.DataFrame()
    seen = {}

    def grab(k, e):
        r = getattr(e, "_shadow", None)
        if r is not None and k in (3, 57, 200):
            seen[k] = (r.panel(k), e.market.symbol_days("A", k))

    run_day(eng, broker, clock, open_utc, until=201, hook=grab)
    assert sorted(seen) == [3, 57, 200]
    for m, (p, sd) in seen.items():
        i = list(p.symbols).index("A")
        for col in ("o", "h", "l", "c", "v", "vw"):
            np.testing.assert_allclose(getattr(p, col)[i, : m + 1], getattr(sd, col)[-1, : m + 1])
        assert np.isnan(p.c[i, m + 1:]).all()
    hist = frames["A"][frames["A"]["ts"] < pd.Timestamp(open_utc)]
    fd, ff = fast_days("A", hist), from_frame("A", hist)
    assert (fd.dates == ff.dates).all()
    for col in ("o", "h", "l", "c", "v", "vw"):
        np.testing.assert_array_equal(getattr(fd, col), getattr(ff, col))
