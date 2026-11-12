"""Random-timing control for SPY-only rules (issue #149, decision 15 section 10, Gate B mark 3), on synthetic bars."""

import numpy as np
import pytest
from test_spy_noise_band import _walk

from kaibot.evaluation.gate_v2 import GateV2, mark_3
from kaibot.replay import spy_noise_band as nb
from kaibot.replay.data import N_MIN

I0 = 20
CARRY = nb.VERSIONS["spy_noise_band_carry"]


def _durations(spy, trips):
    cm = nb.close_minute(spy)
    return [nb.trip_duration(t, cm) for t in trips]


def _spans(trips, cm):
    return sorted((t.i_entry * 400 + t.entry_m, t.i_exit * 400 + (t.exit_m + (t.reason == "close")))
                  for t in trips)


@pytest.mark.parametrize("spec", [nb.BASE, CARRY], ids=["v1", "carry"])
def test_random_accounts_copy_count_durations_and_check_times(spec):
    spy = _walk(n=160, seed=4)
    i1 = len(spy.dates) - 1
    trips = [t for t in nb.signals(spy, spec, I0, i1) if t.i_exit <= i1]
    assert len(trips) > 30
    want = _durations(spy, trips)
    cm = nb.close_minute(spy)
    for k in range(5):
        rt, miss = nb.random_timing_trips(spy, spec, I0, i1, want, np.random.default_rng(k))
        assert miss == 0 and len(rt) == len(trips)
        assert sorted(_durations(spy, rt)) == sorted(want)  # the rule's own holding durations, each once
        assert {t.decide_m for t in rt} <= set(spec.checks())  # :00 / :30 from 10:00
        assert all(t.entry_m == t.decide_m + 1 and t.i_entry >= I0 and t.i_exit <= i1 for t in rt)
        assert all(t.entry_mid == spy.o[t.i_entry, t.entry_m] for t in rt)  # fills at the next bar's open
        sp = _spans(rt, cm)
        assert all(b0 < a1 for (_, b0), (a1, _) in zip(sp, sp[1:], strict=False))  # one at a time, no same-check flip
        assert sum(t.reason == "next_open" for t in rt) == sum(t.reason == "next_open" for t in trips)
    a, _ = nb.random_timing_trips(spy, spec, I0, i1, want, np.random.default_rng(9))
    b, _ = nb.random_timing_trips(spy, spec, I0, i1, want, np.random.default_rng(9))
    assert a == b  # seeded


def test_random_accounts_are_fully_invested_with_the_same_costs():
    spy = _walk(n=80, seed=5)
    i1 = len(spy.dates) - 1
    trips = nb.signals(spy, nb.BASE, I0, i1)
    rt, _ = nb.random_timing_trips(spy, nb.BASE, I0, i1, _durations(spy, trips), np.random.default_rng(1))
    book = nb.run_account(spy, rt, I0, i1, nb.BASE_COST)
    for t in book.trades:
        assert t["net_ret"] == pytest.approx((1 + t["gross_ret"]) * (1 - 0.5e-4) / (1 + 0.5e-4) - 1)
    q0 = book.trades[0]["qty"] * book.trades[0]["entry"]
    assert q0 == pytest.approx(100_000.0)  # all the cash goes in


def test_no_skill_rule_lands_near_the_50th_percentile_on_average():
    """A 'rule' whose trips are themselves random timing is exchangeable with the control: its percentile is
    uniform, so over many seeds the mean is near 50."""
    spy = _walk(n=120, seed=11)
    i1 = len(spy.dates) - 1
    durations = _durations(spy, [t for t in nb.signals(spy, nb.BASE, I0, i1) if t.i_exit <= i1])
    pct, ret_pct = [], []
    for s in range(30):
        rule, _ = nb.random_timing_trips(spy, nb.BASE, I0, i1, durations, np.random.default_rng(1000 + s))
        c = nb.random_timing_control(spy, nb.BASE, rule, I0, i1, runs=60, seed=s)
        assert c["matched"] and c["round_trips"] == len(durations)
        pct.append(c["percentile"])
        ret_pct.append(c["return_percentile"])
    assert 38 <= np.mean(pct) <= 62 and 38 <= np.mean(ret_pct) <= 62
    assert np.mean(np.array(pct) >= 90) < 0.25  # the 90th-percentile bar is rarely cleared by luck


def test_noise_band_on_driftless_random_walks_has_no_timing_skill():
    pct = []
    for s in range(8):
        spy = _walk(n=140, seed=200 + s)
        i1 = len(spy.dates) - 1
        c = nb.random_timing_control(spy, nb.BASE, nb.signals(spy, nb.BASE, I0, i1), I0, i1, runs=60, seed=s)
        pct.append(c["percentile"])
    assert 20 <= np.mean(pct) <= 80


def _planted(n=120, seed=7, every=2, lift=0.002):
    """A random walk in which, on every `every`-th session, the 30 minutes after one check rise by `lift`; the
    'rule' buys exactly there and holds 30 minutes: timing skill, planted."""
    spy = _walk(n=n, seed=seed)
    rng = np.random.default_rng(seed)
    trips = []
    for i in range(I0, n, every):
        m = int(rng.choice(nb.BASE.checks()[:-1]))
        e = m + 1
        k = np.arange(N_MIN)
        bump = np.clip((k - e) / 30.0, 0, 1) * lift  # ramps from the fill to the exit 30 bars later, then stays
        f = 1 + bump
        for a in ("o", "h", "l", "c", "vw"):
            getattr(spy, a)[i] *= f
        spy.o[i, e] = spy.c[i, e - 1]  # the fill bar opens at the decision close
        trips.append(nb.Trip(i, m, e, float(spy.o[i, e]), i, e + 30, float(spy.o[i, e + 30]), "band_vwap"))
    return spy, trips


@pytest.mark.parametrize("seed", [7, 8, 9])
def test_planted_skill_lands_above_the_90th_percentile(seed):
    spy, trips = _planted(seed=seed)
    i1 = len(spy.dates) - 1
    c = nb.random_timing_control(spy, nb.BASE, trips, I0, i1, runs=200, seed=seed)
    assert c["matched"] and c["round_trips"] == len(trips) and c["median_hold_min"] == 30
    assert c["percentile"] > 90 and c["return_percentile"] > 90


def test_mark_3_needs_500_runs_and_the_90th_percentile():
    gate = GateV2.load()
    spy, trips = _planted(n=60, seed=3)
    i1 = len(spy.dates) - 1
    few = nb.random_timing_control(spy, nb.BASE, trips, I0, i1, runs=50)
    assert mark_3(gate, few)["met"] is None  # too few runs: cannot be judged
    full = nb.random_timing_control(spy, nb.BASE, trips, I0, i1)
    assert full["runs"] == 500 and full["seed"] == nb.CONTROL_SEED and len(full["per_run"]["sharpe"]) == 500
    m = mark_3(gate, full)
    assert m["met"] is True and m["numbers"]["percentile_used"] == full["percentile"]
    noise = _walk(n=60, seed=3)
    rule, _ = nb.random_timing_trips(noise, nb.BASE, I0, i1, [(30, False)] * len(trips), np.random.default_rng(0))
    c = nb.random_timing_control(noise, nb.BASE, rule, I0, i1, seed=0)
    assert mark_3(gate, c)["met"] is (c["percentile"] >= 90)
    with pytest.raises(ValueError):
        nb.random_timing_control(noise, nb.BASE, [], I0, i1)
