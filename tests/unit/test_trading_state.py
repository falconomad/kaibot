"""M10 1.21 (#446), step 1: the one trading state (engine/state.py) and its map from the engine's entry blocks."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from kaibot.engine.state import Trading, TradingState, allows, from_entry_block

STATES = st.sampled_from(list(TradingState))
SIDES = st.sampled_from(["buy", "sell"])


@given(STATES, SIDES)
def test_no_buy_leaves_unless_active_and_only_cancels_when_halted(state, side):
    assert allows(state, "submit", side) == (state is TradingState.ACTIVE or
                                             (state is TradingState.REDUCING_ONLY and side == "sell"))
    assert allows(state, "cancel")
    if state is TradingState.HALTED:
        assert not allows(state, "submit", side) and not allows(state, "replace")


@given(st.one_of(st.none(), st.text(min_size=1)))
def test_every_entry_block_is_reducing_only(why):
    t = from_entry_block(why)
    assert t == (Trading(TradingState.ACTIVE) if why is None else Trading(TradingState.REDUCING_ONLY, why))


def test_the_engine_derives_it_from_entry_block():
    from kaibot.engine.core import Engine

    class E:
        trading_state = Engine.trading_state

        def __init__(self, why):
            self.why = why

        def entry_block(self):
            return self.why

    assert E(None).trading_state() == Trading(TradingState.ACTIVE)
    assert E("daily stop").trading_state() == Trading(TradingState.REDUCING_ONLY, "daily stop")
    assert TradingState.REDUCING_ONLY == "reducing_only"
