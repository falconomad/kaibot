"""Issue #384: the minute ledger row (`kaibot.ledger`) carries real cash and, per multi-day sleeve, its shares and
the price it was marked at (contract: the issue, docs/ops/ledger.md "The minute row").

Seen in production on 19 Nov: the row's `cash` was the whole equity and `positions` was `{}` with seven stocks
held. Now: `positions` stays day positions only (the dashboard reads every key of it as a day position), `cash` is
the equity less everything the row lists, and a new `multi_day` block lists the book. Nothing else changes: orders,
the book line, the ledger_day close. The replicator sends rows without the column until migration 0034 is applied.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_failsafe_drills import MD, QTY, Account
from test_journal import Remote

from kaibot.engine import __main__ as engine_main
from kaibot.engine.journal import Journal, JournalStore, Replicator
from kaibot.engine.store import StoreError


def _cents(x: float) -> int:
    return round(100 * x)


def _identity(row: dict) -> None:
    """cash + multi_day.invested + sum(qty x last) over the day positions == the row's equity, to the cent."""
    md = row.get("multi_day") or {}
    day = sum(_cents((p["last"] or 0) * p["qty"]) for p in row["positions"].values())
    assert _cents(row["cash"]) + _cents(md.get("invested", 0)) + day == _cents(row["equity_venue"])
    assert _cents(md.get("invested", 0)) == sum(_cents(x["value"]) for x in md.get("positions", ()))


def test_the_minute_row_lists_the_multi_day_book_and_its_cash(tmp_path):
    a = Account(tmp_path)
    a.day1()
    a.ticks(a.open2, 0, 20)
    row = a.store.tables["ledger"][-1]
    assert row["positions"] == {}  # unchanged: day positions only, a multi-day sleeve never goes here
    md = row["multi_day"]
    assert md["v"] == 1 and md["settling"] == []
    (p,) = md["positions"]
    px = float(a.eng.market.last_close("B"))
    assert (p["rule"], p["symbol"], p["qty"], p["mark_from"]) == (MD, "B", QTY, "minute_bar")
    assert p["mark"] == pytest.approx(px) and p["value"] == round(QTY * px, 2)
    assert p["avg_cost"] == pytest.approx(a.b_px, abs=1e-4)
    # the mark's time is the end of the last minute bar (bar 19 of DAY2 ends at 09:50 ET)
    assert datetime.fromisoformat(p["mark_ts"]) == a.open2 + timedelta(minutes=20)
    assert row["equity_venue"] == row["equity_shadow"]
    _identity(row)
    # real cash: the equity less the holding, and the ledger's own cash within the one cent that rounding each
    # holding's value to the cent can move it (the identity above is exact; this one is to the cent, +-1)
    assert row["cash"] == pytest.approx(row["equity_venue"] - md["invested"], abs=0.005)
    assert _cents(md["cash_ledger"]) == _cents(a.eng.ledger.book(a.rid).cash)
    assert abs(_cents(row["cash"]) - _cents(md["cash_ledger"])) <= len(md["positions"])
    assert row["cash"] < row["equity_venue"] - 0.5 * QTY * px  # not the whole pot any more (19 Nov)


def test_every_minute_row_of_the_session_keeps_the_identity(tmp_path):
    a = Account(tmp_path)
    a.day1()
    n0 = len(a.store.tables["ledger"])
    a.ticks(a.open2, 0, 390)
    rows = a.store.tables["ledger"][n0:]
    assert len(rows) >= 380
    for r in rows:
        _identity(r)
        assert r["positions"] == {} and [x["symbol"] for x in r["multi_day"]["positions"]] == ["B"]


def test_a_holding_outside_the_live_universe_is_marked_at_the_broker_price(tmp_path):
    a = Account(tmp_path)
    a.day1()
    real = a.eng.market.last_close
    a.ticks(a.open2, 0, 5)
    a.eng.market.last_close = lambda s: None if s == "B" else real(s)  # B is not in today's minute bars
    a.ticks(a.open2, 5, 6)
    row = a.store.tables["ledger"][-1]
    (p,) = row["multi_day"]["positions"]
    cp = float(a.eng._bpos["B"]["current_price"])
    assert p["mark_from"] == "broker" and p["mark"] == pytest.approx(cp) and p["mark_ts"] is not None
    _identity(row)
    a.eng._bpos = {}  # nor at the broker: the last close mark, at 16:00 ET of that close
    a.ticks(a.open2, 6, 7)
    a.eng._bpos = {}
    row = a.eng._md_minute()
    (p,) = row["positions"]
    mark = a.eng.ledger.book(a.rid).sleeves[(MD, "B")].mark
    assert p["mark_from"] == "last_close" and p["mark"] == pytest.approx(mark)
    assert datetime.fromisoformat(p["mark_ts"]) == datetime(2026, 8, 3, 20, 0, tzinfo=UTC)


def test_the_marks_are_the_ones_the_equity_counts(tmp_path):
    """The row's marks are not a second price source: equity_now counts each sleeve at exactly that price."""
    a = Account(tmp_path)
    a.day1()
    a.ticks(a.open2, 0, 30)
    eq, _ = a.eng.equity_now()
    md = a.eng._md_minute()
    b = a.eng.ledger.book(a.rid)
    assert eq == pytest.approx(b.cash + sum(x["qty"] * x["mark"] for x in md["positions"]), abs=0.01)


def test_an_engine_without_multi_day_holdings_writes_the_row_it_always_did(tmp_path):
    a = Account(tmp_path)
    a.ticks(a.open1, 0, 10)
    row = a.store.tables["ledger"][-1]
    assert "multi_day" not in row and set(row) - {"id"} == {"run_id", "ts", "cash", "positions", "equity_venue",
                                                   "equity_shadow"}
    assert row["cash"] == row["equity_venue"]


def test_a_sale_under_way_marks_the_symbol_settling(tmp_path):
    a = Account(tmp_path)
    a.day1()
    a.ticks(a.open2, 0, 5)
    a.eng._md_exits["o-x"] = {"run_id": a.rid, "keys": [((MD, "B"), QTY)], "reason": "test"}
    assert a.eng._md_minute()["settling"] == ["B"]


def test_nothing_else_changes_orders_the_close_and_the_book_line(tmp_path, monkeypatch):
    """The same two sessions with the multi-day block switched off write the same orders, closes and book line."""
    from kaibot.engine import core

    def run(tmp, off):
        if off:
            monkeypatch.setattr(core.Engine, "_md_minute", lambda self: None)
        a = Account(tmp)
        a.day1()
        a.ticks(a.open2, 0, 390)
        a.after_close(a.open2)
        orders = sorted((o["symbol"], o["side"], o["type"], float(o["qty"])) for o in a.broker.orders.values())
        closes = [(c.session, round(c.close_equity, 2), round(c.cash, 2)) for c in a.eng.ledger.book(a.rid).closes]
        line = {k: v for k, v in (a.eng.book_line or {}).items() if k != "run_id"}
        return orders, closes, line, [r["equity_venue"] for r in a.store.tables["ledger"]]

    on = run(tmp_path / "on", False)
    off = run(tmp_path / "off", True)
    assert on == off


# ---------------------------------------------------------------- replication before migration 0034


class Pre0034(Remote):
    """Supabase before 0034: PostgREST refuses an unknown column (PGRST204)."""

    def __init__(self):
        super().__init__()
        self.refused = 0
        self.has_column = False

    def insert(self, table, row, returning=True):
        if table == "ledger" and "multi_day" in row and not self.has_column:
            self._gate()
            self.refused += 1
            raise StoreError('POST ledger -> 400: {"code":"PGRST204","details":null,"hint":null,"message":'
                             '"Could not find the \'multi_day\' column of \'ledger\' in the schema cache"}')
        return super().insert(table, row, returning)


def _ledger_row(i: int) -> dict:
    return {"run_id": 7, "ts": f"2026-11-20T15:{i:02d}:00+00:00", "cash": 79671.79, "positions": {},
            "equity_venue": 94796.94, "equity_shadow": 94796.94,
            "multi_day": {"v": 1, "cash_ledger": 79671.79, "invested": 15125.15, "settling": [], "positions": []}}


def test_before_0034_rows_go_without_the_column_and_it_is_tried_again_hourly():
    remote = Pre0034()
    st = JournalStore(Journal(), remote, live=True)
    rep = Replicator(st.journal, remote)
    t = [1000.0]
    rep.clock = lambda: t[0]
    for i in range(3):
        st.insert("ledger", _ledger_row(i), returning=False)
    assert st.select("ledger", {})[0]["multi_day"]["invested"] == 15125.15  # the journal keeps the full row
    assert rep.pump() == 3 and rep.last_error is None and rep.pending() == 0
    assert remote.refused == 1  # asked once; the next rows go without it straight away
    assert [("multi_day" in r, r["cash"]) for r in remote.tables["ledger"]] == [(False, 79671.79)] * 3
    eng = SimpleNamespace(store=st, _rid=lambda: 7)
    engine_main.report_conflicts(eng, rep)
    engine_main.report_conflicts(eng, rep)
    ev = [e for e in st.tables["event"] if e["type"] == "multi_day_column_missing"]
    assert len(ev) == 1 and "0034" in ev[0]["payload"]["action"]
    assert rep.status()["multi_day_fallback"] is True
    remote.has_column = True  # 0034 applied: within the hour the rows still go without it ...
    st.insert("ledger", _ledger_row(3), returning=False)
    rep.pump()
    assert "multi_day" not in remote.tables["ledger"][-1]
    t[0] += 3601  # ... after it, with it, no restart
    st.insert("ledger", _ledger_row(4), returning=False)
    rep.pump()
    assert remote.tables["ledger"][-1]["multi_day"]["v"] == 1


def test_another_refusal_of_a_ledger_row_is_still_an_error():
    class Down(Remote):
        def insert(self, table, row, returning=True):
            raise StoreError("POST ledger -> 503: upstream unavailable")

    down = Down()
    st = JournalStore(Journal(), down, live=True)
    rep = Replicator(st.journal, down)
    st.insert("ledger", _ledger_row(0), returning=False)
    rep.pump()
    assert rep.pending() == 1 and rep.last_error and not rep.multi_day_fallback


def test_near_close_buy_is_settling_until_its_fill_is_booked(tmp_path, monkeypatch):
    """15:50-15:57 ET: an order sent and not yet booked is in `settling` and in neither `cash` nor the positions; the
    tick that books its fill lists the holding at its shares. The identity holds in every row."""
    from test_live_daily_engine import SEND, DailyAccount

    a = DailyAccount(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 390)
    a.after_close(a.open1)
    a.ticks(a.open2, 0, SEND + 1)  # 15:50: the buy goes out
    sent = a.store.tables["ledger"][-1]
    assert sent["multi_day"]["settling"] == ["B"] and sent["multi_day"]["positions"] == []
    assert _cents(sent["cash"]) == _cents(sent["equity_venue"])
    n0 = len(a.store.tables["ledger"])
    a.ticks(a.open2, SEND + 1, 390)
    rows = a.store.tables["ledger"][n0:]
    for r in rows:
        _identity(r)
        assert r["positions"] == {}
    last = rows[-1]["multi_day"]
    assert last["settling"] == [] and [(x["symbol"], x["qty"]) for x in last["positions"]] == [
        ("B", float(a.md_orders()[0]["qty"]))]


def test_an_unreadable_multi_day_block_never_fails_the_minute(tmp_path, monkeypatch):
    """QA-Ops on #384: the block is a report. If building it raises, the minute still completes and its row is
    written (without the block), so the tick's retry and error streak never start from the report."""
    a = Account(tmp_path)
    a.day1()

    def boom(*_a, **_k):
        raise RuntimeError("mark unreadable")

    monkeypatch.setattr(a.eng, "_md_mark", boom)
    n = len(a.store.tables["ledger"])
    a.ticks(a.open2, 0, 5)
    rows = a.store.tables["ledger"][n:]
    assert rows and all("multi_day" not in r for r in rows)
    assert a.eng.day.done_m >= 4
