"""S80 (#120): overnight-safe fail-safes, drilled with an open multi-day position (decision 15 sections 4 and 6).

Two sessions (DAY1, DAY2) on the simulated venue: a multi-day sleeve of `trend_momentum` holds 40 shares of B from
DAY1, with its protective GTC stop at the broker (sent through the order manager, so the local journal knows its
price); intraday rules trade A as before. The drills:

- the engine is killed at 15:50 on DAY2 (the watchdog alone, then the database dead-man alone);
- the network is cut overnight (the engine and the watchdog cannot reach the broker; the dead-man can);
- Supabase is down at the open (the watchdog cannot read the heartbeat or the ledger rows; the local journal can).

In each, entry orders are cancelled, every multi-day position keeps (or gets back) a protective stop in whole shares,
the alarm is raised, intraday shares are sold at the close minus 5 (or 4) minutes, and the multi-day position is
never sold. The dead-man runs as SQL (migration 0019) in a throwaway local Postgres (tests/pg_harness.py).
"""

from __future__ import annotations

import re
from datetime import UTC, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fakes import Clock, FakeBroker, FakeData, FakeStore
from pg_harness import DeadmanDb, orders_json, positions_json
from test_engine import fake_candidate, synth_frames

from kaibot.broker.protocol import BrokerError, OrderRequest
from kaibot.broker.sim import SimBroker
from kaibot.engine import failsafe
from kaibot.engine.core import Engine
from kaibot.engine.journal import Journal
from kaibot.engine.market import LiveMarket
from kaibot.engine.orders import OrderManager
from kaibot.engine.watchdog import POLL_S, Watchdog, book_reader
from kaibot.replay.data import ET
from kaibot.risk.profiles import load_profiles
from kaibot.strategies import registry

ROOT = Path(__file__).resolve().parents[1]
DAY1, DAY2 = "2026-08-03", "2026-08-04"
MD = "trend_momentum"
QTY = 40


def _open(day: str):
    return pd.Timestamp(f"{day} 09:30", tz=ET).tz_convert(UTC).to_pydatetime()


def frames(gap_b: float = 1.0) -> dict:
    """A (intraday), B (multi-day), XLK, SPY; B's whole DAY2 is scaled by `gap_b` (an overnight gap). A drifts up
    smoothly after 10:25 on DAY2, so an intraday entry then is still open at 15:50."""
    def drift_up(c):
        c = c.copy()
        c[55:] = c[55] * np.exp(np.linspace(0, 0.002, 335))
        return c

    f = synth_frames(shock=drift_up)
    b = synth_frames(seed=7)["A"].copy()
    later = b["ts"] >= pd.Timestamp(_open(DAY2))
    for c in ("o", "h", "l", "c", "vw"):
        b.loc[later, c] = b.loc[later, c] * gap_b
    f["B"] = b
    return f


class TwoDays(FakeBroker):
    """The fake venue over two sessions, marking positions as Alpaca does (`current_price`)."""

    clock = SimBroker.clock

    def _days(self):
        return [DAY1, DAY2]

    def positions(self):
        now = self.clk()
        out = {}
        for s, p in {**self.foreign, **self.pos}.items():
            px = self.bars.last_close(s, now) if s in self.bars.frames else None
            out[s] = {**p, "current_price": None if px is None else str(px)}
        return out


class Account:
    def __init__(self, tmp_path, gap_b=1.0, amount=10_000.0):
        self.open1, self.open2 = _open(DAY1), _open(DAY2)
        self.clock = Clock(self.open1 - timedelta(minutes=30))
        self.data = FakeData(frames(gap_b), self.clock)
        self.broker = TwoDays(self.data, self.clock, DAY1)
        self.store = FakeStore()
        self.jpath = tmp_path / "journal.sqlite"
        self.tmp = tmp_path
        self.eng = self.engine()
        self.quiet(self.eng)
        self.store.insert("command", {"kind": "new_run", "payload": {"amount_usd": amount, "level": "balanced"},
                                      "idempotency_key": "k1", "applied_at": None})
        self.eng.startup()
        self.eng.apply_commands()
        self.rid = self.eng.run["id"]

    def engine(self, journal=None) -> Engine:
        market = LiveMarket(self.data, ["A", "B"], {"A": "XLK", "B": "XLK"}, "SPY", self.tmp / "hist",
                            lambda: pd.Series(18.0, index=pd.date_range("2025-01-01", "2026-12-31")))
        return Engine(self.broker, market, self.store, load_profiles(), set(), now=self.clock,
                      journal=journal or Journal(self.jpath))

    @staticmethod
    def quiet(eng: Engine) -> None:
        """No intraday candidates unless a test offers one (fake_candidate): B stays a multi-day holding only."""
        eng.market.candidates_at = lambda m: pd.DataFrame()

    def restart(self) -> Engine:
        """A new engine process on the same journal and store (the old one was killed)."""
        self.eng.journal.close()
        self.eng = self.engine()
        self.quiet(self.eng)
        self.eng.startup()
        return self.eng

    def ticks(self, open_utc, k0: int, k1: int, eng: Engine | None = None, catch: bool = False) -> list:
        errors = []
        for k in range(k0, k1):
            bar = open_utc + timedelta(minutes=k)
            self.broker.advance(pd.Timestamp(bar))
            self.clock.t = bar + timedelta(minutes=1, seconds=7)
            if eng is None and not catch:
                self.eng.tick()
                continue
            try:
                (eng or self.eng).tick()
            except Exception as e:  # what the main loop does: count it and carry on
                (eng or self.eng).on_tick_error(e)
                errors.append(e)
        return errors

    def after_close(self, open_utc):
        self.broker.advance(pd.Timestamp(open_utc + timedelta(minutes=389)))
        self.clock.t = open_utc + timedelta(hours=7)
        self.eng.tick()

    def hold_b(self, k: int = 30):
        """The multi-day sleeve: 40 shares of B bought on DAY1 (booked by the ledger, as the order manager's
        daily path does) and its GTC protective stop at 90% of the fill, sent through the order manager."""
        self.ticks(self.open1, 0, k)
        px = float(self.eng.market.last_close("B"))
        self.broker.pos["B"] = {"qty": QTY, "avg_entry_price": px}
        self.eng._book("fill", {"run_id": self.rid, "strategy": MD, "symbol": "B", "side": "buy", "qty": QTY,
                                "px": px, "fees": 0.0, "coid": f"kb{self.rid}-{MD}-B-20260803-target-1",
                                "session": DAY1})
        om = OrderManager(self.broker, self.eng.journal, self.clock, run_id=self.rid)
        coid = om.next_coid(self.rid, MD, "B", DAY1, "stop")
        om.submit(OrderRequest("B", "sell", QTY, "stop", "gtc", coid, stop_price=round(px * 0.9, 2)), strategy=MD,
                  purpose="stop", session=DAY1, override_block=True)
        self.b_px, self.stop_coid = px, coid
        return px

    def stop_b(self):  # read straight from the venue's book (works while the network is "down")
        return [o for o in self.broker.orders.values() if o["symbol"] == "B" and o["type"] == "stop"
                and o["status"] in ("new", "accepted", "pending_new", "partially_filled")]

    def sells(self, sym):
        return [o for o in self.broker.orders.values() if o["symbol"] == sym and o["side"] == "sell"
                and o["type"] != "stop"]

    def day1(self):
        self.hold_b()
        self.ticks(self.open1, 30, 390)
        self.after_close(self.open1)


def beat(store, ts):
    store.upsert("heartbeat", {"process": "engine", "ts": ts.isoformat(), "detail": {}}, "process")


# ======================================================================= the engine with a multi-day sleeve


def test_a_multi_day_sleeve_is_held_over_the_close_and_not_sold_at_the_next_open(tmp_path):
    a = Account(tmp_path)
    a.day1()
    assert a.broker.pos["B"]["qty"] == QTY and a.stop_b() and not a.sells("B")  # 15:55 flatten: intraday only
    close1 = a.eng.ledger.book(a.rid).closes[-1]
    assert [p["symbol"] for p in close1.positions] == ["B"]
    lp = [r for r in a.store.tables["ledger_position"] if r["symbol"] == "B"]
    assert lp and lp[-1]["stop_state"] == "resting" and lp[-1]["stop_px"] == pytest.approx(round(a.b_px * 0.9, 2))
    a.ticks(a.open2, 0, 15)
    assert a.broker.pos["B"]["qty"] == QTY and a.stop_b() and not a.sells("B")  # not "carried": kept
    assert a.eng.account_clean and a.eng.kill is None  # the account guard knows the sleeve; no drift
    assert a.eng.day.start_equity == pytest.approx(close1.close_equity)


def test_only_intraday_positions_are_carried_into_the_next_open(tmp_path):
    from kaibot.engine.core import Pos

    a = Account(tmp_path)
    a.day1()
    y = (a.open1 + timedelta(hours=1)).isoformat()
    a.eng.pos.add(Pos(1, "A", 5, "c1", "o1", 100, 1, 1, 0, 99, 102, status="open", fill=100, entry_ts=y))
    a.eng.pos.add(Pos(2, "B", 5, "c2", "o2", 100, 1, 1, 0, 99, 0, status="open", fill=100, entry_ts=y,
                      strategy=MD))
    a.clock.t = a.open2 + timedelta(minutes=1, seconds=7)
    a.eng._start_day(a.clock())
    assert a.eng.pos[("dip_reversal", "A")].carried and not a.eng.pos[(MD, "B")].carried


def test_an_overnight_gap_counts_toward_the_daily_stop_which_blocks_entries_and_keeps_the_stop(tmp_path):
    a = Account(tmp_path, gap_b=0.95)
    a.day1()
    start = a.eng.ledger.start_equity(a.rid)
    mark = a.eng.ledger.book(a.rid).sleeves[(MD, "B")].mark
    a.ticks(a.open2, 0, 3)
    eq, _ = a.eng.equity_now()
    assert eq - start == pytest.approx(QTY * (float(a.eng.market.last_close("B")) - mark), abs=0.01)
    assert (start - eq) / start > 0.015  # 2% of equity: past Balanced's 1.5% daily stop
    assert a.eng.day.halted and a.eng.entry_block() == "daily stop" and not a.eng._may_enter(10)
    h = [h for h in a.store.tables["halt"] if h["kind"] == "daily_stop"][-1]
    assert "multi-day positions keep their stops: B" in h["reason"]
    a.ticks(a.open2, 3, 30)
    assert a.broker.pos["B"]["qty"] == QTY and a.stop_b() and not a.sells("B")


def test_flatten_and_stop_still_sells_everything(tmp_path):
    a = Account(tmp_path)
    a.day1()
    a.ticks(a.open2, 0, 10)
    a.store.insert("command", {"kind": "flatten_and_stop", "payload": {}, "idempotency_key": "k9",
                               "applied_at": None})
    a.eng.apply_commands()
    assert a.store.tables["run"][0]["status"] == "stopped"
    a.ticks(a.open2, 10, 13)
    assert "B" not in a.broker.pos and not a.stop_b()
    b = a.eng.ledger.book(a.rid)
    assert (MD, "B") not in b.sleeves and any(e["type"] == "multi_day_sold" for e in a.store.tables["event"])
    assert not a.eng._md_sleeves()


def test_a_new_run_waits_until_the_multi_day_sleeve_is_sold(tmp_path):
    a = Account(tmp_path)
    a.day1()
    a.ticks(a.open2, 0, 10)
    a.store.insert("command", {"kind": "new_run", "payload": {"amount_usd": 8000, "level": "balanced"},
                               "idempotency_key": "k8", "applied_at": None})
    a.eng.apply_commands()
    assert a.eng.run["id"] == a.rid and a.sells("B")  # the old run's sleeve is being sold; the command waits
    a.ticks(a.open2, 10, 12)
    a.eng.apply_commands()
    assert a.eng.run["id"] != a.rid and "B" not in a.broker.pos


def test_the_run_drawdown_end_sells_the_multi_day_sleeve_too(tmp_path):
    a = Account(tmp_path, gap_b=0.95)
    a.day1()
    pol = a.eng.policies["balanced"]
    a.eng.policies = {**a.eng.policies, "balanced": type(pol)(**{**pol.__dict__, "run_drawdown_end_pct": 1.0})}
    a.ticks(a.open2, 0, 4)
    assert any(h["kind"] == "run_drawdown" for h in a.store.tables["halt"])
    assert "B" not in a.broker.pos


def test_the_account_guard_still_blocks_a_foreign_position_next_to_the_sleeve(tmp_path):
    a = Account(tmp_path)
    a.day1()
    a.broker.foreign = {"ZZZ": {"qty": "10"}}
    a.ticks(a.open2, 0, 3)
    assert not a.eng.account_clean and a.eng.entry_block() == "account not clean"


# ======================================================================= the flag


def test_multi_day_rules_are_refused_live_unless_the_flag_is_on(monkeypatch):
    monkeypatch.delenv("KAIBOT_MULTIDAY", raising=False)
    assert not registry.multi_day_enabled()
    with pytest.raises(registry.RefusedStrategy, match="KAIBOT_MULTIDAY=on"):
        Engine(None, None, None, {}, set(), strategies={"balanced": ["dip_reversal", MD]})
    for off in ("off", "1", "true", "yes", ""):
        monkeypatch.setenv("KAIBOT_MULTIDAY", off)
        with pytest.raises(registry.RefusedStrategy):
            registry.live(["dip_reversal", MD])
    monkeypatch.setenv("KAIBOT_MULTIDAY", "on")
    Engine(None, None, None, {}, set(), strategies={"balanced": ["dip_reversal", MD]})
    assert [s.name for s in registry.live(["dip_reversal", MD])] == ["dip_reversal", MD]
    assert [s.name for s in registry.live(["dip_reversal"])] == ["dip_reversal"]  # only the rules a level names


# ======================================================================= drill 1: the engine killed at 15:50


def _killed_at_1550(tmp_path, gap_b=1.0):
    a = Account(tmp_path, gap_b=gap_b)
    a.day1()
    fake_candidate(a.eng, "A", at=(54,))
    a.ticks(a.open2, 0, 379)
    # a multi-day purchase for the closing auction, sent before 15:50 (Alpaca's cut-off for cls orders). #206: next to
    # B's resting sell stop Alpaca would refuse it (wash trade) and the daily path no longer sends it; the drill keeps
    # a resting entry to show the fail-safes cancel one, so the venue's check is off for this one order
    a.broker.wash_check = False
    a.broker.submit(OrderRequest("B", "buy", 5, "market", "cls", f"kb{a.rid}-{MD}-B-20260804-target-1"))
    a.broker.wash_check = True
    a.ticks(a.open2, 379, 380)  # 15:50: the engine dies here
    assert "A" in a.broker.pos and a.broker.pos["B"]["qty"] == QTY
    beat(a.store, a.clock())
    return a


def test_drill_engine_killed_at_1550_watchdog(tmp_path):
    a = _killed_at_1550(tmp_path)
    wd = Watchdog(a.broker, a.store, now=a.clock, book=book_reader(a.jpath, a.store))
    outs = []
    t = a.open2 + timedelta(minutes=382)  # 15:52: the heartbeat is 2 minutes old
    while t < a.open2 + timedelta(minutes=390):
        a.clock.t = t
        outs.append(wd.step())
        t += timedelta(seconds=POLL_S)
        if t.second < POLL_S:
            a.broker.advance(pd.Timestamp(t.replace(second=0)))
    assert outs[0]["canceled"] == ["B"] and outs[0]["stops"] == []  # the entry goes; B's stop is resting
    assert any(h["kind"] == "watchdog" and "multi-day positions are kept" in h["reason"]
               for h in a.store.tables["halt"])
    assert sum(o["closed"] == ["A"] for o in outs) == 1 and all("B" not in o["closed"] for o in outs)
    assert "A" not in a.broker.pos and a.broker.pos["B"]["qty"] == QTY
    assert [o["client_order_id"] for o in a.stop_b()] == [a.stop_coid] and not a.sells("B")
    # the engine starts again: the sleeve is known (not foreign); A, sold by the watchdog behind the engine's back,
    # is the known start-up difference that blocks entries until it is reconciled (S78)
    eng = a.restart()
    assert (MD, "B") in eng._md_sleeves()
    for e in a.store.tables["event"]:
        if e["type"] == "account_not_clean":
            assert "B" not in e["payload"]["foreign_positions"] + e["payload"]["unknown_open_orders"]


def test_drill_engine_killed_at_1550_watchdog_recreates_a_missing_stop_in_whole_shares(tmp_path):
    a = _killed_at_1550(tmp_path)
    a.broker.cancel(a.stop_b()[0]["id"])  # the stop is gone (expired, cancelled by hand)
    a.broker.pos["B"]["qty"] = QTY + 0.6  # and the broker holds a fraction more than the sleeve
    a.clock.t = a.open2 + timedelta(minutes=382)
    wd = Watchdog(a.broker, a.store, now=a.clock, book=book_reader(a.jpath, a.store))
    out = wd.step()
    assert [(s["symbol"], s["qty"], s["stop"], s["why"]) for s in out["stops"]] == [
        ("B", QTY, round(a.b_px * 0.9, 2), "known stop")]
    assert [o["qty"] for o in a.stop_b()] == [QTY]
    a.clock.t += timedelta(seconds=POLL_S * 5)
    assert wd.step()["stops"] == []  # resting now: never sent twice
    a.clock.t = a.open2 + timedelta(minutes=385, seconds=10)
    out = wd.step()
    assert out["closed"] == ["A"] and out["kept"] == {"B": QTY}  # 0.6 of a share is not sold either
    assert not a.sells("B")


def _mirror_ledger(db: DeadmanDb, a: Account) -> None:
    """Supabase's copy of the ledger rows the engine wrote at the last close (the replica)."""
    db.run(a.rid)
    db.ledger(a.rid, DAY1, [(r["strategy"], r["symbol"], r["qty"], r["stop_px"])
                            for r in a.store.tables.get("ledger_position", []) if str(r["session"]) == DAY1])


def test_drill_engine_killed_at_1550_dead_man(tmp_path):
    """The laptop died with the engine: no watchdog. The dead-man in Supabase acts alone."""
    a = _killed_at_1550(tmp_path)
    a.broker.cancel(a.stop_b()[0]["id"])  # and B's stop is missing
    db = DeadmanDb(tmp_path)
    try:
        db.beat(a.clock())
        _mirror_ledger(db, a)
        said = []
        for k in range(381, 390):  # 15:51 ... 15:59, once a minute (pg_cron)
            a.clock.t = a.open2 + timedelta(minutes=k, seconds=2)
            said.append(db.check(a.clock()))
            db.pump(a.broker)
            a.broker.advance(pd.Timestamp(a.open2 + timedelta(minutes=k + 1)))
        assert said[0] == "engine alive" and said[-1].startswith("acted")
        assert "A" not in a.broker.pos and a.broker.pos["B"]["qty"] == QTY  # intraday sold, multi-day kept
        stops = a.stop_b()
        assert len(stops) == 1 and stops[0]["qty"] == QTY and stops[0]["client_order_id"].startswith("kbdm-")
        assert float(stops[0]["stop_price"]) == pytest.approx(round(a.b_px * 0.9, 2))
        assert not [o for o in a.broker.open_orders() if o["side"] == "buy"]  # the auction buy was cancelled
        assert not a.sells("B")
        assert db.one("select count(*) from kaibot.halt where kind = 'deadman' and reason like 'engine silent%'") == "1"
    finally:
        db.stop()


def _re_price_b(a) -> dict:
    """Issue #197: the engine re-priced B's stop before it died (Alpaca: a replace gives the order a new client id
    unless one is sent; the order manager sends the next attempt of its own)."""
    om = OrderManager(a.broker, a.eng.journal, a.clock, run_id=a.rid)
    om.replace(a.stop_coid, stop=round(a.b_px * 0.91, 2))
    (s,) = a.stop_b()
    assert s["client_order_id"] == a.stop_coid[:-1] + "2" and failsafe.parse_coid(s["client_order_id"])[0] == MD
    return s


def test_drill_a_re_priced_stop_keeps_the_sleeve_with_the_watchdog_and_the_book_unknown(tmp_path):
    a = _killed_at_1550(tmp_path)
    s = _re_price_b(a)
    down = DownStore(a.store)  # Supabase down and no journal: the stop's client id is all the watchdog has
    wd = Watchdog(a.broker, down, now=a.clock, book=book_reader(tmp_path / "nowhere.sqlite", down))
    a.clock.t = a.open2 + timedelta(minutes=382)
    out = wd.step()
    assert out["stops"] == [] and not any("B" in x for x in wd._alarms if "without a protective stop" in x)
    a.clock.t = a.open2 + timedelta(minutes=385, seconds=10)
    out = wd.step()
    assert out["closed"] == ["A"] and out["kept"] == {"B": QTY}
    assert a.broker.pos["B"]["qty"] == QTY and [o["id"] for o in a.stop_b()] == [s["id"]] and not a.sells("B")


def test_drill_a_re_priced_stop_keeps_the_sleeve_with_the_dead_man_and_no_ledger_rows(tmp_path):
    a = _killed_at_1550(tmp_path)
    s = _re_price_b(a)
    db = DeadmanDb(tmp_path)
    try:
        db.beat(a.clock())
        db.run(a.rid)  # the run, but no ledger rows: the book is unknown to the dead-man
        for k in range(381, 390):
            a.clock.t = a.open2 + timedelta(minutes=k, seconds=2)
            db.check(a.clock())
            db.pump(a.broker)
            a.broker.advance(pd.Timestamp(a.open2 + timedelta(minutes=k + 1)))
        r = db.last_result()
        assert r["multi_day"] == {"B": QTY} and r["stops"] == []
        assert "A" not in a.broker.pos and a.broker.pos["B"]["qty"] == QTY
        assert [o["id"] for o in a.stop_b()] == [s["id"]] and not a.sells("B")
    finally:
        db.stop()


# ======================================================================= drill 2: the network cut overnight


def test_drill_network_cut_overnight(tmp_path):
    a = Account(tmp_path, gap_b=0.95)
    a.day1()
    a.broker.down = True  # the Mac loses its connection after the close
    a.clock.t = a.open1 + timedelta(hours=10)
    with pytest.raises(BrokerError, match="network down"):
        a.eng.tick()
    wd = Watchdog(a.broker, a.store, now=a.clock, book=book_reader(a.jpath, a.store))
    db = DeadmanDb(tmp_path)
    try:
        db.beat(a.open1 + timedelta(hours=6, minutes=30))  # the last heartbeat that reached Supabase: 16:00 DAY1
        _mirror_ledger(db, a)
        errors = a.ticks(a.open2, 0, 10, catch=True)  # 09:30-09:40: the engine and the watchdog cannot reach Alpaca
        assert len(errors) == 10 and a.eng.kill == "engine_errors"
        with pytest.raises(BrokerError, match="network down"):
            wd.step()
        for k in (5, 6):  # the dead-man (Supabase has its own network): read, then act
            db.check(a.open2 + timedelta(minutes=k, seconds=2))
            db.pump(a.broker)
        r = db.last_result()
        assert r["canceled"] == [] and r["stops"] == [] and r["multi_day"] == {"B": QTY} and not r["flatten"]
        assert a.broker.pos["B"]["qty"] == QTY and a.stop_b() and not a.sells("B")
        a.broker.down = False  # 09:41: the connection is back
        a.ticks(a.open2, 10, 20)
        assert a.eng.day.halted  # the overnight gap took the day past the daily stop: entries blocked
        assert a.broker.pos["B"]["qty"] == QTY and a.stop_b() and not a.sells("B")  # stop kept, nothing sold
    finally:
        db.stop()


# ======================================================================= drill 3: Supabase down at the open


class DownStore(FakeStore):
    def __init__(self, base: FakeStore):
        self.__dict__.update(base.__dict__)

    def select(self, table, params):
        raise RuntimeError("supabase: 503")

    def insert(self, table, row, returning=True):
        raise RuntimeError("supabase: 503")

    def heartbeat(self, process, detail):
        raise RuntimeError("supabase: 503")

    def event(self, kind, detail, run_id=None):
        raise RuntimeError("supabase: 503")


def test_drill_supabase_down_at_the_open(tmp_path):
    a = Account(tmp_path)
    a.day1()
    down = DownStore(a.store)
    a.clock.t = a.open2 - timedelta(minutes=20)
    a.broker.submit(OrderRequest("B", "buy", 5, "market", "cls", f"kb{a.rid}-{MD}-B-20260804-target-1"))
    a.broker.cancel(a.stop_b()[0]["id"])  # and the stop is gone
    wd = Watchdog(a.broker, down, now=a.clock, book=book_reader(a.jpath, down))
    a.clock.t = a.open2 + timedelta(minutes=1)
    out = wd.step()  # the heartbeat cannot be read: stale; the book comes from the local journal
    assert out["state"] == "engine_stale" and out["canceled"] == ["B"]
    assert [(s["symbol"], s["qty"], s["stop"]) for s in out["stops"]] == [("B", QTY, round(a.b_px * 0.9, 2))]
    a.clock.t = a.open2 + timedelta(minutes=385, seconds=10)
    out = wd.step()
    assert out["closed"] == [] and out["kept"] == {"B": QTY}
    assert a.broker.pos["B"]["qty"] == QTY and len(a.stop_b()) == 1 and not a.sells("B")


def test_drill_supabase_down_and_no_journal_keeps_a_position_its_multi_day_stop_explains(tmp_path):
    a = Account(tmp_path)
    a.day1()
    down = DownStore(a.store)
    wd = Watchdog(a.broker, down, now=a.clock, book=book_reader(tmp_path / "nowhere.sqlite", down))
    a.clock.t = a.open2 + timedelta(minutes=385, seconds=10)
    out = wd.step()
    assert out["closed"] == [] and out["kept"] == {"B": QTY} and a.broker.pos["B"]["qty"] == QTY


def test_without_a_book_or_a_multi_day_stop_a_position_counts_as_intraday(tmp_path):
    """Fail closed (docs/ops/failsafe.md): a position nobody can attribute and nothing protects is not carried."""
    a = Account(tmp_path)
    a.day1()
    a.broker.cancel(a.stop_b()[0]["id"])
    down = DownStore(a.store)
    wd = Watchdog(a.broker, down, now=a.clock, book=book_reader(tmp_path / "nowhere.sqlite", down))
    a.clock.t = a.open2 + timedelta(minutes=385, seconds=10)
    assert wd.step()["closed"] == ["B"]


# ======================================================================= the plan: Python and SQL agree


def _o(oid, sym, side, typ, qty, coid, status="new", **kw):
    return {"id": oid, "symbol": sym, "side": side, "type": typ, "qty": str(qty), "filled_qty": "0",
            "client_order_id": coid, "status": status, "filled_at": None, "legs": [], **kw}


R = 7
STOP_B = _o("s1", "B", "sell", "stop", 40, f"kb{R}-{MD}-B-20260803-stop-1", stop_price="90.00")
BRACKET_A = {**_o("e1", "A", "buy", "market", 10, f"kb-{R}-55-1", status="filled", filled_qty="10",
                  filled_at="2026-08-04T14:25:00+00:00"),
             "legs": [{"id": "e1s", "side": "sell", "type": "stop", "qty": "10", "status": "new",
                       "stop_price": "99.00"},
                      {"id": "e1t", "side": "sell", "type": "limit", "qty": "10", "status": "new",
                       "limit_price": "102.00"}]}
CASES = {
    "intraday only": ({"A": ("10", "100")}, [BRACKET_A], [], True),
    "multi-day with its stop, intraday next to it": (
        {"A": ("10", "100"), "B": ("40", "100")}, [BRACKET_A, STOP_B], [(MD, "B", 40, 90.0)], True),
    "a missing stop at the known price": ({"B": ("40", "100")}, [], [(MD, "B", 40, 90.0)], False),
    "a fraction, the stop above the market": ({"B": ("40.6", "80")}, [], [(MD, "B", 40.6, 90.0)], False),
    "no stop known": ({"B": ("40", "100")}, [], [(MD, "B", 40, None)], False),
    "one stock held both ways": ({"B": ("50", "100")}, [STOP_B], [(MD, "B", 40, 90.0)], True),
    "no book: the multi-day stop explains it": ({"B": ("40", "100")}, [STOP_B], [], True),
    "entries of both kinds, and a foreign buy": (
        {}, [_o("b1", "A", "buy", "market", 5, f"kb-{R}-56-1"), _o("b2", "C", "buy", "market", 5,
                                                                   f"kb{R}-{MD}-C-20260804-target-1"),
             _o("b3", "D", "buy", "limit", 5, "manual-1")], [], False),
    # issue #197: a stop the engine re-priced keeps a client id of its family (the next attempt, or `.r<n>`)
    "a re-priced engine stop, no book": (
        {"B": ("40", "100")}, [_o("s2", "B", "sell", "stop", 40, f"kb{R}-{MD}-B-20260803-stop-2", stop_price="91.00")],
        [], True),
    "a re-priced fail-safe stop next to intraday shares, no book": (
        {"B": ("50", "100")}, [_o("s3", "B", "sell", "stop", 40, f"kbwd-{MD}-B-20260804093107-wdstop.r2",
                                  stop_price="91.00")], [], True),
    "a re-priced dead-man stop with its sleeve": (
        {"B": ("40", "100")}, [_o("s4", "B", "sell", "stop", 40, f"kbdm-{MD}-B-20260804093107-dmstop.r3",
                                  stop_price="91.00")], [(MD, "B", 40, 91.0)], False),
    "an intraday neighbour name is not multi-day": (
        {"B": ("40", "100")}, [_o("s9", "B", "sell", "stop", 40, f"kb{R}-dip_reversal-B-20260803-stop-1")], [], True),
}


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    d = DeadmanDb(tmp_path_factory.mktemp("dm"))
    d.run(R)
    yield d
    d.stop()


def _python(positions, orders, sleeves, flatten):
    pos = {s: {"qty": q, "current_price": px} for s, (q, px) in positions.items()}
    book = failsafe.BookView([failsafe.SleeveView(*s) for s in sleeves], "store", R) if sleeves else None
    p = failsafe.plan(pos, orders, book, flatten=flatten)
    return {"cancel": sorted(o["symbol"] for o in p.cancel),
            "stops": sorted((s.strategy, s.symbol, s.qty, s.stop) for s in p.stops),
            "close": sorted(p.close), "sell": sorted((s, q) for s, q, _ in p.sell),
            "multi_day": {k: round(v, 4) for k, v in p.multi_day.items()}}


def _sql(db, positions, orders, sleeves, flatten, n):
    db.sql("delete from kaibot.ledger_position; delete from kaibot.deadman_pass; delete from net.calls; "
           "delete from net._http_response; delete from kaibot.heartbeat")
    if sleeves:
        db.ledger(R, DAY1, sleeves)
    t = pd.Timestamp(f"{DAY2} 10:00", tz=ET).to_pydatetime() + timedelta(hours=n)  # a fresh time per case
    db.check(t, force=True, flatten=flatten)  # the read
    import json

    pos = [{"symbol": s, "qty": q, "current_price": px} for s, (q, px) in positions.items()]
    ids = db.sql("select req_positions, req_orders from kaibot.deadman_pass order by id desc limit 1")[0].split("|")
    for i, body in zip(ids, (pos, orders), strict=True):
        db.sql(f"insert into net._http_response (id, status_code, content) values ({i}, 200, "
               f"'{json.dumps(body)}')")
    db.check(t + timedelta(minutes=1), force=True, flatten=flatten)  # the act
    r = db.last_result()
    return {"cancel": sorted(r["canceled"]),
            "stops": sorted((s["strategy"], s["symbol"], int(s["qty"]), float(s["stop"])) for s in r["stops"]),
            "close": sorted(r["closed"]), "sell": sorted((s["symbol"], int(s["qty"])) for s in r["sold"]),
            "multi_day": {k: round(float(v), 4) for k, v in r["multi_day"].items()}}


@pytest.mark.parametrize("case", list(CASES))
def test_the_dead_man_sql_and_the_watchdog_follow_one_plan(db, case):
    positions, orders, sleeves, flatten = CASES[case]
    n = list(CASES).index(case)
    py, sq = _python(positions, orders, sleeves, flatten), _sql(db, positions, orders, sleeves, flatten, n)
    assert py == sq, case


def test_the_plan_cases_say_what_decision_15_says():
    got = {c: _python(*CASES[c]) for c in CASES}
    assert got["intraday only"]["close"] == ["A"] and got["intraday only"]["multi_day"] == {}
    both = got["multi-day with its stop, intraday next to it"]
    assert both["close"] == ["A"] and both["multi_day"] == {"B": 40} and both["stops"] == []
    assert got["a missing stop at the known price"]["stops"] == [(MD, "B", 40, 90.0)]
    assert got["a fraction, the stop above the market"]["stops"] == [(MD, "B", 40, 72.0)]  # 10% below 80
    assert got["no stop known"]["stops"] == [(MD, "B", 40, 90.0)]
    assert got["one stock held both ways"]["sell"] == [("B", 10)] and got["one stock held both ways"]["close"] == []
    assert got["no book: the multi-day stop explains it"]["close"] == []
    assert got["entries of both kinds, and a foreign buy"]["cancel"] == ["A", "C"]
    assert got["an intraday neighbour name is not multi-day"]["close"] == ["B"]
    for c in ("a re-priced engine stop, no book", "a re-priced fail-safe stop next to intraday shares, no book",
              "a re-priced dead-man stop with its sleeve"):  # #197: still multi-day, never sold, no stop added
        assert got[c]["multi_day"] == {"B": 40} and got[c]["close"] == [] and got[c]["stops"] == [], c
    assert got["a re-priced fail-safe stop next to intraday shares, no book"]["sell"] == [("B", 10)]


# ======================================================================= the dead-man's schedule and guards


def test_the_dead_man_is_quiet_when_it_should_be(db):
    ny = lambda h, m, day=DAY2: pd.Timestamp(f"{day} {h:02d}:{m:02d}", tz=ET).to_pydatetime()  # noqa: E731
    db.sql("delete from kaibot.heartbeat; delete from kaibot.deadman_pass; delete from net.calls")
    assert db.check(ny(9, 0)) == "market closed" and db.check(ny(16, 0)) == "market closed"
    assert db.check(ny(12, 0, "2026-08-08")) == "weekend"
    assert db.check(ny(12, 0, "2026-11-26")) == "market holiday"
    assert db.check(ny(13, 30, "2026-11-27")) == "market closed"  # early close at 13:00
    db.beat(ny(11, 59))
    assert db.check(ny(12, 0)) == "engine alive"
    assert db.one("select count(*) from net.calls") == "0"
    db.sql("delete from kaibot.heartbeat")
    assert db.check(ny(12, 0)) == "reading the account"
    assert db.check(ny(12, 1)) == "waiting for the broker's answers"
    assert db.check(ny(12, 4)) == "nothing due"  # expired after 3 minutes; the next read is 5 minutes after
    assert db.check(ny(12, 6)) == "reading the account"


def test_the_dead_man_flattens_an_intraday_only_book_with_one_call_as_0006_did(db):
    db.sql("delete from kaibot.ledger_position; delete from kaibot.deadman_pass; delete from net.calls; "
           "delete from net._http_response; delete from kaibot.heartbeat")
    t = pd.Timestamp(f"{DAY2} 15:56", tz=ET).to_pydatetime()
    db.check(t)
    import json

    ids = db.sql("select req_positions, req_orders from kaibot.deadman_pass order by id desc limit 1")[0].split("|")
    for i, body in zip(ids, ([{"symbol": "A", "qty": "10", "current_price": "100"}], [BRACKET_A]), strict=True):
        db.sql(f"insert into net._http_response (id, status_code, content) values ({i}, 200, '{json.dumps(body)}')")
    db.check(t + timedelta(minutes=1))
    calls = db.sql("select method || ' ' || url || ' ' || coalesce(params::text, '') from net.calls "
                   "where method <> 'GET' order by id")
    assert calls == ['DELETE https://paper-api.alpaca.markets/v2/positions {"cancel_orders": "true"}']


def test_migration_0019_lists_every_multi_day_rule_and_replaces_the_old_jobs():
    sql = (ROOT / "migrations" / "0019_deadman_overnight.sql").read_text()
    seeded = set(re.findall(r"^\s+\('([a-z0-9_]+)', 'multi_day'\)", sql, re.M))
    assert seeded == {s.name for s in registry.MULTI_DAY}
    assert "NOT APPLIED" in sql and "paper-api.alpaca.markets" in sql and "api.alpaca.markets'" not in sql.replace(
        "paper-api.alpaca.markets", "")
    for job in ("kaibot_deadman_edt", "kaibot_deadman_est", "kaibot_deadman_early_edt", "kaibot_deadman_early_est"):
        assert job in sql
    assert np.isclose(failsafe.EMERGENCY_STOP_PCT, 10.0) and "last * 0.90" in sql  # one number, both places


def test_the_dead_man_schedule_replaces_the_four_old_jobs(db):
    assert db.sql("select jobname || ' ' || schedule from cron.job order by 1") == ["kaibot_deadman * 13-21 * * 1-5"]
    assert db.one("select pg_get_functiondef('kaibot.deadman_flatten(boolean)'::regprocedure)").count("") > 0
    assert "deadman_check(force)" in db.one(
        "select prosrc from pg_proc where proname = 'deadman_flatten'")  # 0006's name, 0019's plan


def test_positions_and_orders_json_shapes():
    """What the harness hands the dead-man is Alpaca's shape (strings for numbers, legs nested)."""
    a_pos = positions_json(type("B", (), {"positions": lambda self: {"A": {"qty": 3, "avg_entry_price": 9.5}}})())
    assert a_pos == [{"symbol": "A", "qty": "3", "current_price": "9.5"}]
    assert orders_json(type("B", (), {"open_orders": lambda self: [], "orders": {}})()) == []


def test_the_dead_man_never_acts_on_an_old_account_read(db):
    """Backend-quant review of #180: a pass read at 11:00 while the engine was briefly stale must not be acted on
    when the engine goes silent again at 15:56. Only a read from the last 3 minutes is acted on; older ones expire."""
    import json

    ny = lambda h, m, s=0: pd.Timestamp(f"{DAY2} {h:02d}:{m:02d}:{s:02d}", tz=ET).to_pydatetime()  # noqa: E731
    db.sql("delete from kaibot.ledger_position; delete from kaibot.deadman_pass; delete from net.calls; "
           "delete from net._http_response; delete from kaibot.heartbeat")
    assert db.check(ny(11, 0)) == "reading the account"  # the engine is briefly stale
    ids = db.sql("select req_positions, req_orders from kaibot.deadman_pass order by id desc limit 1")[0].split("|")
    for i, body in zip(ids, ([{"symbol": "A", "qty": "10", "current_price": "100"}], [BRACKET_A]), strict=True):
        db.sql(f"insert into net._http_response (id, status_code, content) values ({i}, 200, '{json.dumps(body)}')")
    db.beat(ny(11, 0, 30))  # it beats again
    assert db.check(ny(11, 1)) == "engine alive"
    assert db.one("select count(*) from kaibot.deadman_pass where state = 'reading'") == "0"  # expired on alive
    # even a pass left 'reading' (the alive path skipped) is too old to act on hours later
    db.sql("update kaibot.deadman_pass set state = 'reading'")
    db.sql("delete from kaibot.heartbeat")  # the engine goes silent again
    said = db.check(ny(15, 56))
    assert not said.startswith("acted") and said.endswith("reading the account")
    assert db.one("select state from kaibot.deadman_pass order by id limit 1") == "expired"
    assert db.one("select count(*) from net.calls where method <> 'GET'") == "0"  # nothing sent on the old view


def test_a_partly_filled_entry_is_cancelled_too():
    """The rest of a partly filled entry is cancelled (it only takes risk off); what filled keeps its legs."""
    o = {"side": "buy", "client_order_id": "kb-1-2-1", "status": "partially_filled", "filled_at": None,
         "filled_qty": "3", "qty": "10"}
    assert failsafe.is_entry(o)
    p = failsafe.plan({}, [{**o, "id": "x", "symbol": "A"}], None, flatten=False)
    assert [c["symbol"] for c in p.cancel] == ["A"]
