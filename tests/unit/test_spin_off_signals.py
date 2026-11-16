"""#266 (a): a spin-off is value kept in the signals the momentum rules rank on (written before the code). The parent's
history before the ex-date is multiplied by P / (P + V), V the spun value per parent share, from a sourced record
(the daily store's corporate-actions table, type spin_off): the new shares' first close x the record's ratio; else
Alpaca's own close factor that day, net of the table's split and dividend; else the ex-day drop itself. Only from
the ex-date on (point in time). Research credits V as cash at the ex-date. Without records nothing changes.
QA-Ops second key (#275): no ex-day-drop fallback; a spin-off with neither source is not adjusted and its key is not
ranked while the fall is in the look-back."""

from __future__ import annotations

import numpy as np
import pytest
from test_core_pit import raw_market

from kaibot.replay import multiday_core as mc

EX = 430


def spun(panel, j, frac):
    """The parent loses `frac` of its price from row EX on (the spun value leaves it)."""
    for a in (panel.o, panel.h, panel.l, panel.c):
        a[EX:, j] *= 1.0 - frac
    return panel


def test_without_records_the_signals_are_unchanged():
    panel, stocks = raw_market()
    a = mc.Market.from_panel(panel, stocks, "SPY")
    b = mc.Market.from_panel(panel, stocks, "SPY", spin_offs=[])
    np.testing.assert_array_equal(a.sig.c, b.sig.c)


def child_record(panel, j, frac):
    """A sourced record: P21 plays the new company, its ratio sized so that ratio x its close is the spun value."""
    k = panel.col("P21")
    v = panel.c[EX - 1, j] * frac / (1.0 - frac) * (1.0 - frac)  # the value that left the parent at EX
    return [{"key": "P20", "ex_date": str(panel.dates[EX]), "new_symbol": "P21", "ratio": v / panel.c[EX, k]}]


def test_a_spin_off_is_not_a_crash_in_the_signals():
    panel, stocks = raw_market()
    j = panel.col("P20")
    spun(panel, j, 0.85)  # CTVA-sized
    rec = child_record(panel, j, 0.85)
    plain = mc.Market.from_panel(panel, stocks, "SPY")
    m = mc.Market.from_panel(panel, stocks, "SPY", spin_offs=rec)
    assert plain.sig.c[EX, j] / plain.sig.c[EX - 1, j] < 0.2  # today: an 85% one-day fall
    r = m.sig.c[EX, j] / m.sig.c[EX - 1, j]
    assert 0.9 < r < 1.1  # value kept
    assert m.raw.c[EX - 1, j] == panel.c[EX - 1, j]  # orders and costs still read raw prices
    assert m.signals.px[EX - 1, j] == pytest.approx(panel.c[EX - 1, j])  # a tradable price is still raw
    assert not m.signals.big_move[EX - 5:EX + 60, j].any()
    (note,) = m.notes["spin_offs"]
    assert note["key"] == "P20" and note["source"] == "child_close"


def test_the_spun_value_comes_from_the_new_shares_close_when_the_store_has_them():
    panel, stocks = raw_market()
    j, k = panel.col("P20"), panel.col("P21")
    p_ex = panel.c[EX, j] * 0.6  # the parent after the spin
    spun(panel, j, 0.4)
    v = panel.c[EX - 1, j] * 0.4  # the true spun value per parent share
    ratio = v / panel.c[EX, k]  # P21 plays the new company: ratio x its close = v
    rec = [{"key": "P20", "ex_date": str(panel.dates[EX]), "new_symbol": "P21", "ratio": ratio}]
    m = mc.Market.from_panel(panel, stocks, "SPY", spin_offs=rec)
    step = m.sig.c[EX - 1, j] / panel.c[EX - 1, j] / (m.sig.c[EX, j] / panel.c[EX, j])
    assert step == pytest.approx(p_ex / (p_ex + v), rel=1e-9)
    assert m.notes["spin_offs"][0]["source"] == "child_close"


def test_point_in_time_a_view_cut_before_the_ex_date_is_untouched():
    panel, stocks = raw_market()
    j = panel.col("P20")
    spun(panel, j, 0.5)
    rec = [{"key": "P20", "ex_date": str(panel.dates[EX]), "new_symbol": None, "ratio": None}]
    m = mc.Market.from_panel(panel, stocks, "SPY", spin_offs=rec)
    plain = mc.Market.from_panel(panel, stocks, "SPY")
    # rows before the ex-date are one constant per symbol apart: every return the rules read before EX is the same
    ratio = m.sig.c[:EX, j] / plain.sig.c[:EX, j]
    assert np.nanmax(ratio) == pytest.approx(np.nanmin(ratio), rel=1e-12)


def test_research_credits_the_spun_value_as_cash_at_the_ex_date():
    panel, stocks = raw_market()
    j = panel.col("P20")
    spun(panel, j, 0.5)
    rec = child_record(panel, j, 0.5)
    m = mc.Market.from_panel(panel, stocks, "SPY", spin_offs=rec)
    split, div = m.actions
    v = rec[0]["ratio"] * panel.c[EX, panel.col("P21")]  # the record's ratio x the new company's close
    assert div[EX, j] == pytest.approx(v, rel=1e-9)


def test_a_disputed_key_is_left_alone():
    panel, stocks = raw_market()
    j = panel.col("P20")
    spun(panel, j, 0.5)
    rec = [{"key": "P20", "ex_date": str(panel.dates[EX]), "new_symbol": None, "ratio": None}]
    m = mc.Market.from_panel(panel, stocks, "SPY", spin_offs=rec, disputed=["P20"])
    assert m.notes["spin_offs"][0]["source"] == "skipped: disputed key"


def test_an_unsourced_spin_off_is_not_adjusted_and_not_ranked_while_its_fall_is_in_the_look_back():
    """QA-Ops second key (#275): no child close and no Alpaca factor. The ex-day fall is not taken as spun value (it
    would count the parent's own move that day and could lift its momentum): the series is left as it is, and the
    key is not rankable (never bought) from the ex-date for SPIN_UNSOURCED_SESSIONS sessions, but is before it."""
    panel, stocks = raw_market()
    j = panel.col("P20")
    spun(panel, j, 0.5)
    rec = [{"key": "P20", "ex_date": str(panel.dates[EX]), "new_symbol": None, "ratio": None}]
    plain = mc.Market.from_panel(panel, stocks, "SPY")
    m = mc.Market.from_panel(panel, stocks, "SPY", spin_offs=rec)
    np.testing.assert_array_equal(m.sig.c, plain.sig.c)  # nothing invented
    (note,) = m.notes["spin_offs"]
    assert note["source"] == "not adjusted: no sourced value"
    split, div = m.actions
    assert div[EX, j] == plain.actions[1][EX, j]  # no value credited either
    ok, base = m.signals.ok, plain.signals.ok  # rankable: price, liquidity and membership
    jj = m.raw.col("P20")
    assert base[EX:, jj].any()  # the plain view would rank it after the ex-date
    n = min(mc.SPIN_UNSOURCED_SESSIONS, ok.shape[0] - EX)
    assert not ok[EX:EX + n, jj].any()
    np.testing.assert_array_equal(ok[:EX, jj], base[:EX, jj])  # nothing changes before the ex-date
