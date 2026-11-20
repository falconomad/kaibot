"""QA-Ops second key on #366: the fail-safe drill on a CARRIED buy (the engine dies right after the carry-over's send).

The outage drill's venue, engine and off-box dead-man (test_outage_drill_multiday: the real engine path with
KAIBOT_MULTIDAY=on on the simulated venue; migrations 0017-0030 run as SQL in a throwaway local Postgres, never
Supabase), with a rule that decides once:

    D0 (Mon)   trend_momentum holds B and C. After the close it decides for D1: keep B and C, buy D.
    D1 (Tue)   the decision's fill session. 15:50: the buy of D goes out; the venue lets it expire unfilled (#333).
               No decision that evening (not a decision day).
    D2 (Wed)   15:50: the carry-over (#363) sends the buy of D again, at the plan's shares. The engine dies right
               after that tick. The order fills at the next bar; nobody books it.

What must hold, as for a decision-day buy (#337, #342): before the venue got the carried order, today's
ledger_position rows already held D at its target shares, so the dead-man (and the watchdog, from the journal's
intent) count the fill as multi-day shares: D is kept through the flatten window, B and C are untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd
import test_outage_drill_multiday as drill
from pg_harness import DeadmanDb
from test_failsafe_drills import beat
from test_outage_drill_multiday import D0, D1, D2, HELD, MD, NEW, SEND, Drill, Keeps, _open

from kaibot.engine import daily
from kaibot.engine.watchdog import Watchdog, book_reader


@dataclass(frozen=True)
class DecidesOnce(Keeps):
    """`Keeps` (hold B and C, buy D), deciding only after D0's close: D1 is the fill session, D2 carries."""

    def decision_day(self, prev, fill) -> bool:
        return str(prev) == D0

    def signals(self, v):  # the drill's 75 sessions are too short for the rule's averages: every name is scored,
        n = len(v.symbols)  # in the universe and buyable (the carry-over reads all three: Amendment 13)
        return np.zeros(n), np.ones(n, dtype=bool), lambda j: None


def _to_d2(tmp_path, monkeypatch, calm: bool = True) -> Drill:
    """D0 and D1 as in the module's text; the market filter reads "on" (the drill has no 200 sessions of SPY).
    `calm`: without the outage drill's price moves (there C gaps down 15% at D2's open, which is a daily stop)."""
    monkeypatch.setattr(drill, "Keeps", DecidesOnce)
    monkeypatch.setattr(daily, "market_on", lambda spy: True)
    if calm:
        monkeypatch.setattr(drill, "LEVEL", {})
    d = Drill(tmp_path, monkeypatch, laptop=True)
    d.d0()
    d.broker.script.hold(NEW, bars=None)  # D1: the venue never fills the decision's buy; it expires at the close
    d.tick(D1, 0, 390)
    d.broker.advance(pd.Timestamp(_open(D1) + timedelta(minutes=390)))
    d.after_close(D1)
    d.after_close(D1, hours=11)  # 20:30 ET: "not a decision day" is journalled for D2
    return d


def _killed_right_after_the_carry_send(tmp_path, monkeypatch):
    d = _to_d2(tmp_path, monkeypatch)
    (first,) = d.md()
    assert first["status"] == "expired" and float(first["filled_qty"]) == 0 and NEW not in d.broker.pos
    assert "-buy-" in first["client_order_id"]
    seen: dict = {}
    real = d.broker.submit

    def submit(req):
        seen.setdefault("rows", [dict(r) for r in d.store.tables.get("ledger_position", [])
                                 if str(r["session"]) == D2])
        return real(req)

    d.broker.submit = submit
    d.tick(D2, 0, SEND + 1)  # 15:50: the carried buy goes out; the engine dies right after this tick
    d.broker.submit = real
    (carried,) = [o for o in d.md() if "-carry-" in o["client_order_id"]]
    assert carried["status"] in ("new", "accepted") and float(carried["qty"]) == float(first["qty"])
    d.broker.advance(pd.Timestamp(_open(D2) + timedelta(minutes=SEND + 1)))  # the 15:50 bar fills it
    assert carried["status"] == "filled" and (MD, NEW) not in d.eng.ledger.book(d.rid).sleeves  # not booked
    return d, float(carried["qty"]), seen["rows"]


def _kept(d, qty) -> None:
    assert float(d.broker.pos[NEW]["qty"]) == qty  # kept as multi-day shares
    for sym, q in HELD.items():
        assert float(d.broker.pos[sym]["qty"]) == q
    assert not [o for o in d.broker.orders.values() if o["side"] == "sell" and o["type"] != "stop"]


def test_todays_rows_hold_the_carried_buy_before_the_venue_gets_the_order(tmp_path, monkeypatch):
    d, qty, rows = _killed_right_after_the_carry_send(tmp_path, monkeypatch)
    assert [(r["strategy"], r["symbol"], float(r["qty"]), r["priced"]) for r in rows if r["symbol"] == NEW] == [
        (MD, NEW, qty, False)]
    carry = [b["carry"] for *_, b in d.eng.journal.entries(kinds=("md_send",)) if b.get("carry")]
    assert [c["carried"] for c in carry] == [[[NEW, int(qty), 0.0]]] and carry[0]["of"] == D1
    ev = [e["payload"] for e in d.store.tables["event"] if e["type"] == "multi_day_carry"]
    assert [e["session"] for e in ev] == [D2]


def test_a_carried_fill_after_the_engine_died_is_kept_by_the_dead_man(tmp_path, monkeypatch):
    d, qty, _ = _killed_right_after_the_carry_send(tmp_path, monkeypatch)
    db = DeadmanDb(tmp_path)
    try:
        db.beat(d.clock())
        db.run(d.rid)
        rows = d.store.tables.get("ledger_position", [])
        for s in sorted({str(r["session"]) for r in rows}):
            db.ledger(d.rid, s, [(r["strategy"], r["symbol"], r["qty"], r["stop_px"]) for r in rows
                                 if str(r["session"]) == s])
        said = []
        for k in range(SEND + 1, 390):  # 15:51 ... 15:59, once a minute (pg_cron)
            d.clock.t = _open(D2) + timedelta(minutes=k, seconds=2)
            said.append(db.check(d.clock()))
            db.pump(d.broker)
            d.broker.advance(pd.Timestamp(_open(D2) + timedelta(minutes=k + 1)))
        assert any(x.startswith("acted") for x in said)
        _kept(d, qty)
    finally:
        db.stop()


def test_a_carried_fill_after_the_engine_died_is_kept_by_the_watchdog(tmp_path, monkeypatch):
    d, qty, _ = _killed_right_after_the_carry_send(tmp_path, monkeypatch)
    beat(d.store, d.clock())
    wd = Watchdog(d.broker, d.store, now=d.clock, book=book_reader(d.jpath, d.store, now=d.clock))
    for k in range(SEND + 1, 390):
        d.clock.t = _open(D2) + timedelta(minutes=k, seconds=30)
        wd.step()
        d.broker.advance(pd.Timestamp(_open(D2) + timedelta(minutes=k + 1)))
    assert wd.flattened_on is not None
    _kept(d, qty)


def test_on_a_day_with_a_daily_stop_the_carried_buy_is_not_sent(tmp_path, monkeypatch):
    """The outage drill's D2: C opens 15% lower, the run's daily stop halts entries. The carry-over is planned and
    journalled, and the target loop refuses it like any buy while entries are blocked: nothing is bought."""
    d = _to_d2(tmp_path, monkeypatch, calm=False)
    d.tick(D2, 0, 390)
    assert d.eng.entry_block() and NEW not in d.broker.pos
    assert not [o for o in d.md() if "-carry-" in o["client_order_id"]]
    (ev,) = [e["payload"] for e in d.store.tables["event"] if e["type"] == "multi_day_orders"
             and e["payload"]["session"] == D2]
    assert ev["sent"] == [] and ev["skipped"][0][0] == NEW and ev["skipped"][0][1].startswith("entries blocked")


def test_with_no_cash_room_the_engine_carries_nothing_and_says_why(tmp_path, monkeypatch):
    """The same days through the real engine, with the ledger's cash gone before D2's window: the carried buy does
    not fit (the venue would lend), nothing is sent, and the send's journal and the event give the reason."""
    d = _to_d2(tmp_path, monkeypatch)
    d.tick(D2, 0, SEND - 1)
    monkeypatch.setattr(type(d.eng.daily.account), "cash", lambda self: 0.0)
    d.tick(D2, SEND - 1, SEND + 3)
    assert not [o for o in d.md() if "-carry-" in o["client_order_id"]]
    (note,) = [b["carry"] for *_, b in d.eng.journal.entries(kinds=("md_send",)) if b.get("carry")]
    assert note["carried"] == [] and note["skipped"] == {
        NEW: "no cash room at the last close (the cash reserve is kept)"}
    (ev,) = [e["payload"] for e in d.store.tables["event"] if e["type"] == "multi_day_carry"]
    assert ev["skipped"] == note["skipped"] and ev["session"] == D2
