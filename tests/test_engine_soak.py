"""Story S74 (#114), safe soak: three faults that made the paper soak untrustworthy (audit D6, D4, D7 in
docs/research/engine_v3/code_audit.md). Every test here drives the fakes only: no network, no broker.

D6  A tick that raised is run again by the loop's retry (it used to come back as "already done"), every failed
    attempt counts toward the error streak, and a failure that recurs every minute trips the kill switch. A minute
    that completed is still never run twice, and a retry repeats an entry scan only if it had not acted yet.
D4  A broker reject (403 / 422) on one candidate is contained: it is recorded on the signal, the other candidates
    are still evaluated, and it only becomes a loop error (and a halt) when it repeats.
D7  A flatten closes every position on its own, retries a refused close, and says what is still open; the Alpaca
    adapter waits for the cancel to be confirmed before it closes the position.

The loop tests use `Sim` from test_outage_drill, which mirrors the main loop of kaibot.engine.__main__ (commands,
one tick per completed minute, retry every 10 s, on_tick_ok / on_tick_error).
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from test_engine import make, synth_frames
from test_engine_safety import step
from test_outage_drill import Sim, buys, drift_up, start, stop_leg
from test_spy_momentum_engine import STRATS, no_dips, strong_morning

import kaibot.engine.alpaca as alp
from kaibot.engine import core
from kaibot.strategies.spy_momentum import ENTRY_M

NO_BUYING_POWER = '{"code":40310000,"message":"insufficient buying power"}'
STOP_ABOVE_MARKET = '{"code":42210000,"message":"stop_loss.stop_price must be <= base_price - 0.01"}'
SHARES_HELD = '{"code":40310000,"message":"insufficient qty available for order (requested: 3, available: 0)"}'


def climb_from(k: int):
    """Like drift_up, from minute k: a smooth, slow climb, so neither bracket leg of a later entry triggers."""

    def shock(c):
        c = c.copy()
        c[k:] = c[k] * np.exp(np.linspace(0, 0.002, len(c) - k))
        return c

    return shock


def frames_ab(shock=drift_up):
    """Two tradable symbols, A and B, both calm after the entry (drift_up: an entry at 10:25 is never stopped)."""
    frames = synth_frames(shock=shock)
    frames["B"] = synth_frames(seed=2, shock=shock)["A"]
    return frames


def offer(eng, at: dict[int, list[str]]) -> None:
    """Offer these symbols as dip candidates at these minutes, in this order (the rules downstream still apply)."""

    def cands(m):
        rows = []
        for sym in at.get(m, []):
            px = eng.market.last_close(sym)
            rows.append({"symbol": sym, "m": m, "price": px, "atr5": px * 0.002, "vwap": px * 1.002, "z": -2.0,
                         "resid_z": 0.1, "turnover": 1.0, "sigma_1m": 0.0006, "news_24h": False})
        return pd.DataFrame(rows)

    eng.market.candidates_at = cands
    eng.market.regime_at = lambda m: (False, 18.0)


def errors(sim) -> list[str]:
    """The error of every failed loop iteration so far, in order."""
    return [d["error"] for _, d in sim.beats if d.get("state") == "error"]


def _down(*a, **k):
    raise ConnectionError("simulated outage")


# =====================================================================================================
# D6: tick retry and the kill switch
# =====================================================================================================
def test_d6_a_failure_after_the_minute_started_trips_the_kill_switch(tmp_path):
    """Order sync fails on every attempt while the clock and the data feed still answer, so the tick fails after
    it has started the minute. The retry used to come back as "already done" and reset the error streak: the kill
    switch never tripped, however long it lasted."""
    sim, eng, broker, store = start(tmp_path)
    sim.run_until(sim.at(60, 2))
    assert eng.pos["A"].status == "open"
    leg = dict(stop_leg(broker))
    ledger_rows = len(store.tables["ledger"])
    broker.order = _down
    sim.run_until(sim.at(61, 2))  # attempts at 10:30:10, :20, :30, ... all fail
    assert eng.kill == "engine_errors" and eng.error_streak >= 3
    assert any(h["kind"] == "engine_errors" for h in store.tables["halt"])
    assert sim.status["state"] == "error" and len(errors(sim)) >= 3
    assert len(store.tables["ledger"]) == ledger_rows  # no minute was reported as done meanwhile
    assert stop_leg(broker) == leg and len(buys(broker)) == 1  # protection untouched, nothing new
    del broker.order  # the endpoint is back
    sim.run_until(sim.at(63))
    assert sim.status["state"] == "managing" and eng.error_streak == 0
    assert eng.kill == "engine_errors" and not eng._may_enter(eng.day.last_m)  # entries stay off for the session
    assert eng.pos["A"].status == "open" and len(store.tables["ledger"]) > ledger_rows


def test_d6_a_tick_that_raised_is_run_again_by_the_retry(tmp_path):
    """One failed call (the positions read of the drift check) on the very bar that offers a setup. The retry was a
    no-op, so that minute's entry and ledger row were skipped. Now the retry does the minute's work, once."""
    sim, eng, broker, store = start(tmp_path)  # A is offered at minute 54 only
    sim.run_until(sim.at(55, 2))
    ledger_rows = len(store.tables["ledger"])
    real, calls = broker.positions, []

    def positions():
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("positions read failed once")
        return real()

    broker.positions = positions
    sim.run_until(sim.at(55, 12))  # the 10:25:10 attempt fails after the minute was started
    assert sim.status["state"] == "error" and eng.error_streak == 1 and not buys(broker)
    sim.run_until(sim.at(55, 22))  # the 10:25:20 retry
    assert sim.status["minute"] == 54 and eng.error_streak == 0 and eng.kill is None
    assert len(buys(broker)) == 1, "the setup offered on that bar must be taken by the retry"
    assert len(store.tables["ledger"]) == ledger_rows + 1
    sim.run_until(sim.at(60))
    assert len(errors(sim)) == 1 and len(buys(broker)) == 1 and len(store.tables["trade"]) == 1
    assert eng.pos["A"].status == "open"


def test_d6_an_entry_scan_that_failed_before_acting_is_repeated(tmp_path):
    """The news lookup of the first candidate fails once. Nothing had been decided or sent, so the retry scans the
    bar again and takes the setup: one signal, one order."""
    sim, eng, broker, store = start(tmp_path)
    sim.run_until(sim.at(55, 2))
    real, calls = eng.market.data.news_count, []

    def news_count(symbol, t0, t1):
        calls.append(symbol)
        if len(calls) == 1:
            raise ConnectionError("news endpoint blip")
        return real(symbol, t0, t1)

    eng.market.data.news_count = news_count
    sim.run_until(sim.at(56))
    assert len(errors(sim)) == 1 and "news endpoint blip" in errors(sim)[0]
    assert len(buys(broker)) == 1 and len(store.tables["signal"]) == 1 and len(store.tables["trade"]) == 1


def test_d6_an_entry_scan_that_keeps_failing_trips_the_kill_switch(tmp_path):
    sim, eng, broker, store = start(tmp_path)
    sim.run_until(sim.at(55, 2))
    eng.market.data.news_count = _down
    sim.run_until(sim.at(56, 2))
    assert eng.kill == "engine_errors" and not buys(broker) and "signal" not in store.tables


def test_d6_a_retry_never_repeats_an_entry_scan_that_has_acted(tmp_path):
    """The broker accepts the entry, then writing its row fails, so the tick raises after the order went out.
    Client order ids are kb-<run>-<signal row id>-1: a second scan of the same bar would write a NEW signal row
    and send a second order under a new id, which the broker's duplicate check cannot catch. So the retry finishes
    the minute without scanning again, and the unknown position is caught as drift, as before."""
    sim, eng, broker, store = start(tmp_path)
    real_insert, failed = store.insert, []

    def insert(table, row, returning=True):
        if table == "order" and not failed:
            failed.append(row["client_order_id"])
            raise ConnectionError("store blip after the broker accepted the order")
        return real_insert(table, row, returning)

    store.insert = insert
    sim.run_until(sim.at(55, 12))  # the 10:25:10 attempt
    assert failed and len(buys(broker)) == 1 and sim.status["state"] == "error"
    sim.run_until(sim.at(55, 32))  # retries at 10:25:20 and 10:25:30
    assert len(buys(broker)) == 1, "a retry must not send a second entry for the same bar"
    assert len(store.tables["signal"]) == 1 and sim.status["minute"] == 54 and len(errors(sim)) == 1
    sim.run_until(sim.at(60))
    assert eng.kill == "drift" and len(buys(broker)) == 1 and "trade" not in store.tables


def test_d6_a_retry_leaves_a_finished_entry_scan_alone(tmp_path):
    """The ledger write fails once on a bar with no setup. The scan of that bar had finished, so the retry does
    the rest of the minute only: the dashboard's scan counters still count the bar once."""
    sim, eng, broker, store = start(tmp_path)
    sim.run_until(sim.at(50, 2))  # next: minute 49, the bar that closes at 10:20
    bars = eng.day.scan["bars"]
    real_insert, failed = store.insert, []

    def insert(table, row, returning=True):
        if table == "ledger" and not failed:
            failed.append(row)
            raise ConnectionError("store blip on the ledger row")
        return real_insert(table, row, returning)

    store.insert = insert
    sim.run_until(sim.at(50, 22))  # 10:20:10 fails at the very end of the minute, 10:20:20 finishes it
    assert failed and len(errors(sim)) == 1 and sim.status["minute"] == 49 and eng.error_streak == 0
    assert eng.day.scan["bars"] == bars + 1


def test_d6_a_completed_minute_is_never_run_twice(tmp_path):
    sim, eng, broker, store = start(tmp_path)
    sim.run_until(sim.at(60, 12))  # the 10:30:10 tick completed minute 59
    assert sim.status["minute"] == 59
    ledger_rows, orders = len(store.tables["ledger"]), dict(broker.orders)
    real, sync_reads = broker.order, []
    broker.order = lambda oid: sync_reads.append(oid) or real(oid)
    again = [eng.tick(), eng.tick()]  # the same minute again, as a slow loop iteration would ask
    assert [st["minute"] for st in again] == [59, 59]
    assert not sync_reads and len(store.tables["ledger"]) == ledger_rows and broker.orders == orders


# =====================================================================================================
# D4: broker rejects
# =====================================================================================================
def rejecting(broker, symbols, status, body) -> list[str]:
    """The fake venue refuses bracket entries for `symbols` the way Alpaca does. Returns the list of attempts."""
    real, sent = broker.submit_bracket, []

    def submit(coid, symbol, qty, stop, target):
        sent.append(symbol)
        if symbol in symbols:
            raise alp.AlpacaError(status, body)
        return real(coid, symbol, qty, stop, target)

    broker.submit_bracket = submit
    return sent


def by_symbol(store) -> dict[str, list[tuple[dict, dict]]]:
    """{symbol: [(signal row, its gate_run row)]} in decision order."""
    gate = {g["signal_id"]: g for g in store.tables["gate_run"]}
    out: dict = {}
    for s in store.tables["signal"]:
        out.setdefault(s["symbol"], []).append((s, gate[s["id"]]))
    return out


@pytest.mark.parametrize("status,body,why", [
    (422, STOP_ABOVE_MARKET, "stop_loss.stop_price must be <= base_price - 0.01"),
    (403, NO_BUYING_POWER, "insufficient buying power"),
    (422, "<html>not json</html>", "<html>not json</html>"),
])
def test_d4_a_broker_reject_is_contained_and_recorded(tmp_path, status, body, why):
    """It used to raise through _decide, _entries and tick: B was never evaluated, A stayed stored as approved and
    the minute counted as a loop error."""
    eng, broker, clock, store, open_utc = make(tmp_path, frames_ab())
    offer(eng, {54: ["A", "B"]})
    sent = rejecting(broker, {"A"}, status, body)
    step(eng, broker, clock, open_utc, 0, 57)  # no exception: one reject is not a loop error
    assert sent == ["A", "B"]  # B was still evaluated and sent
    assert [t["symbol"] for t in store.tables["trade"]] == ["B"] and eng.pos["B"].status == "open"
    assert "A" not in eng.pos and [o["symbol"] for o in buys(broker)] == ["B"]
    ((sig_a, gate_a),), ((sig_b, gate_b),) = by_symbol(store)["A"], by_symbol(store)["B"]
    assert sig_a["rules_veto"] == f"broker rejected: {why}"
    rej = sig_a["features"]["broker_reject"]
    assert rej["status"] == status and rej["reason"] == why and rej["qty"] > 0
    assert rej["client_order_id"] == f"kb-{eng._rid()}-{sig_a['id']}-1"
    assert sig_a["features"]["z"] == -2.0  # the decision's features are kept
    assert gate_a["approved"] is False and gate_a["qty"] == 0  # no longer stored as approved
    assert sig_b["rules_veto"] is None and gate_b["approved"] is True and "broker_reject" not in sig_b["features"]
    assert eng.day.entries == 1 and eng.kill is None and eng.error_streak == 0
    assert "halt" not in store.tables


def test_d4_repeated_rejects_become_a_loop_error_and_halt_entries(tmp_path):
    """Three in a row with no accepted order in between is not one bad candidate any more (account restricted, no
    buying power, an order format the venue refuses): the third is raised like before and entries stop."""
    eng, broker, clock, store, open_utc = make(tmp_path, frames_ab())
    offer(eng, {m: ["A"] for m in (54, 59, 64, 69, 74)})
    sent = rejecting(broker, {"A"}, 403, NO_BUYING_POWER)
    step(eng, broker, clock, open_utc, 0, 64)  # bars 54 and 59: two rejects, both contained
    assert sent == ["A", "A"] and eng.kill is None and "halt" not in store.tables
    with pytest.raises(alp.AlpacaError):
        step(eng, broker, clock, open_utc, 64, 65)  # bar 64: the third in a row is a loop error again
    assert eng.kill == "broker_rejects" and core.MAX_REJECT_STREAK == 3
    (halt,) = store.tables["halt"]
    assert halt["kind"] == "broker_rejects" and "3 entry orders in a row" in halt["reason"]
    assert "insufficient buying power" in halt["reason"]
    step(eng, broker, clock, open_utc, 65, 76)  # bars 69 and 74: nothing more is sent this session
    assert sent == ["A", "A", "A"] and "trade" not in store.tables
    rows = by_symbol(store)["A"]
    assert len(rows) == 3  # every reject is on its signal, the third included
    assert all(s["rules_veto"] == "broker rejected: insufficient buying power" and not g["approved"] for s, g in rows)


def test_d4_an_accepted_order_resets_the_reject_count(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, frames_ab())
    offer(eng, {54: ["A"], 59: ["A"], 64: ["B"], 69: ["A"], 74: ["A"]})
    sent = rejecting(broker, {"A"}, 422, STOP_ABOVE_MARKET)
    step(eng, broker, clock, open_utc, 0, 76)  # reject, reject, accepted, reject, reject: never three in a row
    assert sent == ["A", "A", "B", "A", "A"] and eng.kill is None and "halt" not in store.tables
    assert [t["symbol"] for t in store.tables["trade"]] == ["B"] and eng.day.entries == 1


@pytest.mark.parametrize("err", [
    alp.AlpacaError(0, "giving up on POST https://paper-api.alpaca.markets/v2/orders"),  # 5xx / network, retried
    alp.AlpacaError(500, "internal server error"),
    alp.AlpacaError(401, '{"message":"unauthorized"}'),
    alp.AlpacaError(422, '{"code":40010001,"message":"client_order_id must be unique"}'),  # our order IS there
    ConnectionError("network down"),
])
def test_d4_network_errors_and_5xx_keep_their_handling(tmp_path, err):
    """Only a 403 or 422 says "this order was refused". Anything else may mean the order is at the venue, so it is
    still a loop error, the scan stops, and the signal stays as it was written (the next start adopts or halts).
    That includes the one 422 that says an order with our client id already exists."""
    eng, broker, clock, store, open_utc = make(tmp_path, frames_ab())
    offer(eng, {54: ["A", "B"]})
    sent = []

    def submit(coid, symbol, qty, stop, target):
        sent.append(symbol)
        raise err

    broker.submit_bracket = submit
    with pytest.raises(type(err)) as ei:
        step(eng, broker, clock, open_utc, 0, 55)
    assert ei.value is err and sent == ["A"]  # B is not sent: as before
    ((sig_a, gate_a),) = by_symbol(store)["A"]
    assert sig_a["rules_veto"] is None and "broker_reject" not in sig_a["features"] and gate_a["approved"] is True
    assert eng.kill is None and "halt" not in store.tables and eng.day.entries == 0


def test_d4_a_reject_of_the_stop_only_entry_is_contained_too(tmp_path):
    """SPY late momentum sends a market buy with a protective stop only (OTO): same handling."""
    eng, broker, clock, store, open_utc = make(tmp_path, strong_morning(synth_frames()), start_level="balanced")
    eng.strategies = STRATS
    no_dips(eng)
    sent = []

    def submit(coid, symbol, qty, stop):
        sent.append(symbol)
        raise alp.AlpacaError(422, STOP_ABOVE_MARKET)

    broker.submit_oto_stop = submit
    clock.t = open_utc + timedelta(minutes=ENTRY_M + 1, seconds=7)  # 15:30:07, the momentum decision
    status = eng.tick()
    assert sent == ["SPY"] and status["minute"] == ENTRY_M and status["positions"] == {}
    ((sig, gate),) = by_symbol(store)["SPY"]
    assert sig["strategy"] == "spy_late_momentum" and sig["rules_veto"].startswith("broker rejected: stop_loss")
    assert gate["approved"] is False and eng.day.entries == 0 and eng.day.spy_mom_done and eng.kill is None


# =====================================================================================================
# D7: flatten isolation
# =====================================================================================================
def two_open(tmp_path, at=54, close="16:00"):
    """A and B are offered together at minute `at` (and bought); the session closes at `close` ET."""
    eng, broker, clock, store, open_utc = make(tmp_path, frames_ab(climb_from(at + 1)))
    broker.calendar = lambda day: {"date": day, "open": "09:30", "close": close}
    offer(eng, {at: ["A", "B"]})
    return eng, broker, clock, store, open_utc


def refuse_close(broker, symbols, times=None) -> list[str]:
    """The venue refuses to close `symbols` (403: the shares are still held), `times` times each or for ever."""
    real, calls = broker.close_position, []

    def close(symbol):
        calls.append(symbol)
        if symbol in symbols and (times is None or calls.count(symbol) <= times):
            raise alp.AlpacaError(403, SHARES_HELD)
        return real(symbol)

    broker.close_position = close
    return calls


def test_d7_one_failed_close_does_not_stop_the_others(tmp_path):
    """The loop had no try: when A (first in the book) could not be closed, B was not even attempted."""
    eng, broker, clock, store, open_utc = two_open(tmp_path)
    step(eng, broker, clock, open_utc, 0, 57)
    assert {s: p.status for s, p in eng.pos.items()} == {"A": "open", "B": "open"}
    calls = refuse_close(broker, {"A"})
    with pytest.raises(Exception) as ei:
        eng.flatten_all("manual")
    assert eng.pos["B"].exit_order_id and eng.pos["B"].exit_reason == "manual"  # B went out all the same
    assert eng.pos["A"].exit_order_id is None and eng.pos["A"].status == "open"
    assert isinstance(ei.value, core.FlattenIncomplete)  # raised only after every position was tried
    assert isinstance(ei.value.__cause__, alp.AlpacaError)  # the venue's refusal stays in the traceback
    assert set(ei.value.left) == {"A"} and "insufficient qty available" in ei.value.left["A"]
    assert str(ei.value).startswith("flatten (manual) incomplete, still open: A")
    assert calls.count("A") == core.FLATTEN_TRIES and calls.count("B") == 1  # A retried, B closed once


def test_d7_a_refused_close_is_retried_at_once(tmp_path):
    eng, broker, clock, store, open_utc = two_open(tmp_path)
    step(eng, broker, clock, open_utc, 0, 57)
    calls = refuse_close(broker, {"A"}, times=1)  # refused once (the cancel was still pending), then accepted
    eng.flatten_all("manual")  # no error: the second attempt closed it
    assert calls == ["A", "B", "A"] and all(p.exit_order_id for p in eng.pos.values())
    step(eng, broker, clock, open_utc, 57, 59)
    assert broker.pos == {} and eng.pos == {}
    assert sorted((t["symbol"], t["exit_reason"]) for t in store.tables["trade"]) == [("A", "manual"), ("B", "manual")]


def test_d7_an_unreachable_broker_is_left_to_the_next_loop_pass(tmp_path):
    """The adapter has already retried a 5xx or a network error for about 30 s: trying again inside the same call
    would only block the loop (and the heartbeat). Every position is still attempted once."""
    eng, broker, clock, store, open_utc = two_open(tmp_path)
    step(eng, broker, clock, open_utc, 0, 57)
    calls = []

    def close(symbol):
        calls.append(symbol)
        raise alp.AlpacaError(0, f"giving up on DELETE https://paper-api.alpaca.markets/v2/positions/{symbol}")

    broker.close_position = close
    with pytest.raises(Exception) as ei:
        eng.flatten_all("daily_stop")
    assert calls == ["A", "B"]  # both attempted, each once
    assert isinstance(ei.value, core.FlattenIncomplete) and set(ei.value.left) == {"A", "B"}
    assert str(ei.value).startswith("flatten (daily_stop) incomplete, still open: A, B")


def test_d7_the_time_flatten_keeps_trying_until_the_book_is_flat(tmp_path):
    """Through the loop, on an early close (flatten at 10:30): A cannot be closed for a while. B is closed at
    once, the loop comes back for A every 10 s, the failures are counted, and the session is only marked as
    flattened when A is out too."""
    eng, broker, clock, store, open_utc = two_open(tmp_path, at=44, close="10:35")
    sim = Sim(eng, broker, clock, store, open_utc)
    sim.run_until(sim.at(61, 2))
    assert {s: p.status for s, p in eng.pos.items()} == {"A": "open", "B": "open"} and eng.day.flatten_m == 60
    calls = refuse_close(broker, {"A"})
    sim.run_until(sim.at(61, 12))  # the 10:31:10 tick flattens
    assert eng.pos["B"].exit_order_id and eng.pos["B"].exit_reason == "time", "B must not wait for A"
    assert eng.pos["A"].exit_order_id is None and not eng.day.flattened
    assert sim.status["state"] == "error"
    assert sim.status["error"].startswith("FlattenIncomplete: flatten (time) incomplete, still open: A")
    sim.run_until(sim.at(61, 32))  # 10:31:20 and 10:31:30: the loop tries A again, B is left alone
    assert calls.count("B") == 1 and calls.count("A") == 3 * core.FLATTEN_TRIES
    assert eng.kill == "engine_errors" and not eng.day.flattened
    del broker.close_position  # the venue accepts the close now
    sim.run_until(sim.at(63, 12))
    assert eng.day.flattened and broker.pos == {} and eng.pos == {}
    assert sorted((t["symbol"], t["exit_reason"]) for t in store.tables["trade"]) == [("A", "time"), ("B", "time")]
    assert sim.status["state"] == "managing" and eng.error_streak == 0


def test_d7_a_manual_flatten_reports_what_is_still_open(tmp_path):
    eng, broker, clock, store, open_utc = two_open(tmp_path)
    step(eng, broker, clock, open_utc, 0, 57)
    refuse_close(broker, {"A"})
    store.insert("command", {"kind": "flatten_and_stop", "payload": {}, "idempotency_key": "k9", "applied_at": None})
    eng.apply_commands()  # a bad command never stops the engine: the error goes on the command
    cmd = store.tables["command"][-1]
    assert cmd["applied_at"] and cmd["payload"]["error"].startswith("flatten (manual) incomplete, still open: A")
    assert eng.pos["B"].exit_order_id and eng.pos["A"].exit_order_id is None


# ---------- the Alpaca adapter: cancel, wait for the confirmation, then close ----------
class Venue:
    """A scripted Alpaca trading API for one symbol with two open bracket legs. After the cancel requests the legs
    stay listed as open (pending_cancel) until the `confirm_after`-th read, and while they are listed the venue
    refuses to close the position (403), as Alpaca does while the shares are held for orders."""

    def __init__(self, confirm_after: int, legs: bool = True, has_position: bool = True):
        self.confirm_after, self.has_position = confirm_after, has_position
        self.legs = [{"id": "leg-stop", "symbol": "AAPL", "status": "new"},
                     {"id": "leg-target", "symbol": "AAPL", "status": "new"}] if legs else []
        self.other = [{"id": "other-1", "symbol": "MSFT", "status": "new"}]
        self.calls: list[tuple[str, str]] = []
        self.reads_after_cancel = 0

    def call(self, method, url, params=None, body=None):
        path = url.removeprefix("https://paper-api.alpaca.markets")
        self.calls.append((method, path))
        if (method, path) == ("GET", "/v2/orders"):
            if any(o["status"] == "pending_cancel" for o in self.legs):
                self.reads_after_cancel += 1
                if self.reads_after_cancel >= self.confirm_after:
                    self.legs = []
            return [dict(o) for o in self.legs + self.other]
        if method == "DELETE" and path.startswith("/v2/orders/"):
            for o in self.legs:
                if o["id"] == path.rsplit("/", 1)[1]:
                    o["status"] = "pending_cancel"
            return None
        if (method, path) == ("DELETE", "/v2/positions/AAPL"):
            if self.legs:
                raise alp.AlpacaError(403, SHARES_HELD)
            if not self.has_position:
                raise alp.AlpacaError(404, '{"message":"position does not exist"}')
            return {"id": "close-1", "symbol": "AAPL", "side": "sell", "type": "market", "status": "accepted"}
        raise AssertionError(f"unexpected call {method} {path}")


def paper(monkeypatch, venue: Venue):
    monkeypatch.setattr(alp._Http, "call", lambda self, method, url, params=None, body=None:
                        venue.call(method, url, params, body))
    p = alp.AlpacaPaper("k", "s")
    slept: list[float] = []
    p.http._sleep = slept.append  # the wait goes through the HTTP layer's sleep: nothing really sleeps here
    return p, slept


def test_d7_alpaca_close_waits_for_the_cancel_to_be_confirmed(monkeypatch):
    """It used to send the close right after the cancel requests; the venue refuses that while the cancel is
    pending (the shares are still held)."""
    venue = Venue(confirm_after=3)
    p, slept = paper(monkeypatch, venue)
    order = p.close_position("AAPL")
    assert order["id"] == "close-1"
    assert venue.calls == [
        ("GET", "/v2/orders"), ("DELETE", "/v2/orders/leg-stop"), ("DELETE", "/v2/orders/leg-target"),
        ("GET", "/v2/orders"), ("GET", "/v2/orders"), ("GET", "/v2/orders"),  # still pending, pending, gone
        ("DELETE", "/v2/positions/AAPL"),
    ]
    assert slept == [0.5, 0.5, 0.5]  # one pause before each confirmation read; each cancel is sent once


def test_d7_alpaca_close_wait_is_bounded(monkeypatch):
    """A cancel that is never confirmed must not hang the engine, and the position is not deleted while it is
    unconfirmed: after about 5 s the close is given up with a 409 (a refusal, so Engine.flatten_all retries it and
    reports it), and DELETE /v2/positions is never sent."""
    venue = Venue(confirm_after=10**6)
    p, slept = paper(monkeypatch, venue)
    with pytest.raises(alp.AlpacaError) as ei:
        p.close_position("AAPL")
    assert ei.value.status == 409 and "AAPL" in str(ei.value) and core._refused(ei.value)
    assert len(slept) == 10 and sum(slept) == 5.0
    assert venue.calls.count(("GET", "/v2/orders")) == 11
    assert ("DELETE", "/v2/positions/AAPL") not in venue.calls, "never delete before the cancel is confirmed"
    assert sum(1 for c in venue.calls if c[1].startswith("/v2/orders/")) == 2  # each cancel sent once, not per poll


def test_d7_alpaca_close_with_nothing_to_cancel_is_unchanged(monkeypatch):
    venue = Venue(confirm_after=0, legs=False)
    p, slept = paper(monkeypatch, venue)
    assert p.close_position("AAPL")["id"] == "close-1"
    assert venue.calls == [("GET", "/v2/orders"), ("DELETE", "/v2/positions/AAPL")] and slept == []
    gone = Venue(confirm_after=0, legs=False, has_position=False)
    p, slept = paper(monkeypatch, gone)
    assert p.close_position("AAPL") is None and slept == []  # already flat: still None, as before
