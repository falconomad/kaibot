"""M9 ticket 1.1 (audit R1): the multi-day book's run end fired on one unconfirmed price. Written before the fix.

The book end sells every multi-day position at market and retires every rule. Before:
- the price of a holding was the last IEX minute bar's close, however old, with no second source;
- the two breach checks 60 s apart could both read that one bar;
- the peak was journalled as soon as one reading was half a cent above it, and never came down;
- a split the ledger had not booked yet (old share count, new price) read as a fall.

Now (RISK RULE, tests first):
- a bar older than MD_BAR_MAX_AGE_MIN minutes, or further than MD_FAR_PCT % from the holding's last close mark, counts
  only when the broker's own price (read at this tick) agrees within MD_AGREE_PCT %; otherwise the holding is
  measured at its last close mark;
- a new peak is taken from the close marks, or from two readings above the old peak at least BREACH_CONFIRM_S apart
  (the lower of the two);
- the check is skipped while a held symbol's shares differ between the ledger and the broker, or has a corporate
  action today;
- a real fall (the minute bars and the broker agree) still ends the book.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from test_failsafe_drills import DAY2, MD
from test_live_daily_engine import DailyAccount, _book_b

from kaibot.engine import core


def _kinds(eng, *kinds):
    return [b for *_, b in eng.journal.entries(kinds=kinds)]


def _ended(a) -> bool:
    return bool(_kinds(a.eng, "md_book_end")) or a.eng.daily.account.book_ended()


class Feed:
    """What the engine reads for B: the minute bars (`bar`: a factor on the real close; `frozen`: the bar's end
    stays where it was, the symbol has not printed since) and the broker's `current_price` (`broker`: a factor;
    None: the broker gives no price)."""

    def __init__(self, a, eng=None, sym="B"):
        self.a, self.eng, self.sym = a, eng or a.eng, sym
        self.bar, self.broker, self.frozen = 1.0, 1.0, None
        m = self.eng.market
        self._close, self._end, self._pos = m.last_close, m.last_bar_end, a.broker.positions
        m.last_close = self.last_close
        m.last_bar_end = self.last_bar_end
        a.broker.positions = self.positions

    def last_close(self, s):
        px = self._close(s)
        return px * self.bar if s == self.sym and px else px

    def last_bar_end(self, s):
        return self.frozen if s == self.sym and self.frozen is not None else self._end(s)

    def positions(self):
        out = self._pos()
        if self.sym in out:
            cp = out[self.sym].get("current_price")
            out[self.sym] = {**out[self.sym], "current_price": None if self.broker is None or cp is None
                             else str(float(cp) * self.broker)}
        return out

    def freeze(self):
        self.frozen = self._end(self.sym)

    def undo(self):
        m = self.eng.market
        m.last_close, m.last_bar_end, self.a.broker.positions = self._close, self._end, self._pos


def _held(tmp_path, monkeypatch, book_end=5.0, **kw):
    a = DailyAccount(tmp_path, monkeypatch, book_end=book_end, **kw)
    _book_b(a)
    a.ticks(a.open1, 30, 40)
    return a, Feed(a)


# ---------------------------------------------------------------- a stale bar, a bad print


def test_a_stale_bar_the_broker_does_not_agree_with_never_ends_the_book(tmp_path, monkeypatch):
    """B's last IEX bar is a print 50% down and the symbol does not print again: every later check reads that one
    bar. The broker's price is normal. (Before: breach at the first check, book end at the second.)"""
    a, f = _held(tmp_path, monkeypatch)
    f.freeze()
    f.bar = 0.5
    a.ticks(a.open1, 40, 50)
    assert not _ended(a) and not _kinds(a.eng, "md_book_breach") and "B" in a.broker.pos
    assert a.eng.daily.retirement(MD) is None
    line = a.eng.book_line
    assert line["drawdown_pct"] < 1 and line["unconfirmed"] == ["B"]  # measured at the close mark, and said so
    a.ticks(a.open1, 50, 60)  # QA-Ops (#527): a bar older than 15 minutes is no price; the broker's (normal) is used
    assert not _ended(a) and not _kinds(a.eng, "md_book_breach") and a.eng.daily.retirement(MD) is None
    assert a.eng.book_line["drawdown_pct"] < 1 and "unconfirmed" not in a.eng.book_line


def test_a_stale_bar_with_no_broker_price_never_ends_the_book(tmp_path, monkeypatch):
    a, f = _held(tmp_path, monkeypatch)
    f.freeze()
    f.bar, f.broker = 0.5, None
    a.ticks(a.open1, 40, 50)
    assert not _ended(a) and not _kinds(a.eng, "md_book_breach")


def test_a_fresh_bad_print_read_by_both_checks_never_ends_the_book(tmp_path, monkeypatch):
    """The bar is fresh (it ended a minute ago) and both checks, a minute apart, read it; the broker's price never
    moved. Far from the last close and not confirmed by the broker: not counted."""
    a, f = _held(tmp_path, monkeypatch)
    f.bar = 0.5  # every bar of B reads 50% down for three minutes, the broker's price stays
    a.ticks(a.open1, 40, 43)
    f.bar = 1.0
    a.ticks(a.open1, 43, 50)
    assert not _ended(a) and not _kinds(a.eng, "md_book_breach") and a.eng.daily.retirement(MD) is None


def test_a_bad_broker_price_alone_never_ends_the_book(tmp_path, monkeypatch):
    a, f = _held(tmp_path, monkeypatch)
    f.broker = 0.5  # the bars are fresh and near the last close: they count, the broker's print does not
    a.ticks(a.open1, 40, 45)
    assert not _ended(a) and not _kinds(a.eng, "md_book_breach")
    assert "unconfirmed" not in a.eng.book_line


# ---------------------------------------------------------------- the peak


def test_one_high_reading_does_not_raise_the_peak(tmp_path, monkeypatch):
    """A print 60% up, confirmed by the broker on that one tick only. Before: the peak went up at once and for good,
    and the normal price a minute later was a 5% "fall" from it: breach, then the book end."""
    a, f = _held(tmp_path, monkeypatch)
    peak0 = a.eng._book_peak(a.rid)
    f.bar = f.broker = 1.6
    a.ticks(a.open1, 40, 41)
    f.bar = f.broker = 1.0
    a.ticks(a.open1, 41, 50)
    assert a.eng._book_peak(a.rid) == peak0 and not _kinds(a.eng, "md_book_breach") and not _ended(a)


def test_a_rise_seen_on_two_checks_a_minute_apart_raises_the_peak_to_the_lower_reading(tmp_path, monkeypatch):
    a, f = _held(tmp_path, monkeypatch)
    peak0 = a.eng._book_peak(a.rid)
    f.bar = f.broker = 1.6
    a.ticks(a.open1, 40, 41)
    first = a.eng.book_line["equity"]
    assert a.eng._book_peak(a.rid) == peak0 and first > peak0  # seen once: not yet
    f.bar = f.broker = 1.5
    a.ticks(a.open1, 41, 42)
    second = a.eng.book_line["equity"]
    assert peak0 < second < first and a.eng._book_peak(a.rid) == pytest.approx(second, abs=0.01)
    assert a.eng.book_line["peak"] == pytest.approx(second, abs=0.01)


def test_the_close_marks_raise_the_peak_at_once(tmp_path, monkeypatch):
    a, _ = _held(tmp_path, monkeypatch)
    pot = float(a.eng.ledger.book(a.rid).amount)
    assert a.eng._book_peak(a.rid) < pot + 700.0
    a.eng._book_pnl = lambda live=True: 700.0  # at the close marks the book is 700 up
    a.eng._book_line_marked()
    assert a.eng._book_peak(a.rid) == pytest.approx(pot + 700.0)


# ---------------------------------------------------------------- shares that differ, a corporate action today


def test_an_unbooked_split_skips_the_check_and_never_ends_the_book(tmp_path, monkeypatch):
    """A 2-for-1 split the ledger has not booked: the broker holds 80 B at half the price, the ledger 40 at the old
    one. Bars and broker agree on the new price, so the book read 50% down in B (before: the book end)."""
    a, f = _held(tmp_path, monkeypatch)
    a.broker.pos["B"]["qty"] = 80
    f.bar = f.broker = 0.5
    a.ticks(a.open1, 40, 46, catch=True)
    assert not _ended(a) and not _kinds(a.eng, "md_book_breach") and a.eng.daily.retirement(MD) is None
    skipped = _kinds(a.eng, "md_book_check_skipped")
    assert len(skipped) == 1 and "B" in skipped[0]["why"] and "shares" in skipped[0]["why"]  # journalled once
    assert "shares" in a.eng.book_line["unchecked"]
    a.broker.pos["B"]["qty"] = 40  # the books agree again (here: the venue's count is back)
    f.bar = f.broker = 1.0
    a.ticks(a.open1, 46, 49, catch=True)
    assert "unchecked" not in a.eng.book_line and not _ended(a)


def test_a_corporate_action_today_in_a_held_symbol_skips_the_check(tmp_path, monkeypatch):
    from kaibot.engine.corp_guard import Announced

    a, f = _held(tmp_path, monkeypatch)
    today = str(a.eng.day.day)
    a.eng._book_actions_today = lambda symbols: {"B": Announced("B", "reverse split", today)}
    f.bar = f.broker = 0.5
    a.ticks(a.open1, 40, 45)
    assert not _ended(a) and not _kinds(a.eng, "md_book_breach")
    assert "reverse split" in _kinds(a.eng, "md_book_check_skipped")[0]["why"]


def test_an_action_on_another_day_or_in_a_stock_not_held_does_not_skip_the_check(tmp_path, monkeypatch):
    from kaibot.engine.corp_guard import Announced

    a, f = _held(tmp_path, monkeypatch)
    a.eng._book_actions_today = lambda symbols: {}
    assert a.eng._book_check_blocked(a.eng.ledger.book(a.rid)) is None
    assert str(a.eng.day.day) != DAY2
    ca = type("Ca", (), {"known": lambda self, ev, syms: {"B": Announced("B", "split", DAY2)}})()
    monkeypatch.setattr(a.eng.daily, "ca", ca, raising=False)
    monkeypatch.setattr(a.eng.daily, "_ca_evening", a.eng.day.day, raising=False)
    del a.eng._book_actions_today
    assert a.eng._book_actions_today(["B"]) == {}  # announced for tomorrow: today's check runs
    f.bar = f.broker = 0.5
    a.ticks(a.open1, 40, 42)
    assert _ended(a)


# ---------------------------------------------------------------- a real fall still ends the book


def test_a_real_fall_seen_in_the_bars_and_at_the_broker_ends_the_book_at_the_second_check(tmp_path, monkeypatch):
    a, f = _held(tmp_path, monkeypatch)
    f.bar = f.broker = 0.5
    a.ticks(a.open1, 40, 41)
    assert len(_kinds(a.eng, "md_book_breach")) == 1 and not _ended(a)  # the first check
    a.ticks(a.open1, 41, 42)
    (end,) = _kinds(a.eng, "md_book_end")
    assert end["drawdown_pct"] >= 5 and a.eng.daily.retirement(MD) is not None
    a.ticks(a.open1, 42, 45)
    assert "B" not in a.broker.pos  # sold


def test_a_real_27_percent_fall_overnight_ends_hunters_book(tmp_path, monkeypatch):
    """The level's widest book end (27%). B is the whole book here, bought with the whole pot, and opens DAY2 30%
    lower in the bars and at the broker: breach at the first tick, the end at the second."""
    a = DailyAccount(tmp_path, monkeypatch, gap_b=0.70, book_end=27.0)
    a.ticks(a.open1, 0, 30)
    px = float(a.eng.market.last_close("B"))
    qty = int(10_000 // px)
    a.broker.pos["B"] = {"qty": qty, "avg_entry_price": px}
    a.eng._book("fill", {"run_id": a.rid, "strategy": MD, "symbol": "B", "side": "buy", "qty": qty, "px": px,
                         "fees": 0.0, "coid": f"kb{a.rid}-{MD}-B-20260803-buy-1", "session": "2026-08-03"})
    a.ticks(a.open1, 30, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, 1)
    assert not _ended(a) and len(_kinds(a.eng, "md_book_breach")) == 1
    a.ticks(a.open2, 1, 4)
    (end,) = _kinds(a.eng, "md_book_end")
    assert end["end_pct"] == 27.0 and 27.0 <= end["drawdown_pct"] < 32.0
    assert "B" not in a.broker.pos and a.eng.daily.retirement(MD) is not None


def test_a_real_fall_in_a_symbol_without_minute_bars_ends_the_book_on_the_brokers_price(tmp_path, monkeypatch):
    """A holding outside the live universe has no minute bars: the broker's price, read again at every check, is
    the only one there is."""
    a, f = _held(tmp_path, monkeypatch)
    f.bar = 0.0  # no bar: `last_close` answers nothing for B
    f.broker = 0.5
    a.ticks(a.open1, 40, 42)
    assert _ended(a)


def test_the_limits_are_named_constants(tmp_path):
    assert (core.MD_BAR_MAX_AGE_MIN, core.MD_FAR_PCT, core.MD_AGREE_PCT) == (5.0, 10.0, 2.0)
    assert core.BREACH_CONFIRM_S == 60.0


def test_the_intraday_run_end_is_measured_as_before_while_a_price_is_rejected(tmp_path, monkeypatch):
    """The run's intraday end is the run's equity less the book's P&L, both at the same prices. A rejected price in
    the book check must not leak the bad print into it (that would end the RUN and sell everything)."""
    a, f = _held(tmp_path, monkeypatch)
    f.freeze()
    f.bar = 0.5
    a.ticks(a.open1, 40, 50)
    assert not any(h["kind"] in ("run_drawdown", "book_drawdown") for h in a.store.tables.get("halt", []))
    assert a.eng.run is not None and a.eng.run["status"] == "running" and "B" in a.broker.pos
    assert a.clock.t - a.open1 < timedelta(hours=1)


# ---------------------------------------------------------------- QA-Ops second key on #527: the other direction


def _events(a, kind):
    return [e["payload"] for e in a.store.tables["event"] if e["type"] == kind]


def _also_holds(a, sym, qty):
    """A second multi-day holding with minute bars, booked as the daily path books a fill."""
    px = float(a.eng.market.last_close(sym))
    a.broker.pos[sym] = {"qty": qty, "avg_entry_price": px}
    a.eng._book("fill", {"run_id": a.rid, "strategy": MD, "symbol": sym, "side": "buy", "qty": qty, "px": px,
                         "fees": 0.0, "coid": f"kb{a.rid}-{MD}-{sym}-20260803-buy-1", "session": "2026-08-03"})
    return px


def test_with_the_market_data_down_a_fall_the_broker_shows_ends_the_book_within_the_bound(tmp_path, monkeypatch):
    """The minute bars stop (the data read fails, or the symbol is silent on IEX) while the broker's price falls
    50%. For MD_BAR_DEAD_MIN minutes the old bar and the broker disagree and B stands at its last close mark, named
    in the heartbeat and, after MD_UNCONFIRMED_ALARM_MIN minutes, in one event. Then the bar is no price any more
    and the broker's price counts alone: breach, and the end a minute later. Never blind for longer."""
    a, f = _held(tmp_path, monkeypatch)
    f.freeze()
    f.broker = 0.5
    a.ticks(a.open1, 40, 44)  # the bar is still fresh and near the last close: it counts, the broker alone does not
    assert "unconfirmed" not in a.eng.book_line and not _kinds(a.eng, "md_book_breach")
    a.ticks(a.open1, 44, 48)
    assert a.eng.book_line["unconfirmed"] == ["B"] and _events(a, "multi_day_book_unconfirmed") == []
    a.ticks(a.open1, 48, 54)
    assert not _ended(a) and a.eng.book_line["unconfirmed"] == ["B"]
    (e,) = _events(a, "multi_day_book_unconfirmed")  # once
    assert e["symbols"] == ["B"] and "last close mark" in e["action"]
    a.ticks(a.open1, 54, 40 + int(core.MD_BAR_DEAD_MIN) + 4)
    (end,) = _kinds(a.eng, "md_book_end")
    assert end["drawdown_pct"] >= 5 and a.eng.daily.retirement(MD) is not None
    assert len(_events(a, "multi_day_book_unconfirmed")) == 1


def test_a_fall_while_both_sources_are_silent_ends_the_book_when_the_symbol_prints_again(tmp_path, monkeypatch):
    """A long IEX silence: the last bar and the broker's price are the same old print for half an hour (they agree,
    so the old price is used, as before M9 1.1; nothing can know better). The first print after it is 50% down in
    both: breach at that check, the end a minute later."""
    a, f = _held(tmp_path, monkeypatch)
    f.freeze()
    a.ticks(a.open1, 40, 70)
    assert not _ended(a) and not _kinds(a.eng, "md_book_breach") and "unconfirmed" not in a.eng.book_line
    assert _events(a, "multi_day_book_unconfirmed") == []
    f.frozen = None
    f.bar = f.broker = 0.5
    a.ticks(a.open1, 70, 71)
    assert len(_kinds(a.eng, "md_book_breach")) == 1 and not _ended(a)
    a.ticks(a.open1, 71, 72)
    assert _ended(a) and a.eng.daily.retirement(MD) is not None


def test_a_holding_with_no_price_at_all_is_named_and_raises_the_event(tmp_path, monkeypatch):
    """No minute bar and no broker price: the holding stands at its last close mark. That was silent."""
    a, f = _held(tmp_path, monkeypatch)
    f.bar, f.broker = 0.0, None
    a.ticks(a.open1, 40, 47)
    assert a.eng.book_line["unconfirmed"] == ["B"] and not _ended(a)
    (e,) = _events(a, "multi_day_book_unconfirmed")
    assert e["symbols"] == ["B"]
    f.bar = f.broker = 1.0
    a.ticks(a.open1, 47, 49)
    assert "unconfirmed" not in a.eng.book_line


def test_a_share_difference_in_one_holding_does_not_blind_the_book_end_for_the_others(tmp_path, monkeypatch):
    """B has an unbooked 2-for-1 split (80 at the broker, 40 in the ledger, half the price in both sources) and the
    difference stays open. XLK, the other holding, then falls 60% for real. Before this key the whole check was
    skipped while B's difference was open, for days if nobody cleared it: the fall was never seen. Now B stands at
    its last close mark (its fake fall is not counted), one event says so, and XLK's fall ends the book."""
    a, f = _held(tmp_path, monkeypatch, book_end=20.0)
    px = _also_holds(a, "XLK", int(4000 // float(a.eng.market.last_close("XLK"))))
    g = Feed(a, sym="XLK")
    a.broker.pos["B"]["qty"] = 80
    f.bar = f.broker = 0.5
    a.ticks(a.open1, 40, 50, catch=True)
    assert not _ended(a) and not _kinds(a.eng, "md_book_breach")  # B's halved price alone: nothing
    assert a.eng.book_line["drawdown_pct"] < 1 and "B" in a.eng.book_line["unchecked"]
    (e,) = _events(a, "multi_day_book_unchecked")  # the alarm, once
    assert e["symbols"] == ["B"] and "shares differ" in e["why"]
    assert len(_kinds(a.eng, "md_book_check_skipped")) == 1
    g.bar = g.broker = 0.4
    a.ticks(a.open1, 50, 51, catch=True)
    assert len(_kinds(a.eng, "md_book_breach")) == 1 and not _ended(a)
    a.ticks(a.open1, 51, 53, catch=True)
    (end,) = _kinds(a.eng, "md_book_end")
    lost_xlk = 0.6 * px * a.eng.ledger.book(a.rid).sleeves.get((MD, "XLK"), type("S", (), {"qty": 0})).qty
    assert 20.0 <= end["drawdown_pct"] < 30.0, (end, lost_xlk)  # XLK's fall only, not B's halved price as well
    assert a.eng.daily.retirement(MD) is not None
    assert len(_events(a, "multi_day_book_unchecked")) == 1


def test_a_corporate_action_in_one_holding_and_a_share_difference_in_another_are_both_set_aside(tmp_path,
                                                                                                  monkeypatch):
    from kaibot.engine.corp_guard import Announced

    a, f = _held(tmp_path, monkeypatch)
    _also_holds(a, "XLK", 5)
    today = str(a.eng.day.day)
    a.eng._book_actions_today = lambda symbols: {"XLK": Announced("XLK", "split", today)}
    a.broker.pos["B"]["qty"] = 80
    f.bar = f.broker = 0.5
    a.ticks(a.open1, 40, 44, catch=True)
    why, doubt = a.eng._book_doubt(a.eng.ledger.book(a.rid))
    assert doubt == ["B", "XLK"] and "corporate action" in why and "shares differ" in why
    assert not _ended(a) and not _kinds(a.eng, "md_book_breach")
