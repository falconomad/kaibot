"""Issue #206: the live daily path (engine/live_daily.py) runs the research runner's decision core.

Parity: on the committed fixture (tests/fixtures/daily_momentum) the live path, driven session by session on
`SimBroker` exactly as the engine drives it (decide after the close from the view of that evening, journal; next
session 09:31: size, journal, send `cls` orders through the order manager; the closing print; book the fills from
the account activities), sends the SAME orders and gets the SAME fills as `engine/daily.run_daily` on the same
sessions, at the pre-registered limits and at the live book's limits of #205. The window (sessions 280-399) has no
corporate action on a stock (live, splits and dividends are the engine's S77 path, booked from Alpaca's activities).

Restart safety: the same run with the process killed and rebuilt from the journal between the decision and the send,
in the middle of a send (before and after the venue got the order), between the send and the fill, and between the
fill and its booking, ends with the same orders at the venue, each once, and the same fills, each booked once.
"""

from __future__ import annotations

import dataclasses
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

import numpy as np
import pytest

from kaibot.broker.sim import NY, AuctionImpactCosts, SimBroker
from kaibot.engine import daily_report as dr
from kaibot.engine import live_daily as ld
from kaibot.engine.clock import SimClock
from kaibot.engine.daily import DailyData, momentum_limits, run_daily
from kaibot.engine.journal import Journal
from kaibot.engine.ledger import LEDGER_KINDS, Ledger, price_key
from kaibot.engine.reconcile import Reconciler
from kaibot.risk.profiles import load_profiles, rule_book_limits
from kaibot.strategies import momentum as mom

POL = load_profiles()["balanced"]
I0, I1 = 280, 399
EQ0 = 100_000.0
RID = 1


@pytest.fixture(scope="module")
def data() -> DailyData:
    m, _ = dr.load_fixture(dr.FIXTURE)
    return DailyData.from_market(m)


def _at(d, t: time) -> datetime:
    return datetime.combine(d, t, NY).astimezone(UTC)


class Crash(BaseException):
    """The process dies (not an exception the engine's code catches)."""


class SimAccount:
    """`live_daily.Account` over a SimBroker account and a ledger folded from the journal (what the engine has)."""

    def __init__(self, journal: Journal, broker: SimBroker) -> None:
        self.journal, self.broker = journal, broker
        self.ledger = Ledger.fold(journal.entries(kinds=LEDGER_KINDS))
        self.recon = Reconciler(lambda: {"ledger_qty": self.book.qty_by_symbol(),
                                         "broker_positions": broker.positions(), "ledger_cash": self.book.cash,
                                         "ledger_cash0": EQ0, "broker_cash": broker.cash, "broker_cash0": EQ0,
                                         "equity": EQ0}, journal)

    @property
    def book(self):
        return self.ledger.book(RID)

    def run_id(self):
        return RID

    def equity(self):
        b = self.book
        return b.closes[-1].close_equity_venue if b.closes else b.amount

    def cash(self):
        return self.book.cash

    def held(self, strategy):
        return {s: (sl.qty, sl.mark) for (st, s), sl in self.book.sleeves.items() if st == strategy and sl.qty > 0}

    def book_fill(self, body):
        self.ledger.apply("fill", body)

    def entry_block(self):
        return None

    def allows(self, symbol, side, purpose="target", qty=None):
        return self.recon.allows(symbol, side, purpose, qty)

    def splits(self, day):
        return {}

    def book_ended(self):
        return False


class Harness:
    """The engine's calls to the daily path, session by session, on SimBroker. `restart(where)` kills the process
    at a point and rebuilds the path, the order manager and the ledger from the journal."""

    def __init__(self, data: DailyData, strategy, builder, tmp_path, crash: dict | None = None) -> None:
        self.d, self.strategy, self.builder = data, strategy, builder
        self.clock = SimClock(_at(data.day(I0 - 1), time(16, 30)))
        costs = AuctionImpactCosts(sigma=lambda sym, day: data.sigma_at(data.index_of(day), data.col(sym)), mult=1.0)
        self.broker = SimBroker(data.bar_history(), self.clock, costs=costs, cash=EQ0, calendar=data.calendar(),
                                cash_at="fill")
        self.path = tmp_path / "journal.sqlite"
        self.journal = Journal(self.path, now=self.clock)
        self.journal.append("run", {"run_id": RID, "amount": EQ0})
        self.crash = dict(crash or {})
        self.restarts = 0
        self.events: list[tuple[str, dict]] = []
        self.build()

    def build(self) -> None:
        self.account = SimAccount(self.journal, self.broker)
        d = self.d
        self.live = ld.LiveDaily(
            self.broker, self.journal, self.clock, [ld.Rule(self.strategy, self.builder)],
            lambda k, now: ld.as_of(d, d.index_of(k)), self.account,
            next_session=lambda k: d.day(d.index_of(k) + 1), prev_session=lambda k: d.day(d.index_of(k) - 1),
            event=lambda kind, body: self.events.append((kind, body)))
        self.live.startup()
        self.account.recon.check("startup")  # the engine's start-up reconciliation (Engine.startup)

    def restart(self) -> None:
        self.journal.close()
        self.journal = Journal(self.path, now=self.clock)
        self.restarts += 1
        self.build()

    def maybe(self, where: str, i: int) -> None:
        if i in self.crash.get(where, ()):
            self.restart()

    def run(self) -> None:
        d = self.d
        self.live.after_close(d.day(I0 - 1))
        for i in range(I0, I1 + 1):
            day = d.day(i)
            self.maybe("after_decision", i)
            self.account.recon.check("session_start")
            for minute in (31, 32, 33, 36, 40, 45, 50, 59):  # the engine's ticks (each step safe to repeat)
                self.clock.set(_at(day, time(9, minute)))
                self.live.sync()
                try:
                    self.live.send(day)
                except Crash:
                    self.restart()
            self.maybe("after_send", i)
            self.broker.advance(_at(day, time(9, 30)))
            self.clock.set(_at(day, time(15, 59)))
            self.broker.advance(_at(day, time(15, 59)))
            self.maybe("before_booking", i)
            self.clock.set(_at(day, time(16, 30)))
            self.live.sync()
            self.live.sync(force=True)  # a second read books nothing again
            self.account.ledger.apply("mark", self.mark(i))
            self.journal.append("mark", self.mark(i))
            self.live.after_close(day)
            self.live.after_close(day)  # the next tick: decided once

    def mark(self, i: int) -> dict:
        d, name = self.d, self.strategy.name
        prices = {price_key(name, s): float(d.raw_c[i, d.col(s)]) for s in self.account.held(name)
                  if np.isfinite(d.raw_c[i, d.col(s)])}
        return {"run_id": RID, "session": str(d.day(i)), "prices": prices, "pnl_venue": 0.0, "pnl_shadow": 0.0}


def venue_orders(broker: SimBroker) -> list[tuple]:
    return sorted((str(datetime.fromisoformat(o["submitted_at"]).astimezone(NY).date()), o["symbol"], o["side"],
                   float(o["qty"]), o["time_in_force"]) for o in broker.orders.values())


def venue_fills(broker: SimBroker) -> list[tuple]:
    return sorted((str(datetime.fromisoformat(f["transaction_time"]).astimezone(NY).date()), f["symbol"], f["side"],
                   float(f["qty"]), round(float(f["price"]), 6)) for f in broker.fills)


def reference(data, strategy, builder):
    return run_daily(strategy, data, POL, I0, I1, builder=builder)


def live_book_limits(strategy):
    """The live book of #205 at Balanced with its overnight cap as configured for research (allow_overnight is
    still false in config/profiles.yaml, which makes the live overnight cap 0: see test_overnight_off_buys_nothing)."""
    return ld.live_limits(strategy, POL, rule_book_limits(strategy.name, "balanced", research=True))


# ---------------------------------------------------------------- parity


@pytest.mark.parametrize("limits", ["prereg", "live_book"])
def test_the_live_path_reproduces_the_research_runner_order_by_order_and_fill_by_fill(data, tmp_path, limits):
    s = mom.VERSIONS["trend_momentum"]
    lim = momentum_limits(s, POL, 100.0) if limits == "prereg" else live_book_limits(s)
    if limits == "live_book":
        assert lim.max_gross_pct == 60 and lim.max_names == 30 and lim.max_sector_pct == 40  # #205, Balanced
        # #206: confirmed as pre-registered, so the live book is the tested sizing at the rule's 60% cap
        assert dataclasses.replace(lim, run_drawdown_end_pct=27.0) == momentum_limits(s, POL, 60.0)
    b = ld.live_builder(s, lim)
    ref = reference(data, s, ld.live_builder(s, lim))
    h = Harness(data, s, b, tmp_path)
    h.run()
    want, got = venue_orders(ref.broker), venue_orders(h.broker)
    assert len(want) > 20 and got == want
    assert all(o[4] == "cls" and o[3].is_integer() for o in got)  # closing-auction day orders, whole shares
    assert venue_fills(h.broker) == venue_fills(ref.broker) and len(venue_fills(h.broker)) > 20
    # the ledger the engine folds from the journal holds what the venue holds, cash to the cent
    led = Ledger.fold(h.journal.entries(kinds=LEDGER_KINDS)).book(RID)
    assert {s: q for s, q in led.qty_by_symbol().items() if q} == {s: float(p["qty"]) for s, p in
                                                                    h.broker.positions().items()}
    assert led.cash == pytest.approx(h.broker.cash, abs=0.01)
    # #206: the book's own P&L (what its run end is measured on) is the whole account's P&L here (one rule)
    venue = led.cash + sum(x.qty * x.mark for x in led.sleeves.values())
    assert led.strategy_pnl({s.name}) == pytest.approx(venue - EQ0, abs=0.01)
    assert led.strategy_pnl({"dip_reversal"}) == 0
    # every decision and every send step is in the journal once
    dec = [b for *_, b in h.journal.entries(kinds=("md_decision",))]
    assert len(dec) == I1 - I0 + 2 and len({(b["strategy"], b["session"]) for b in dec}) == len(dec)
    sent = [b for *_, b in h.journal.entries(kinds=("md_sent",))]
    assert len({b["session"] for b in sent}) == len(sent) == I1 - I0 + 1


def test_the_decision_reads_nothing_after_its_close(data):
    """The view the live loader gives for session k equals the full data cut at k, and its decision equals the
    research runner's decision of the same evening."""
    s = mom.VERSIONS["trend_momentum"]
    k = I0 + 7
    while not s.decision_day(data.day(k), data.day(k + 1)):
        k += 1
    cut = ld.as_of(data, k)
    assert len(cut.dates) == k + 1 and cut.dates[-1] == data.dates[k]
    for name in ("atr20", "atr14", "px", "ok", "adv", "sigma"):
        np.testing.assert_array_equal(getattr(cut, name), getattr(data, name)[:k + 1])
    from kaibot.engine.daily import decide_after_close

    a = decide_after_close(s, cut, k, data.day(k + 1), {})
    b = decide_after_close(s, data, k, data.day(k + 1), {})
    assert a is not None and a == b
    again = ld.target_from_json(ld.target_to_json(a))
    assert again == a and list(again.weights) == list(a.weights)  # the journal keeps the priority order


# ---------------------------------------------------------------- restart safety


@pytest.mark.parametrize("where", ["after_decision", "after_send", "before_booking"])
def test_a_restart_does_each_step_exactly_once(data, tmp_path, where):
    s = mom.VERSIONS["trend_momentum"]
    lim = momentum_limits(s, POL, 100.0)
    ref = reference(data, s, ld.live_builder(s, lim))
    trading = sorted({data.index_of(datetime.fromisoformat(o["submitted_at"]).astimezone(NY).date())
                      for o in ref.broker.orders.values()})
    days = trading[1:4]  # three sessions with orders: a restart on each
    h = Harness(data, s, ld.live_builder(s, lim), tmp_path, crash={where: set(days)})
    h.run()
    assert h.restarts == len(days)
    assert venue_orders(h.broker) == venue_orders(ref.broker)  # nothing sent twice, nothing missing
    assert venue_fills(h.broker) == venue_fills(ref.broker)
    fills = [b for *_, b in h.journal.entries(kinds=("fill",))]
    assert len(fills) == len(h.broker.fills) == len({b["activity"] for b in fills})  # each fill booked once


@pytest.mark.parametrize("when", ["before_venue", "after_venue"])
def test_a_crash_in_the_middle_of_a_send_sends_every_order_once(data, tmp_path, when):
    """The process dies inside the target loop: before the venue got the third order (the intent is journalled,
    no order exists: looked up by client id, found lost, sent again under the next attempt's id) or after it (the
    order exists, its answer is lost: adopted by client id, never sent twice)."""
    s = mom.VERSIONS["trend_momentum"]
    lim = momentum_limits(s, POL, 100.0)
    ref = reference(data, s, ld.live_builder(s, lim))
    h = Harness(data, s, ld.live_builder(s, lim), tmp_path)
    real = h.broker.submit
    seen = {"n": 0, "crashed": False}

    def submit(req):
        seen["n"] += 1
        if seen["n"] == 3 and not seen["crashed"]:
            seen["crashed"] = True
            if when == "after_venue":
                real(req)
            raise Crash()
        return real(req)

    h.broker.submit = submit
    h.run()
    assert seen["crashed"] and h.restarts == 1
    assert venue_orders(h.broker) == venue_orders(ref.broker)
    assert venue_fills(h.broker) == venue_fills(ref.broker)
    states = [b for *_, b in h.journal.entries(kinds=("om_state",)) if b.get("reason", "").startswith("lost")]
    assert (len(states) == 1) == (when == "before_venue")


# ---------------------------------------------------------------- the book's limits


def test_overnight_off_buys_nothing_but_still_decides(data, tmp_path):
    """While all_levels.allow_overnight is false the live book's overnight cap is 0: decisions are journalled,
    nothing is bought."""
    s = mom.VERSIONS["trend_momentum"]
    lim = ld.live_limits(s, POL, rule_book_limits(s.name, "balanced"))
    assert lim.max_overnight_pct == 0
    h = Harness(data, s, ld.live_builder(s, lim), tmp_path)
    h.run()
    assert h.broker.orders == {} and h.broker.fills == []
    assert any(b["target"] for *_, b in h.journal.entries(kinds=("md_decision",)))


def test_the_live_limits_only_tighten(data):
    s = mom.VERSIONS["trend_momentum"]
    for level, cap in (("conservative", 30), ("balanced", 60), ("aggressive", 100)):
        pol = load_profiles()[level]
        pre = momentum_limits(s, pol, 100.0)
        book = rule_book_limits(s.name, level, research=True)
        lim = ld.live_limits(s, pol, book)
        for f in ("atr_risk_pct", "max_name_pct", "max_gross_pct", "max_overnight_pct", "max_sector_pct",
                  "max_names", "max_gap_loss_pct"):
            assert getattr(lim, f) <= min(getattr(pre, f), getattr(book, f)) + 1e-12
        assert lim.min_cash_pct >= max(pre.min_cash_pct, book.min_cash_pct)
        assert lim.max_gross_pct == cap
    with pytest.raises(ValueError, match="below the rule's k"):
        ld.live_builder(s, replace(momentum_limits(s, POL, 100.0), atr_risk_pct=0.05))


def test_a_rule_with_a_prereg_stop_is_refused_and_the_momentum_preregs_have_none():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for rule in ("trend_momentum", "mom_12_1_band"):
        text = (root / "docs" / "prereg" / f"{rule}.md").read_text()
        assert "No fixed stop" in text or "No stops exist" in text or "no stop" in text.lower()
        assert ld.PREREG_STOP_ATR[rule] is None
    s = replace(mom.VERSIONS["trend_momentum"], name="trend_momentum")
    ld.PREREG_STOP_ATR["trend_momentum"] = 3.0
    try:
        with pytest.raises(ValueError, match="protective stop"):
            ld.LiveDaily(None, Journal(), lambda: datetime.now(UTC), [ld.Rule(s, None)], None, None,
                         next_session=lambda d: d, prev_session=lambda d: d)
    finally:
        ld.PREREG_STOP_ATR["trend_momentum"] = None


def test_the_send_window_ends_before_alpacas_cls_cutoff():
    assert time(15, 50) >= ld.SEND_UNTIL and time(9, 30) < ld.SEND_FROM


def test_the_store_loader_reads_the_research_loader_up_to_k_once_its_bars_are_available(tmp_path, data):
    """Point in time: the view of session k is read only once k's bars are stamped available (20:15 ET), it ends at
    k, and its decision is the research runner's decision of that evening on the same store."""
    import json
    import shutil

    import pyarrow as pa
    import pyarrow.parquet as pq

    from kaibot.engine.daily import decide_after_close
    from kaibot.ingest.daily_bars import COMPLETE_AT

    shutil.copytree(dr.FIXTURE, tmp_path / "daily")
    meta = json.loads((dr.FIXTURE / "fixture.json").read_text())
    for sym in meta["symbols"]:  # the stamps scripts/fetch_daily.py writes: 20:15 ET of each session
        f = tmp_path / "daily" / "bars" / f"symbol={sym}.parquet"
        t = pq.read_table(f)
        days = t.column("date").to_pylist()
        ts = [datetime.combine(d, COMPLETE_AT, NY).astimezone(UTC) for d in days]
        pq.write_table(t.append_column("available_ts", pa.array(ts, pa.timestamp("s", tz="UTC"))), f)
    uni = tmp_path / "universe.yaml"
    uni.write_text(json.dumps({"sectors": {"XLK": meta["stocks"]}, "market": "SPY"}))
    loader = ld.StoreLoader(tmp_path, uni, mode="today")
    s = mom.VERSIONS["trend_momentum"]
    k = I0 + 7
    while not s.decision_day(data.day(k), data.day(k + 1)):
        k += 1
    day = data.day(k)
    early = datetime.combine(day, time(16, 30), NY).astimezone(UTC)
    assert "not yet" in loader.available(day, early)
    with pytest.raises(ld.DataNotReady):
        loader(day, early)  # at 16:30 the store's bar of k is not available yet: no decision
    late = datetime.combine(day, time(20, 16), NY).astimezone(UTC)
    view = loader(day, late)
    assert view.dates[-1] == data.dates[k] and view.terminal == {}
    tl = decide_after_close(s, view, len(view.dates) - 1, data.day(k + 1), {})
    m, _ = dr.load_store(tmp_path, uni, "today", str(data.dates[0]), str(data.dates[-1]))
    full = DailyData.from_market(m)
    assert tl is not None and tl == decide_after_close(s, full, k, data.day(k + 1), {})
    nxt = data.day(len(data.dates) - 1) + timedelta(days=1)
    assert "no SPY bar" in loader.available(nxt, datetime.now(UTC))


def test_the_store_loader_waits_for_a_refresh_that_stopped_part_way(tmp_path, data):
    """QA-Ops review of #207: SPY has session k but most stocks do not (fetch_daily stopped part-way). The view
    would carry their last close forward, so it is not ready: no decision on yesterday's prices."""
    import json
    import shutil

    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    from kaibot.ingest.daily_bars import COMPLETE_AT

    shutil.copytree(dr.FIXTURE, tmp_path / "daily")
    meta = json.loads((dr.FIXTURE / "fixture.json").read_text())
    k = I0 + 7
    day = data.day(k)
    cut = meta["stocks"][: max(1, len(meta["stocks"]) // 2)]
    for sym in meta["symbols"]:
        f = tmp_path / "daily" / "bars" / f"symbol={sym}.parquet"
        t = pq.read_table(f)
        if sym in cut:  # this stock's refresh never ran: its last bar is k - 1
            t = t.filter(pc.less(t.column("date"), pa.scalar(day, t.schema.field("date").type)))
        ts = [datetime.combine(d, COMPLETE_AT, NY).astimezone(UTC) for d in t.column("date").to_pylist()]
        pq.write_table(t.append_column("available_ts", pa.array(ts, pa.timestamp("s", tz="UTC"))), f)
    uni = tmp_path / "universe.yaml"
    uni.write_text(json.dumps({"sectors": {"XLK": meta["stocks"]}, "market": "SPY"}))
    loader = ld.StoreLoader(tmp_path, uni, mode="today")
    late = datetime.combine(day, time(20, 16), NY).astimezone(UTC)
    assert loader.available(day, late) is None  # SPY alone says "ready"
    with pytest.raises(ld.DataNotReady, match="has not finished"):
        loader(day, late)
