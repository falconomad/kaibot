"""M9 ticket 1.7 (audit M5): three failed loop passes in a row (about a minute of API trouble) set the kill
`engine_errors`, and until this ticket only the next session start cleared it: a blip at 10:00 blocked the 15:50
buys of the multi-day book. Written before the fix. Now the kill is cleared after `CLEAN_TICKS_TO_CLEAR` clean
ticks in a row; the halt row stays for the record and a `halt_cleared` event says why. Any other kill is untouched."""

from __future__ import annotations

from test_failsafe_drills import DAY2, MD
from test_live_daily_engine import SEND, DailyAccount

from kaibot.engine import core


def _loop(a, open_utc, k0, k1):
    """The main loop's passes for minutes k0..k1: the tick, then `on_tick_ok` (as kaibot.engine.__main__ does)."""
    for k in range(k0, k1):
        assert a.ticks(open_utc, k, k + 1, catch=True) == []
        a.eng.on_tick_ok()


def _halts(a, kind="engine_errors"):
    return [h for h in a.store.tables.get("halt", []) if h["kind"] == kind]


def _cleared(a):
    return [e["payload"] for e in a.store.tables["event"] if e["type"] == "halt_cleared"]


def _blip(a, n=core.MAX_ERROR_STREAK):
    for _ in range(n):
        a.eng.on_tick_error(RuntimeError("alpaca: 503"))


def test_a_minute_of_errors_at_ten_does_not_block_the_near_close_buys(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    _loop(a, a.open1, 0, 390)
    a.after_close(a.open1)  # the rule decides: buy B on DAY2
    _loop(a, a.open2, 0, 30)
    _blip(a)  # 10:00: three failed passes in a row
    assert a.eng.kill == "engine_errors" and a.eng.entry_block() == "halted: engine_errors"
    assert len(_halts(a)) == 1
    _loop(a, a.open2, 30, 30 + core.CLEAN_TICKS_TO_CLEAR - 1)
    assert a.eng.kill == "engine_errors"  # four clean ticks are not enough
    _loop(a, a.open2, 34, 35)
    assert a.eng.kill is None and a.eng.entry_block() is None
    assert len(_halts(a)) == 1  # the halt row is kept
    (c,) = _cleared(a)
    assert c["kind"] == "engine_errors" and c["clean_ticks"] == core.CLEAN_TICKS_TO_CLEAR and c["day"] == DAY2
    _loop(a, a.open2, 35, 390)
    (o,) = a.md_orders()  # the 15:50 buy went out and filled
    assert o["symbol"] == "B" and o["side"] == "buy" and o["status"] == "filled"
    assert o["client_order_id"].startswith(f"kb{a.rid}-{MD}-B-20260804-buy-")
    assert len(_cleared(a)) == 1


def test_errors_that_go_on_keep_the_kill_and_block_the_buys(tmp_path, monkeypatch):
    """A venue that keeps failing: fewer than five clean ticks in a row between the failures, so the kill stays
    (fail closed, as before) and nothing is bought at 15:50."""
    a = DailyAccount(tmp_path, monkeypatch)
    _loop(a, a.open1, 0, 390)
    a.after_close(a.open1)
    _loop(a, a.open2, 0, 30)
    _blip(a)
    for k in range(30, 390, 4):  # four clean ticks, then one failed pass, all day
        _loop(a, a.open2, k, min(k + 4, 390))
        a.eng.on_tick_error(RuntimeError("alpaca: 503"))
        assert a.eng.kill == "engine_errors"
    assert a.md_orders() == [] and _cleared(a) == [] and SEND < 390


def test_a_second_blip_after_the_clear_halts_again_with_its_own_row(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    _loop(a, a.open1, 0, 30)
    _blip(a)
    _loop(a, a.open1, 30, 35)
    assert a.eng.kill is None
    _blip(a, core.MAX_ERROR_STREAK - 1)
    assert a.eng.kill is None  # the streak started again from zero
    _blip(a, 1)
    assert a.eng.kill == "engine_errors" and len(_halts(a)) == 2


def test_clean_ticks_never_clear_another_halt(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    _loop(a, a.open1, 0, 30)
    a.eng._halt_entries("drift", "positions differ from the broker")
    for _ in range(3 * core.CLEAN_TICKS_TO_CLEAR):
        a.eng.on_tick_ok()
    assert a.eng.kill == "drift" and _cleared(a) == []


def test_a_halt_raised_while_the_errors_kill_is_set_takes_over_and_is_not_cleared(tmp_path, monkeypatch):
    """QA-Ops (#483): before M9 1.7 a second halt behind `engine_errors` was dropped without harm (nothing cleared
    the kill before the next session). Now clean ticks clear `engine_errors`, so a drift seen meanwhile must take
    the kill over, with its own halt row, or five clean ticks would let buys out on books that differ."""
    a = DailyAccount(tmp_path, monkeypatch)
    _loop(a, a.open1, 0, 30)
    _blip(a)
    assert a.eng.kill == "engine_errors"
    a.eng._halt_entries("drift", "positions differ from the broker")
    assert a.eng.kill == "drift" and len(_halts(a)) == 1 and len(_halts(a, "drift")) == 1
    _blip(a)  # more errors neither take the kill back nor write another row
    assert a.eng.kill == "drift" and len(_halts(a)) == 1
    for _ in range(3 * core.CLEAN_TICKS_TO_CLEAR):
        a.eng.on_tick_ok()
    assert a.eng.kill == "drift" and a.eng.entry_block() == "halted: drift" and _cleared(a) == []
