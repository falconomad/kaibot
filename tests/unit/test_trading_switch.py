"""M10 1.21 (#446), step 2: the trading switch the triggers set (engine/state.py `TradingSwitch`), and the engine's
fail-closed halt (`Engine.kill`) as its `halt` hold. Behaviour-neutral: `kill` reads and writes as before."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kaibot.engine.core import Engine
from kaibot.engine.state import Trading, TradingState, TradingSwitch

HOLDS = st.lists(st.tuples(st.sampled_from(["halt", "owner", "lease", "daily"]),
                           st.sampled_from([TradingState.REDUCING_ONLY, TradingState.HALTED]),
                           st.text(min_size=1, max_size=8)), max_size=8)


@given(HOLDS, st.sets(st.sampled_from(["halt", "owner", "lease", "daily"])))
def test_the_switch_is_the_strictest_hold_left(holds, released):
    sw, left = TradingSwitch(), {}
    for source, state, why in holds:
        sw.hold(source, state, why)
        left[source] = Trading(state, why)
    for source in released:
        sw.release(source)
        left.pop(source, None)
    got = sw.state()
    if not left:
        assert got == Trading(TradingState.ACTIVE)
    else:
        worst = TradingState.HALTED if any(t.state is TradingState.HALTED for t in left.values()) else \
            TradingState.REDUCING_ONLY
        assert got.state is worst and got in left.values()
    for source in ("halt", "owner", "lease", "daily"):
        assert sw.held(source) == (left[source].why if source in left else None)


def test_a_hold_is_never_active():
    with pytest.raises(ValueError):
        TradingSwitch().hold("owner", TradingState.ACTIVE, "x")


def test_the_engines_kill_is_the_switchs_halt_hold():
    e = Engine.__new__(Engine)  # only the attributes the switch needs
    assert e.kill is None and e.trading.state() == Trading(TradingState.ACTIVE)
    e.kill = "drift"
    assert e.kill == "drift" and e.trading.held("halt") == "drift"
    assert e.trading.state() == Trading(TradingState.REDUCING_ONLY, "drift")
    e.kill = "broker_rejects"  # a later halt replaces it, as the assignment did
    assert e.kill == "broker_rejects"
    e.kill = None
    assert e.kill is None and e.trading.state() == Trading(TradingState.ACTIVE)
    e.kill = ""  # falsy, as before: no halt
    assert not e.kill and e.trading.held("halt") is None


def test_entry_block_still_reads_the_halt_in_its_place_and_words():
    e = Engine.__new__(Engine)
    e.run, e._cmd_waiting, e.account_clean = {"status": "running"}, {}, True
    e.recon = type("R", (), {"last": type("L", (), {"diffs": []})(), "blocks_every_buy": lambda self: False})()
    e.lease, e.clock_skew, e.day = None, None, None
    assert e.entry_block() is None
    e.kill = "llm_budget"
    assert e.entry_block() == "halted: llm_budget"
    e.account_clean = False  # an earlier block still wins, as before
    assert e.entry_block() == "account not clean"
