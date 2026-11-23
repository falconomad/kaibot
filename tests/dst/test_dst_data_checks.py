"""M12 ticket 3.11 (#473): the fault seeds of ticket 3.2 with bad minute bars (kaibot.broker.faulty: bar_zero,
bar_spike, bar_stale) trigger the data-check block on entries when KAIBOT_DATA_CHECKS_BLOCK is on, and change nothing
when it is off. The block holds a buy back (it goes out on a later session, the carry-over); sales are never held,
and every invariant of the simulation still holds."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC

import pytest
from scenarios import quiet
from world import D1, D2, World, et, run_seed

from kaibot.broker.faulty import DEFAULT_FAULTS
from kaibot.engine import market as mk


def buys(w: World, minute: bool = False) -> list[tuple[str, str]]:
    return sorted((o["symbol"], str(o["created_at"])[:16 if minute else 10]) for o in w.sim.orders.values()
                  if o["side"] == "buy")


def run_with(fault: str | None, switch: str | None, monkeypatch, plan=None, start="15:30", until="15:50"):
    monkeypatch.delenv(mk.DATA_CHECKS_ENV, raising=False)
    if switch is None:
        monkeypatch.delenv(mk.DATA_BLOCK_ENV, raising=False)
    else:
        monkeypatch.setenv(mk.DATA_BLOCK_ENV, switch)
    w = World(quiet(plan or {D1: ("B",)}))
    if fault is not None:  # B's minute bars go bad from `start` to the send window (15:50-15:57 ET) on D1
        w.data.bar_faults = [("B", fault, et(D1, start).astimezone(UTC), et(D1, until).astimezone(UTC))]
    res = w.run()
    assert res.ok, res.report()
    return w, res


@pytest.mark.parametrize("fault", ["bar_zero", "bar_spike", "bar_stale"])
def test_bad_bars_change_nothing_when_off(fault, monkeypatch):
    clean, _ = run_with(None, None, monkeypatch)
    assert buys(clean) == [("B", D1)]
    off, res_off = run_with(fault, None, monkeypatch)
    assert fault in res_off.stats["fault_kinds"]
    assert buys(off, minute=True) == buys(clean, minute=True)  # off: the bad bars change no order, not by a minute


def test_a_bad_print_holds_the_buy_back_for_the_session(monkeypatch):
    """A zero price anywhere in today's bars is a bad_price finding for the rest of the day: the buy waits for the
    next session (the carry-over buys it on D2, on clean bars)."""
    on, _ = run_with("bar_zero", "on", monkeypatch)
    assert buys(on) == [("B", D2)]


def test_a_stale_stock_is_bought_only_once_its_bars_are_fresh_again(monkeypatch):
    clean, _ = run_with(None, None, monkeypatch)
    on, _ = run_with("bar_stale", "on", monkeypatch)  # no bar of B from 15:30: stale at 15:50, fresh at 15:51
    (_, t_clean), (_, t_on) = buys(clean, minute=True)[0], buys(on, minute=True)[0]
    assert t_on[:10] == D1 and t_on > t_clean


def test_a_move_the_index_does_not_share_holds_the_buy_while_it_is_in_the_window(monkeypatch):
    clean, _ = run_with(None, None, monkeypatch)
    on, _ = run_with("bar_spike", "on", monkeypatch, start="15:46")  # B 8 % off from 15:46 to 15:49
    (_, t_clean), (_, t_on) = buys(clean, minute=True)[0], buys(on, minute=True)[0]
    assert t_on[:10] == D1 and t_on > t_clean  # held back until the jump back has left the five-minute window


def test_a_stock_with_clean_bars_is_bought_while_another_one_is_bad(monkeypatch):
    on, _ = run_with("bar_zero", "on", monkeypatch, plan={D1: ("B", "C")})
    assert buys(on) == [("B", D2), ("C", D1)]


def test_only_the_named_checks_block(monkeypatch):
    named, _ = run_with("bar_zero", "stale", monkeypatch)  # a zero print is bad_price, not named
    assert buys(named) == [("B", D1)]


BAD_BARS = replace(DEFAULT_FAULTS, bar_zero=0.002, bar_spike=0.002, bar_stale=0.01)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_invariants_hold_under_fault_seeds_with_bad_bars_and_the_block_on(seed, monkeypatch):
    monkeypatch.setenv(mk.DATA_BLOCK_ENV, "on")
    res = run_seed(seed, BAD_BARS)
    assert res.ok, res.report()


def test_the_default_fault_profile_has_no_bar_faults():
    """The pull-request seeds draw exactly what they drew before this ticket."""
    assert DEFAULT_FAULTS.bar_zero == DEFAULT_FAULTS.bar_spike == DEFAULT_FAULTS.bar_stale == 0.0
