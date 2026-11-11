"""Dashboard scan counters: near-misses never change what trades (strict set == candidates() defaults), and the
heartbeat reports bars checked, setups, vetoes and recent near-misses."""

from __future__ import annotations

import numpy as np
import pandas as pd
from test_engine import fake_candidate, make, run_day, synth_frames

from kaibot.engine.core import new_scan, note_scan
from kaibot.engine.market import EMPTY_NEWS, NEAR, STRICT, _near_why
from kaibot.replay.features import candidates


def strict_reference(market, m):
    out = []
    etf_days = {e: market.symbol_days(e, m) for e in set(market.etf_of.values())}
    for s in market.symbols:
        sd, ed = market.symbol_days(s, m), etf_days.get(market.etf_of[s])
        if market.has_today(sd) is None or market.has_today(ed) is None:
            continue
        c = candidates(sd, ed, EMPTY_NEWS)
        if len(c):
            c = c[(c["date"] == np.datetime64(market.day, "D")) & (c["m"] == m)]
            if len(c):
                out.append(c)
    return pd.concat(out, ignore_index=True).sort_values("z") if out else pd.DataFrame()


def test_strict_set_unchanged_by_near_pass(tmp_path):

    def dip(depth):
        def f(c):
            t = np.arange(len(c))
            return c * (1 - depth * np.clip(t / 40, 0, 1) + depth * 0.4 * np.clip((t - 40) / 60, 0, 1))
        return f

    for seed, depth in ((1, 0.03),):
        eng, broker, clock, store, open_utc = make(tmp_path / str(seed), synth_frames(seed=seed, shock=dip(depth)))
        mk = eng.market
        orig = mk.candidates_at
        pairs = []

        def spy(m, orig=orig, mk=mk, pairs=pairs):
            got = orig(m)
            pairs.append((got, strict_reference(mk, m), list(mk.last_near)))
            return got

        mk.candidates_at = spy
        run_day(eng, broker, clock, open_utc)
        for got, ref, near in pairs:
            cols = ["symbol", "m", "z", "resid_z", "turnover", "price", "atr5"]
            a = got[cols].reset_index(drop=True) if len(got) else pd.DataFrame(columns=cols)
            b = ref[cols].reset_index(drop=True) if len(ref) else pd.DataFrame(columns=cols)
            pd.testing.assert_frame_equal(a, b, check_dtype=False)
            assert not {n["symbol"] for n in near} & set(a["symbol"])  # a symbol is either a setup or a near-miss
    assert NEAR["z_max"] > STRICT["z_max"]


def test_near_why_names_the_failed_rule():
    r = pd.Series({"z": -1.2, "resid_z": 1.8, "turnover": 1.0})
    w = _near_why(r)
    assert "dip only -1.20" in w and "sector" in w and "volume" not in w


def test_note_scan_counts_completed_bars_only():
    sc = new_scan()
    note_scan(sc, 8, 59, [], 0)  # minute 8: no 5-minute bar completed
    assert sc["bars"] == 0
    near = [{"symbol": f"S{i}", "m": 9, "z": -1.1, "why": "x"} for i in range(7)]
    note_scan(sc, 9, 59, near, 1)
    assert sc == {**new_scan(), "bars": 1, "checks": 59, "setups": 1, "near": 7, "recent_near": near[-5:]}


def test_heartbeat_reports_scan_and_vetoes(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    fake_candidate(eng, at=(54,))
    eng.market.data.news_count = lambda *a: 3  # every candidate has company news
    run_day(eng, broker, clock, open_utc)
    sc = eng.day.scan
    assert sc["bars"] > 0
    assert sc["setups"] == 1
    assert sc["vetoed"] == {"company news in 24 h": 1}
    assert eng._status(60)["scan"]["vetoed"] == {"company news in 24 h": 1}


def test_near_superset_filters_back_to_strict_exactly():
    """Direct parity with the liquidity floor off (synthetic volume is small): over many shocked days, filtering the
    NEAR pass with STRICT gives exactly the default candidates, and the NEAR pass finds extra rows."""
    from fakes import frame_from_days  # noqa: F401  (keeps the fakes import path consistent)

    from kaibot.replay.data import from_frame

    n_strict = n_near = 0
    for seed in range(1, 9):
        rng = np.random.default_rng(seed)
        frames = synth_frames(seed=seed, shock=lambda c, r=rng: c * (1 - r.uniform(0.004, 0.03)
                                                                     * np.clip(np.arange(len(c)) / 40, 0, 1)))
        sd, ed = from_frame("A", frames["A"]), from_frame("XLK", frames["XLK"])
        ref = candidates(sd, ed, EMPTY_NEWS, min_adv=0)
        wide = candidates(sd, ed, EMPTY_NEWS, min_adv=0, **NEAR)
        if not len(wide):
            assert not len(ref)
            continue
        keep = (wide["z"] <= STRICT["z_max"]) & (wide["resid_z"].abs() <= STRICT["resid_abs_max"]) & (
            wide["turnover"] <= STRICT["turnover_max"])
        if not len(ref):
            assert not keep.any()
        else:
            pd.testing.assert_frame_equal(wide[keep].reset_index(drop=True), ref.reset_index(drop=True))
        n_strict, n_near = n_strict + len(ref), n_near + int((~keep).sum())
    assert n_strict > 0 and n_near > 0
