"""S80 (#120): the pure pieces of the overnight-safe fail-safes (engine/failsafe.py)."""

from __future__ import annotations

import pytest

from kaibot.engine import failsafe
from kaibot.engine.journal import Journal
from kaibot.engine.orders import client_order_id


def test_client_ids_tell_the_strategy_and_the_symbol():
    coid = client_order_id(12, "trend_momentum", "BRK.B", "2026-08-03", "stop")
    assert failsafe.parse_coid(coid) == ("trend_momentum", "BRK.B", "stop")
    assert failsafe.parse_coid("kbwd-trend_momentum-B-20260804155200-wdstop") == ("trend_momentum", "B", "wdstop")
    assert failsafe.parse_coid("kbdm-mom_12_1_band-B-20260804155700-dmstop") == ("mom_12_1_band", "B", "dmstop")
    for other in ("kb-12-345-1", "kb-12-345-1-moc", None, "", "manual-1"):
        assert failsafe.parse_coid(other) is None


def test_multi_day_names_include_neighbours_and_nothing_intraday():
    assert failsafe.is_multi_day("trend_momentum") and failsafe.is_multi_day("trend_momentum~reg60")
    assert failsafe.is_multi_day("mom_12_1_band_nofilter")
    for name in ("dip_reversal", "spy_late_momentum", "orb_stocks_in_play", "news_continuation", "", None):
        assert not failsafe.is_multi_day(name)


def test_an_entry_is_an_open_unfilled_kaibot_buy():
    o = {"side": "buy", "client_order_id": "kb-1-2-1", "status": "new", "filled_at": None, "filled_qty": "0"}
    assert failsafe.is_entry(o)
    assert failsafe.is_entry({**o, "client_order_id": "kb3-trend_momentum-B-20260804-target-1"})
    assert not failsafe.is_entry({**o, "side": "sell"})
    assert not failsafe.is_entry({**o, "client_order_id": "manual"})
    assert not failsafe.is_entry({**o, "filled_qty": "3"})
    assert not failsafe.is_entry({**o, "status": "filled"})


def test_the_emergency_stop_is_ten_percent_below_the_last_price_rounded_down():
    px, why = failsafe._stop_price(None, 123.456)
    assert px == 111.11 and why.startswith("emergency: 10% below")
    assert failsafe._stop_price(90.0, 100.0)[0] == 90.0
    assert failsafe._stop_price(101.0, 100.0)[0] == 90.0  # a stop at or above the market is not a stop
    assert failsafe._stop_price(None, None) == (None, "no stop and no last price")


def test_the_book_from_the_local_journal_is_read_without_writing(tmp_path):
    path = tmp_path / "j.sqlite"
    j = Journal(path)
    j.append("run", {"run_id": 5, "amount": 10_000})
    j.append("fill", {"run_id": 5, "strategy": "trend_momentum", "symbol": "B", "side": "buy", "qty": 40, "px": 100,
                      "fees": 0})
    j.append("om_intent", {"coid": client_order_id(5, "trend_momentum", "B", "2026-08-03", "stop"),
                           "strategy": "trend_momentum", "symbol": "B", "side": "sell", "qty": 40, "type": "stop",
                           "tif": "gtc", "stop": 90.0, "purpose": "stop", "session": "2026-08-03",
                           "created": "2026-08-03T14:00:00+00:00"})
    n = j.last_seq()
    j.close()
    b = failsafe.book_from_journal(failsafe.ReadOnlyJournal(path))
    assert b.source == "journal" and b.run_id == 5
    assert b.sleeves == [failsafe.SleeveView("trend_momentum", "B", 40.0, 90.0)]
    assert Journal(path).last_seq() == n
    with pytest.raises(FileNotFoundError):
        failsafe.ReadOnlyJournal(tmp_path / "missing.sqlite").entries()


class _Store:
    def __init__(self, rows):
        self.rows = rows

    def select(self, table, params):
        if table == "run":
            return [{"id": 5}]
        rows = [r for r in self.rows if str(r["run_id"]) == params["run_id"][3:]]
        if "session" in params:
            return [r for r in rows if r["session"] == params["session"][3:]]
        return sorted(rows, key=lambda r: r["session"], reverse=True)[: int(params.get("limit", 1000))]


def test_the_book_from_supabase_is_the_last_close():
    rows = [{"run_id": 5, "session": "2026-08-03", "strategy": "trend_momentum", "symbol": "B", "qty": 40,
             "stop_px": 90.0},
            {"run_id": 5, "session": "2026-08-04", "strategy": "trend_momentum", "symbol": "B", "qty": 30,
             "stop_px": None}]
    b = failsafe.book_from_store(_Store(rows))
    assert b.source == "store" and b.sleeves == [failsafe.SleeveView("trend_momentum", "B", 30.0, None)]


def test_shares_held_by_other_orders_are_not_promised_to_a_new_stop():
    pos = {"B": {"qty": "40", "current_price": "100"}}
    held = [{"id": "x", "symbol": "B", "side": "sell", "type": "limit", "qty": "30", "filled_qty": "0",
             "status": "new", "client_order_id": "manual"}]
    book = failsafe.BookView([failsafe.SleeveView("trend_momentum", "B", 40, 90.0)], "journal", 5)
    p = failsafe.plan(pos, held, book, flatten=False)
    assert [(s.qty, s.stop) for s in p.stops] == [(10, 90.0)] and p.unprotected == {"B": 30}
    assert p.alarms and "without a protective stop" in p.alarms[0]


def test_the_watchdog_reads_the_position_again_before_a_new_stop():
    """#179: a stop that fills between the read and the new stop must not leave a sell stop on a flat position."""
    from datetime import UTC, datetime

    from kaibot.engine.watchdog import Watchdog

    class Venue:
        def __init__(self):
            self.reads, self.sent = 0, []

        def positions(self):
            self.reads += 1
            return {"B": {"qty": "40", "current_price": "100"}} if self.reads == 1 else {}

        def open_orders(self):
            return []

        def submit(self, req):
            self.sent.append(req)

    v = Venue()
    book = failsafe.BookView([failsafe.SleeveView("trend_momentum", "B", 40, 90.0)], "journal", 5)
    wd = Watchdog(v, None, now=lambda: datetime(2026, 8, 4, 15, tzinfo=UTC), book=lambda: book)
    wd._event = lambda *a, **k: None
    out = {}
    wd.protect(out, [])
    assert v.sent == [] and out["stops"] == []
