"""QA-Ops on #342 (non-blocking finding, issue #344): a planned row for a stock that never filled makes both
fail-safes keep an unrelated position that holds exactly the same shares.

How. Since #342 the engine writes today's `ledger_position` row for each planned buy just before the send (15:50 ET),
at its target shares, so that a fill landing after the engine died counts as multi-day. If the buy never fills (the
venue holds it, refuses it or lets it expire) and the engine dies, the planned stock is a multi-day row with no
position at the broker. The #291 symbol-change rule (`failsafe.plan`, migration 0022's `deadman_act`) then reads it
as "a multi-day symbol gone at the broker" and takes ANY unexplained position (no book row, no sell order) with the
same share count as the renamed holding: kept as multi-day, given an emergency stop, never sold at the close.

Fail closed (docs/ops/failsafe.md) says the opposite: a position that neither the book nor a multi-day stop explains
is intraday and is sold in the flatten window. Here A, a position of exactly the planned shares with no legs (a
manual buy at the paper dashboard, or an intraday position whose legs are gone), is that position.

These tests assert the fail-closed behaviour (fixed in #344: a sleeve opened in its own row's session, a planned or
just-bought buy, can never be a holding renamed overnight, so it is not "gone at the broker"). The dead-man runs as SQL
(migrations 0017/0019/0022) in a throwaway local Postgres (tests/pg_harness.py).
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
from test_failsafe_drills import DAY2, MD, beat
from test_live_daily_engine import SEND, _killed_right_after_the_send

from kaibot.engine import failsafe
from kaibot.engine.watchdog import Watchdog, book_reader


def _planned_b_unfilled_and_a_with_the_same_shares(tmp_path, monkeypatch):
    """DAY2 15:50: the engine plans a buy of B (today's row written), sends it, the venue holds it unfilled, and the
    engine dies. The account also holds A: exactly as many shares as B's plan, no book row, no sell order."""
    a, qty, rows = _killed_right_after_the_send(tmp_path, monkeypatch, fill=False)
    assert [(r["strategy"], r["symbol"], float(r["qty"]), r["opened_session"]) for r in rows] == [
        (MD, "B", qty, DAY2)]  # the planned row: opened today, nothing held
    assert "B" not in a.broker.pos and qty == int(qty) and qty >= 1
    px = float(a.eng.market.last_close("A"))
    a.broker.pos["A"] = {"qty": int(qty), "avg_entry_price": px}
    assert not [o for o in a.broker.open_orders() if o["symbol"] == "A"]
    return a, int(qty)


def _sells_of(a, sym):
    return [o for o in a.broker.orders.values() if o["symbol"] == sym and o["side"] == "sell"]


def test_the_plan_alone_shows_it():
    """The pure plan, as both fail-safes build it today: the book view cannot tell B's planned row (opened today,
    nothing held) from a holding renamed overnight, so A, unexplained with B's shares, is kept. This one passes now
    and documents the mechanism; the fix gives the plan that distinction (`opened_today`, below)."""
    book = failsafe.BookView([failsafe.SleeveView(MD, "B", 12.0, None)], "store", 7)
    p = failsafe.plan({"A": {"qty": "12", "current_price": "100"}}, [], book, flatten=True)
    assert p.multi_day == {"A": 12.0} and p.close == []  # today: A is kept, with an emergency stop on it
    assert [(s.symbol, s.qty) for s in p.stops] == [("A", 12)]
    assert any("symbol change" in x for x in p.alarms)


def test_a_sleeve_opened_today_is_never_gone_at_the_broker():
    """#344: B opened in its own row's session (planned, not filled) is not a holding renamed overnight: A, unexplained
    with B's shares, is intraday and closed in the flatten window. B's row still counts as a book row."""
    book = failsafe.BookView([failsafe.SleeveView(MD, "B", 12.0, None, opened_today=True)], "store", 7)
    p = failsafe.plan({"A": {"qty": "12", "current_price": "100"}}, [], book, flatten=True)
    assert p.multi_day == {} and p.close == ["A"] and p.stops == []
    assert not any("symbol change" in x for x in p.alarms)
    # B held after all (it filled): its row explains it, it is kept as multi-day with its stop, not taken as A's
    p = failsafe.plan({"A": {"qty": "12", "current_price": "100"}, "B": {"qty": "12", "current_price": "50"}}, [],
                      book, flatten=True)
    assert p.multi_day == {"B": 12.0} and p.close == ["A"]


def test_an_older_sleeve_next_to_one_opened_today_is_still_gone_by_its_own_shares():
    """Only the sleeve opened today leaves the gone set: an older sleeve of the same symbol still counts, by its
    shares alone (the SQL sums the older rows only, too)."""
    book = failsafe.BookView([failsafe.SleeveView(MD, "B", 12.0, None),
                              failsafe.SleeveView("mom_12_1_band", "B", 5.0, None, opened_today=True)], "store", 7)
    p = failsafe.plan({"A": {"qty": "12", "current_price": "100"}}, [], book, flatten=True)
    assert p.multi_day == {"A": 12.0} and p.close == []


class _Store:
    def __init__(self, rows):
        self.tables = {"run": [{"id": 7, "status": "running"}], "ledger_position": rows}

    def select(self, table, q):
        rows = self.tables[table]
        if table == "ledger_position" and "session" in q:
            rows = [r for r in rows if r["session"] == q["session"].removeprefix("eq.")]
        return rows[:1] if q.get("limit") == "1" else rows


def test_book_from_store_marks_rows_opened_in_their_session():
    rows = [{"session": "2026-11-19", "strategy": MD, "symbol": "B", "qty": "12", "stop_px": None,
             "opened_session": "2026-11-19"},
            {"session": "2026-11-19", "strategy": MD, "symbol": "C", "qty": "3", "stop_px": "90",
             "opened_session": "2026-11-12"},
            {"session": "2026-11-19", "strategy": MD, "symbol": "D", "qty": "4", "stop_px": None,
             "opened_session": None}]
    b = failsafe.book_from_store(_Store(rows))
    assert {s.symbol: s.opened_today for s in b.sleeves} == {"B": True, "C": False, "D": False}

def test_the_watchdog_sells_an_unexplained_position_next_to_a_planned_row(tmp_path, monkeypatch):
    a, qty = _planned_b_unfilled_and_a_with_the_same_shares(tmp_path, monkeypatch)
    beat(a.store, a.clock())
    wd = Watchdog(a.broker, a.store, now=a.clock, book=book_reader(a.jpath, a.store, now=a.clock))
    outs = []
    for k in range(SEND + 1, 390):  # 15:51 ... 15:59, the watchdog's 15 s polls sampled every 30 s
        a.clock.t = a.open2 + timedelta(minutes=k, seconds=30)
        outs.append(wd.step())
        a.broker.advance(pd.Timestamp(a.open2 + timedelta(minutes=k + 1)))
    assert any("B" in o.get("canceled", []) for o in outs)  # the unfilled planned buy is cancelled (works today)
    assert not [s for o in outs for s in o.get("stops", []) if s["symbol"] == "A"], "no emergency stop on A"
    assert "A" not in a.broker.pos, "A is unexplained: sold in the flatten window (fail closed)"


def test_the_dead_man_sells_an_unexplained_position_next_to_a_planned_row(tmp_path, monkeypatch):
    from pg_harness import DeadmanDb

    a, qty = _planned_b_unfilled_and_a_with_the_same_shares(tmp_path, monkeypatch)
    db = DeadmanDb(tmp_path)
    try:
        db.beat(a.clock())
        db.run(a.rid)
        for s in sorted({str(r["session"]) for r in a.store.tables.get("ledger_position", [])}):
            db.ledger(a.rid, s, [(r["strategy"], r["symbol"], r["qty"], r["stop_px"], r.get("opened_session"))
                                 for r in a.store.tables["ledger_position"] if str(r["session"]) == s])
        said = []
        for k in range(SEND + 1, 390):  # 15:51 ... 15:59, once a minute (pg_cron)
            a.clock.t = a.open2 + timedelta(minutes=k, seconds=2)
            said.append(db.check(a.clock()))
            db.pump(a.broker)
            a.broker.advance(pd.Timestamp(a.open2 + timedelta(minutes=k + 1)))
        assert any(x.startswith("acted") for x in said)
        assert not [o for o in a.broker.open_orders() if o["symbol"] == "B" and o["side"] == "buy"]  # cancelled
        stops = db.sql("select st->>'symbol' from kaibot.deadman_pass p, jsonb_array_elements(p.result->'stops') st")
        assert "A" not in stops, "no emergency stop on A"
        assert "A" not in a.broker.pos, "A is unexplained: sold in the flatten window (fail closed)"
    finally:
        db.stop()


def test_0030_is_0022s_deadman_act_with_only_the_344_line_added():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "migrations"

    def fn(name):
        t = (root / name).read_text()
        return t[t.index("create or replace function kaibot.deadman_act("):].split("\n$$;", 1)[0].splitlines()

    old, new = fn("0022_deadman_exdate.sql"), fn("0030_deadman_planned_row_not_gone.sql")
    added = [x for x in new if x not in old]
    assert [x for x in new if x != added[0]] == old and len(added) == 1
    assert "lp.opened_session is null or lp.opened_session < lp.session" in added[0]
    m30 = "0030_deadman_planned_row_not_gone.sql"
    later = [p.name for p in sorted(root.glob("*.sql"))
             if p.name > m30 and "function kaibot.deadman_act(" in p.read_text()]
    assert later == ["0044_deadman_idempotency_key.sql"]  # #585: md5-guarded on 0030's body
