"""Issue #119 (M7·S79): the gate's book-level checks. Written before the code (CLAUDE.md: tests first for the gate).

The book fields of `Snapshot` (gross, overnight and sector exposure, the gap-scenario loss) are optional. A snapshot
without them, which is every snapshot the intraday path builds today, gets exactly today's verdict; a proposal for a
multi-day sleeve (`overnight=True`) without them is refused (fail closed)."""

from dataclasses import fields, replace

import pytest

from kaibot.risk.gate import (
    BOOK_HARD_CEILINGS,
    GAP_SCENARIO_ATR,
    GAP_SCENARIO_PCT,
    HARD_CEILINGS,
    BookLimits,
    Proposal,
    RiskPolicy,
    Side,
    Snapshot,
    evaluate,
    gap_loss_ps,
    tighten_book,
    validate_book,
)

hyp = pytest.importorskip("hypothesis")
st = hyp.strategies

POL = RiskPolicy(risk_per_trade_pct=0.5, max_position_pct=15, max_open_risk_pct=1.5, max_daily_loss_pct=1.5,
                 max_positions=8)
BOOK = BookLimits(atr_risk_pct=0.1, max_name_pct=15, max_gross_pct=100, max_overnight_pct=100, max_sector_pct=30,
                  max_names=20, min_cash_pct=2, max_gap_loss_pct=12)


def snap(**kw):
    base = dict(equity=100_000, day_start_equity=100_000, open_positions=0, symbol_exposure=0, open_risk=0,
                quote_age_s=1, kill_switch=False, reconciled=True, market_open=True)
    base.update(kw)
    return Snapshot(**base)


def book_snap(**kw):
    base = dict(gross_exposure=0.0, overnight_exposure=0.0, sector_exposure=0.0, gap_loss=0.0, overnight=True)
    base.update(kw)
    return snap(**base)


# ------------------------------------------------------------------ the intraday path is untouched
def _same(a, b):
    assert (a.approved, a.qty, a.binding, a.max_loss) == (b.approved, b.qty, b.binding, b.max_loss)
    assert [(c.rule, c.state, c.actual, c.limit) for c in a.checks] == [(c.rule, c.state, c.actual, c.limit)
                                                                       for c in b.checks]


@hyp.settings(max_examples=300, deadline=None)
@hyp.given(entry=st.floats(1, 1000), stop_frac=st.floats(0.5, 0.999), equity=st.floats(1000, 1e7),
           open_risk_frac=st.floats(0, 0.03), atr_frac=st.floats(0.001, 0.2))
def test_property_without_book_fields_the_verdict_is_todays(entry, stop_frac, equity, open_risk_frac, atr_frac):
    """No book field set and not overnight: the book limits and the proposal's ATR change nothing at all, so the
    intraday dip path (and its goldens) keeps today's decisions."""
    s = snap(equity=equity, day_start_equity=equity, open_risk=equity * open_risk_frac)
    p = Proposal("X", Side.BUY, entry, entry * stop_frac)
    base = evaluate(p, s, POL)
    _same(base, evaluate(p, s, POL, BOOK))
    _same(base, evaluate(replace(p, atr=entry * atr_frac), s, POL, BOOK))
    assert {c.rule for c in base.checks}.isdisjoint({"gross_pct", "overnight_pct", "sector_pct", "gap_loss_pct",
                                                     "book_known"})


def test_snapshot_book_fields_default_to_unknown():
    s = snap()
    assert (s.gross_exposure, s.overnight_exposure, s.sector_exposure, s.gap_loss, s.overnight) == (
        None, None, None, None, False)


# ------------------------------------------------------------------ fail closed for multi-day sleeves
@pytest.mark.parametrize("missing", ["gross_exposure", "overnight_exposure", "sector_exposure", "gap_loss"])
def test_overnight_proposal_without_the_book_is_refused(missing):
    v = evaluate(Proposal("X", Side.BUY, 100, 80, atr=2), book_snap(**{missing: None}), POL, BOOK)
    assert not v.approved and "book_known" in {c.rule for c in v.failures}


def test_overnight_proposal_without_book_limits_is_refused():
    v = evaluate(Proposal("X", Side.BUY, 100, 80, atr=2), book_snap(), POL)
    assert not v.approved and "book_known" in {c.rule for c in v.failures}


def _reviewer_case(atr):
    """QA-Ops review of PR #163: balanced research limits, equity 100k, the book already loses $11,900 in the gap
    scenario, entry 100, stop 50."""
    from kaibot.risk.profiles import load_book_limits, load_profiles
    pol, book = load_profiles()["balanced"], load_book_limits(research=True)["balanced"]
    return evaluate(Proposal("X", Side.BUY, 100, 50, atr=atr), book_snap(gap_loss=11_900), pol, book), book


@pytest.mark.parametrize("atr", [None, 0.0, -1.0, float("nan"), float("inf")])
def test_overnight_buy_without_a_usable_atr_is_refused(atr):
    """Fail closed: without ATR(20) the gap scenario would fall back to the flat 10% gap and under-count the loss
    (reviewer's repro: 10 shares approved at 12.0% reported; with the real ATR of 30 the loss is 12.8% > 12%)."""
    v, _ = _reviewer_case(atr)
    assert not v.approved
    assert "atr_known" in {c.rule for c in v.failures}


def test_overnight_buy_with_the_real_atr_is_sized_by_the_gap_cap():
    v, book = _reviewer_case(30.0)
    assert v.approved and v.qty == 1 and v.binding == "gap_headroom"
    assert 11_900 + v.qty * gap_loss_ps(100, 30.0) <= 100_000 * book.max_gap_loss_pct / 100


def test_atr_rule_leaves_intraday_and_sells_alone():
    intraday = evaluate(Proposal("X", Side.BUY, 100, 80), snap(), POL, BOOK)
    assert "atr_known" not in {c.rule for c in intraday.checks}
    sell = evaluate(Proposal("X", Side.SELL, 100, 120), book_snap(symbol_exposure=10_000), POL, BOOK)
    assert "atr_known" not in {c.rule for c in sell.failures}


def test_overnight_proposal_with_the_book_passes():
    v = evaluate(Proposal("X", Side.BUY, 100, 80, atr=2), book_snap(), POL, BOOK)
    assert v.approved and v.qty > 0
    assert {"gross_pct", "overnight_pct", "sector_pct", "gap_loss_pct"} <= {c.rule for c in v.checks}


# ------------------------------------------------------------------ worked examples
def test_gap_scenario_per_share():
    assert GAP_SCENARIO_PCT == 10.0 and GAP_SCENARIO_ATR == 3.0
    assert gap_loss_ps(100, 2) == pytest.approx(10.0)  # the 10% floor binds for a calm name
    assert gap_loss_ps(100, 5) == pytest.approx(15.0)  # 3 ATR binds for a wild one
    assert gap_loss_ps(100, None) == pytest.approx(10.0)


def test_gross_headroom_binds():
    # 90% invested, 98% allowed (2% cash buffer): $8,000 left = 80 shares at $100
    v = evaluate(Proposal("X", Side.BUY, 100, 99, atr=2), book_snap(gross_exposure=90_000, overnight_exposure=0),
                 POL, BOOK)
    assert v.approved and v.qty == 80 and v.binding == "gross_headroom"


def test_sector_headroom_binds():
    v = evaluate(Proposal("X", Side.BUY, 100, 99, atr=2), book_snap(sector_exposure=29_000), POL, BOOK)
    assert v.approved and v.qty == 10 and v.binding == "sector_headroom"


def test_overnight_headroom_binds_and_zero_cap_blocks():
    v = evaluate(Proposal("X", Side.BUY, 100, 99, atr=2), book_snap(overnight_exposure=95_000), POL,
                 replace(BOOK, max_overnight_pct=97))
    assert v.approved and v.qty == 20 and v.binding == "overnight_headroom"
    v0 = evaluate(Proposal("X", Side.BUY, 100, 80, atr=2), book_snap(), POL, replace(BOOK, max_overnight_pct=0))
    assert not v0.approved and v0.qty == 0


def test_gap_headroom_binds():
    # book already loses $11,500 in the gap scenario, 12% = $12,000 allowed: $500 / $10 per share = 50 shares
    v = evaluate(Proposal("X", Side.BUY, 100, 99, atr=2), book_snap(gap_loss=11_500), POL, BOOK)
    assert v.approved and v.qty == 50 and v.binding == "gap_headroom"


def test_sell_is_never_blocked_by_book_caps():
    """Selling reduces every exposure: the book caps do not apply to a sell of a held name."""
    s = book_snap(gross_exposure=200_000, overnight_exposure=200_000, sector_exposure=200_000, gap_loss=50_000,
                  symbol_exposure=10_000)
    v = evaluate(Proposal("X", Side.SELL, 100, 120, atr=2), s, POL, BOOK)
    assert {"gross_pct", "overnight_pct", "sector_pct", "gap_loss_pct"}.isdisjoint({c.rule for c in v.failures})


# ------------------------------------------------------------------ properties
@hyp.settings(max_examples=500, deadline=None)
@hyp.given(entry=st.floats(1, 2000), stop_frac=st.floats(0.5, 0.99), atr_frac=st.floats(0.002, 0.15),
           equity=st.floats(1000, 1e7), gross=st.floats(0, 1.2), overnight_share=st.floats(0, 1),
           sector=st.floats(0, 0.5), gap=st.floats(0, 0.2), gross_cap=st.floats(0, 100),
           overnight_cap=st.floats(0, 100), sector_cap=st.floats(0, 40), gap_cap=st.floats(0, 15),
           cash=st.floats(1, 50), overnight=st.booleans())
def test_property_approved_orders_respect_every_book_cap(entry, stop_frac, atr_frac, equity, gross,
                                                         overnight_share, sector, gap, gross_cap, overnight_cap,
                                                         sector_cap, gap_cap, cash, overnight):
    book = replace(BOOK, max_gross_pct=gross_cap, max_overnight_pct=overnight_cap, max_sector_pct=sector_cap,
                   max_gap_loss_pct=gap_cap, min_cash_pct=cash)
    g = equity * gross
    s = snap(equity=equity, day_start_equity=equity, gross_exposure=g, overnight_exposure=g * overnight_share,
             sector_exposure=min(g, equity * sector), gap_loss=equity * gap, overnight=overnight)
    p = Proposal("X", Side.BUY, entry, entry * stop_frac, atr=entry * atr_frac)
    v = evaluate(p, s, POL, book)
    if not v.approved:
        return
    add = v.qty * entry
    tol = 1e-6 * max(equity, 1)
    assert s.gross_exposure + add <= equity * min(gross_cap, 100 - cash) / 100 + tol
    assert s.sector_exposure + add <= equity * sector_cap / 100 + tol
    if overnight:
        assert s.overnight_exposure + add <= equity * overnight_cap / 100 + tol
        assert s.gap_loss + v.qty * gap_loss_ps(entry, p.atr) <= equity * gap_cap / 100 + tol
    # and the per-trade limits of today still hold
    assert v.qty * entry * (1 - stop_frac) <= equity * POL.risk_per_trade_pct / 100 + tol
    assert v.qty * entry <= equity * POL.max_position_pct / 100 + tol


@hyp.settings(max_examples=300, deadline=None)
@hyp.given(entry=st.floats(1, 2000), gross=st.floats(0, 1.5), sector=st.floats(0, 1), gap=st.floats(0, 0.3))
def test_property_more_exposure_never_means_a_bigger_order(entry, gross, sector, gap):
    """Monotone: adding exposure to the book never lets the gate approve more shares."""
    p = Proposal("X", Side.BUY, entry, entry * 0.8, atr=entry * 0.02)
    lo = evaluate(p, book_snap(gross_exposure=gross * 1e5 / 2, overnight_exposure=gross * 1e5 / 2,
                               sector_exposure=sector * 1e5 / 2, gap_loss=gap * 1e5 / 2), POL, BOOK)
    hi = evaluate(p, book_snap(gross_exposure=gross * 1e5, overnight_exposure=gross * 1e5,
                               sector_exposure=sector * 1e5, gap_loss=gap * 1e5), POL, BOOK)
    assert hi.qty <= lo.qty


# ------------------------------------------------------------------ ceilings and tighten-only
def test_book_hard_ceilings():
    c = BOOK_HARD_CEILINGS
    assert c.max_gross_pct <= 100 and c.max_overnight_pct <= 100  # decision 15 section 6: never above 100%
    assert c.atr_risk_pct == 0.25 and c.max_name_pct == HARD_CEILINGS.max_position_pct
    assert (c.max_sector_pct, c.max_names, c.min_cash_pct, c.max_gap_loss_pct) == (40, 30, 1.0, 15)
    assert c.run_drawdown_end_pct == 27  # #206: the multi-day book's own end (stakeholder, 13 Nov 2026)
    validate_book(BOOK)
    validate_book(BookLimits())  # the defaults sit inside the ceilings


CEILING_FIELDS = ("atr_risk_pct", "max_name_pct", "max_gross_pct", "max_overnight_pct", "max_sector_pct", "max_names",
                  "max_gap_loss_pct", "run_drawdown_end_pct")


def test_every_book_field_is_covered_by_a_ceiling_or_a_floor():
    assert set(CEILING_FIELDS) | {"min_cash_pct"} == {f.name for f in fields(BookLimits)}


@pytest.mark.parametrize("name", CEILING_FIELDS)
def test_a_limit_above_the_ceiling_is_refused(name):
    bad = replace(BOOK, **{name: getattr(BOOK_HARD_CEILINGS, name) + 1})
    with pytest.raises(ValueError, match=name):
        validate_book(bad)


def test_a_cash_buffer_below_the_floor_is_refused():
    with pytest.raises(ValueError, match="min_cash_pct"):
        validate_book(replace(BOOK, min_cash_pct=0.5))


@pytest.mark.parametrize("name", [*CEILING_FIELDS, "min_cash_pct"])
def test_negative_limits_are_refused(name):
    with pytest.raises(ValueError):
        validate_book(replace(BOOK, **{name: -1}))


@hyp.settings(max_examples=300, deadline=None)
@hyp.given(name=st.sampled_from(CEILING_FIELDS + ("min_cash_pct",)), factor=st.floats(0, 3))
def test_property_owner_settings_only_tighten(name, factor):
    """tighten_book: a lower cap (or a higher cash buffer) is taken as given; anything looser is refused, never
    clamped (the same rule as engine/userconfig.tighten)."""
    lv = getattr(BOOK, name)
    v = lv * factor
    v = int(v) if name == "max_names" else v
    looser = v < lv if name == "min_cash_pct" else v > lv
    if looser or (name == "min_cash_pct" and v > 100):
        with pytest.raises(ValueError):
            tighten_book(BOOK, {name: v})
        return
    out = tighten_book(BOOK, {name: v})
    assert getattr(out, name) == v
    validate_book(out, BOOK)
    validate_book(out)


def test_tighten_book_refuses_unknown_keys_and_types():
    with pytest.raises(ValueError):
        tighten_book(BOOK, {"leverage": 2})
    with pytest.raises(ValueError):
        tighten_book(BOOK, {"max_names": 2.5})
    with pytest.raises(ValueError):
        tighten_book(BOOK, {"max_gross_pct": "50"})
    with pytest.raises(ValueError):
        tighten_book(BOOK, {"max_gross_pct": True})
    assert tighten_book(BOOK, None) == BOOK and tighten_book(BOOK, {}) == BOOK
