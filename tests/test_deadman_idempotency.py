"""#585 (M13 4.3b, the rest of #477): the fail-safes' orders get a deterministic idempotency key.

Migration 0044 (`kaibot.deadman_act`) and the watchdog name a fail-safe order once per decision and session:
`kbdm-<strategy>-<symbol>-<yyyymmdd>-dmstop.g<n>` and `kbdm-intraday-<symbol>-<yyyymmdd>-x.g<n>`, the SAME id from
both fail-safes, so whichever comes second is refused by the venue. A re-send reuses the id, so the venue refuses a
second order; `<n>` rises only on the venue's own word that the order under it has ended (or, for a stop, when this
read lists it and still more shares need one). A filled sale is never that word (a second sale could sell short).

The venue here is `SimBroker`; pg_net is played by tests/pg_harness.py (a duplicate client id is answered 422, as
Alpaca does). Alpaca reserves the shares of an open sell order, so a second stop for the same shares is usually
refused for want of shares; that is a side effect, not a key (a partly filled sleeve or a resized stop lets both
through). The drills that prove the key turn the reservation off (`no_reservation`).
"""

from __future__ import annotations

import hashlib
import re
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "unit"))
from pg_harness import DeadmanDb  # noqa: E402
from test_order_manager import DAY, NY, venue  # noqa: E402

from kaibot.broker.protocol import BrokerError, OrderRequest  # noqa: E402
from kaibot.engine import failsafe  # noqa: E402
from kaibot.engine.failsafe import BookView, SleeveView  # noqa: E402
from kaibot.engine.watchdog import Watchdog, failsafe_coid  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
M30 = ROOT / "migrations" / "0030_deadman_planned_row_not_gone.sql"
M44 = ROOT / "migrations" / "0044_deadman_idempotency_key.sql"
MD = "trend_momentum"
R = 7
SESSION = DAY.replace("-", "")


def at(hhmm: str):
    return pd.Timestamp(f"{DAY} {hhmm}", tz=NY).to_pydatetime()


def no_reservation(b):
    """Every share counts as free for a sale: the partly filled or resized case, where the venue's share check
    does not stop a second order."""
    b._free_shares = lambda symbol: 1e9
    return b


def sells(b, sym, typ=None):
    return [o for o in b.orders.values() if o["symbol"] == sym and o["side"] == "sell"
            and (typ is None or o["type"] == typ)]


def working(b, sym, typ=None):
    return [o for o in sells(b, sym, typ) if o["status"] in ("new", "accepted", "partially_filled")]


def posts(call):
    return call["method"] == "POST"


@pytest.fixture
def dm(tmp_path):
    made = []

    def make(upto=None):
        d = DeadmanDb(tmp_path / f"dm{len(made)}", upto=upto)
        made.append(d)
        d.run(R)
        return d

    yield make
    for d in made:
        d.stop()


def b_held(qty=40, stop=90.0):
    b, _ = venue()
    b.pos["B"] = {"qty": qty, "avg_entry_price": 100.0}
    return b


def a_pass(db, b, read, act, lose=lambda c: False, hide=lambda o: False, flatten=None):
    """One dead-man pass: the read at `read`, the act at `act` (pg_cron's minutes), Alpaca playing pg_net."""
    db.check(read, flatten=flatten)
    db.pump(b, lose=lose, hide=hide)
    db.check(act, flatten=flatten)
    db.pump(b, lose=lose, hide=hide)


# ======================================================================= the migration and its guard


def body(sql: str) -> str:
    return sql[sql.index("create or replace function kaibot.deadman_act("):].split("as $$", 1)[1].split("$$;", 1)[0]


def test_the_md5_guard_names_0030s_body():
    h = hashlib.md5(body(M30.read_text()).encode()).hexdigest()
    assert f"'{h}'" in M44.read_text() and M44.read_text().count(h) == 2  # the check and its message


def test_0044_applies_on_0030_and_refuses_anything_else(dm):
    db = dm(upto=30)
    h30 = db.one("select md5(prosrc) from pg_proc where proname = 'deadman_act'")
    assert h30 == hashlib.md5(body(M30.read_text()).encode()).hexdigest()
    db.file(M44)
    h44 = db.one("select md5(prosrc) from pg_proc where proname = 'deadman_act'")
    assert h44 != h30 and h44 == hashlib.md5(body(M44.read_text()).encode()).hexdigest()
    with pytest.raises(RuntimeError, match="is not 0030's"):  # applied twice: refused, nothing changed
        db.file(M44)
    assert db.one("select md5(prosrc) from pg_proc where proname = 'deadman_act'") == h44
    assert db.one("select has_function_privilege('authenticated', "
                  "'kaibot.deadman_act(bigint, jsonb, boolean, timestamptz)', 'execute')") == "f"
    assert db.one("select count(*) from cron.job where jobname = 'kaibot_deadman'") == "1"


def test_0044_refuses_a_hand_edited_body_and_leaves_it(dm):
    db = dm(upto=30)
    src = M30.read_text().replace("-- a partly filled entry too", "-- edited by hand", 1)
    db.sql(src)
    edited = db.one("select md5(prosrc) from pg_proc where proname = 'deadman_act'")
    with pytest.raises(RuntimeError, match="is not 0030's"):
        db.file(M44)
    assert db.one("select md5(prosrc) from pg_proc where proname = 'deadman_act'") == edited


def test_0044_changes_only_the_ids_and_their_record():
    """Every line of 0030's body is in 0044's except those that built the timestamped ids, sent them or closed the
    result's literal (it gains `sales`, `lookups`, `already_sent`); the plan itself is 0030's."""
    old, new = body(M30.read_text()).splitlines(), body(M44.read_text()).splitlines()
    gone = [x for x in old if x not in new]
    allowed = ("'unprotected', '{}'::jsonb, 'alarms', '[]'::jsonb, 'flatten', flatten);",
               "to_char(ny, 'YYYYMMDDHH24MISS')", "perform net.http_post(", "'client_order_id', 'kbdm-",
               "|| left(kaibot.deadman_clean(sym), 12) || '-'", "'time_in_force', 'day', 'client_order_id'",
               "'qty', q, 'stop', px, 'why', why));")
    assert all(any(a in x for a in allowed) for x in gone), gone
    assert "to_char(ny, 'YYYYMMDDHH24MISS')" not in M44.read_text().split("begin;", 1)[1]


def test_both_parsers_take_the_new_ids(dm):
    db = dm()
    for coid in (f"kbdm-{MD}-B-{SESSION}-dmstop.g0", f"kbdm-intraday-B-{SESSION}-x.g3",
                 failsafe_coid(MD, "B", at("11:00"), gen=2), f"kbdm-{MD}-B-{SESSION}-dmstop.g0.r1"):
        assert failsafe.FAILSAFE_COID.match(coid), coid
        assert db.one(f"select (kaibot.deadman_coid('{coid}'))[2]") == ("B"), coid
    assert failsafe.parse_coid(f"kbdm-{MD}-B-{SESSION}-dmstop.g0") == (MD, "B", "dmstop.g0")
    assert failsafe.is_multi_day(failsafe.parse_coid(failsafe_coid(MD, "B", at("11:00")))[0])


# ======================================================================= the dead-man's drills


def test_a_lost_answer_and_late_reads_place_one_stop(dm):
    """The stop's POST is carried out but its answer is lost; the next two reads are late and do not list the stop.
    Each later pass sends the same id again: the venue answers 422 and one stop rests."""
    db = dm()
    db.ledger(R, DAY, [(MD, "B", 40, 90.0)])
    b = no_reservation(b_held())
    a_pass(db, b, at("11:00"), at("11:01"), lose=posts)
    assert len(working(b, "B", "stop")) == 1
    late = lambda o: o["symbol"] == "B" and o["type"] == "stop"  # noqa: E731
    a_pass(db, b, at("11:05"), at("11:06"), hide=late)
    a_pass(db, b, at("11:10"), at("11:11"), hide=late)
    (stop,) = sells(b, "B")
    assert stop["client_order_id"] == f"kbdm-{MD}-B-{SESSION}-dmstop.g0" and stop["status"] == "new"
    posted = db.sql("select body->>'client_order_id' from net.calls where method = 'POST' order by id")
    assert posted == [stop["client_order_id"]] * 3  # sent three times, placed once
    r = db.last_result()
    assert r["already_sent"] == [stop["client_order_id"]] and r["stops"][0]["gen"] == 0
    assert not [a for a in r["alarms"] if "without a protective stop" in a]


def test_planted_regression_the_timestamped_ids_of_0030_place_two_stops(dm):
    """The same drill on 0030 (the id carries the second it was sent): the late read makes it send a second stop
    under a new id, and the venue places it."""
    db = dm(upto=30)
    db.ledger(R, DAY, [(MD, "B", 40, 90.0)])
    b = no_reservation(b_held())
    a_pass(db, b, at("11:00"), at("11:01"), lose=posts)
    a_pass(db, b, at("11:05"), at("11:06"), hide=lambda o: o["symbol"] == "B" and o["type"] == "stop")
    assert len(working(b, "B", "stop")) == 2


def test_with_the_venues_share_check_a_lost_answer_still_places_one_stop(dm):
    db = dm()
    db.ledger(R, DAY, [(MD, "B", 40, 90.0)])
    b = b_held()
    a_pass(db, b, at("11:00"), at("11:01"), lose=posts)
    a_pass(db, b, at("11:05"), at("11:06"), hide=lambda o: o["type"] == "stop")
    assert len(working(b, "B", "stop")) == 1


def test_a_stop_the_engine_cancelled_is_sent_again_as_the_next_generation(dm):
    """The engine comes back, cancels the dead-man's stop and dies again. The next pass sends the same id (422) and
    asks the venue about it; the one after reads "canceled" and sends `.g1`. One working stop at the end."""
    db = dm()
    db.ledger(R, DAY, [(MD, "B", 40, 90.0)])
    b = no_reservation(b_held())
    a_pass(db, b, at("11:00"), at("11:01"))
    (g0,) = working(b, "B", "stop")
    b.cancel(g0["id"])
    a_pass(db, b, at("11:05"), at("11:06"))
    assert working(b, "B", "stop") == []  # 422: the id is the cancelled order's
    assert [lk["coid"] for lk in db.last_result()["lookups"]] == [g0["client_order_id"]]
    a_pass(db, b, at("11:10"), at("11:11"))
    (g1,) = working(b, "B", "stop")
    assert g1["client_order_id"] == f"kbdm-{MD}-B-{SESSION}-dmstop.g1" and float(g1["qty"]) == 40
    a_pass(db, b, at("11:15"), at("11:16"))
    assert working(b, "B", "stop") == [g1]


@pytest.mark.parametrize("answer", ["lost", "unreadable", "not found"])
def test_no_clear_word_from_the_venue_keeps_the_generation(dm, answer):
    db = dm()
    db.ledger(R, DAY, [(MD, "B", 40, 90.0)])
    b = no_reservation(b_held())
    a_pass(db, b, at("11:00"), at("11:01"))
    (g0,) = working(b, "B", "stop")
    b.cancel(g0["id"])
    lookup = lambda c: "by_client_order_id" in c["url"]  # noqa: E731
    a_pass(db, b, at("11:05"), at("11:06"), lose=lookup)
    if answer != "lost":
        content, status = ("not json", 200) if answer == "unreadable" else ("null", 404)
        req = db.last_result()["lookups"][0]["req"]
        db.sql(f"insert into net._http_response (id, status_code, content) values ({req}, {status}, '{content}')")
    a_pass(db, b, at("11:10"), at("11:11"), lose=lookup)
    assert working(b, "B", "stop") == []  # fail closed: g0 again, refused
    assert {s["gen"] for s in db.last_result()["stops"]} == {0}


def test_a_listed_stop_that_covers_too_little_gets_the_next_generation(dm):
    """The sleeve grew (40 -> 50 shares) while the dead-man's stop for 40 rests and is listed: the 10 shares left
    need a stop of their own, under `.g1` (the listed `.g0` is already counted)."""
    db = dm()
    db.ledger(R, DAY, [(MD, "B", 40, 90.0)])
    b = no_reservation(b_held())
    a_pass(db, b, at("11:00"), at("11:01"))
    db.sql("update kaibot.ledger_position set qty = 50")
    b.pos["B"]["qty"] = 50
    a_pass(db, b, at("11:05"), at("11:06"))
    got = sorted((o["client_order_id"].rsplit(".", 1)[1], float(o["qty"])) for o in working(b, "B", "stop"))
    assert got == [("g0", 40.0), ("g1", 10.0)]


def test_a_new_session_starts_at_generation_0():
    from_0044 = M44.read_text()
    assert "to_char(p.session, 'YYYYMMDD')" in from_0044 and "dp.session = p.session" in from_0044


def held_both_ways(db):
    """B: 40 multi-day shares with the engine's stop, and 10 intraday shares next to them (sold in the window)."""
    db.ledger(R, DAY, [(MD, "B", 40, 90.0)])
    b = no_reservation(b_held(qty=50))
    b.submit(OrderRequest("B", "sell", 40, "stop", "gtc", f"kb{R}-{MD}-B-20260914-stop-1", stop_price=90.0))
    return b


def test_the_intraday_sale_sent_twice_with_a_lost_answer_sells_once(dm):
    """The flatten window: the sale of B's 10 intraday shares is carried out but its answer is lost, and the next
    read does not list it: the same id goes again and the venue refuses it. One sale."""
    db = dm()
    b = held_both_ways(db)
    late = lambda o: o["type"] == "market"  # noqa: E731
    a_pass(db, b, at("15:56"), at("15:57"), lose=posts, hide=late)  # (the 15:57 read is in this pump too)
    assert len(working(b, "B", "market")) == 1
    a_pass(db, b, at("15:57"), at("15:58"), hide=late)
    (sale,) = sells(b, "B", "market")
    assert sale["client_order_id"] == f"kbdm-intraday-B-{SESSION}-x.g0" and float(sale["qty"]) == 10
    a_pass(db, b, at("15:58"), at("15:59"))  # listed now: our own working sale is left alone, not cancelled
    assert sells(b, "B", "market") == [sale] and sale["status"] == "new" and db.last_result()["cancel"] == []
    assert len(working(b, "B", "stop")) == 1  # the multi-day stop is kept


def test_planted_regression_0030_sells_twice(dm):
    db = dm(upto=30)
    b = held_both_ways(db)
    late = lambda o: o["type"] == "market"  # noqa: E731
    a_pass(db, b, at("15:56"), at("15:57"), lose=posts, hide=late)
    a_pass(db, b, at("15:57"), at("15:58"), hide=late)
    assert len(working(b, "B", "market")) == 2


def test_the_pass_records_the_lease_epoch_when_there_is_one(dm):
    db = dm()
    db.ledger(R, DAY, [(MD, "B", 40, 90.0)])
    b = b_held()
    a_pass(db, b, at("11:00"), at("11:01"))
    assert "epoch" not in db.last_result()  # no lease table here: nothing recorded, nothing failed
    db.sql("create table kaibot.engine_lease (id boolean primary key default true, epoch bigint not null default 0);"
           "insert into kaibot.engine_lease (epoch) values (4)")
    a_pass(db, b, at("11:05"), at("11:06"))
    assert db.last_result()["epoch"] == 4


# ======================================================================= the watchdog


class _Store:
    def select(self, table, params):
        return []

    def heartbeat(self, process, detail):
        pass

    def event(self, kind, detail, run_id=None):
        pass

    def insert(self, table, row, returning=True):
        pass


def watchdog(b, clock, qty=40):
    book = BookView([SleeveView(MD, "B", float(qty), 90.0)], "journal", 1)
    return Watchdog(b, _Store(), now=clock, book=lambda: book)


def test_the_watchdogs_id_is_one_per_decision_and_session_and_the_dead_mans():
    t = at("11:00")
    assert failsafe_coid(MD, "B", t) == failsafe_coid(MD, "B", t + timedelta(hours=3)) == \
        f"kbdm-{MD}-B-{SESSION}-dmstop.g0"  # 0044's stop id, character for character
    assert failsafe_coid("intraday", "A", t, "x", 2) == f"kbdm-intraday-A-{SESSION}-x.g2"  # 0044's sale id
    assert re.search(r"-\d{8}-", failsafe_coid(MD, "B", t + timedelta(days=1))).group(0) != f"-{SESSION}-"


def test_the_watchdog_with_a_lost_answer_and_a_late_read_places_one_stop():
    b, clock = venue()
    no_reservation(b)
    b.pos["B"] = {"qty": 40, "avg_entry_price": 100.0}
    b.script.timeout("B", accepted=True)  # the stop is placed, its answer never comes
    wd = watchdog(b, clock)
    out: dict = {}
    wd.protect(out, [])
    assert "B" in out.get("protect_failed", "") or out["stops"] == []
    (stop,) = working(b, "B", "stop")
    wd._sent.clear()  # its own memory of the send is gone (or older than SENT_COVERS_S)
    clock.set(clock() + timedelta(minutes=2))
    out = {}
    wd.protect(out, [])  # a late read: the stop is not listed
    assert working(b, "B", "stop") == [stop] and out["stops"] == []
    assert out["stops_present"] == [{"strategy": MD, "symbol": "B", "client_order_id": stop["client_order_id"],
                                     "status": "new"}]


def test_the_watchdog_moves_to_the_next_generation_when_its_stop_was_cancelled():
    b, clock = venue()
    no_reservation(b)
    b.pos["B"] = {"qty": 40, "avg_entry_price": 100.0}
    wd = watchdog(b, clock)
    wd.protect({}, [])
    (g0,) = working(b, "B", "stop")
    b.cancel(g0["id"])
    wd._sent.clear()
    clock.set(clock() + timedelta(minutes=2))
    out: dict = {}
    wd.protect(out, None)
    (g1,) = working(b, "B", "stop")
    assert g0["client_order_id"].endswith(".g0") and g1["client_order_id"] == g0["client_order_id"][:-1] + "1"
    assert [s["client_order_id"] for s in out["stops"]] == [g1["client_order_id"]]


def test_the_watchdog_sends_nothing_when_the_venue_cannot_be_asked():
    b, clock = venue()
    no_reservation(b)
    b.pos["B"] = {"qty": 40, "avg_entry_price": 100.0}

    def down(coid):
        raise BrokerError(0, "no answer")

    b.order_by_client_id = down
    out: dict = {}
    watchdog(b, clock).protect(out, [])
    assert sells(b, "B") == [] and "B" in out.get("protect_failed", "") + str(out)


def test_the_watchdog_builds_the_dead_mans_ids_for_the_same_sleeve(dm):
    """The decision on #585's open question: one name for both fail-safes. The ids 0044 sends (stop and sale) are
    the ones `failsafe_coid` builds for the same sleeve, symbol and session."""
    db = dm()
    b = held_both_ways(db)
    b.cancel(working(b, "B", "stop")[0]["id"])  # the engine's stop is gone: the dead-man re-creates it
    a_pass(db, b, at("15:56"), at("15:57"))
    r = db.last_result()
    assert [s["coid"] for s in r["stops"]] == [failsafe_coid(MD, "B", at("15:57"))]
    assert [s["coid"] for s in r["sales"]] == [failsafe_coid("intraday", "B", at("15:57"), "x")]


def test_the_dead_man_then_the_watchdog_in_the_same_minute_place_one_stop(dm):
    """The dead-man's stop is placed but its answer lost; the watchdog's read in the same minute is late and does
    not list it. The watchdog asks the venue by the shared id, finds it working and sends nothing."""
    db = dm()
    db.ledger(R, DAY, [(MD, "B", 40, 90.0)])
    b = no_reservation(b_held())
    a_pass(db, b, at("11:00"), at("11:01"), lose=posts)
    (stop,) = working(b, "B", "stop")
    b.clk.set(at("11:01"))
    wd = watchdog(b, b.clk)
    out: dict = {}
    wd.protect(out, [])  # the late read: no stop listed
    assert working(b, "B", "stop") == [stop] and out["stops"] == []
    assert [s["client_order_id"] for s in out["stops_present"]] == [stop["client_order_id"]]


def test_the_watchdog_then_the_dead_man_in_the_same_minute_place_one_stop(dm):
    """The watchdog places the stop; the dead-man's read of the same minute is late and does not list it, so it
    sends its stop under the same id: the venue answers 422 (present, not an alarm). One stop. With different names
    (kbwd/kbdm, before this decision) and the venue's share check off, two stops would rest."""
    db = dm()
    db.ledger(R, DAY, [(MD, "B", 40, 90.0)])
    b = no_reservation(b_held())
    b.clk.set(at("11:00"))
    watchdog(b, b.clk).protect({}, [])
    (stop,) = working(b, "B", "stop")
    a_pass(db, b, at("11:00"), at("11:01"), hide=lambda o: o["type"] == "stop")
    assert working(b, "B", "stop") == [stop]
    a_pass(db, b, at("11:05"), at("11:06"), hide=lambda o: o["type"] == "stop")
    assert working(b, "B", "stop") == [stop] and db.last_result()["already_sent"] == [stop["client_order_id"]]


def filled_but_still_held(sale):
    """The venue filled our sale; the positions read is late (stale) and still shows the shares it sold."""
    sale.update(status="filled", filled_qty=sale["qty"], filled_avg_price="100", filled_at=at("15:57").isoformat())


def test_a_filled_intraday_sale_is_never_evidence_the_dead_man_sells_once(dm):
    """QA (#615): the intraday sale under `.g0` filled at the venue, and the positions read still shows the shares.
    The dead-man sends `.g0` again (422), reads the venue's "filled" for it, and keeps `.g0`: no second sale, no
    `.g1`. Planting 'filled' into 0044's final statuses for a sale makes it sell twice."""
    db = dm()
    b = held_both_ways(db)
    a_pass(db, b, at("15:56"), at("15:57"))
    (sale,) = sells(b, "B", "market")
    assert sale["client_order_id"] == f"kbdm-intraday-B-{SESSION}-x.g0"
    filled_but_still_held(sale)
    assert float(b.positions()["B"]["qty"]) == 50  # the read still shows the 10 sold shares
    a_pass(db, b, at("15:57"), at("15:58"))  # .g0 again: 422; the lookup goes out with it
    assert [lk["coid"] for lk in db.last_result()["lookups"]] == [sale["client_order_id"]]
    a_pass(db, b, at("15:58"), at("15:59"))  # the lookup says "filled": not evidence, .g0 again
    assert sells(b, "B", "market") == [sale]
    posted = db.sql("select body->>'client_order_id' from net.calls where method = 'POST' "
                    "and body->>'type' = 'market' order by id")
    assert set(posted) == {sale["client_order_id"]} and len(posted) >= 3  # sent again and again, placed once
    assert {s["gen"] for s in db.last_result()["sales"]} == {0}


def test_a_filled_intraday_sale_is_never_evidence_the_watchdog_sells_once():
    """QA (#615): the same for the watchdog's flatten: its `.g0` sale filled, the positions read still shows the
    shares. Its retry asks the venue by id, finds the filled `.g0` and sends nothing: no `.g1`. Planting "filled"
    into FINAL_SALE makes it sell twice."""
    b, clock = venue()
    no_reservation(b)
    b.pos["B"] = {"qty": 50, "avg_entry_price": 100.0}  # 40 multi-day (the book), 10 intraday
    clock.set(at("15:56"))
    wd = watchdog(b, clock)
    wd._flatten(pd.Timestamp(DAY).date(), 1, {"closed": []})
    (sale,) = sells(b, "B", "market")
    assert sale["client_order_id"] == f"kbdm-intraday-B-{SESSION}-x.g0" and float(sale["qty"]) == 10
    filled_but_still_held(sale)
    assert float(b.positions()["B"]["qty"]) == 50
    clock.set(at("15:57"))
    out = {"closed": []}
    wd._flatten(pd.Timestamp(DAY).date(), 2, out)
    assert sells(b, "B", "market") == [sale] and not out["flatten_failed"]
    assert not [o for o in b.orders.values() if str(o.get("client_order_id")).endswith(".g1")]


# ======================================================================= QA on #615: a replaced fail-safe stop


def replacing_venue(qty=40):
    """Alpaca's PATCH: the old order turns `replaced`, `replaced_by` names the new one (#197)."""
    b, clock = venue(replace_new_id=True)
    b.pos["B"] = {"qty": qty, "avg_entry_price": 100.0}
    return no_reservation(b), clock


def adopt_and_replace(b, stop):
    """The engine came back, adopted the fail-safe stop and re-priced it: `<id>.r1` (orders.py, #197)."""
    return b.replace(stop["id"], stop_price=91.0, client_order_id=stop["client_order_id"] + ".r1")


def test_dead_man_a_replaced_stop_whose_replacement_ended_gets_one_new_stop(dm):
    """`.g0` placed, adopted and replaced by the engine (`.g0.r1`), then `.r1` cancelled while the engine is silent
    again; the shares are still held. The dead-man re-sends `.g0` (422), its lookup answers `replaced`, `.r1` is not
    listed: exactly one new stop, `.g1`. Counting `replaced` as live makes it send none (the gap QA found)."""
    db = dm()
    db.ledger(R, DAY, [(MD, "B", 40, 90.0)])
    b, _ = replacing_venue()
    a_pass(db, b, at("11:00"), at("11:01"))
    (g0,) = working(b, "B", "stop")
    r1 = adopt_and_replace(b, g0)
    b.cancel(r1["id"])
    assert working(b, "B", "stop") == [] and b.order_by_client_id(g0["client_order_id"])["status"] == "replaced"
    a_pass(db, b, at("11:05"), at("11:06"))  # .g0 again: 422, the lookup goes out with it
    a_pass(db, b, at("11:10"), at("11:11"))  # the lookup says replaced, nothing of the chain listed: .g1
    (g1,) = working(b, "B", "stop")
    assert g1["client_order_id"] == f"kbdm-{MD}-B-{SESSION}-dmstop.g1" and float(g1["qty"]) == 40
    a_pass(db, b, at("11:15"), at("11:16"))
    assert working(b, "B", "stop") == [g1]
    posted = db.sql("select body->>'client_order_id' from net.calls where method = 'POST' order by id")
    assert posted.count(g1["client_order_id"]) <= 2 and len([o for o in sells(b, "B") if
                                                             o["client_order_id"].endswith(".g1")]) == 1


def test_dead_man_a_replaced_stop_whose_replacement_works_and_is_listed_sends_nothing(dm):
    db = dm()
    db.ledger(R, DAY, [(MD, "B", 40, 90.0)])
    b, _ = replacing_venue()
    a_pass(db, b, at("11:00"), at("11:01"))
    (g0,) = working(b, "B", "stop")
    r1 = adopt_and_replace(b, g0)
    a_pass(db, b, at("11:05"), at("11:06"))
    a_pass(db, b, at("11:10"), at("11:11"))
    assert working(b, "B", "stop") == [r1]  # the plan counts .r1: no new stop
    assert db.sql("select count(*) from net.calls where method = 'POST' and "
                  "body->>'client_order_id' like '%.g1'") == ["0"]


def test_watchdog_a_replaced_stop_whose_replacement_ended_gets_one_new_stop():
    b, clock = replacing_venue()
    wd = watchdog(b, clock)
    wd.protect({}, [])
    (g0,) = working(b, "B", "stop")
    b.cancel(adopt_and_replace(b, g0)["id"])
    wd._sent.clear()
    clock.set(clock() + timedelta(minutes=2))
    out: dict = {}
    wd.protect(out, None)
    (g1,) = working(b, "B", "stop")
    assert g1["client_order_id"] == f"kbdm-{MD}-B-{SESSION}-dmstop.g1" and float(g1["qty"]) == 40
    assert [s["client_order_id"] for s in out["stops"]] == [g1["client_order_id"]] and "stops_present" not in out
    wd._sent.clear()
    clock.set(clock() + timedelta(minutes=2))
    wd.protect({}, None)
    assert working(b, "B", "stop") == [g1]


def test_watchdog_a_replaced_stop_whose_replacement_works_sends_nothing_listed_or_not():
    """`.r1` works: listed, the plan counts it; not listed (a late read), the chain is followed to it: present."""
    b, clock = replacing_venue()
    wd = watchdog(b, clock)
    wd.protect({}, [])
    (g0,) = working(b, "B", "stop")
    r1 = adopt_and_replace(b, g0)
    for orders in (None, []):  # None: the watchdog reads the open orders (.r1 listed); []: a late read
        wd._sent.clear()
        clock.set(clock() + timedelta(minutes=2))
        out: dict = {}
        wd.protect(out, orders)
        assert working(b, "B", "stop") == [r1] and out["stops"] == []
    assert [(s["client_order_id"], s["status"]) for s in out["stops_present"]] == [(g0["client_order_id"], "new")]
