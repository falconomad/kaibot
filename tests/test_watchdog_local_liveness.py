"""M9 ticket 1.6 (audit M4): the watchdog confirms the engine's death on this machine before it acts on a heartbeat
it could not READ, and a buy the watchdog cancelled is carried like one that never reached the venue.

Before: an unreadable Supabase heartbeat counted as an engine that is down, so three minutes without Supabase
cancelled the working buys of a healthy engine, and the carry-over then left them out ("order canceled").

What must not change: a heartbeat that is readable and stale, an engine whose process is gone, an engine that
stopped writing its journal: the watchdog acts exactly as before.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

from kaibot.engine import failsafe
from kaibot.engine.journal import Journal
from kaibot.engine.watchdog import CANCELS_FILE, LOCAL_ALIVE_S, PID_FILE, LocalEngine, Watchdog, book_reader

T0 = datetime(2026, 8, 4, 19, 52, tzinfo=UTC)  # 15:52 ET: inside the 15:50-15:57 send window


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Venue:
    """One working buy of the daily path, nothing held."""

    def __init__(self):
        self.orders = [{"id": "o1", "client_order_id": "kb7-trend_momentum-D-20260804-buy-1", "symbol": "D",
                        "side": "buy", "qty": "10", "filled_qty": "0", "status": "new", "type": "market"}]
        self.canceled: list[str] = []

    def clock(self):
        return {"is_open": True, "next_close": "2026-08-04T20:00:00+00:00"}

    def calendar(self, day):
        return {"close": "16:00"}

    def open_orders(self):
        return [o for o in self.orders if o["status"] == "new"]

    def positions(self):
        return {}

    def cancel_order(self, oid):
        self.canceled.append(oid)
        for o in self.orders:
            if o["id"] == oid:
                o["status"] = "canceled"


class DownStore:
    """Supabase unreachable from this machine: every call fails."""

    def __init__(self):
        self.calls = 0

    def _fail(self, *a, **k):
        self.calls += 1
        raise RuntimeError("supabase unreachable")

    select = heartbeat = event = insert = _fail


class StaleStore:
    """Supabase answers; the engine's last heartbeat is five minutes old."""

    def __init__(self, clock):
        self.clock, self.halts, self.events = clock, [], []

    def select(self, table, params):
        return [{"process": "engine", "ts": (self.clock() - timedelta(minutes=5)).isoformat()}]

    def heartbeat(self, *a):
        pass

    def event(self, kind, detail):
        self.events.append(kind)

    def insert(self, table, row, returning=True):
        self.halts.append(row)


def _engine_writes(tmp_path, clock) -> Journal:
    j = Journal(tmp_path / "journal.sqlite", now=clock)
    j.append("row", {"op": "insert", "table": "ledger", "row": {"ts": clock().isoformat()}})
    return j


def _watchdog(tmp_path, clock, store, **kw) -> tuple[Watchdog, Venue]:
    venue = Venue()
    local = LocalEngine(tmp_path, now=clock, **kw)
    return Watchdog(venue, store, now=clock, local=local, cancel_log=tmp_path / CANCELS_FILE), venue


def test_supabase_unreachable_for_three_minutes_with_a_healthy_engine_cancels_nothing(tmp_path):
    clock = Clock(T0)
    j = _engine_writes(tmp_path, clock)
    store = DownStore()
    wd, venue = _watchdog(tmp_path, clock, store)
    for k in range(12):  # three minutes, the watchdog polls every 15 s; the engine writes its minute row
        clock.t = T0 + timedelta(seconds=15 * (k + 1))
        if k % 4 == 3:
            j.append("row", {"op": "insert", "table": "ledger", "row": {"ts": clock().isoformat()}})
        out = wd.step()
        assert out["state"] == "heartbeat_unreadable_engine_alive", out
    assert venue.canceled == [] and not wd.tripped
    assert not (tmp_path / CANCELS_FILE).exists()


def test_supabase_unreachable_and_the_journal_silent_is_a_dead_engine_as_before(tmp_path):
    clock = Clock(T0)
    _engine_writes(tmp_path, clock)
    wd, venue = _watchdog(tmp_path, clock, DownStore())
    clock.t = T0 + timedelta(seconds=LOCAL_ALIVE_S - 1)
    assert wd.step()["state"] == "heartbeat_unreadable_engine_alive"
    clock.t = T0 + timedelta(seconds=LOCAL_ALIVE_S + 1)  # no journal write since T0: the engine hangs or is gone
    out = wd.step()
    assert out["state"] == "engine_stale" and out["canceled"] == ["D"] and venue.canceled == ["o1"]
    assert wd.tripped


def test_supabase_unreachable_and_no_journal_at_all_is_a_dead_engine(tmp_path):
    wd, venue = _watchdog(tmp_path, Clock(T0), DownStore())
    assert wd.step()["state"] == "engine_stale" and venue.canceled == ["o1"]


def test_a_gone_engine_process_is_dead_at_once_whatever_the_journal_says(tmp_path):
    clock = Clock(T0)
    _engine_writes(tmp_path, clock)  # written a moment ago: the engine was killed right after
    (tmp_path / PID_FILE).write_text(json.dumps({"pid": 4242, "host": "mac"}))
    wd, venue = _watchdog(tmp_path, clock, DownStore(), host="mac", pid_alive=lambda pid: False)
    clock.t = T0 + timedelta(seconds=15)
    out = wd.step()
    assert out["state"] == "engine_stale" and venue.canceled == ["o1"]


def test_a_live_process_alone_proves_nothing_the_journal_decides(tmp_path):
    clock = Clock(T0)
    _engine_writes(tmp_path, clock)
    (tmp_path / PID_FILE).write_text(json.dumps({"pid": os.getpid(), "host": "mac"}))
    wd, venue = _watchdog(tmp_path, clock, DownStore(), host="mac")
    clock.t = T0 + timedelta(seconds=LOCAL_ALIVE_S + 30)  # the process exists and has written nothing: it hangs
    assert wd.step()["state"] == "engine_stale" and venue.canceled == ["o1"]


def test_a_pid_file_from_another_host_is_not_checked(tmp_path):
    """Docker: the engine's pid lives in another container. Only the journal speaks."""
    clock = Clock(T0)
    _engine_writes(tmp_path, clock)
    (tmp_path / PID_FILE).write_text(json.dumps({"pid": 1, "host": "engine-container"}))
    wd, venue = _watchdog(tmp_path, clock, DownStore(), host="watchdog-container", pid_alive=lambda pid: False)
    clock.t = T0 + timedelta(seconds=15)
    assert wd.step()["state"] == "heartbeat_unreadable_engine_alive" and venue.canceled == []


def test_a_readable_stale_heartbeat_is_acted_on_even_when_the_journal_is_fresh(tmp_path):
    """Not weakened: Supabase answered, and what it says is that the engine has been silent for five minutes."""
    clock = Clock(T0)
    _engine_writes(tmp_path, clock)
    store = StaleStore(clock)
    wd, venue = _watchdog(tmp_path, clock, store)
    out = wd.step()
    assert out["state"] == "engine_stale" and venue.canceled == ["o1"]
    assert [h["kind"] for h in store.halts] == ["watchdog"]


def test_no_heartbeat_row_is_acted_on_even_when_the_journal_is_fresh(tmp_path):
    clock = Clock(T0)
    _engine_writes(tmp_path, clock)
    store = StaleStore(clock)
    store.select = lambda table, params: []
    wd, venue = _watchdog(tmp_path, clock, store)
    assert wd.step()["state"] == "engine_stale" and venue.canceled == ["o1"]


def test_without_local_evidence_wired_an_unreadable_heartbeat_is_stale_as_before():
    venue = Venue()
    out = Watchdog(venue, DownStore(), now=Clock(T0)).step()
    assert out["state"] == "engine_stale" and venue.canceled == ["o1"]


def test_every_cancel_is_written_to_the_local_file_before_the_venue_is_called(tmp_path):
    clock = Clock(T0)
    wd, venue = _watchdog(tmp_path, clock, StaleStore(clock))
    seen = []
    real = venue.cancel_order
    venue.cancel_order = lambda oid: (seen.append(failsafe.watchdog_cancels(tmp_path / CANCELS_FILE)), real(oid))
    wd.step()
    assert seen == [{"o1", "kb7-trend_momentum-D-20260804-buy-1"}]
    (line,) = (tmp_path / CANCELS_FILE).read_text().splitlines()
    assert json.loads(line)["symbol"] == "D" and json.loads(line)["ts"] == T0.isoformat()


def test_a_cancel_file_that_cannot_be_written_never_stops_the_cancel(tmp_path):
    clock = Clock(T0)
    venue = Venue()
    wd = Watchdog(venue, StaleStore(clock), now=clock, cancel_log=tmp_path / "no" / "such" / "dir" / CANCELS_FILE)
    assert wd.step()["canceled"] == ["D"] and venue.canceled == ["o1"]


def test_the_cancel_file_reader_survives_a_missing_file_and_a_torn_line(tmp_path):
    p = tmp_path / CANCELS_FILE
    assert failsafe.watchdog_cancels(p) == set() and failsafe.watchdog_cancels(None) == set()
    p.write_text('{"order_id": "a", "client_order_id": "kb1-r-X-20260804-buy-1"}\n{"order_id": "b", "clie')
    assert failsafe.watchdog_cancels(p) == {"a", "kb1-r-X-20260804-buy-1"}


# ---------------------------------------------------------------- the carry-over after a watchdog cancel
def test_ended_buys_does_not_count_a_buy_the_watchdog_cancelled():
    from types import SimpleNamespace as O

    from kaibot.engine.daily import ended_buys
    from kaibot.engine.orders import St

    def order(sym, state, coid, oid, booked=0.0, session="d1"):
        return O(strategy="r", symbol=sym, side="buy", session=session, state=state, qty=10.0, booked=booked,
                 cum=booked, purpose="buy", reason=None, created=session, coid=coid, order_id=oid, ids=[oid],
                 client_ids=[], open=state not in (St.FILLED, St.EXPIRED, St.CANCELED, St.REJECTED))

    om = O(orders={n: o for n, o in enumerate([
        order("A", St.CANCELED, "kb1-r-A-1", "a1"),              # the venue's (or the engine's) cancel: as before
        order("B", St.CANCELED, "kb1-r-B-1", "b1"),              # the watchdog's, known by the venue's order id
        order("C", St.CANCELED, "kb1-r-C-1", "c1", booked=4.0),  # the watchdog's, by client id; part filled before
        order("D", St.EXPIRED, "kb1-r-D-1", "d1"),               # an earlier watchdog cancel, a later order decides
        order("D", St.CANCELED, "kb1-r-D-2", "d2", session="d2"),
        order("E", St.FILLED, "kb1-r-E-1", "e1", booked=10.0),   # in the file, but it filled: a fill is a fill
    ])})
    marked = {"b1", "kb1-r-C-1", "d1", "e1"}
    assert ended_buys(om, "r", "d1", "d3") == {"A": "canceled", "B": "canceled", "C": "canceled", "D": "canceled",
                                               "E": "filled"}  # without the file: unchanged
    got = ended_buys(om, "r", "d1", "d3", failsafe_cancels=marked)
    assert got == {"A": "canceled", "D": "canceled", "E": "filled"}
    # C lost no shares: its four booked shares are still held, so it is not "sold" either
    assert ended_buys(om, "r", "d1", "d3", base={}, held={"C": 4.0, "E": 10.0}, failsafe_cancels=marked) == got


def test_a_buy_the_watchdog_cancelled_is_carried_at_the_next_session(tmp_path, monkeypatch):
    """The drill of test_carry_failsafe_drill, with D1's buy cancelled by the watchdog (a forced trip: its heartbeat
    read says five minutes) instead of expiring. D2's carry-over must send it again, at the plan's shares."""
    import pandas as pd
    import test_outage_drill_multiday as drill
    from test_carry_failsafe_drill import DecidesOnce
    from test_outage_drill_multiday import D1, D2, NEW, SEND, Drill, _open

    from kaibot.engine import daily

    monkeypatch.setattr(drill, "Keeps", DecidesOnce)
    monkeypatch.setattr(daily, "market_on", lambda spy: True)
    monkeypatch.setattr(drill, "LEVEL", {})
    d = Drill(tmp_path, monkeypatch, laptop=True)
    d.d0()
    d.broker.script.hold(NEW, bars=None)  # D1: the decision's buy rests unfilled
    d.tick(D1, 0, 388)  # past the 15:50-15:57 window: the engine sends nothing more today
    (first,) = d.md()
    assert first["status"] in ("new", "accepted")
    wd = Watchdog(d.broker, StaleStore(d.clock), now=d.clock, cancel_log=d.jpath.parent / CANCELS_FILE,
                  book=book_reader(d.jpath, d.store, now=d.clock))
    assert wd.step()["canceled"] == [NEW] and first["status"] == "canceled"
    d.tick(D1, 388, 390)
    d.broker.advance(pd.Timestamp(_open(D1) + timedelta(minutes=390)))
    d.after_close(D1)
    d.after_close(D1, hours=11)
    assert len(d.md()) == 1  # nothing was sent again on D1
    d.tick(D2, 0, SEND + 1)
    carried = [o for o in d.md() if "-carry-" in o["client_order_id"]]
    assert len(carried) == 1 and float(carried[0]["qty"]) == float(first["qty"])
    (note,) = [b["carry"] for *_, b in d.eng.journal.entries(kinds=("md_send",)) if b.get("carry")]
    assert note["skipped"] == {} and [c[0] for c in note["carried"]] == [NEW]


def test_a_buy_cancelled_without_the_watchdogs_record_is_still_not_carried(tmp_path, monkeypatch):
    """The other half: the same cancel with no local record (the owner cancelled it at the venue) stays left out."""
    import pandas as pd
    import test_outage_drill_multiday as drill
    from test_carry_failsafe_drill import DecidesOnce
    from test_outage_drill_multiday import D1, D2, NEW, SEND, Drill, _open

    from kaibot.engine import daily

    monkeypatch.setattr(drill, "Keeps", DecidesOnce)
    monkeypatch.setattr(daily, "market_on", lambda spy: True)
    monkeypatch.setattr(drill, "LEVEL", {})
    d = Drill(tmp_path, monkeypatch, laptop=True)
    d.d0()
    d.broker.script.hold(NEW, bars=None)
    d.tick(D1, 0, 388)
    (first,) = d.md()
    d.broker.cancel_order(first["id"])
    d.tick(D1, 388, 390)
    d.broker.advance(pd.Timestamp(_open(D1) + timedelta(minutes=390)))
    d.after_close(D1)
    d.after_close(D1, hours=11)
    d.tick(D2, 0, SEND + 1)
    assert not [o for o in d.md() if "-carry-" in o["client_order_id"]]
    (note,) = [b["carry"] for *_, b in d.eng.journal.entries(kinds=("md_send",)) if b.get("carry")]
    assert note["skipped"] == {NEW: "order canceled"}


# ---------------------------------------------------------------- QA-Ops review of #485: the end of the outage
class BackStore(StaleStore):
    """Supabase unreachable, then back. The heartbeat row it then shows is the engine's last one from before the
    outage, until the engine's next beat lands (`beat`)."""

    def __init__(self, clock, last_beat):
        super().__init__(clock)
        self.down, self.ts = True, last_beat

    def select(self, table, params):
        if self.down:
            raise RuntimeError("supabase unreachable")
        return [{"process": "engine", "ts": self.ts.isoformat()}]

    def beat(self):
        self.ts = self.clock()


def _outage(tmp_path, minutes=3):
    """Three minutes without Supabase, a healthy engine (a journal write a minute); then Supabase answers."""
    clock = Clock(T0)
    j = _engine_writes(tmp_path, clock)
    store = BackStore(clock, T0)
    wd, venue = _watchdog(tmp_path, clock, store)
    for k in range(4 * minutes):
        clock.t = T0 + timedelta(seconds=15 * (k + 1))
        if k % 4 == 3:
            j.append("row", {"op": "insert", "table": "ledger", "row": {"ts": clock().isoformat()}})
        assert wd.step()["state"] == "heartbeat_unreadable_engine_alive"
    store.down = False
    return clock, j, store, wd, venue


def test_supabase_back_before_the_engines_next_beat_cancels_nothing(tmp_path):
    """The watchdog polls every 15 s and the engine beats about every 10 s, so after an outage the watchdog often
    reads the old row first: a heartbeat three minutes old, of an engine whose journal was written seconds ago.
    That engine is alive. It has `stale_s` from the first good read to land a beat."""
    clock, j, store, wd, venue = _outage(tmp_path)
    clock.t += timedelta(seconds=5)
    out = wd.step()  # read, three minutes old
    assert out["state"] == "heartbeat_unreadable_engine_alive" and venue.canceled == [] and not wd.tripped
    store.beat()
    clock.t += timedelta(seconds=15)
    assert wd.step()["state"] == "ok" and venue.canceled == [] and store.halts == []


def test_an_engine_that_does_not_beat_within_the_stale_time_after_supabase_is_back_is_acted_on(tmp_path):
    """The allowance ends: Supabase answers, the journal is still written, and no beat lands for 90 s (the engine
    cannot write there). A heartbeat that is read and stale is acted on, as before."""
    clock, j, store, wd, venue = _outage(tmp_path)
    for _ in range(7):  # good reads from +15 s to +105 s: 90 s from the first one, the journal written all along
        clock.t += timedelta(seconds=15)
        j.append("row", {"op": "insert", "table": "ledger", "row": {"ts": clock().isoformat()}})
        assert wd.step()["state"] == "heartbeat_unreadable_engine_alive"
    clock.t += timedelta(seconds=15)
    j.append("row", {"op": "insert", "table": "ledger", "row": {"ts": clock().isoformat()}})
    out = wd.step()  # past the allowance, the journal fresh: the read heartbeat decides
    assert out["state"] == "engine_stale" and venue.canceled == ["o1"] and wd.tripped


def test_supabase_back_and_the_journal_silent_is_a_dead_engine(tmp_path):
    clock, j, store, wd, venue = _outage(tmp_path)
    clock.t += timedelta(seconds=60)  # inside the allowance; the last journal write is now 105 s old: alive
    assert wd.step()["state"] == "heartbeat_unreadable_engine_alive"
    clock.t = T0 + timedelta(seconds=180 + LOCAL_ALIVE_S + 1)  # no write since the outage ended: dead
    out = wd.step()
    assert out["state"] == "engine_stale" and venue.canceled == ["o1"]


def test_supabase_back_and_the_engine_process_gone_is_dead_at_once(tmp_path):
    clock, j, store, wd, venue = _outage(tmp_path)
    (tmp_path / PID_FILE).write_text(json.dumps({"pid": 4242, "host": wd.local.host}))
    wd.local.pid_alive = lambda pid: False
    clock.t += timedelta(seconds=5)
    assert wd.step()["state"] == "engine_stale" and venue.canceled == ["o1"]


def test_supabase_back_with_no_heartbeat_row_is_acted_on(tmp_path):
    clock, j, store, wd, venue = _outage(tmp_path)
    store.select = lambda table, params: []
    clock.t += timedelta(seconds=5)
    assert wd.step()["state"] == "engine_stale" and venue.canceled == ["o1"]
