"""M12 ticket 3.5 (#467): tests written for surviving mutants of `engine/lease.py` (the first mutation run).

The renewal's rate limit (`RENEW_MIN_GAP_S`) had no test: a lease that renewed on every progress callback, or never
waited at the boundary, passed the suite (mutants `renew__mutmut_11`, `_12`, `_13`).
"""

from __future__ import annotations

import pytest

from kaibot.engine import lease as L


class Store:
    def __init__(self) -> None:
        self.renewals = 0

    def lease_acquire(self, holder, host, ttl, machine):
        return {"acquired": True, "host": host}

    def lease_renew(self, holder, ttl, machine, epoch=None):
        self.renewals += 1
        return True


def _lease():
    t = [1000.0]
    store = Store()
    lease = L.EngineLease(store, "h", "mac", mono=lambda: t[0], wall=lambda: t[0])
    lease.acquire()
    return lease, store, t


def test_renewals_closer_than_the_gap_reach_the_database_once():
    lease, store, t = _lease()
    assert lease.renew() is True and store.renewals == 0  # just acquired: inside the gap
    t[0] += L.RENEW_MIN_GAP_S - 0.5
    assert lease.renew() is True and store.renewals == 0
    t[0] += 1.0  # now past the gap since the acquire
    assert lease.renew() is True and store.renewals == 1
    for _ in range(5):  # a burst of progress callbacks
        t[0] += 0.1
        assert lease.renew() is True
    assert store.renewals == 1


def test_a_renewal_exactly_at_the_gap_goes_out():
    lease, store, t = _lease()
    t[0] += L.RENEW_MIN_GAP_S
    lease.renew()
    assert store.renewals == 1


@pytest.mark.parametrize("force", [True])
def test_force_renews_inside_the_gap(force):
    lease, store, t = _lease()
    t[0] += 0.1
    lease.renew(force=force)
    assert store.renewals == 1
