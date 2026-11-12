"""S77 (#117): the ledger inside the engine, on fakes. Positions by (strategy, symbol), mark-to-market closes with a
position held over the close, dividends booked to it, a restart after the close that finalises the day from the
journal, and the drill: Supabase unreachable for 10 minutes around the 15:55 flatten."""

from __future__ import annotations

from datetime import UTC, timedelta

import numpy as np
import pandas as pd
import pytest
from fakes import Clock, FakeBroker, FakeData, FakeStore
from test_engine import synth_frames
from test_journal import Remote

from kaibot.engine.core import Engine
from kaibot.engine.journal import LIVE_ID_BASE, Journal, JournalStore, Replicator
from kaibot.engine.ledger import Action
from kaibot.engine.market import LiveMarket
from kaibot.replay.data import ET
from kaibot.risk.profiles import load_profiles

D1, D2 = "2026-08-03", "2026-08-04"  # synth_frames ends on D2


def open_utc(day):
    return pd.Timestamp(f"{day} 09:30", tz=ET).tz_convert(UTC).to_pydatetime()


def calm(frames, days=(D1, D2)):
    """A climbs smoothly from 10:25 on these days: an entry at 10:25 fills at 10:26 and no leg triggers."""
    f = frames["A"].copy()
    d = f["ts"].dt.tz_convert(ET).dt.date.astype(str)
    for day in days:
        idx = np.flatnonzero((d == day).to_numpy())
        c = f.loc[idx, "c"].to_numpy().copy()
        c[55:] = c[55] * np.exp(np.linspace(0, 0.002, len(c) - 55))
        o = np.r_[f.loc[idx[0], "o"], c[:-1]]
        f.loc[idx, "c"], f.loc[idx, "o"] = c, o
        f.loc[idx, "h"], f.loc[idx, "l"] = np.maximum(o, c) * 1.0003, np.minimum(o, c) * 0.9997
        f.loc[idx, "vw"] = (f.loc[idx, "h"] + f.loc[idx, "l"] + c) / 3
    return {**frames, "A": f}


def offer(eng, day, sym="A", at=(54,)):
    """The dip candidate at minute 54 of any day (as test_engine.fake_candidate, for a given date)."""
    real = eng.market.candidates_at

    def cands(m):
        if m not in at:
            return real(m).iloc[0:0] if len(real(m)) else pd.DataFrame()
        px = eng.market.last_close(sym)
        return pd.DataFrame([{"symbol": sym, "date": np.datetime64(eng.day.day if eng.day else day, "D"), "m": m,
                              "price": px, "atr5": px * 0.002, "vwap": px * 1.002, "z": -2.0, "resid_z": 0.1,
                              "turnover": 1.0, "sigma_1m": 0.0006, "news_24h": False}])

    eng.market.candidates_at = cands
    eng.market.regime_at = lambda m: (False, 18.0)


def build(tmp_path, frames, day, store=None, amount=5000.0, clock=None, broker=None, new_run=True, **kw):
    clock = clock or Clock(open_utc(day) - timedelta(minutes=30))
    data = FakeData(frames, clock)
    broker = broker or FakeBroker(data, clock, day)
    market = LiveMarket(data, ["A"], {"A": "XLK"}, "SPY", tmp_path / "hist",
                        lambda: pd.Series(18.0, index=pd.date_range("2025-01-01", "2026-12-31")))
    store = store if store is not None else FakeStore()
    eng = Engine(broker, market, store, load_profiles(), set(), now=clock, **kw)
    if new_run:
        store.insert("command", {"kind": "new_run", "payload": {"amount_usd": amount, "level": "balanced"},
                                 "idempotency_key": "k1", "applied_at": None})
    eng.startup()
    eng.apply_commands()
    return eng, broker, clock, store


def session(eng, broker, clock, day, until=390, after_close=True, each=None):
    o = open_utc(day)
    broker.day = day
    clock.t = o - timedelta(minutes=30)
    eng.tick()
    for k in range(until):
        broker.advance(pd.Timestamp(o + timedelta(minutes=k)))
        clock.t = o + timedelta(minutes=k + 1, seconds=7)
        if each is not None:
            each(k)
        else:
            eng.tick()
    if after_close:
        clock.t = o + timedelta(hours=7)
        eng.tick()


def rows(store, t):
    return store.tables.get(t, [])


# ---------------------------------------------------------------- two rules, one stock
def test_two_rules_hold_the_same_stock_and_each_is_closed_on_its_own(tmp_path):
    eng, broker, clock, store = build(tmp_path, calm(synth_frames()), D2)
    offer(eng, D2)
    second = {}

    def each(k):
        eng.tick()
        if k == 59 and not second:  # minute 60: a second rule buys A next to the dip rule
            ref = eng.market.last_close("A")
            second["sig"] = eng._decide("A", "toy_rule", ref, ref * 0.99, None, {"m": 59}, ["test"], None,
                                        sigma_1m=0.0006, atr=ref * 0.002)
        if k == 70:
            second["book"] = {k2: p.qty for k2, p in eng.pos.sleeves()}
            second["broker"] = float(broker.positions()["A"]["qty"])
            second["ledger_row"] = rows(store, "ledger")[-1]["positions"]

    session(eng, broker, clock, D2, each=each)
    book = second["book"]
    assert set(book) == {("dip_reversal", "A"), ("toy_rule", "A")}
    assert second["broker"] == sum(book.values()), "the broker holds one net position: the sum of both rules'"
    assert {"A", "A|toy_rule"} == set(second["ledger_row"]), "the minute ledger keeps one entry per rule"
    assert not any(h["kind"] == "drift" for h in rows(store, "halt")), "two rules on one stock are not drift"
    trades = rows(store, "trade")
    assert sorted(t["strategy"] for t in trades) == ["dip_reversal", "toy_rule"]
    assert all(t["exit_reason"] == "time" for t in trades) and broker.positions() == {} and eng.pos == {}
    sells = [o for o in broker.orders.values() if o["side"] == "sell" and o["symbol"] == "A"]
    assert len(sells) == 2 and sorted(float(o["qty"]) for o in sells) == sorted(book.values())
    day = rows(store, "ledger_day")[-1]
    assert day["positions"] == 0 and day["reconciled"]["ok"] is True


# ---------------------------------------------------------------- mark to market over a close
def carried(tmp_path, **kw):
    """D1: a dip position opened at 10:26 is still open at the close (the engine stopped before 15:55)."""
    eng, broker, clock, store = build(tmp_path, calm(synth_frames()), D1, **kw)
    offer(eng, D1)
    session(eng, broker, clock, D1, until=380)  # no 15:55 flatten: the position is held over the close
    return eng, broker, clock, store


def test_start_equity_is_yesterdays_mark_to_market_with_a_position_held_over_the_close(tmp_path):
    eng, broker, clock, store = carried(tmp_path)
    t = rows(store, "trade")[0]
    d1 = rows(store, "ledger_day")[0]
    pos = rows(store, "ledger_position")
    assert d1["session"] == D1 and d1["positions"] == 1 and len(pos) == 1
    p = pos[0]
    assert (p["strategy"], p["symbol"], p["qty"], p["stop_state"]) == ("dip_reversal", "A", t["qty"], "resting")
    assert d1["close_equity"] == pytest.approx(5000.0 + (p["mark"] - p["avg_cost"]) * p["qty"], abs=0.01)
    assert d1["invested"] == pytest.approx(p["mark"] * p["qty"], abs=0.01)
    assert rows(store, "daily_result")[0]["pnl_venue"] == 0.0, "realised P&L alone would say nothing happened"

    seen = {}

    def each(k):
        eng.tick()
        if k == 0:
            seen["start"] = eng.day.start_equity
            seen["eq"], seen["unreal"] = eng.equity_now()
            seen["last"] = eng.market.last_close("A")

    offer(eng, D2, at=())
    session(eng, broker, clock, D2, each=each)
    assert seen["start"] == pytest.approx(d1["close_equity"], abs=0.01), "start equity = yesterday's MTM close"
    # The daily stop sees today's change only: from the close mark, not from the fill (audit B1 item 4).
    assert seen["unreal"] == pytest.approx((seen["last"] - p["mark"]) * p["qty"], abs=0.01)
    t = rows(store, "trade")[0]
    assert t["exit_reason"] == "carried_over" and broker.positions() == {}
    d2 = rows(store, "ledger_day")[1]
    reported = [min(r["pnl_venue"], r["pnl_shadow"]) for r in rows(store, "daily_result")]
    assert d2["start_equity"] == pytest.approx(d1["close_equity"], abs=0.01)
    assert d2["close_equity"] == pytest.approx(5000.0 + sum(reported), abs=0.01)
    # The day's change is the trade's result minus the part already counted at yesterday's close.
    assert d2["close_equity"] - d2["start_equity"] == pytest.approx(
        reported[1] - (p["mark"] - p["avg_cost"]) * p["qty"], abs=0.02)
    assert eng.equity_now()[0] == pytest.approx(d2["close_equity"], abs=0.02)


@pytest.mark.parametrize("source", ["daily_store", "alpaca_activities"])
def test_a_dividend_on_a_held_position_is_booked(tmp_path, source):
    eng, broker, clock, store = carried(tmp_path)
    qty = rows(store, "trade")[0]["qty"]
    if source == "daily_store":
        eng.corporate_actions = lambda day: [Action("A", str(day), "dividend", per_share=0.5, ref="daily:1")]
        cash = 0.5 * qty
    else:  # the live hook: Alpaca's non-trade activity for the payment
        broker.non_trade.append({"activity_type": "DIV", "id": "20260804::1", "date": D2, "net_amount": "1.25",
                                 "symbol": "A", "qty": str(qty), "per_share_amount": "0.1"})
        cash = 1.25
    offer(eng, D2, at=())
    seen = {}

    def each(k):
        eng.tick()
        if k == 0:
            seen["adjust"] = eng.day.adjust

    session(eng, broker, clock, D2, each=each)
    assert seen["adjust"] == pytest.approx(cash)
    d2 = rows(store, "ledger_day")[1]
    assert d2["dividends"] == pytest.approx(cash, abs=0.005)
    reported = [min(r["pnl_venue"], r["pnl_shadow"]) for r in rows(store, "daily_result")]
    assert d2["close_equity"] == pytest.approx(5000.0 + sum(reported) + cash, abs=0.01)
    assert any(e["type"] == "dividend_booked" for e in rows(store, "event"))


# ---------------------------------------------------------------- restart after the close (audit D8)
def test_a_restart_after_the_close_finalises_the_day_from_the_journal(tmp_path):
    path = tmp_path / "journal.sqlite"
    frames = calm(synth_frames())
    eng, broker, clock, store = build(tmp_path, frames, D2, store=JournalStore(Journal(path)))
    offer(eng, D2)
    session(eng, broker, clock, D2, until=389, after_close=False)  # dies at 15:59, before the day is finalised
    assert rows(store, "daily_result") == [] and rows(store, "trade")[0]["exit_reason"] == "time"
    store.journal.close()

    clock.t = open_utc(D2) + timedelta(hours=9)  # 18:30 ET, market closed
    store2 = JournalStore(Journal(path))
    eng2, *_ = build(tmp_path, frames, D2, store=store2, clock=clock, broker=broker, new_run=False)
    assert eng2.ledger.book(eng2.run["id"]) is not None, "the ledger is the fold of the same journal"
    eng2.tick()
    eng2.tick()  # once only
    dr = rows(store2, "daily_result")
    t = rows(store2, "trade")[0]
    assert len(dr) == 1 and dr[0]["session"] == D2 and dr[0]["trades"] == 1
    assert dr[0]["pnl_venue"] == pytest.approx(t["features"]["pnl_venue"], abs=0.01)
    ld = rows(store2, "ledger_day")
    assert len(ld) == 1 and ld[0]["close_equity"] == pytest.approx(5000.0 + min(dr[0]["pnl_venue"],
                                                                                 dr[0]["pnl_shadow"]), abs=0.01)
    assert [e["type"] for e in rows(store2, "event")].count("session_finalized_late") == 1
    store2.journal.close()

    store3 = JournalStore(Journal(path))  # and a third start finds it done
    eng3, *_ = build(tmp_path, frames, D2, store=store3, clock=clock, broker=broker, new_run=False)
    eng3.tick()
    assert len(rows(store3, "daily_result")) == 1 and len(rows(store3, "ledger_day")) == 1
    assert eng3.ledger.start_equity(eng3.run["id"]) == pytest.approx(ld[0]["close_equity"])


def test_a_run_from_before_the_ledger_is_imported_with_the_same_start_equity(tmp_path):
    """A FakeStore engine restarted with an empty journal (as on the first start of this code on paper)."""
    eng, broker, clock, store = build(tmp_path, calm(synth_frames()), D1)
    offer(eng, D1)
    session(eng, broker, clock, D1)
    legacy = float(eng.run["amount_usd"]) + store.run_realized(eng.run["id"])
    eng2, *_ = build(tmp_path, calm(synth_frames()), D1, store=store, clock=clock, broker=broker, new_run=False)
    assert eng2.journal is not eng.journal and eng2.ledger.start_equity(eng2.run["id"]) == legacy


# ---------------------------------------------------------------- the drill (audit H2)
def test_drill_supabase_unreachable_for_10_minutes_keeps_stops_and_the_flatten(tmp_path):
    remote = Remote()
    store = JournalStore(Journal(tmp_path / "journal.sqlite"), remote, id_base=LIVE_ID_BASE)
    remote.insert("command", {"kind": "new_run", "payload": {"amount_usd": 5000.0, "level": "balanced"},
                              "idempotency_key": "k1", "applied_at": None})
    eng, broker, clock, _ = build(tmp_path, calm(synth_frames()), D2, store=store, new_run=False)
    rep = Replicator(store.journal, remote)
    offer(eng, D2)
    managed, errors, lag = [], [], []
    real_manage = eng._manage_stops
    eng._manage_stops = lambda m: (managed.append(m), real_manage(m))[1]
    down = range(378, 388)  # 15:48-15:58: ten minutes around the 15:55 flatten

    def each(k):  # the main loop: commands, one tick, the heartbeat, replication in the background
        remote.down = k in down
        try:
            eng.apply_commands()
            eng.tick()
            eng.on_tick_ok()
        except Exception as e:  # pragma: no cover - the assertion below says what happened
            errors.append((k, e))
        store.heartbeat("engine", {"state": "x"})
        rep.pump()
        if k in down:
            lag.append(rep.pending())

    session(eng, broker, clock, D2, each=each)
    assert errors == []
    assert set(down) <= set(managed), "stop management ran every minute of the outage"
    t = store.tables["trade"][0]
    assert t["exit_reason"] == "time" and broker.positions() == {}, "the 15:55 flatten went out during the outage"
    assert lag[-1] > 0, "rows queued in the journal while Supabase was down"
    rep.pump()
    assert rep.pending() == 0, "and arrived afterwards"
    rt = remote.tables["trade"][0]
    assert rt["id"] == t["id"] > LIVE_ID_BASE and rt["exit_reason"] == "time"
    assert len(remote.tables["fill"]) == 2 and remote.tables["daily_result"][0]["session"] == D2
    assert remote.tables["ledger_day"][0]["close_equity"] == store.tables["ledger_day"][0]["close_equity"]
    assert remote.tables["command"][0]["applied_at"] is not None
