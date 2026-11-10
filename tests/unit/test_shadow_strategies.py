"""Shadow strategies (S14): ORB on Stocks in Play and news continuation are pure, point-in-time and sized by the
risk gate; counterfactual P&L follows the replay fill model."""

from __future__ import annotations

import numpy as np
import pytest

from kaibot.broker.shadow_costs import sell_fees
from kaibot.engine.shadow import ShadowBook, merged_stats, stats_for_session, step
from kaibot.replay.data import N_MIN, SymbolDays
from kaibot.replay.sim import EXIT_M, buy_px, sell_px
from kaibot.risk.gate import Proposal, RiskPolicy, Side, Snapshot, evaluate
from kaibot.strategies import news_continuation as news
from kaibot.strategies import orb
from kaibot.strategies.shadow_types import Panel, ShadowCandidate, ffill_rows

POL = RiskPolicy(risk_per_trade_pct=0.5, max_position_pct=15, max_open_risk_pct=2.0, max_daily_loss_pct=1.5,
                 max_positions=5, max_entries_per_day=20)


def days(n=30, price=100.0, seed=1, vol=0.001, v=1e5, sym="X"):
    rng = np.random.default_rng(seed)
    c = price * np.exp(np.cumsum(rng.normal(0, vol, (n, N_MIN)), axis=1))
    o = np.c_[c[:, :1], c[:, :-1]]
    h, l = np.maximum(o, c) + 0.02, np.minimum(o, c) - 0.02  # noqa: E741
    dates = np.arange(np.datetime64("2026-01-05"), np.datetime64("2026-01-05") + np.timedelta64(n, "D"))
    return SymbolDays(sym, dates, o, h, l, c, np.full_like(c, v), c.copy())


def orb_day(sd: SymbolDays, rel=5.0, up=True, break_at=40):
    """Make the last session a Stock in Play: heavy first 5 minutes, an up (or down) candle, a break at break_at."""
    p0 = sd.c[-2, -1]
    path = np.r_[np.linspace(0, 0.004 if up else -0.004, 5), np.full(break_at - 5, 0.003 if up else -0.006),
                 np.linspace(0.006, 0.01, N_MIN - break_at)]
    c = p0 * (1 + path)
    o = np.r_[p0, c[:-1]]
    sd.c[-1], sd.o[-1] = c, o
    sd.h[-1], sd.l[-1] = np.maximum(o, c) + 0.01, np.minimum(o, c) - 0.01
    sd.vw[-1] = c
    sd.v[-1, :5] = sd.v[-2, :5] * rel
    return sd


def panel_of(sds: list[SymbolDays], i=-1, upto: int | None = None) -> tuple[Panel, dict]:
    rows = [stats_for_session(sd, len(sd.dates) + i if i < 0 else i) for sd in sds]
    arr = {k: np.stack([getattr(sd, k)[i] for sd in sds]).astype(float) for k in ("o", "h", "l", "c", "v", "vw")}
    if upto is not None:
        for k in arr:
            arr[k][:, upto + 1:] = np.nan
    return Panel(np.array([sd.symbol for sd in sds]), **arr), merged_stats(rows)


# ---------------- ORB ----------------
def test_orb_triggers_once_on_first_close_above_range_with_atr_stop():
    sd = orb_day(days())
    p, st = panel_of([sd])
    hits = [c for m in range(N_MIN) for c in orb.decide(p, st[orb.NAME], m)]
    assert len(hits) == 1
    c = hits[0]
    assert c.m == 40 and c.entry_ref == p.c[0, 40] and c.target is None and c.exit_rule == "time_1555"
    assert c.stop == pytest.approx(c.entry_ref - 0.10 * st[orb.NAME]["atr14"][0])
    assert c.features["rel_vol_5m"] == pytest.approx(5.0)


def test_orb_filters_down_candle_low_rel_vol_and_cheap_stocks():
    p, st = panel_of([orb_day(days(), up=False)])
    assert not any(orb.decide(p, st[orb.NAME], m) for m in range(N_MIN))
    p, st = panel_of([orb_day(days(), rel=0.9)])
    assert not any(orb.decide(p, st[orb.NAME], m) for m in range(N_MIN))
    p, st = panel_of([orb_day(days(price=4.0, vol=0.0001))])
    assert not any(orb.decide(p, st[orb.NAME], m) for m in range(N_MIN))


def test_orb_keeps_top_20_by_relative_volume_including_down_names():
    sds = [orb_day(days(seed=k, sym=f"S{k}"), rel=2.0 + k, up=k % 2 == 0) for k in range(26)]
    p, st = panel_of(sds)
    mask, rel = orb.in_play(p, st[orb.NAME])
    assert mask.sum() == 20 and set(np.flatnonzero(mask)) == set(range(6, 26))
    traded = {c.symbol for m in range(N_MIN) for c in orb.decide(p, st[orb.NAME], m)}
    assert traded == {f"S{k}" for k in range(6, 26) if k % 2 == 0}


def test_orb_no_lookahead():
    sd = orb_day(days())
    full, st = panel_of([sd])
    sd2 = orb_day(days())
    sd2.c[-1, 150:] *= 0.9
    sd2.l[-1, 150:] *= 0.9
    sd2.v[-1, 150:] *= 7
    alt, st2 = panel_of([sd2])
    for m in range(0, 150):
        cut, _ = panel_of([sd], upto=m)
        a, b, c = (orb.decide(x, st[orb.NAME], m) for x in (full, alt, cut))
        assert a == b == c


def test_prior_stats_use_only_earlier_sessions():
    a, b = days(), days()
    b.c[-1] *= 1.5
    b.v[-1] *= 9
    for strat in (orb, news):
        sa, sb = strat.prior_stats(a), strat.prior_stats(b)
        for k in sa:
            np.testing.assert_array_equal(sa[k][:-1], sb[k][:-1])  # known before each session: unchanged
    assert orb.prior_stats(a)["adv14_shares"][-2] == pytest.approx(N_MIN * 1e5)


# ---------------- news continuation ----------------
def news_day(sd: SymbolDays):
    """Gap up on news, heavy volume, above VWAP, then a 5-minute pullback that holds VWAP at bar 5 (m = 29)."""
    p0 = sd.c[-2, -1]
    c = p0 * np.r_[np.linspace(1.01, 1.05, 25), np.full(5, 1.048), np.linspace(1.05, 1.06, N_MIN - 30)]
    c[25:30] = p0 * np.array([1.045, 1.040, 1.043, 1.047, 1.0505])
    o = np.r_[p0 * 1.01, c[:-1]]
    sd.c[-1], sd.o[-1] = c, o
    sd.h[-1], sd.l[-1] = np.maximum(o, c) + 0.01, np.minimum(o, c) - 0.01
    sd.vw[-1] = c
    sd.v[-1] = sd.v[-2] * 4
    return sd


def test_news_continuation_needs_news_and_the_pullback():
    sd = news_day(days())
    p, st = panel_of([sd])
    s = st[news.NAME]
    got = {m: news.decide(p, s, m, np.array([True])) for m in range(N_MIN)}
    first = min(m for m, v in got.items() if v)
    c = got[first][0]
    assert first == 29 and (first - 4) % 5 == 0
    assert c.stop < c.entry_ref and c.target is None
    assert c.features["rel_vol_cum"] >= 3 and c.features["move_sigma"] >= 1 and c.entry_ref > c.features["vwap"]
    assert not any(news.decide(p, s, m, np.array([False])) for m in range(N_MIN))
    assert not news.decide(p, s, 30, np.array([True]))  # only on 5-minute bar closes


def test_news_continuation_no_lookahead():
    sd = news_day(days())
    full, st = panel_of([sd])
    sd2 = news_day(days())
    sd2.c[-1, 100:] *= 0.95
    sd2.l[-1, 100:] *= 0.95
    alt, _ = panel_of([sd2])
    for m in range(4, 100, 5):
        cut, _ = panel_of([sd], upto=m)
        a, b, c = (news.decide(x, st[news.NAME], m, np.array([True])) for x in (full, alt, cut))
        assert a == b == c


# ---------------- book: gate sizing, counterfactual ----------------
def cand(sym="X", row=0, m=40, ref=100.0, stop=99.0, strat=orb.NAME):
    return ShadowCandidate(strat, sym, row, m, ref, stop, None, "time_1555", ("t",), {})


def flat_panel(n=3, price=100.0):
    c = np.full((n, N_MIN), price)
    return Panel(np.array([f"S{k}" for k in range(n)]), c.copy(), c + 0.05, c - 0.05, c.copy(), np.full_like(c, 1e5),
                 c.copy())


def test_book_qty_is_the_gate_verdict_and_positions_cap_applies():
    pol = RiskPolicy(**{**POL.__dict__, "max_positions": 1})
    book = ShadowBook(orb.NAME, pol, 5000.0)
    p = flat_panel()
    e1 = book.consider(cand("S0", 0), p, 40)
    snap = Snapshot(equity=5000.0, day_start_equity=5000.0, open_positions=0, symbol_exposure=0.0, open_risk=0.0,
                    quote_age_s=0.0, kill_switch=False, reconciled=True, market_open=True, buying_power=5000.0)
    assert e1.qty == evaluate(Proposal("S0", Side.BUY, 100.0, 99.0), snap, pol).qty > 0
    assert e1.qty * 100.0 <= 0.15 * 5000 and e1.qty * 1.0 <= 0.005 * 5000
    e2 = book.consider(cand("S1", 1, m=41), p, 41)
    assert e2.qty == 0 and "max_positions" in e2.failures
    e3 = book.consider(cand("S2", 2, m=380), p, 380)  # after 15:45
    assert e3.qty == 0 and "entry_window" in e3.failures


def test_book_frees_slot_after_stop_using_only_past_bars():
    pol = RiskPolicy(**{**POL.__dict__, "max_positions": 1})
    book = ShadowBook(orb.NAME, pol, 100_000.0)
    p = flat_panel()
    book.consider(cand("S0", 0, m=40, stop=99.9), p, 40)
    p.l[0, 60] = 99.0  # S0 stopped at minute 60
    assert book.consider(cand("S1", 1, m=59), p, 59).qty == 0
    assert book.consider(cand("S2", 2, m=61), p, 61).qty > 0


def test_counterfactual_matches_fill_model_time_and_stop_exits():
    book = ShadowBook(orb.NAME, POL, 100_000.0)
    p = flat_panel()
    p.c[0, 41:] = 101.0
    p.o[0, 42:] = 101.0
    p.h[0, 41:], p.l[0, 41:] = 101.05, 100.95
    e = book.consider(cand("S0", 0, m=40, ref=100.0, stop=99.0), p, 40)
    r = book.counterfactual(e, p, 0.0005)
    fill = buy_px(p.o[0, 41], e.qty, 0.0005, 1e5)
    xp = sell_px(101.0, e.qty, 0.0005, 1e5)
    assert r["reason"] == "time" and r["exit_m"] == EXIT_M and r["fill_m"] == 41
    assert r["pnl_usd"] == pytest.approx((xp - fill) * e.qty - sell_fees(e.qty, xp), abs=0.01)
    assert r["r_multiple"] == pytest.approx(r["pnl_usd"] / (1.0 * e.qty), abs=1e-3)
    # stop: re-anchored on the fill, filled at min(stop, open)
    p2 = flat_panel()
    p2.l[0, 70], p2.o[0, 70] = 98.0, 98.5
    book = ShadowBook(orb.NAME, POL, 100_000.0)
    e2 = book.consider(cand("S0", 0, m=40), p2, 40)
    r2 = book.counterfactual(e2, p2, 0.0005)
    fill2 = buy_px(100.0, e2.qty, 0.0005, 1e5)
    assert r2["reason"] == "stop" and r2["exit_m"] == 70 and r2["stop"] == pytest.approx(fill2 - 1.0, abs=1e-4)
    assert r2["pnl_usd"] < 0


def test_counterfactual_early_close_exits_at_flatten_minute():
    book = ShadowBook(orb.NAME, POL, 100_000.0, n_min=210)  # 13:00 close -> flatten 12:55 (m = 205)
    p = flat_panel()
    p.c[0, 205] = 100.5
    p.c[0, 206:] = np.nan
    p.o[0, 206:] = p.h[0, 206:] = p.l[0, 206:] = np.nan
    e = book.consider(cand("S0", 0, m=40), p, 40)
    r = book.counterfactual(e, p, 0.0005)
    assert r["reason"] == "time" and np.isfinite(r["pnl_usd"])
    assert r["exit"] == pytest.approx(sell_px(100.5, e.qty, 0.0005, 1e5), abs=1e-4)


def test_step_one_signal_per_symbol_and_news_queried_only_for_setups():
    sd = news_day(days())
    p, st = panel_of([sd, days(seed=3, sym="Y")])
    books = {k: ShadowBook(k, POL, 100_000.0) for k in (orb.NAME, news.NAME)}
    asked, seen, out = [], set(), []
    for m in range(N_MIN):
        out += step(books, p, st, m, lambda rows, m: asked.append(list(rows)) or np.ones(len(rows), bool), seen)
    nc = [e for e in out if e.cand.strategy == news.NAME]
    assert len(nc) == 1 and nc[0].cand.symbol == "X"
    assert all(r == [0] for r in asked) and len(asked) == 1  # Y never set up; X not re-asked after signalling


def test_ffill_rows():
    x = np.array([[np.nan, 1.0, np.nan, 3.0, np.nan], [2.0, np.nan, np.nan, np.nan, 5.0]])
    np.testing.assert_array_equal(ffill_rows(x), np.array([[np.nan, 1, 1, 3, 3], [2, 2, 2, 2, 5]]))
