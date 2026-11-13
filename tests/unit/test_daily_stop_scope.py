"""Issue #199 (QA-Ops follow-up of #195): the daily runner scopes its protective-stop `ensure` calls to its own
strategy, so when two multi-day strategies share one order manager and one symbol, one strategy's call never
cancels the other's stops as "gone", and the stops of the symbol never sum above the venue position."""

from __future__ import annotations

from datetime import timedelta

from test_protect_shared import never_over, qty_of, resting, shared

from kaibot.engine.daily import protect_sleeves
from kaibot.engine.protect import ProtectiveStops

RULE, TREND = "rule", "trend"
KA, KB = (RULE, "A"), (TREND, "A")


def coids(ps, key) -> list[str]:
    return sorted(r.coid for r in ps.stops_of(key))


def state(ps, key):
    return [(r.coid, r.qty, r.stop, r.state) for r in sorted(ps.stops_of(key), key=lambda r: r.coid)]


def test_199_unscoped_the_other_strategys_stop_is_cancelled_as_gone():
    """The hazard #199 removes: a call with one strategy's sleeves and no scope cancels the other's stop."""
    b, clock, om, ps, asked = shared(50)
    ps.ensure({KA: 30.0, KB: 20.0}, {KA: 90.0, KB: 91.0}, clock(), "2026-09-15")
    assert qty_of(ps, KB) == 20
    ps.ensure({KA: 30.0}, {KA: 90.0}, clock(), "2026-09-15")
    assert ps.stops_of(KB) == []


def test_199_two_daily_strategies_on_one_manager_and_symbol_each_keep_the_others_stops():
    """Two multi-day strategies take turns through the daily runner's stop step (after the close, and a split's
    re-protection before the open) on one shared order manager: each call leaves the other strategy's stops as
    they were, and the stops of A never sum above the venue position."""
    b, clock, om, ps, asked = shared(50)
    pos = {RULE: {"A": 30.0}, TREND: {"A": 20.0}}
    px = {KA: 90.0, KB: 91.0}

    def step(name, only=None):
        other = KB if name == RULE else KA
        before = state(ps, other)
        protect_sleeves(ps, name, pos[name], px, clock(), "2026-09-15", only=only)
        assert state(ps, other) == before, f"{name}'s call touched {other[0]}'s stops"
        assert resting(om) <= b.pos.get("A", {}).get("qty", 0)
        never_over(asked)

    step(RULE)
    step(TREND)
    assert (qty_of(ps, KA), qty_of(ps, KB)) == (30, 20) and resting(om) == 50
    for name in (RULE, TREND, RULE, TREND):  # each daily call again: nothing cancelled, nothing re-sent
        step(name)
    assert (qty_of(ps, KA), qty_of(ps, KB)) == (30, 20)
    kept = coids(ps, KA) + coids(ps, KB)

    step(RULE, only={"A"})  # a split's re-protection before the open, scoped to the split name
    step(TREND, only={"A"})
    assert coids(ps, KA) + coids(ps, KB) == kept

    px[KA] = 92.0  # rule's stop moves up: only rule's stop is replaced
    step(RULE)
    assert ps.stops_of(KA)[0].stop == 92.0 and ps.stops_of(KB)[0].stop == 91.0

    assert ps.release(KA, 10.0)  # rule sells 20: its stop shrinks first, the sale fills
    b.pos["A"]["qty"] = 30
    pos[RULE]["A"] = 10.0
    step(RULE)
    step(TREND)
    assert (qty_of(ps, KA), qty_of(ps, KB)) == (10, 20) and resting(om) == 30

    clock.set(clock() + timedelta(days=95))  # both stops near their 90-day expiry: each re-arm is its own
    step(RULE)
    step(TREND)
    assert (qty_of(ps, KA), qty_of(ps, KB)) == (10, 20) and resting(om) <= 30

    pos[RULE] = {}  # rule exits: its own stop goes, trend's stays
    assert ps.release(KA, 0.0)
    b.pos["A"]["qty"] = 20
    step(RULE)
    step(TREND)
    assert ps.stops_of(KA) == [] and qty_of(ps, KB) == 20 and resting(om) == 20


def test_199_the_daily_runner_scopes_every_stop_call_to_its_own_strategy():
    """Both call sites in `run_daily` (after the close, and a split before the open) pass `strategies={name}`
    and only that strategy's sleeves."""
    import numpy as np
    from test_engine_momentum import FIXTURE, POL

    from kaibot.engine import daily_report as dr
    from kaibot.engine.daily import DailyData, run_daily
    from kaibot.strategies import momentum as mom

    m, info = dr.load_fixture(FIXTURE)
    data = DailyData.from_market(m)
    i0 = int(np.searchsorted(data.dates, np.datetime64(info["window"][0])))
    strat = mom.VERSIONS["trend_momentum"]
    calls = []
    real = ProtectiveStops.ensure

    def ensure(self, sleeves, prices, now, session, only=None, strategies=None):
        calls.append((only, strategies, {k[0] for k in sleeves}))
        return real(self, sleeves, prices, now, session, only=only, strategies=strategies)

    ProtectiveStops.ensure = ensure
    try:
        run_daily(strat, data, POL, i0, len(data.dates) - 1, protect_atr=10.0)
    finally:
        ProtectiveStops.ensure = real
    assert any(o is None for o, _, _ in calls) and any(o is not None for o, _, _ in calls), "both call sites ran"
    assert all(s == {strat.name} and names <= {strat.name} for _, s, names in calls), calls[:3]
