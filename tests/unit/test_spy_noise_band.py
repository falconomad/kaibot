"""SPY noise-band rule and its carry (issue #128): band arithmetic, checks, fills, costs, look-ahead, the script."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kaibot.replay import spy_noise_band as nb
from kaibot.replay.data import N_MIN, SymbolDays

ROOT = Path(__file__).resolve().parents[2]


def _days(c: np.ndarray, start="2024-01-02", v: np.ndarray | None = None) -> SymbolDays:
    """SymbolDays from closes; each bar opens at the previous bar's close (the first at c[:, 0])."""
    o = np.c_[c[:, :1], c[:, :-1]]
    dates = np.arange(np.datetime64(start), np.datetime64(start) + np.timedelta64(len(c), "D"))
    vol = np.full_like(c, 1e5) if v is None else v
    return SymbolDays("SPY", dates, o, np.maximum(o, c), np.minimum(o, c), c, vol, c.copy())


def _flat_history(n_days=15) -> np.ndarray:
    """n days that open at 100 and trade at 100.1 from the first bar: sigma = 0.001 at every minute."""
    c = np.full((n_days + 1, N_MIN), 100.1)
    c[:, 0] = 100.1
    return c


def _walk(n=60, seed=0, drift=0.0) -> SymbolDays:
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, 0.0005, (n, N_MIN))
    gap = rng.normal(0, 0.003, n)
    c = np.empty((n, N_MIN))
    p = 400.0
    for i in range(n):
        c[i] = p * (1 + gap[i]) * np.exp(np.cumsum(r[i]))
        p = c[i, -1]
    o = np.c_[c[:, :1], c[:, :-1]]
    o[:, 0] = c[:, 0] / np.exp(r[:, 0])
    dates = np.arange(np.datetime64("2024-01-02"), np.datetime64("2024-01-02") + np.timedelta64(n, "D"))
    v = rng.uniform(5e4, 2e5, (n, N_MIN))
    return SymbolDays("SPY", dates, o, np.maximum(o, c) * 1.0001, np.minimum(o, c) * 0.9999, c, v, (o + c) / 2)


def _breakout_day(c: np.ndarray, fall_at: int | None = None) -> SymbolDays:
    """History of `_flat_history`, then a last day at 100 that jumps to 100.3 at 09:59 (above UB = 100.2001)."""
    c = c.copy()
    c[-1, :] = 100.0
    c[-1, 29:] = 100.3
    if fall_at is not None:
        c[-1, fall_at:] = 100.15  # below UB at the next check
    sd = _days(c)
    sd.o[:, 0] = 100.0  # every session opens at 100
    return sd


# ---------------------------------------------------------------- construction


def test_check_minutes_are_the_half_hours_from_ten():
    assert nb.BASE.checks() == [29 + 30 * k for k in range(12)]
    assert nb.BASE.checks()[-1] == nb.CHECK_1530_M == 359
    n15 = {s.name.split("~")[1]: s for s in nb.NEIGHBOURS}["check15"].checks()
    assert len(n15) == 24 and n15[0] == 29 and n15[-1] == 374
    assert len(nb.NEIGHBOURS) <= 5


def test_sigma_is_the_mean_absolute_move_of_previous_sessions_only():
    sd = _breakout_day(_flat_history())
    sg = nb.sigma_table(sd, 14, 12)
    assert np.isnan(sg[:12]).all()  # fewer than 12 previous sessions
    np.testing.assert_allclose(sg[15, 29], 0.001, rtol=1e-9)  # today's own jump is not in it
    np.testing.assert_allclose(sg[14, 200], 0.001, rtol=1e-9)


def test_entry_above_band_fills_next_open_and_exits_at_close():
    sd = _breakout_day(_flat_history())
    dec = []
    trips = nb.signals(sd, nb.BASE, 15, 15, dec)
    assert dec == [(15, 29, "buy")]
    (t,) = trips
    assert (t.decide_m, t.entry_m, t.entry_mid, t.reason, t.exit_m) == (29, 30, 100.3, "close", N_MIN - 1)
    assert t.exit_mid == 100.3


def test_exit_below_max_band_vwap_at_a_check_only():
    sd = _breakout_day(_flat_history(), fall_at=40)  # falls at 10:10, seen at the 10:30 check
    trips = nb.signals(sd, nb.BASE, 15, 15)
    assert trips[0].reason == "band_vwap" and trips[0].exit_m == 60 and trips[0].exit_mid == pytest.approx(100.15)


def test_no_entry_between_checks_or_before_ten():
    c = _flat_history()
    sd = _breakout_day(c)
    sd.c[-1, :] = 100.0
    sd.c[-1, 5:28] = 101.0  # far above the band, but only between checks
    sd.o[-1, 1:] = sd.c[-1, :-1]
    assert nb.signals(sd, nb.BASE, 15, 15) == []


def test_carry_sells_at_the_next_open():
    sd = _breakout_day(_flat_history())
    nxt = np.full((1, N_MIN), 100.5)
    sd = SymbolDays("SPY", np.r_[sd.dates, sd.dates[-1] + np.timedelta64(1, "D")],
                    *(np.vstack([getattr(sd, k), nxt]) for k in ("o", "h", "l", "c", "v", "vw")))
    (t,) = nb.signals(sd, nb.VERSIONS["spy_noise_band_carry"], 15, 15)
    assert (t.reason, t.i_exit, t.exit_m, t.exit_mid) == ("next_open", 16, 0, 100.5)
    book = nb.run_account(sd, [t], 15, 16, 0.0)
    assert book.daily[0]["carry_out"] and book.daily[1]["carry_in"] and book.daily[1]["invested"]
    assert book.equity == pytest.approx(100_000 * 100.5 / 100.3)
    nights = nb.carry_nights(sd, [t], 15, 15)
    assert nights["carry_nights"] == 1 and nights["carry_mean_bp"] == pytest.approx((100.5 / 100.3 - 1) * 1e4, abs=0.01)


def test_exit1555_neighbour_sells_at_the_1555_open():
    sd = _breakout_day(_flat_history())
    sp = {s.name.split("~")[1]: s for s in nb.NEIGHBOURS}["exit1555"]
    (t,) = nb.signals(sd, sp, 15, 15)
    assert (t.reason, t.exit_m) == ("exit1555", nb.EXIT_1555_M)


def test_early_close_session_stops_checks_and_closes_at_its_last_bar():
    c = _flat_history()
    v = np.full_like(c, 1e5)
    v[-1, 210:] = 0  # last bar 12:59
    sd = _breakout_day(c)
    sd = SymbolDays("SPY", sd.dates, sd.o, sd.h, sd.l, sd.c, v, sd.vw)
    assert nb.close_minute(sd)[-1] == 209 and nb.close_minute(sd)[0] == N_MIN - 1
    dec = []
    (t,) = nb.signals(sd, nb.BASE, 15, 15, dec)
    assert t.exit_m == 209 and all(m + 1 <= 209 for _, m, _ in dec)


# ---------------------------------------------------------------- the account and costs


def test_fixed_bp_costs_are_exact_and_break_even_zeroes_the_account():
    sd = _walk(80, seed=3, drift=0.00004)
    trips = nb.signals(sd, nb.BASE, 20, 79)
    assert trips, "fixture should trigger"
    gross = nb.run_account(sd, trips, 20, 79, 0.0)
    one = nb.run_account(sd, trips, 20, 79, 1.0)
    for g, n in zip(gross.trades, one.trades, strict=True):
        assert n["net_ret"] == pytest.approx((1 + g["gross_ret"]) * (1 - 1e-4) / (1 + 1e-4) - 1, abs=1e-12)
    be = nb.break_even(np.array([t["gross_ret"] for t in gross.trades]))
    assert be["per_trip_bp"] == pytest.approx(np.mean([t["gross_ret"] for t in gross.trades]) * 1e4 / 2, abs=0.01)
    if be["account_bp"] is not None:
        at = nb.run_account(sd, trips, 20, 79, be["account_bp"])
        assert at.equity == pytest.approx(100_000, rel=len(trips) * 1.1e-6)  # account_bp is rounded to 0.01 bp
    v1 = nb.run_account(sd, trips, 20, 79, "v1")  # cost model v1: at least 1 bp half spread a side, plus impact
    for g, n in zip(gross.trades, v1.trades, strict=True):
        assert n["net_ret"] < (1 + g["gross_ret"]) * (1 - 1e-4) / (1 + 1e-4) - 1


def test_account_is_one_position_at_a_time_and_marks_every_session():
    sd = _walk(80, seed=5)
    trips = nb.signals(sd, nb.VERSIONS["spy_noise_band_carry"], 20, 79)
    book = nb.run_account(sd, trips, 20, 79, 0.5)
    assert len(book.daily) == 60
    st = nb.account_stats(book)
    assert 0 <= st["days_invested_pct"] <= 100 and st["sessions"] == 60
    eq = [r["end"] for r in book.daily]
    assert all(b["start"] == pytest.approx(a) for a, b in zip(eq, book.daily[1:], strict=False))
    for t in book.trades:  # entries never overlap
        assert t["exit_date"] >= t["date"]


# ---------------------------------------------------------------- look-ahead


@pytest.mark.parametrize("carry", [False, True])
@pytest.mark.parametrize("cut", [(30, 29), (35, 200), (41, 358), (50, 0)])
def test_perturbing_future_minutes_leaves_earlier_decisions_unchanged(cut, carry):
    spec = nb.VERSIONS["spy_noise_band_carry" if carry else "spy_noise_band"]
    sd = _walk(60, seed=11, drift=0.00003)
    base: list = []
    nb.signals(sd, spec, 16, 59, base)
    assert base, "fixture should trigger"
    ci, cm = cut
    rng = np.random.default_rng(1)
    arrs = {k: getattr(sd, k).copy() for k in ("o", "h", "l", "c", "v", "vw")}
    fut = np.zeros((len(sd.dates), N_MIN), dtype=bool)
    fut[ci, cm + 1:] = True
    fut[ci + 1:] = True
    shock = rng.uniform(0.97, 1.03, fut.shape)
    for k in ("o", "h", "l", "c", "vw"):
        arrs[k] = np.where(fut, arrs[k] * shock, arrs[k])
    arrs["v"] = np.where(fut, arrs["v"] * rng.uniform(0.2, 5, fut.shape), arrs["v"])  # stays > 0: same calendar
    pert: list = []
    nb.signals(SymbolDays("SPY", sd.dates, **arrs), spec, 16, 59, pert)
    before = [d for d in base if (d[0], d[1]) <= cut]
    assert before == [d for d in pert if (d[0], d[1]) <= cut]
    assert base != pert  # the perturbation did reach later decisions


# ---------------------------------------------------------------- marks and the live-trade statement


def _st(bp, t, ret=1.0, dd=5.0, years=None):
    return {"net_bp_per_trip": bp, "t_net": t, "return_pct": ret, "max_drawdown_pct": dd, "trades": 900,
            "years": years or {str(y): 1.0 for y in range(2017, 2027)}, "sum_net_pct": ret}


def test_marks_v1_and_statement():
    good = {"0.5": _st(2.0, 2.5), "1.0": _st(1.0, 1.0, ret=3.0)}
    lbs = {k: _st(1.0, 1.0) for k in nb.LOOKBACK_NEIGHBOURS}
    m = nb.marks_v1(good, lbs)
    assert [x["verdict"] for x in m] == ["pass"] * 5
    assert nb.live_trade_statement(m, _st(0.1, 0.1), _st(0, 0, ret=-1))["verdict"] == "absorb"
    bad_years = {"0.5": _st(2.0, 2.5, years={str(y): (1.0 if y < 2023 else -1.0) for y in range(2017, 2027)}),
                 "1.0": _st(1.0, 1.0, ret=-0.5)}
    m = nb.marks_v1(bad_years, {**lbs, "lb30": _st(0.5, 1.0)})
    assert [x["verdict"] for x in m] == ["pass", "fail", "fail", "pass", "fail"]
    assert nb.live_trade_statement(m, _st(2.0, 2.1), _st(1.0, 1.0, ret=0.4))["verdict"] == "neither yet"
    assert nb.live_trade_statement(m, _st(2.0, 1.5), _st(1.0, 1.0, ret=0.4))["verdict"] == "retire"
    assert nb.marks_v1(good, {})[4]["verdict"] == "cannot be judged"


def test_marks_carry_and_welch():
    assert nb.welch_t(np.array([1.0, 2, 3]), np.array([1.0, 2, 3])) == 0.0
    m = nb.marks_carry({"welch_t": 2.4, "carry_nights_2022_23_pct": -0.3, "carry_mean_bp": 9, "other_mean_bp": 2,
                        "carry_nights": 400, "carry_nights_2022_23": 50})
    assert [x["verdict"] for x in m] == ["pass", "fail"]


# ---------------------------------------------------------------- the script end to end


def _write_parquet(data: Path, start="2016-01-04", end="2017-08-31", seed=2):
    sessions = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    out = data / "bars_1m" / "symbol=SPY"
    out.mkdir(parents=True)
    p, frames = 200.0, []
    for d in sessions:
        r = rng.normal(0.0000005, 0.0005, N_MIN)
        c = p * (1 + rng.normal(0, 0.003)) * np.exp(np.cumsum(r))
        o = np.r_[c[0] / np.exp(r[0]), c[:-1]]
        p = c[-1]
        ts = pd.date_range(d + pd.Timedelta(hours=9, minutes=30), periods=N_MIN, freq="1min",
                           tz="America/New_York").tz_convert("UTC")
        frames.append(pd.DataFrame({"ts": ts, "o": o, "h": np.maximum(o, c), "l": np.minimum(o, c), "c": c,
                                    "v": rng.uniform(5e4, 2e5, N_MIN), "vw": (o + c) / 2}))
    df = pd.concat(frames, ignore_index=True)
    df["month"] = df["ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m")
    for m, g in df.groupby("month"):
        g.drop(columns="month").to_parquet(out / f"{m}.parquet", index=False)


def test_script_end_to_end_resumable_with_one_table(tmp_path):
    data = tmp_path / "data"
    _write_parquet(data)
    py, script = sys.executable, str(ROOT / "scripts" / "spy_noise_band.py")
    common = ["--data", str(data), "--start", "2016-01-01", "--end", "2017-08-31"]
    assert subprocess.run([py, script, "--has", "spy_noise_band", *common]).returncode == 1
    for v in nb.VERSIONS:
        r = subprocess.run([py, script, "--version", v, *common], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert subprocess.run([py, script, "--has", v, *common]).returncode == 0
    r = subprocess.run([py, script, "--summary", "--data", str(data)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = (data / "reports" / "experiments_spy_summary.md").read_text()
    assert "PROVISIONAL" in text and "BENCHMARK: SPY buy-and-hold" in text and "Not run yet" not in text
    assert "Live last-30-minutes SPY trade:" in text and "Carry nights" in text
    assert text.count("Mark ") == 7 and "lb30" in text and "model v1" in text
    rep = json.loads(next((data / "reports").glob("spy_balanced_spy_noise_band_2*.json")).read_text())
    assert rep["oos_window"][0] >= "2017-05-01" and rep["prereg"]["file"] == "docs/prereg/spy_noise_band.md"
    trials = [json.loads(x) for x in (data / "trials.jsonl").read_text().splitlines()]
    assert sum(t["n_trials"] for t in trials) == 7  # v1, carry and five neighbours at 0.5 bp a side


def test_research_only_and_costs_come_from_the_replay():
    """Nothing the engine runs imports this routine, and it prices fills with sim.py's cost functions."""
    for f in (ROOT / "src" / "kaibot").rglob("*.py"):
        if f.name != "spy_noise_band.py":
            assert "spy_noise_band" not in f.read_text(), f
    from kaibot.broker import shadow_costs
    from kaibot.replay import sim

    assert nb.buy_px is sim.buy_px and nb.sell_px is sim.sell_px and nb.sell_fees is shadow_costs.sell_fees
    text = (ROOT / "src" / "kaibot" / "replay" / "spy_noise_band.py").read_text()
    for banned in ("kaibot.engine", "kaibot.strategies", "kaibot.risk", "shadow_fill_price"):
        assert banned not in text, banned
