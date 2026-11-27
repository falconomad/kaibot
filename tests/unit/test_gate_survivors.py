"""#546 (weekly mutation testing): tests for surviving mutants of `risk/gate.py` (risk_caps target).

The gate's checks are its audit record: the engine journals every check's rule, state, actual and limit
(engine/intraday_legacy.py) and the shadow rules keep the names of the failed ones (engine/shadow.py). So the record is
pinned here whole, for a plain buy, a refused one and a multi-day buy against a known book. The boundaries each
rule is defined by are pinned too: a limit reached is a failure where the rule says "below" (daily loss, run
drawdown, positions, entries) and a pass where it says "at most" (quote age, the % caps up to 1e-9); a warning
starts AT warn_fraction of a cap. Plus the pure helpers: drawdown_pct, run_drawdown_reached, gap_loss_ps,
open_risk_budget, size_position's candidates, _atr_known, validate_policy, validate_book and tighten_book.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from kaibot.risk import gate as G
from kaibot.risk.gate import BookLimits, Proposal, RiskPolicy, Side, Snapshot, State

POL = RiskPolicy(risk_per_trade_pct=0.5, max_position_pct=10, max_open_risk_pct=2.0, max_daily_loss_pct=2.0,
                 max_positions=5)
SNAP = Snapshot(equity=100_000, day_start_equity=100_000, open_positions=0, symbol_exposure=0, open_risk=0,
                quote_age_s=1, kill_switch=False, reconciled=True, market_open=True)
BUY = Proposal("AAA", Side.BUY, 100, 98)
BOOK_SNAP = replace(SNAP, overnight=True, gross_exposure=20_000.0, overnight_exposure=10_000.0,
                    sector_exposure=5_000.0, gap_loss=1_000.0)


def record(v):
    return [(c.rule, c.state.value, c.actual, c.limit) for c in v.checks]


def check(v, rule):
    return next(c for c in v.checks if c.rule == rule)


def test_a_plain_buys_record_is_complete_and_exact():
    v = G.evaluate(BUY, SNAP, POL)
    assert (v.approved, v.qty, v.binding, v.max_loss) == (True, 100, "position_cap", 200)
    assert record(v) == [
        ("kill_switch", "pass", "off", "off"), ("reconciled", "pass", True, True),
        ("market_open", "pass", True, True), ("entry_window", "pass", True, "09:35-15:45 ET"),
        ("event_blackout", "pass", False, False), ("data_ok", "pass", True, True),
        ("pump_risk", "pass", False, False), ("symbol_halted", "pass", False, False),
        ("duplicate_symbol", "pass", False, False), ("entries_today", "pass", 0, 20),
        ("run_drawdown_pct", "pass", 0.0, 8.0), ("quote_age_s", "pass", 1, 5.0),
        ("no_short", "pass", "buy", "long-only"), ("stop_side", "pass", 98, "< entry"),
        ("daily_loss_pct", "pass", 0.0, 2.0), ("max_positions", "pass", 0, 5),
        ("qty_positive", "pass", 100, "> 0"), ("position_pct", "warn", 10.0, 10), ("open_risk_pct", "pass", 0.2, 2.0),
    ]
    for actual in (c.actual for c in v.checks):
        assert actual is not None


def test_a_refused_buys_record_names_every_failure_with_its_actual_value():
    s = replace(SNAP, kill_switch=True, reconciled=False, market_open=False, in_entry_window=False,
                event_blackout=True, data_ok=False, pump_risk=True, symbol_halted=True,
                has_open_position_in_symbol=True, entries_today=20, quote_age_s=5.5, open_positions=5,
                equity=97_000, run_peak_equity=106_000)
    v = G.evaluate(BUY, s, POL)
    assert (v.approved, v.qty, v.max_loss) == (False, 0, 0.0)
    got = dict((r, (st, a, lim)) for r, st, a, lim in record(v))
    assert got["kill_switch"] == ("fail", "on", "off")
    assert got["reconciled"] == ("fail", False, True)
    assert got["market_open"] == ("fail", False, True)
    assert got["entry_window"] == ("fail", False, "09:35-15:45 ET")
    assert got["event_blackout"] == ("fail", True, False)
    assert got["data_ok"] == ("fail", False, True)
    assert got["pump_risk"] == ("fail", True, False)
    assert got["symbol_halted"] == ("fail", True, False)
    assert got["duplicate_symbol"] == ("fail", True, False)
    assert got["entries_today"] == ("fail", 20, 20)
    assert got["run_drawdown_pct"] == ("fail", 8.491, 8.0)  # (106000 - 97000) / 106000
    assert got["quote_age_s"] == ("fail", 5.5, 5.0)
    assert got["daily_loss_pct"] == ("fail", 3.0, 2.0)
    assert got["max_positions"] == ("fail", 5, 5)
    assert [c.rule for c in v.failures] == [
        "kill_switch", "reconciled", "market_open", "entry_window", "event_blackout", "data_ok", "pump_risk",
        "symbol_halted", "duplicate_symbol", "entries_today", "run_drawdown_pct", "quote_age_s", "daily_loss_pct",
        "max_positions", "qty_positive"]


def test_a_multi_day_buy_against_a_known_book_records_each_book_cap():
    v = G.evaluate(replace(BUY, atr=2.0), BOOK_SNAP, POL, BookLimits())
    assert (v.approved, v.qty, v.binding) == (True, 100, "position_cap")
    tail = record(v)[-9:]
    assert tail == [
        ("book_known", "pass", True, True), ("atr_known", "pass", 2.0, "> 0"), ("qty_positive", "pass", 100, "> 0"),
        ("position_pct", "warn", 10.0, 10), ("open_risk_pct", "pass", 0.2, 2.0),
        ("gross_pct", "pass", 30.0, 98.0), ("sector_pct", "pass", 15.0, 30.0),
        ("overnight_pct", "pass", 20.0, 100.0), ("gap_loss_pct", "pass", 2.0, 12.0),
    ]
    _, _, cands = G.size_position(replace(BUY, atr=2.0), BOOK_SNAP, POL, BookLimits())
    assert cands == {"risk_per_trade": 250, "position_cap": 100, "open_risk_headroom": 1000, "gross_headroom": 780,
                     "sector_headroom": 250, "overnight_headroom": 900, "gap_headroom": 1100}


def test_an_unknown_book_or_atr_is_recorded_as_such():
    v = G.evaluate(replace(BUY, atr=None), replace(SNAP, overnight=True), POL, BookLimits())
    assert check(v, "book_known").state is State.FAIL and check(v, "book_known").actual is False
    assert check(v, "book_known").limit is True
    assert (check(v, "atr_known").state, check(v, "atr_known").actual, check(v, "atr_known").limit) == (
        State.FAIL, None, "> 0")
    # the book is given but the snapshot lacks one of its fields: still unknown
    v = G.evaluate(replace(BUY, atr=2.0), replace(BOOK_SNAP, gap_loss=None), POL, BookLimits())
    assert check(v, "book_known").actual is False and not v.approved


def test_an_invalid_stop_is_refused_with_qty_zero_and_says_so():
    v = G.evaluate(Proposal("AAA", Side.BUY, 100, 100), SNAP, POL)
    assert (v.approved, v.qty, v.binding, v.max_loss) == (False, 0, "invalid_stop", 0.0)
    assert check(v, "stop_side").state is State.FAIL and check(v, "stop_side").limit == "< entry"
    assert check(v, "qty_positive").actual == 0
    v = G.evaluate(Proposal("AAA", Side.SELL, 100, 100), replace(SNAP, symbol_exposure=5_000), POL)
    assert check(v, "stop_side").state is State.FAIL and check(v, "stop_side").limit == "> entry"
    assert v.binding == "invalid_stop"


def test_a_stop_on_the_wrong_side_is_refused_without_sizing():
    v = G.evaluate(Proposal("AAA", Side.BUY, 100, 101), SNAP, POL)
    assert (v.qty, v.binding) == (0, "invalid_stop")
    v = G.evaluate(Proposal("AAA", Side.SELL, 100, 99), replace(SNAP, symbol_exposure=5_000), POL)
    assert (v.qty, v.binding) == (0, "invalid_stop")
    assert check(v, "stop_side").actual == 99


def test_a_sell_is_long_only_unless_it_reduces_a_holding():
    sell = Proposal("AAA", Side.SELL, 100, 102)
    v = G.evaluate(sell, SNAP, POL)  # nothing held: a short
    assert check(v, "no_short").state is State.FAIL and check(v, "no_short").actual == "sell"
    assert check(G.evaluate(sell, replace(SNAP, symbol_exposure=0.5), POL), "no_short").state is State.PASS
    assert check(G.evaluate(sell, SNAP, replace(POL, allow_short=True)), "no_short").state is State.PASS


@pytest.mark.parametrize("equity, state", [(98_000.0, State.FAIL), (98_000.01, State.PASS)])
def test_the_daily_loss_fails_at_the_limit(equity, state):
    assert check(G.evaluate(BUY, replace(SNAP, equity=equity), POL), "daily_loss_pct").state is state


def test_the_daily_loss_is_never_negative_and_is_a_percentage():
    assert check(G.evaluate(BUY, replace(SNAP, equity=101_000), POL), "daily_loss_pct").actual == 0.0
    assert check(G.evaluate(BUY, replace(SNAP, equity=99_500), POL), "daily_loss_pct").actual == 0.5
    assert check(G.evaluate(BUY, replace(SNAP, equity=99_987.6543), POL), "daily_loss_pct").actual == 0.012


@pytest.mark.parametrize("equity, state", [(92_000.0, State.FAIL), (92_000.01, State.PASS)])
def test_the_run_drawdown_fails_at_the_limit(equity, state):
    s = replace(SNAP, equity=equity, day_start_equity=equity, run_peak_equity=100_000)
    assert check(G.evaluate(BUY, s, POL), "run_drawdown_pct").state is state


def test_the_run_drawdown_is_never_negative_and_is_a_percentage():
    s = replace(SNAP, equity=99_000, day_start_equity=99_000, run_peak_equity=100_000)
    assert check(G.evaluate(BUY, s, POL), "run_drawdown_pct").actual == 1.0
    s = replace(SNAP, equity=99_987.6543, day_start_equity=99_987.6543, run_peak_equity=100_000)
    assert check(G.evaluate(BUY, s, POL), "run_drawdown_pct").actual == 0.012
    s = replace(SNAP, run_peak_equity=90_000)  # above the peak
    assert check(G.evaluate(BUY, s, POL), "run_drawdown_pct").actual == 0.0


@pytest.mark.parametrize("age, state", [(5.0, State.PASS), (5.01, State.FAIL)])
def test_a_quote_exactly_at_the_age_limit_passes(age, state):
    assert check(G.evaluate(BUY, replace(SNAP, quote_age_s=age), POL), "quote_age_s").state is state


@pytest.mark.parametrize("n, state", [(4, State.PASS), (5, State.FAIL)])
def test_positions_and_entries_fail_at_their_limits(n, state):
    assert check(G.evaluate(BUY, replace(SNAP, open_positions=n), POL), "max_positions").state is state
    e = replace(POL, max_entries_per_day=n + 1 if state is State.PASS else n)
    assert check(G.evaluate(BUY, replace(SNAP, entries_today=n), e), "entries_today").state is state


def test_position_cap_warns_from_the_warn_fraction_and_fails_only_above_the_cap():
    # 10% cap, warn at 8%: 79 shares of $100 on $100k = 7.9% pass, 80 = 8.0% warn
    pol = replace(POL, max_position_pct=10)
    v = G.evaluate(BUY, replace(SNAP, symbol_exposure=2_100), pol)  # cap room 7,900 -> 79 shares -> 10.0%
    assert check(v, "position_pct").actual == 10.0 and check(v, "position_pct").state is State.WARN
    v = G.evaluate(Proposal("AAA", Side.BUY, 100, 99.5), SNAP, replace(pol, risk_per_trade_pct=0.04))
    assert v.qty == 80 and check(v, "position_pct").actual == 8.0 and check(v, "position_pct").state is State.WARN
    v = G.evaluate(Proposal("AAA", Side.BUY, 100, 99.5), SNAP, replace(pol, risk_per_trade_pct=0.0395))
    assert v.qty == 79 and check(v, "position_pct").state is State.PASS
    # held above the cap already: the buy itself is sized 0 and the record shows the excess
    v = G.evaluate(BUY, replace(SNAP, symbol_exposure=10_500), pol)
    assert check(v, "position_pct").state is State.FAIL and check(v, "position_pct").actual == 10.5


def test_a_value_a_hair_over_a_cap_by_rounding_is_not_a_failure():
    # 1/3 of equity per share: 30 shares = 9.99999...% then 10% cap: the 1e-9 tolerance keeps float noise a pass
    pol = replace(POL, max_position_pct=10, risk_per_trade_pct=1.0)
    s = replace(SNAP, equity=3_000.0, day_start_equity=3_000.0)
    v = G.evaluate(Proposal("AAA", Side.BUY, 10.0, 9.0), s, pol)
    assert v.qty == 30 and check(v, "position_pct").actual == 10.0 and check(v, "position_pct").state is State.WARN


def test_the_open_risk_record_uses_the_budget_left_today():
    s = replace(SNAP, equity=99_000, open_risk=500)  # lost 1,000 of the 2,000 daily stop: 1,000 of budget left
    v = G.evaluate(BUY, s, POL)
    assert check(v, "open_risk_pct").limit == round(1_000 / 99_000 * 100, 6)
    assert check(v, "open_risk_pct").actual == round((500 + v.max_loss) / 99_000 * 100, 3)
    assert v.qty == 99 and v.max_loss == 198  # 10% of 99,000 = 99 shares, risk 2 each


def test_open_risk_over_the_budget_fails_and_warns_from_the_fraction():
    v = G.evaluate(BUY, replace(SNAP, open_risk=2_100), POL)
    assert check(v, "open_risk_pct").state is State.FAIL and check(v, "open_risk_pct").actual == 2.1
    v = G.evaluate(BUY, replace(SNAP, open_risk=1_400), POL)  # 1,400 + 200 = 1.6% of a 2.0% budget: warn
    assert check(v, "open_risk_pct").actual == 1.6 and check(v, "open_risk_pct").state is State.WARN


def test_a_book_cap_warns_from_the_fraction_and_fails_above_it():
    lim = BookLimits(max_sector_pct=30.0)
    p = replace(BUY, atr=2.0)
    v = G.evaluate(p, replace(BOOK_SNAP, sector_exposure=14_000.0), POL, lim)  # 14k + 10k = 24% = 0.8 x 30
    assert check(v, "sector_pct").actual == 24.0 and check(v, "sector_pct").state is State.WARN
    v = G.evaluate(p, replace(BOOK_SNAP, sector_exposure=13_999.0), POL, lim)
    assert check(v, "sector_pct").state is State.PASS
    v = G.evaluate(p, replace(BOOK_SNAP, sector_exposure=31_000.0), POL, lim)  # already over: sized 0
    assert v.qty == 0 and check(v, "sector_pct").state is State.FAIL and check(v, "sector_pct").actual == 31.0
    assert check(v, "sector_pct").limit == 30.0


def test_book_cap_records_round_their_values():
    lim = BookLimits(max_sector_pct=30.0, min_cash_pct=2.0)
    v = G.evaluate(replace(BUY, atr=2.0), replace(BOOK_SNAP, sector_exposure=5_001.2345), POL, lim)
    assert check(v, "sector_pct").actual == 15.001  # (5,001.2345 + 10,000) / 100,000, 3 places
    v = G.evaluate(replace(BUY, atr=2.0), BOOK_SNAP, POL, replace(lim, min_cash_pct=2.1234567))
    assert check(v, "gross_pct").limit == 97.876543  # 100 - 2.1234567, 6 places


def test_an_approved_verdict_rounds_max_loss_to_cents():
    v = G.evaluate(Proposal("AAA", Side.BUY, 100.0, 98.76543), SNAP, POL)
    assert v.approved and v.qty == 100
    assert v.max_loss == 123.46  # 100 x 1.23457 = 123.457


def test_a_refused_verdict_carries_no_shares_and_no_loss_even_when_sizing_found_some():
    v = G.evaluate(BUY, replace(SNAP, kill_switch=True), POL)
    assert check(v, "qty_positive").actual == 100  # sizing found 100 shares
    assert (v.approved, v.qty, v.max_loss) == (False, 0, 0.0)


def test_no_room_is_zero_shares_also_for_a_share_price_below_a_dollar():
    cheap = Proposal("AAA", Side.BUY, 0.5, 0.25)
    _, _, cands = G.size_position(cheap, replace(SNAP, symbol_exposure=10_000), POL)
    assert cands["position_cap"] == 0  # not floor(1 / 0.5) = 2
    _, _, cands = G.size_position(cheap, replace(SNAP, open_risk=2_000), POL)
    assert cands["open_risk_headroom"] == 0
    _, _, cands = G.size_position(replace(cheap, atr=0.01), replace(BOOK_SNAP, sector_exposure=30_000.0), POL,
                                  BookLimits())
    assert cands["sector_headroom"] == 0
    assert G.evaluate(cheap, replace(SNAP, symbol_exposure=10_000), POL).qty == 0
    for bp in (0.0, -5.0, 0.4):  # no buying power (or under one share's price): not floor(1 / 0.5) = 2 shares
        qty, binding, cands = G.size_position(cheap, replace(SNAP, buying_power=bp), POL)
        assert (qty, binding, cands["buying_power"]) == (0, "buying_power", 0)


# ---------------------------------------------------------------- helpers


def test_drawdown_pct():
    assert G.drawdown_pct(100.0, 100.0) == 0.0
    assert G.drawdown_pct(100.0, 90.0) == 10.0
    assert G.drawdown_pct(100.0, -50.0) == 100.0  # capped at 100
    assert G.drawdown_pct(0.0, -10.0) == 0.0 and G.drawdown_pct(-5.0, -10.0) == 0.0


def test_run_drawdown_is_reached_exactly_at_the_end_pct():
    assert G.run_drawdown_reached(100.0, 92.0, 8.0) is True
    assert G.run_drawdown_reached(100.0, 92.01, 8.0) is False
    assert G.book_run_ended(100.0, 87.0, BookLimits(run_drawdown_end_pct=13.0)) is True


def test_gap_loss_per_share():
    assert G.gap_loss_ps(100.0, None) == 10.0
    assert G.gap_loss_ps(100.0, 0.0) == 10.0
    assert G.gap_loss_ps(5.0, None) == 0.5  # the 10% floor alone, not 3 x a made-up ATR of 1
    assert G.gap_loss_ps(100.0, 4.0) == 12.0


@pytest.mark.parametrize("atr, ok", [(None, False), (0, False), (-1.0, False), (True, False), (float("nan"), False),
                                     (float("inf"), False), (0.5, True), (2, True)])
def test_atr_known(atr, ok):
    assert G._atr_known(atr) is ok


def test_open_risk_budget():
    assert G.open_risk_budget(SNAP, POL) == 2_000.0
    assert G.open_risk_budget(replace(SNAP, equity=100_500), POL) == 2_000.0  # a gain adds no room to the stop
    assert G.open_risk_budget(replace(SNAP, equity=98_500), POL) == 500.0  # 2,000 stop - 1,500 lost
    assert G.open_risk_budget(replace(SNAP, equity=98_000), POL) == 0.0  # at the daily stop: nothing, not 1
    assert G.open_risk_budget(replace(SNAP, equity=97_000), POL) == 0.0
    assert G.open_risk_budget(SNAP, replace(POL, max_open_risk_pct=0.5)) == 500.0


def test_size_position_candidates_and_their_floors():
    s = replace(SNAP, symbol_exposure=10_050, open_risk=2_001, buying_power=-5)
    qty, binding, cands = G.size_position(BUY, s, POL)
    assert cands["risk_per_trade"] == 250
    assert cands["position_cap"] == 0 and cands["open_risk_headroom"] == 0 and cands["buying_power"] == 0
    assert qty == 0
    _, _, cands = G.size_position(BUY, replace(SNAP, symbol_exposure=1_000), POL)
    assert cands["position_cap"] == 90  # (10,000 - 1,000) / 100, not (10,000 + 1,000) / 100
    _, _, cands = G.size_position(BUY, replace(SNAP, buying_power=5_050.0), POL)
    assert cands["buying_power"] == 50


@pytest.mark.parametrize("p", [Proposal("AAA", Side.BUY, 100, 100), Proposal("AAA", Side.BUY, 0, -1),
                               Proposal("AAA", Side.BUY, -1, -2)])
def test_size_position_refuses_a_zero_risk_or_a_non_positive_price(p):
    assert G.size_position(p, SNAP, POL) == (0, "invalid_stop", {})


def test_size_position_sizes_a_price_at_or_below_one_dollar():
    qty, _, cands = G.size_position(Proposal("AAA", Side.BUY, 1.0, 0.9), SNAP, POL)
    assert cands["position_cap"] == 10_000 and qty > 0
    qty, _, _ = G.size_position(Proposal("AAA", Side.BUY, 0.5, 0.4), SNAP, POL)
    assert qty > 0


def test_a_book_cap_without_room_or_with_a_zero_per_share_gives_zero(monkeypatch):
    p = replace(BUY, atr=2.0)
    _, _, cands = G.size_position(p, replace(BOOK_SNAP, sector_exposure=30_050.0), POL, BookLimits())
    assert cands["sector_headroom"] == 0
    _, _, cands = G.size_position(p, replace(BOOK_SNAP, sector_exposure=29_950.0), POL, BookLimits())
    assert cands["sector_headroom"] == 0  # 50 dollars of room buys no $100 share
    # a per-share figure of 0 (only reachable through _book_caps' gap row at price 0, refused earlier) or < 1
    caps = [("x", "tiny", 0.0, 0.5, 10.0), ("z", "zero", 0.0, 0.0, 10.0)]
    monkeypatch.setattr(G, "_book_caps", lambda *a: caps)
    _, _, cands = G.size_position(BUY, SNAP, POL, BookLimits())
    assert cands["tiny"] == 20_000 and cands["zero"] == 0


def test_validate_policy_refuses_shorts_and_loosened_ceilings():
    G.validate_policy(POL)
    with pytest.raises(ValueError, match="shorts are disabled"):
        G.validate_policy(replace(POL, allow_short=True))
    G.validate_policy(replace(POL, allow_short=True), replace(G.HARD_CEILINGS, allow_short=True))
    with pytest.raises(ValueError, match=r"loosens hard ceiling: max_positions=9 > 8"):
        G.validate_policy(replace(POL, max_positions=9))


def test_validate_book_refuses_bad_numbers_and_a_floor_above_100():
    G.validate_book(BookLimits())
    for bad in (float("nan"), float("inf"), -1.0, True, "1"):
        with pytest.raises(ValueError, match="must be a number >= 0"):
            G.validate_book(replace(BookLimits(), max_sector_pct=bad))
    with pytest.raises(ValueError, match="must be a number >= 0"):
        G.validate_book(replace(BookLimits(), min_cash_pct=float("nan")))
    G.validate_book(replace(BookLimits(), min_cash_pct=99.5))
    with pytest.raises(ValueError, match="hard floor"):
        G.validate_book(replace(BookLimits(), min_cash_pct=100.5))
    G.validate_book(replace(BookLimits(), min_cash_pct=100))  # 100 itself is allowed (nothing may be held)


def test_tighten_book_keeps_types_and_refuses_unknown_or_wrong_settings():
    out = G.tighten_book(BookLimits(), {"max_names": 10.0, "max_sector_pct": 20})
    assert out.max_names == 10 and type(out.max_names) is int
    assert out.max_sector_pct == 20.0 and type(out.max_sector_pct) is float
    with pytest.raises(ValueError, match="unknown book setting 'nope'"):
        G.tighten_book(BookLimits(), {"nope": 1})
    with pytest.raises(ValueError, match="max_names=10.5 has the wrong type"):
        G.tighten_book(BookLimits(), {"max_names": 10.5})
