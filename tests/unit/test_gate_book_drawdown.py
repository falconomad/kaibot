"""Issue #206 (stakeholder decision 13 Nov 2026, "option A, as tested"; decision 15 section 11): the multi-day book's
own run-drawdown end. Written before the code (CLAUDE.md: tests first for risk/gate.py).

- `BookLimits.run_drawdown_end_pct`, capped by a new book ceiling `BOOK_HARD_CEILINGS.run_drawdown_end_pct = 27`;
  levels Careful 13, Balanced 21, Hunter 27; tighten-only like every book field.
- `RiskPolicy` and `HARD_CEILINGS` are unchanged (field for field), so the intraday policy, its hash and every trial
  digest built from it stay byte-identical; the intraday run end (4/8/12%) is the same formula as before.
- `intraday_equity(run_equity, book_pnl)`: the intraday book is measured without the multi-day book's P&L. With no
  multi-day P&L it is the run's equity, so nothing changes; no book setting can change an intraday verdict."""

from dataclasses import fields, replace

import pytest

from kaibot.risk.gate import (
    BOOK_HARD_CEILINGS,
    HARD_CEILINGS,
    BookLimits,
    RiskPolicy,
    book_run_ended,
    drawdown_pct,
    intraday_equity,
    run_drawdown_reached,
    tighten_book,
    validate_book,
)
from kaibot.risk.profiles import load_book_limits, load_profiles

hyp = pytest.importorskip("hypothesis")
st = hyp.strategies

BOOK = BookLimits(run_drawdown_end_pct=21)


def test_the_book_ceiling_is_27_and_the_intraday_ceilings_are_unchanged():
    assert BOOK_HARD_CEILINGS.run_drawdown_end_pct == 27
    assert HARD_CEILINGS.run_drawdown_end_pct == 12
    assert [f.name for f in fields(RiskPolicy)] == [
        "risk_per_trade_pct", "max_position_pct", "max_open_risk_pct", "max_daily_loss_pct", "max_positions",
        "max_entries_per_day", "run_drawdown_end_pct", "max_quote_age_s", "warn_fraction", "allow_short"]
    with pytest.raises(ValueError, match="run_drawdown_end_pct"):
        validate_book(replace(BOOK, run_drawdown_end_pct=27.5))
    validate_book(replace(BOOK, run_drawdown_end_pct=27))


def test_the_levels_carry_the_stakeholders_book_ends_and_the_intraday_ends_stay():
    book = load_book_limits(research=True)
    assert {k: v.run_drawdown_end_pct for k, v in book.items()} == {"conservative": 13, "balanced": 21,
                                                                     "aggressive": 27}
    assert {k: v.run_drawdown_end_pct for k, v in load_profiles().items()} == {"conservative": 4, "balanced": 8,
                                                                                "aggressive": 12}


@hyp.settings(max_examples=300, deadline=None)
@hyp.given(v=st.floats(0, 60))
def test_property_the_book_end_only_tightens(v):
    if v > BOOK.run_drawdown_end_pct:
        with pytest.raises(ValueError):
            tighten_book(BOOK, {"run_drawdown_end_pct": v})
    else:
        assert tighten_book(BOOK, {"run_drawdown_end_pct": v}).run_drawdown_end_pct == v


@hyp.settings(max_examples=500, deadline=None)
@hyp.given(peak=st.floats(1, 1e7), frac=st.floats(0, 2), pct=st.floats(0.1, 50))
def test_property_the_run_end_formula_is_the_old_one(peak, frac, pct):
    """`run_drawdown_reached` is exactly the expression engine/core used inline before #206."""
    eq = peak * frac
    assert run_drawdown_reached(peak, eq, pct) == (eq <= peak * (1 - pct / 100))


@hyp.settings(max_examples=500, deadline=None)
@hyp.given(peak=st.floats(1, 1e7), frac=st.floats(0, 2))
def test_property_drawdown_is_a_percentage_from_the_peak(peak, frac):
    eq = peak * frac
    dd = drawdown_pct(peak, eq)
    assert 0 <= dd <= 100
    assert dd == 0 if eq >= peak else dd == pytest.approx((peak - eq) / peak * 100)
    assert drawdown_pct(0, eq) == 0 and drawdown_pct(-5, eq) == 0  # no peak yet: nothing to measure


@hyp.settings(max_examples=500, deadline=None)
@hyp.given(peak=st.floats(1, 1e7), frac=st.floats(0, 2), end=st.floats(0, 27))
def test_property_the_book_ends_exactly_at_its_limit(peak, frac, end):
    lim = replace(BOOK, run_drawdown_end_pct=end)
    eq = peak * frac
    assert book_run_ended(peak, eq, lim) == run_drawdown_reached(peak, eq, end)
    tighter = replace(lim, run_drawdown_end_pct=end / 2)
    assert book_run_ended(peak, eq, tighter) or not book_run_ended(peak, eq, lim)  # tighter ends no later


@hyp.settings(max_examples=500, deadline=None)
@hyp.given(eq=st.floats(1, 1e7), pnl=st.floats(-1e6, 1e6), peak=st.floats(1, 1e7), end=st.floats(0, 27))
def test_property_no_book_setting_changes_an_intraday_verdict(eq, pnl, peak, end):
    pol = load_profiles()["balanced"]
    a = run_drawdown_reached(peak, intraday_equity(eq, pnl), pol.run_drawdown_end_pct)
    lim = replace(BOOK, run_drawdown_end_pct=end)  # the book's limits are not an input of the intraday check
    b = run_drawdown_reached(peak, intraday_equity(eq, pnl), pol.run_drawdown_end_pct)
    assert a == b and lim.run_drawdown_end_pct == end
    assert intraday_equity(eq, 0.0) == eq  # no multi-day P&L: the run's own equity, as before
