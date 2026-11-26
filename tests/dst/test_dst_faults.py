"""Audit 2, 5.18 (#698): three fault kinds the seeded generator lacked, and the proof that the fixes of 5.8-5.10 would
have been found from seeds.

    lease_renew_fail   (FaultyStore) every renewal of the engine's lease fails for a spell of one or two minutes
    sale_refused       (FaultyBroker) the venue refuses a market sale (403), drawn per attempt
    endpoint_down      (FaultyBroker) a partial outage: one read endpoint answers 503 for 5, 15 or 40 minutes

They are in DEFAULT_FAULTS and drawn in the DST world's new weeks (audit 2 5.16), which run the live process's loop
with the lease (5.17); the summer week's seeds run without them, as before. Invariant 12 (world.Checker.sales_side)
says what a refused or blocked sale must not do.

Before each fix (the parent commits 64de471, da4a471, f31614a; here: the fix planted away, scenarios.py) the seeds
below break an invariant; with the fix they hold:

    #735 (5.8)   a buy the fence refused for a failed renewal was not sent again that day (invariant 12c; the
                 carry-over buys it a session later, so the end of the run alone does not show it)
    #737 (5.10)  a failing positions() read failed every minute, the due sale waited behind it (invariant 12a)
    #736 (5.9)   a sale the venue refused was not sent again that session (invariant 12b)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from scenarios import (
    lease_blip_drops_the_buy,
    positions_read_fails_the_minute,
    quiet_in,
    refused_sale_not_sent_again,
)
from world import SUMMER, THANKSGIVING, Scenario, run_scenario, run_seed, week_of

from kaibot.broker.faulty import (
    AUDIT2_FAULTS,
    DEFAULT_FAULTS,
    DOWN_ENDPOINTS,
    NO_FAULTS,
    FaultProfile,
    FaultyBroker,
    FaultyStore,
    Forced,
)
from kaibot.broker.protocol import BrokerError, OrderRequest

# found at the parent commits (see the pull request), each confirmed on the commit before its fix and on the fix;
# each breaks the named invariant with its fix taken away, and holds them all as the engine is
SEEDS = {
    "735": (387, 579),  # also 873: invariant 12c at 64de471, held at da4a471
    "737": (1637, 2098),  # also 1705, 2854: invariant 12a at da4a471, held at f31614a
    "736": (83, 95),  # also 138: invariant 12b at f31614a (and before), held at 60c7b03
}
PLANTS = {"735": lease_blip_drops_the_buy, "737": positions_read_fails_the_minute, "736": refused_sale_not_sent_again}
WORDS = {"735": "invariant 12c", "737": "invariant 12a", "736": "invariant 12b"}


class _Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def _broker(profile, t):
    from world import Venue, minute_frames

    from kaibot.replay.history import HistoricalData

    clk = _Clock(t)
    sim = Venue(HistoricalData(minute_frames(0), clk).history, clk)
    return FaultyBroker(sim, 5, profile), clk


def test_the_new_kinds_are_in_the_profile_and_off_in_the_summer_week():
    assert all(getattr(DEFAULT_FAULTS, k) > 0 for k in AUDIT2_FAULTS)
    for seed in range(1, 80):
        s = Scenario.from_seed(seed)
        if week_of(seed) is SUMMER:
            assert all(getattr(s.profile, k) == 0 for k in AUDIT2_FAULTS)
        elif s.profile.hold > 0:  # (a seed whose faults are scaled to zero has none at all)
            assert all(getattr(s.profile, k) > 0 for k in AUDIT2_FAULTS)


def test_a_sale_refusal_is_drawn_per_attempt_and_takes_nothing():
    b, _ = _broker(FaultProfile(sale_refused=0.5), datetime(2026, 8, 3, 19, 50, tzinfo=UTC))
    b.sim.pos["B"] = {"qty": 30.0, "avg_entry_price": "100"}
    b.sim.wash_check = False
    refused = []
    for n in range(1, 21):
        try:
            b.submit(OrderRequest("B", "sell", 1, "market", "day", f"kb1-r-B-20260803-sell-{n}"))
        except BrokerError as e:
            assert e.status == 403
            refused.append(n)
    assert 3 <= len(refused) <= 17  # drawn per client order id
    assert not any(o["client_order_id"].endswith(f"-{n}") for o in b.sim.orders.values() for n in refused)
    b.submit(OrderRequest("B", "buy", 1, "market", "day", "kb1-r-B-20260803-buy-1"))  # never a buy


def test_a_partial_outage_hits_one_endpoint_for_a_spell():
    t0 = datetime(2026, 8, 4, 19, 0, tzinfo=UTC)
    b, clk = _broker(NO_FAULTS, t0)
    b.forced = (Forced("endpoint_down", f"positions|{t0:%Y%m%dT%H}"),)  # the whole hour 19:00-19:59 UTC
    with pytest.raises(BrokerError) as e:
        b.positions()
    assert e.value.status == 503
    b.open_orders()
    b.account()
    clk.t = t0 + timedelta(hours=1)
    b.positions()
    # drawn: with a probability of one per minute every endpoint is down; the spell lasts 5, 15 or 40 minutes
    b2, _ = _broker(FaultProfile(endpoint_down=1.0), t0)
    for call in DOWN_ENDPOINTS:
        with pytest.raises(BrokerError):
            b2._limited(call)
    b2._limited("submit")  # never an order call


def test_a_lease_renewal_spell_fails_every_renewal_and_ends():
    from fakes import FakeStore

    t = datetime(2026, 8, 4, 19, 50, tzinfo=UTC)
    clk = _Clock(t)
    inner = FakeStore()
    inner.lease_now = clk
    inner.lease_acquire("H", "h", 90, "m")
    store = FaultyStore(inner, 3, FaultProfile(lease_renew_fail=1.0), now=clk)
    with pytest.raises(Exception, match="lease_renew"):
        store.lease_renew("H", 90, "m")
    store.armed = False
    store.lease_renew("H", 90, "m")  # healed: passed through to the store
    # one spell in a quiet hour, at most two minutes long, the same minutes whoever asks
    store = FaultyStore(inner, 3, FaultProfile(lease_renew_fail=0.02), now=clk)
    down = []
    for m in range(600):
        clk.t = t + timedelta(minutes=m)
        try:
            store.lease_renew("H", 90, "m")
        except Exception:
            down.append(m)
    again = []
    for m in range(600):
        clk.t = t + timedelta(minutes=m)
        try:
            store.lease_renew("H", 90, "m")
        except Exception:
            again.append(m)
    assert down and down == again and len(down) < 60


def test_the_seeds_reach_every_new_kind():
    """Over the new weeks' seeds of the suite's own lists, each new kind is injected (cheap: the runs are needed
    anyway by the reproduction tests below, so only their stats are read)."""
    kinds: set[str] = set()
    for seeds in SEEDS.values():
        for seed in seeds[:1]:
            kinds |= set(run_seed(seed).stats["fault_kinds"])
    assert {"lease_renew_fail", "sale_refused", "endpoint_down"} <= kinds, kinds


@pytest.mark.parametrize(("fix", "seed"), [(f, s) for f, ss in SEEDS.items() for s in ss])
def test_the_fix_is_found_from_a_seed_when_it_is_taken_away(fix, seed, monkeypatch):
    """5.18's "5.8-5.10 reproduce from seeds before their fixes and not after", in the suite: the seed holds every
    invariant as the engine is; with the fix planted away it breaks the one the fix is about."""
    assert week_of(seed) is not SUMMER
    res = run_seed(seed)
    assert res.ok, res.report()
    PLANTS[fix](monkeypatch)
    bad = run_seed(seed)
    assert any(WORDS[fix] in v for v in bad.violations), bad.report()


def test_a_refused_sale_is_sent_again_in_the_window_on_the_live_loop():
    """#736 at work behind the lease's fence: Thanksgiving week, the 25 Nov decision sells B on 27 Nov at 12:50; the
    venue refuses the first two attempts; the third goes out at 12:52 and fills."""
    res = run_scenario(quiet_in(THANKSGIVING, {"2026-11-25": ("B",), "2026-11-27": ()}, lease=True,
                                forced=(Forced("submit_refused", "|B|sell", 2),)))
    assert res.ok, res.report()
    sells = [t for t in res.trace if " om_intent " in t and "-B-20261127-sell-" in t]
    assert [t[6:11] for t in sells] == ["12:50", "12:51", "12:52"], sells
