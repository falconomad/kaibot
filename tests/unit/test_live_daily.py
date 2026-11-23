"""Issue #206: the live daily path (engine/live_daily.py) runs the research runner's decision core.

Parity: on the committed fixture (tests/fixtures/daily_momentum) the live path, driven session by session on
`SimBroker` exactly as the engine drives it (decide after the close from the view of that evening, journal; next
session 15:50: size, journal, send near-close DAY market orders through the order manager (issue #333); the closing
print; book the fills from the account activities), sends the SAME orders and gets the SAME fills as
`engine/daily.run_daily` on the same sessions, at the pre-registered limits and at the live book's limits of #205.
The window (sessions 280-399) has no corporate action on a stock (live, splits and dividends are the engine's S77
path, booked from Alpaca's activities).

Restart safety: the same run with the process killed and rebuilt from the journal between the decision and the send,
in the middle of a send (before and after the venue got the order), between the send and the fill, and between the
fill and its booking, ends with the same orders at the venue, each once, and the same fills, each booked once.
"""

from __future__ import annotations

import dataclasses
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

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
from kaibot.engine.targets import Target
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
                                cash_at="fill", near_close=ld.SEND_FROM)
        self.broker.wash_check, self.broker.shorting_enabled = True, False  # as Alpaca, shorting off
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
            self.clock.set(_at(day, time(9, 30)))
            self.broker.advance(_at(day, time(9, 30)))
            for minute in (31, 45):  # the morning's ticks: nothing goes out before 15:50 (issue #333)
                self.clock.set(_at(day, time(9, minute)))
                self.live.sync()
                self.live.send(day)
            for minute in (49, 50, 51, 52, 53, 55, 56, 57, 58):  # the engine's ticks (each step safe to repeat)
                self.clock.set(_at(day, time(15, minute)))
                self.live.sync()
                try:
                    self.live.send(day)
                except Crash:
                    self.restart()
            self.maybe("after_send", i)
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
        assert lim.max_index_pct == 0.0  # #458: the live book holds no index fund
    b = ld.live_builder(s, lim)
    ref = reference(data, s, ld.live_builder(s, lim))
    h = Harness(data, s, b, tmp_path)
    h.run()
    want, got = venue_orders(ref.broker), venue_orders(h.broker)
    assert len(want) > 20 and got == want
    assert all(o[4] == "day" and o[3].is_integer() for o in got)  # near-close day market orders, whole shares
    assert {datetime.fromisoformat(o["submitted_at"]).astimezone(NY).time() for o in h.broker.orders.values()} <= \
        {time(15, m) for m in range(50, 57)}
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
    nothing is bought (the repository allows overnight since 13 Nov 2026; a book with the cap at 0 is built here)."""
    s = mom.VERSIONS["trend_momentum"]
    lim = ld.live_limits(s, POL, replace(rule_book_limits(s.name, "balanced"), max_overnight_pct=0.0))
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


def test_the_send_window_is_the_last_ten_minutes_less_three_and_sends_day_market_orders():
    """Issue #333: near-close DAY market orders from 15:50 to 15:57 ET (three minutes for paper to fill them before
    16:00); the pre-open review still ends at 09:31."""
    window = (ld.SEND_FROM, ld.SEND_UNTIL, ld.PREOPEN_UNTIL)
    assert window == (time(15, 50), time(15, 57), time(9, 31))
    p = ld.LiveDaily(None, Journal(), lambda: datetime.now(UTC), [], None, None, next_session=lambda d: d,
                     prev_session=lambda d: d).policy_for(datetime(2026, 11, 19).date())
    assert p.style == "near_close" and p.close == time(16, 0)


def test_an_expired_remainder_is_one_multi_day_unfilled_event_even_across_a_restart(data, tmp_path):
    """Issue #333, question 4: what the venue let expire at 16:00 (nothing filled, or part of the order) is
    journalled once by client id (`md_unfilled`) and reported as `multi_day_unfilled`, so the dashboard can show
    "not filled". It is not sent again that session (#363: a buy's remainder is carried to the next sessions, see
    test_an_expired_buy_is_carried_to_the_next_sessions_up_to_its_target_until_the_next_decision)."""
    s = mom.VERSIONS["trend_momentum"]
    lim = momentum_limits(s, POL, 100.0)
    h = Harness(data, s, ld.live_builder(s, lim), tmp_path)
    h.broker.script.hold(None, bars=None)  # the first order never fills
    h.broker.script.fills(None, [1.0])  # the second fills one share at the print, the rest expires
    h.run()
    ev = [b for k, b in h.events if k == "multi_day_unfilled"]
    first = sorted((o for o in h.broker.orders.values()), key=lambda o: int(o["id"][1:]))[:2]
    assert [o["status"] for o in first] == ["expired", "expired"] and float(first[1]["filled_qty"]) == 1.0
    assert [(b["symbol"], b["side"], b["qty"], b["filled"], b["left"], b["status"]) for b in ev] == [
        (o["symbol"], o["side"], float(o["qty"]), float(o["filled_qty"]), float(o["qty"]) - float(o["filled_qty"]),
         "expired") for o in first]
    assert all(b["strategy"] == s.name and b["session"] == ev[0]["session"] and b["coid"] for b in ev)
    assert len([b for *_, b in h.journal.entries(kinds=("md_unfilled",))]) == 2
    sent = {(o["symbol"], o["side"]) for o in h.broker.orders.values() if o["submitted_at"][:10] == ev[0]["session"]}
    assert len([o for o in h.broker.orders.values() if (o["symbol"], o["side"]) == (ev[0]["symbol"], "buy")
                and o["submitted_at"][:10] == ev[0]["session"]]) == 1 and sent  # not sent again that session
    h.restart()
    h.live.sync(force=True)
    assert len([k for k, _ in h.events if k == "multi_day_unfilled"]) == 2  # once, also after a restart


def _stamped_store(tmp_path, cut=(), before=None, edit=None):
    """The fixture store under tmp_path/daily as scripts/fetch_daily.py writes it: NYSE sessions only (the synthetic
    fixture also has bars on market holidays, which the integrity check refuses) and each bar stamped available at
    20:15 ET of its session. Stocks in `cut` lose every bar from `before` on; `edit(sym, df)` may change bars.
    Returns (universe file, the store's own DailyData)."""
    import json
    import shutil

    import pyarrow as pa
    import pyarrow.parquet as pq

    from kaibot.ingest import nyse_sessions
    from kaibot.ingest.daily_bars import COMPLETE_AT

    shutil.copytree(dr.FIXTURE, tmp_path / "daily")
    meta = json.loads((dr.FIXTURE / "fixture.json").read_text())
    for sym in meta["symbols"]:
        f = tmp_path / "daily" / "bars" / f"symbol={sym}.parquet"
        t = pq.read_table(f)
        days = t.column("date").to_pylist()
        cal = set(nyse_sessions.sessions(min(days), max(days)))
        keep = [d in cal and not (sym in cut and d >= before) for d in days]
        t = t.filter(pa.array(keep))
        if edit is not None:
            df = edit(sym, t.to_pandas())
            t = pa.Table.from_pandas(df, schema=t.schema, preserve_index=False)
        ts = [datetime.combine(d, COMPLETE_AT, NY).astimezone(UTC) for d in t.column("date").to_pylist()]
        pq.write_table(t.append_column("available_ts", pa.array(ts, pa.timestamp("s", tz="UTC"))), f)
    uni = tmp_path / "universe.yaml"
    uni.write_text(json.dumps({"sectors": {"XLK": meta["stocks"]}, "market": "SPY"}))
    m, _ = dr.load_store(tmp_path, uni, "today", "2000-01-01", "2100-01-01")
    return uni, DailyData.from_market(m)


def _decision_k(full, s, k=I0 + 7):
    while not s.decision_day(full.day(k), full.day(k + 1)):
        k += 1
    return k


def test_the_store_loader_reads_the_research_loader_up_to_k_once_its_bars_are_available(tmp_path):
    """Point in time: the view of session k is read only once k's bars are stamped available (20:15 ET), it ends at
    k, and its decision is the research runner's decision of that evening on the same store."""
    from kaibot.engine.daily import decide_after_close

    uni, full = _stamped_store(tmp_path)
    loader = ld.StoreLoader(tmp_path, uni, mode="today")
    s = mom.VERSIONS["trend_momentum"]
    k = _decision_k(full, s)
    day = full.day(k)
    early = datetime.combine(day, time(16, 30), NY).astimezone(UTC)
    assert "not yet" in loader.available(day, early)
    with pytest.raises(ld.DataNotReady):
        loader(day, early)  # at 16:30 the store's bar of k is not available yet: no decision
    late = datetime.combine(day, time(20, 16), NY).astimezone(UTC)
    view = loader(day, late)
    assert view.dates[-1] == full.dates[k] and view.terminal == {}
    tl = decide_after_close(s, view, len(view.dates) - 1, full.day(k + 1), {})
    assert tl is not None and tl == decide_after_close(s, full, k, full.day(k + 1), {})
    nxt = full.day(len(full.dates) - 1) + timedelta(days=1)
    assert "no SPY bar" in loader.available(nxt, datetime.now(UTC))


def test_the_store_loader_waits_for_a_refresh_that_stopped_part_way(tmp_path, data):
    """QA-Ops review of #207: SPY has session k but most stocks do not (fetch_daily stopped part-way). The view
    would carry their last close forward, so it is not ready: no decision on yesterday's prices."""
    import json

    meta = json.loads((dr.FIXTURE / "fixture.json").read_text())
    day = data.day(I0 + 7)
    cut = meta["stocks"][: max(1, len(meta["stocks"]) // 2)]
    uni, _ = _stamped_store(tmp_path, cut=cut, before=day)  # their refresh never ran: the last bar is k - 1
    loader = ld.StoreLoader(tmp_path, uni, mode="today")
    late = datetime.combine(day, time(20, 16), NY).astimezone(UTC)
    assert loader.available(day, late) is None  # SPY alone says "ready"
    with pytest.raises(ld.DataNotReady, match="has not finished"):
        loader(day, late)


def _edit_bar(syms, at, how):
    """An `edit` for _stamped_store: `how(df, i)` changes the bar of each of `syms` dated `at`."""
    def edit(s, df):
        if s in syms:
            i = int((df["date"] == at).to_numpy().nonzero()[0][0])
            how(df, i)
        return df
    return edit


def _high_below_close(df, i):
    df.loc[i, "h"] = df.loc[i, "c"] * 0.5


def _gate_case(tmp_path, data, syms, how, at=None):
    import json

    meta = json.loads((dr.FIXTURE / "fixture.json").read_text())
    day = data.day(I0 + 7)
    at = at or data.day(I0 + 6)
    syms = [meta["stocks"][i] if isinstance(i, int) else i for i in syms]
    uni, _ = _stamped_store(tmp_path, edit=_edit_bar(syms, at, how))
    late = datetime.combine(day, time(20, 16), NY).astimezone(UTC)
    return ld.StoreLoader(tmp_path, uni, mode="today"), day, late, syms, at, meta


def test_a_stock_with_a_bad_bar_is_dropped_from_the_view_and_the_rule_decides_on_the_rest(tmp_path, data):
    """#230 (QA-Ops proposal, point 2): a bad bar in a stock of the view leaves that stock out of this evening's
    view: it may not be bought (as a disputed key), the column stays (so a holder is never routed through the "not
    in the daily view" sale), and the reason is on the view."""
    loader, day, late, syms, at, _ = _gate_case(tmp_path, data, [0], _high_below_close)
    view = loader(day, late)
    assert view.dates[-1].astype(object) == day
    assert view.dropped == {syms[0]: f"1 bad bar in 1 key: {syms[0]} {at} ohlc_order"}
    j = view.symbols.index(syms[0])
    assert view.member is not None and not view.member[:, j].any()  # never rankable tonight
    others = [int(c) for c in view.stocks if int(c) != j]
    assert view.member[-1, others].any()  # the rest stay rankable


@pytest.mark.parametrize("what", ["spy", "structural", "too_many", "early_stamp"])
def test_the_gate_stops_every_decision(tmp_path, data, what):
    """#230 (QA-Ops proposal, points 3 and 4): a bad bar in SPY, a structural error (here a duplicate session or a
    bar stamped before the store's own 20:15 ET), or more stocks failing than 1% of the view (at least one may):
    StoreFailedCheck, no decision for any rule."""
    import pandas as pd

    if what == "spy":
        loader, day, late, *_ = _gate_case(tmp_path, data, ["SPY"], _high_below_close)
        match = "SPY .* ohlc_order"
    elif what == "too_many":
        loader, day, late, *_ = _gate_case(tmp_path, data, [0, 1], _high_below_close)
        match = "2 of .* stocks fail the check"
    else:
        import pyarrow as pa
        import pyarrow.parquet as pq

        loader, day, late, syms, at, _ = _gate_case(tmp_path, data, [0], lambda df, i: None)
        f = tmp_path / "daily" / "bars" / f"symbol={syms[0]}.parquet"
        df = pq.read_table(f).to_pandas()
        i = int((df["date"] == at).to_numpy().nonzero()[0][0])
        if what == "structural":
            df = pd.concat([df.iloc[: i + 1], df.iloc[[i]], df.iloc[i + 1:]], ignore_index=True)
            match = "duplicate_date"
        else:
            # 16:15 ET: fine for the research check (close + 15 min), but before the 20:15 ET the live path relies on
            df.loc[i, "available_ts"] = df.loc[i, "available_ts"] - pd.Timedelta(hours=4)
            match = "early_available"
        pq.write_table(pa.Table.from_pandas(df, preserve_index=False), f)
    with pytest.raises(ld.StoreFailedCheck, match=match) as e:
        loader(day, late)
    assert isinstance(e.value, ld.DataNotReady)


def test_a_bad_bar_older_than_the_look_back_or_in_a_key_the_view_does_not_load_changes_nothing(tmp_path, data):
    old = data.day(0)
    uni, full = _stamped_store(tmp_path, edit=_edit_bar({"SPY"}, old, _high_below_close))
    day = full.day(len(full.dates) - 5)  # an NYSE session late in the fixture
    late = datetime.combine(day, time(20, 16), NY).astimezone(UTC)
    assert (day - old).days > ld.StoreLoader.LOOKBACK_DAYS
    loader = ld.StoreLoader(tmp_path, uni, mode="today")
    extra = tmp_path / "daily" / "bars" / "symbol=ZZZZ.parquet"  # not in the universe file: not read by the view
    extra.write_bytes(b"PAR1 not a parquet file")
    view = loader(day, late)
    assert view.dates[-1].astype(object) == day and view.dropped == {}


def test_the_store_loader_refuses_a_store_with_a_bars_file_it_cannot_read(tmp_path, data):
    """QA-Ops review of #234: a truncated bars file of a key the view loads is a failed check (StoreFailedCheck,
    so the engine's fail-closed handling applies), never another exception out of the loader."""
    import json

    uni, _ = _stamped_store(tmp_path)
    sym = json.loads((dr.FIXTURE / "fixture.json").read_text())["stocks"][0]
    f = tmp_path / "daily" / "bars" / f"symbol={sym}.parquet"
    good = f.read_bytes()
    f.write_bytes(b"PAR1 not a parquet file")
    day = data.day(I0 + 7)
    late = datetime.combine(day, time(20, 16), NY).astimezone(UTC)
    loader = ld.StoreLoader(tmp_path, uni, mode="today")
    with pytest.raises(ld.StoreFailedCheck, match=f"bars unreadable: .*symbol={sym}.parquet"):
        loader(day, late)
    f.write_bytes(good)
    assert loader(day, late).dates[-1].astype(object) == day


def test_the_heartbeat_names_each_rules_exit_kind_for_the_dashboard(data, tmp_path):
    """Product, 18 Nov 2026: the dashboard counted the momentum positions as "no safety stop". Their exit is the
    target list (no stop in either pre-registration), so each rule's heartbeat row says so: `exit` is "target_list"
    for a rule without a pre-registered stop and "broker_stop" for one with it, and only "broker_stop" sleeves can be
    unprotected. A retired rule's row carries it too."""
    assert ld.exit_kind("trend_momentum") == ld.exit_kind("mom_12_1_band~nofilter") == "target_list"
    ld.PREREG_STOP_ATR["trend_momentum"] = 3.0
    try:
        assert ld.exit_kind("trend_momentum") == "broker_stop"
    finally:
        ld.PREREG_STOP_ATR["trend_momentum"] = None
    assert ld.exit_kind("unknown_rule") == "broker_stop"  # fail closed: an unknown rule's sleeve wants a stop
    s = mom.VERSIONS["trend_momentum"]
    h = Harness(data, s, ld.live_builder(s, momentum_limits(s, POL, 100.0)), tmp_path)
    st = h.live.status()
    assert st["rules"][s.name]["exit"] == "target_list" and st["exits"] == {s.name: "target_list"}
    h.journal.append("md_retired", {"rule": s.name, "run_id": RID, "session": str(data.day(I0)), "drawdown_pct": 30.0,
                                    "end_pct": 27.0, "peak": EQ0, "retirement_id": "x"})
    row = h.live.status()["rules"][s.name]
    assert row.get("retired") and row["exit"] == "target_list"


# ---------------------------------------------------------------- #363: the latest decision's buys carried over


def _first_orders(h, n=2):
    return sorted(h.broker.orders.values(), key=lambda o: int(o["id"][1:]))[:n]


def _carry_run(data, tmp_path, *, hold=True, ca=None):
    s = mom.VERSIONS["trend_momentum"]
    h = Harness(data, s, ld.live_builder(s, momentum_limits(s, POL, 100.0)), tmp_path)
    if hold:
        h.broker.script.hold(None, bars=None)  # the first order of the run expires unfilled
        h.broker.script.fills(None, [1.0])  # the second fills one share, the rest expires
    if ca is not None:
        h.live.ca = ca
    return s, h


def test_an_expired_buy_is_carried_to_the_next_sessions_up_to_its_target_until_the_next_decision(data, tmp_path):
    """#363 (trend_momentum Amendment 11, mom_12_1_band Amendment 9): what the venue let expire of the latest
    decision's buys is sent again at the next session's near-close window, as target - held - open, never above the
    decision's target, and journalled (`md_send.carry`, event `multi_day_carry`). The next decision ends it."""
    s, h = _carry_run(data, tmp_path)
    h.run()
    x, y = _first_orders(h)
    assert [x["status"], y["status"]] == ["expired", "expired"] and float(y["filled_qty"]) == 1.0
    day = datetime.fromisoformat(x["submitted_at"]).astimezone(NY).date()
    sends = {b["session"]: b for *_, b in h.journal.entries(kinds=("md_send",)) if b["strategy"] == s.name}
    plan = {t[0]: t[1] for t in sends[str(day)]["targets"] if t[2] in ("buy", "resize")}
    nxt = data.day(data.index_of(day) + 1)
    carry = sends[str(nxt)]["carry"]
    assert carry["of"] == str(day)
    assert {c[0]: (c[1], c[2]) for c in carry["carried"]} == {x["symbol"]: (plan[x["symbol"]], 0.0),
                                                                y["symbol"]: (plan[y["symbol"]], 1.0)}
    sent = [o for o in h.broker.orders.values() if datetime.fromisoformat(o["submitted_at"]).astimezone(NY).date()
            == nxt and o["symbol"] in (x["symbol"], y["symbol"])]
    assert sorted((o["symbol"], float(o["qty"]), o["status"]) for o in sent) == sorted(
        [(x["symbol"], float(plan[x["symbol"]]), "filled"), (y["symbol"], float(plan[y["symbol"]]) - 1, "filled")])
    assert all("-carry-" in o["client_order_id"] for o in sent)
    ev = [b for k, b in h.events if k == "multi_day_carry"]
    assert ev and ev[0]["session"] == str(nxt) and {c[0] for c in ev[0]["carried"]} == {x["symbol"], y["symbol"]}
    # never above the target, and nothing is carried once the sleeve is at its target or a new decision came
    led = Ledger.fold(h.journal.entries(kinds=LEDGER_KINDS)).book(RID)
    later = [o for o in h.broker.orders.values() if "-carry-" in o["client_order_id"]
             and datetime.fromisoformat(o["submitted_at"]).astimezone(NY).date() > nxt]
    assert not [o for o in later if o["symbol"] in (x["symbol"], y["symbol"])]
    for sym in (x["symbol"], y["symbol"]):
        bought = sum(float(f["qty"]) for f in h.broker.fills if f["symbol"] == sym and f["side"] == "buy"
                     and datetime.fromisoformat(f["transaction_time"]).astimezone(NY).date() <= nxt)
        assert bought == plan[sym]
    assert led is not None
    decided = [b for *_, b in h.journal.entries(kinds=("md_decision",)) if b["target"] is not None]
    for b in sends.values():  # a carry only on sessions that are not a decision's fill session
        if b.get("carry"):
            assert b["session"] not in {d["fill_session"] for d in decided}


def test_a_carry_waits_while_the_market_filter_is_off(data, tmp_path, monkeypatch):
    """Section 2: new buys only while SPY closes above its 200-day average. A carried buy is a buy of that session,
    so the filter is read at the previous close; while it is off nothing is carried (the plan is kept)."""
    from kaibot.engine import daily

    monkeypatch.setattr(daily, "market_on", lambda spy: False)  # the carry's own read (decisions read momentum's)
    s, h = _carry_run(data, tmp_path)
    h.run()
    assert not [o for o in h.broker.orders.values() if "-carry-" in o["client_order_id"]]
    notes = [b["carry"] for *_, b in h.journal.entries(kinds=("md_send",)) if b.get("carry")]
    assert notes and all(n["risk_off"] and not n["carried"] for n in notes)


def test_a_carry_skips_a_stock_with_an_announced_corporate_action(data, tmp_path):
    """Amendment 6 (live-safety exclusion) applies to a carried buy as to any buy."""
    from types import SimpleNamespace

    hit: dict = {}

    class CA:
        def lookup(self, prev, session, symbols):
            sym = hit.get("sym")
            if sym is None or sym not in symbols:
                return {}, None
            return {sym.upper(): SimpleNamespace(action="spin-off", on="2099-01-01",
                                                 skip_text=lambda: "skipped: corporate action on 2099-01-01")}, None

    ca = CA()
    s, h = _carry_run(data, tmp_path, ca=ca)
    real = h.broker.submit

    def submit(req):  # once the first (held, expiring) order is known, its stock gets an announcement
        o = real(req)
        hit.setdefault("sym", o["symbol"])
        return o

    h.broker.submit = submit
    h.run()
    x, y = _first_orders(h)
    carried = [o for o in h.broker.orders.values() if "-carry-" in o["client_order_id"]]
    assert x["symbol"] not in {o["symbol"] for o in carried} and y["symbol"] in {o["symbol"] for o in carried}


def test_carry_targets_drop_what_the_pre_registration_drops(data):
    """The shared core (engine/daily.carry_targets): a buy whose stock had no bar on the fill day is dropped
    (section 4), an order the venue cancelled or refused is not sent again, a stock the rule could not buy now
    (below its 100-day average, a 15% move) is skipped, nothing above the target, sales are never carried."""
    from kaibot.engine import daily

    s = mom.VERSIONS["trend_momentum"]
    k = I0 + 10
    ok = [sym for sym in data.symbols[:60] if np.isfinite(data.raw_c[k - 1, data.col(sym)])][:6]
    a, b, c, d, e, f = ok
    gap = replace(data, raw_c=data.raw_c.copy())
    gap.raw_c[k - 1, gap.col(e)] = np.nan
    plan = [(a, 10, "buy"), (b, 10, "buy"), (c, 10, "resize"), (d, 10, "buy"), (e, 10, "buy"), (f, 0, "sell")]
    v = gap.view(k)
    _, _, sell_if = s.signals(v)
    out, note = daily.carry_targets(s, s.name, plan, {a: 4.0, c: 10.0}, gap, k - 1, k, {d: "canceled"})
    want = {a: 10, b: 10} if sell_if(gap.col(a)) is None and sell_if(gap.col(b)) is None else None
    got = {t.symbol: t.shares for t in out}
    if want is not None:
        assert got == want
    assert all(t.purpose == "carry" for t in out)
    assert note["skipped"][d] == "order canceled" and note["skipped"][e] == "no bar on the fill day"
    assert c not in got and f not in got  # at its target; a sale
    for sym, why in note["skipped"].items():
        if why.startswith("not buyable"):
            assert sell_if(gap.col(sym)) is not None


# ---------------------------------------------------------------- #366 (QA-Ops second key): the carry-over under attack
# The first change that buys on a session without a decision. What must hold whatever the venue or the process does:
# held + open + new order <= the latest decision's target shares, only names of that plan, only for this run, never a
# name that lost shares since the decision, and never past the cash or the book's caps.


class Guarded(Harness):
    """Checks EVERY buy the venue receives: its symbol is in the plan in force (the send of the latest decision's
    fill session) and the venue's position + its open buys + this order is at most that plan's target shares."""

    def build(self) -> None:
        super().build()
        if getattr(self, "_guarded", None) is not self.broker:
            self._guarded, self.violations, real = self.broker, [], self.broker.submit

            def submit(req):
                if req.side == "buy":
                    self.check(req)
                return real(req)

            self.broker.submit = submit

    def plan_in_force(self) -> dict:
        name = self.strategy.name
        fills = {b["fill_session"] for *_, b in self.journal.entries(kinds=("md_decision",))
                 if b["strategy"] == name and b["target"] is not None}
        last = {}
        for *_, b in self.journal.entries(kinds=("md_send",)):
            if b["strategy"] == name and (b["session"] in fills or b.get("late_of") in fills):  # M9 1.4: or late
                last = b
        return last

    def check(self, req) -> None:
        target = {t[0]: int(t[1]) for t in self.plan_in_force().get("targets", []) if t[2] in ("buy", "resize")}
        pos = float((self.broker.positions().get(req.symbol) or {}).get("qty") or 0)
        working = sum(float(o["qty"]) - float(o.get("filled_qty") or 0) for o in self.broker.open_orders()
                      if o["symbol"] == req.symbol and o["side"] == "buy")
        if req.symbol not in target:
            self.violations.append((req.client_order_id, "not in the plan in force"))
        elif pos + working + float(req.qty) > target[req.symbol] + 1e-9:
            self.violations.append((req.client_order_id, f"{pos} + {working} + {req.qty} > {target[req.symbol]}"))


FILL_DAY = I0 + 2  # 2023-02-01: the fixture's first decision is filled here; the next sessions carry
CARRY_DAY = FILL_DAY + 1


def _guarded(data, tmp_path, crash=None):
    s = mom.VERSIONS["trend_momentum"]
    return s, Guarded(data, s, ld.live_builder(s, momentum_limits(s, POL, 100.0)), tmp_path, crash=crash)


def _carries(h):
    return [o for o in h.broker.orders.values() if "-carry-" in o["client_order_id"]]


def _session(h, i, *, send=True, decide=True) -> None:
    """One session as Harness.run drives it (no crash points)."""
    d, day = h.d, h.d.day(i)
    h.account.recon.check("session_start")
    h.clock.set(_at(day, time(9, 30)))
    h.broker.advance(_at(day, time(9, 30)))
    for minute in (49, 50, 51, 52, 53, 55, 56, 57, 58):
        h.clock.set(_at(day, time(15, minute)))
        h.live.sync()
        if send:
            h.live.send(day)
    h.clock.set(_at(day, time(15, 59)))
    h.broker.advance(_at(day, time(15, 59)))
    h.clock.set(_at(day, time(16, 30)))
    h.live.sync()
    h.live.sync(force=True)
    h.account.ledger.apply("mark", h.mark(i))
    h.journal.append("mark", h.mark(i))
    if decide:
        h.live.after_close(d.day(i))


def _to_the_carry_day(h, *, expire=True, sessions=None, at: time | None = time(15, 50)) -> date:
    """Drive the run up to the 15:50 of the first carry day (`at=None`: to the evening before it); the fill day's
    first buy expires, its second fills 1."""
    if expire:
        h.broker.script.hold(None, bars=None)
        h.broker.script.fills(None, [1.0])
    h.live.after_close(h.d.day(I0 - 1))
    for i in range(I0, CARRY_DAY):
        _session(h, i, **(sessions or {}).get(i, {}))
    day = h.d.day(CARRY_DAY)
    if at is not None:
        h.account.recon.check("session_start")
        h.clock.set(_at(day, at))
    return day


def test_no_buy_ever_exceeds_the_plan_when_a_third_of_all_orders_expire_or_fill_in_part(data, tmp_path):
    """Every third buy of the run expires unfilled and every third fills one share, carried buys included (so they
    are carried again). At every buy the venue receives: position + open buys + the order <= the plan's target."""
    s, h = _guarded(data, tmp_path)
    real, n = h.broker.submit, {"i": 0}

    def submit(req):
        n["i"] += 1
        if req.side == "buy" and n["i"] % 3 == 0:
            h.broker.script.hold(req.symbol, bars=None)
        elif req.side == "buy" and n["i"] % 3 == 1:
            h.broker.script.fills(req.symbol, [1.0])
        return real(req)

    h.broker.submit = submit
    h.run()
    assert h.violations == [] and len(_carries(h)) > 20
    sends = {b["session"]: b for *_, b in h.journal.entries(kinds=("md_send",))}
    fills = sorted({b["fill_session"] for *_, b in h.journal.entries(kinds=("md_decision",))
                    if b["target"] is not None})
    for a, nxt in zip(fills, [*fills[1:], "9999"], strict=True):  # per decision: bought <= target - held then
        for sym, q, p, _ in sends[a]["targets"]:
            got = sum(float(f["qty"]) for f in h.broker.fills if f["symbol"] == sym and f["side"] == "buy"
                      and a <= f["transaction_time"][:10] < nxt)
            assert p not in ("buy", "resize") or got <= q, (a, sym, got, q)
    for b in sends.values():  # a carry only between decisions, of the latest decision, and only names of its plan
        if b.get("carry"):
            assert b["session"] not in fills
            assert b["carry"]["of"] == max(f for f in fills if f < b["session"])
            plan = {t[0] for t in sends[b["carry"]["of"]]["targets"] if t[2] in ("buy", "resize")}
            assert {c[0] for c in b["carry"]["carried"]} <= plan
            assert {t[0] for t in b["targets"] if t[2] == "carry"} <= plan


@pytest.mark.parametrize("where", ["after_decision", "after_send", "before_booking"])
def test_a_restart_on_a_carry_day_sends_each_carried_buy_once(data, tmp_path, where):
    """The process is killed and rebuilt from the journal on three sessions after the fill day: before the window,
    between the carried order and its fill, and between the fill and its booking."""
    s, h = _guarded(data, tmp_path, crash={where: {CARRY_DAY, CARRY_DAY + 1, CARRY_DAY + 2}})
    h.broker.script.hold(None, bars=None)
    h.broker.script.fills(None, [1.0])
    h.run()
    assert h.restarts == 3 and h.violations == []
    assert len(_carries(h)) == 2 and {o["status"] for o in _carries(h)} == {"filled"}
    booked = [b for *_, b in h.journal.entries(kinds=("fill",))]
    assert len(booked) == len(h.broker.fills) == len({b["activity"] for b in booked})


@pytest.mark.parametrize("when", ["before_venue", "after_venue"])
def test_a_crash_inside_the_carry_send_sends_nothing_twice(data, tmp_path, when):
    """The process dies in the target loop of a carry day, before or after the venue got the first carried order."""
    s, h = _guarded(data, tmp_path)
    h.broker.script.hold(None, bars=None)
    h.broker.script.fills(None, [1.0])
    real, seen = h.broker.submit, {"crashed": False}

    def submit(req):
        if "-carry-" in req.client_order_id and not seen["crashed"]:
            seen["crashed"] = True
            if when == "after_venue":
                real(req)
            raise Crash()
        return real(req)

    h.broker.submit = submit
    h.run()
    assert seen["crashed"] and h.restarts == 1 and h.violations == []
    plan = {t[0]: t[1] for t in next(b for *_, b in h.journal.entries(kinds=("md_send",))
                                     if b["session"] == str(data.day(FILL_DAY)))["targets"]}
    week = str(data.day(FILL_DAY + 4))
    for sym, q in plan.items():
        got = sum(float(f["qty"]) for f in h.broker.fills if f["symbol"] == sym and f["side"] == "buy"
                  and f["transaction_time"][:10] <= week)
        assert got == q, (sym, got, q)  # each name at its target, once


def test_a_fill_the_ledger_has_not_booked_yet_is_not_bought_again_by_the_carry(data, tmp_path):
    """#337 across sessions: a buy of the fill day reads `filled` at the venue, but its FILL activity is not listed
    until after the next session's window. The ledger holds 0, so `target - held` is the whole order again. The
    carry-over waits for the booking (the order manager knows the venue's filled quantity), also with the
    reconciliation's own block switched off here to show it does not depend on it."""
    s, h = _guarded(data, tmp_path)
    real_act, real_submit = h.broker.activities, h.broker.submit
    hide = {"sym": None, "until": _at(data.day(CARRY_DAY), time(16, 20))}

    def submit(req):
        o = real_submit(req)
        hide["sym"] = hide["sym"] or o["symbol"]
        return o

    h.broker.submit = submit
    h.broker.activities = lambda *a, **k: [r for r in real_act(*a, **k)
                                           if h.clock() >= hide["until"] or r.get("symbol") != hide["sym"]]
    h.live.after_close(data.day(I0 - 1))
    for i in range(I0, CARRY_DAY + 2):
        h.account.recon.allows = lambda *a, **k: None
        _session(h, i)
    sym = hide["sym"]
    sends = {b["session"]: b for *_, b in h.journal.entries(kinds=("md_send",))}
    q = {t[0]: t[1] for t in sends[str(data.day(FILL_DAY))]["targets"]}[sym]
    assert sends[str(data.day(CARRY_DAY))]["carry"]["skipped"] == {
        sym: "a fill the venue reported is not booked yet"}
    assert h.violations == [] and not _carries(h)
    assert sum(float(f["qty"]) for f in h.broker.fills if f["symbol"] == sym and f["side"] == "buy") == q
    assert h.account.held(s.name)[sym][0] == q  # booked once it was listed; nothing more was wanted


def test_a_new_run_never_buys_the_old_runs_plan(data, tmp_path):
    """The owner starts a new run after the decision's fill day (the old one is flattened first). The old run's
    plan was sized on the old run's equity: the new run carries nothing of it and waits for its own decision."""
    s, h = _guarded(data, tmp_path)
    day = _to_the_carry_day(h)
    assert h.live._plan(h.live.rules[0], day, RID)["carry"]["carried"]  # the same run: it would carry
    h.journal.append("run", {"run_id": 2, "amount": 10_000.0})
    h.account.ledger.apply("run", {"run_id": 2, "amount": 10_000.0})
    h.account.run_id = lambda: 2
    try:
        type(h.account).book = property(lambda self: self.ledger.book(2))
        planned = h.live._plan(h.live.rules[0], day, 2)
    finally:
        type(h.account).book = property(lambda self: self.ledger.book(RID))
    assert planned["targets"] == [] and "carry" not in planned


def test_a_name_an_emergency_stop_sold_since_the_decision_is_not_bought_back(data, tmp_path):
    """The engine is down after the fill day; the watchdog puts an emergency stop on a held name and it fills. Back
    up, the engine adopts the stop and books the sale. The name is "sold since the decision": the carry-over leaves
    it to the next decision (the amendment's order-history rule), whatever order sold it."""
    from kaibot.broker.protocol import OrderRequest
    from kaibot.engine.watchdog import failsafe_coid

    s, h = _guarded(data, tmp_path)
    day = _to_the_carry_day(h, at=None)
    held = h.account.held(s.name)
    sym = sorted(x for x in held if held[x][0] > 1)[0]
    h.clock.set(_at(day, time(9, 0)))
    px = float(data.raw_c[CARRY_DAY - 1, data.col(sym)])
    h.broker.submit(OrderRequest(sym, "sell", held[sym][0], "stop", "gtc", failsafe_coid(s.name, sym, h.clock()),
                                 stop_price=px * 2))  # far above the market: it fills at the next bar
    for t in (time(9, 30), time(12, 0)):
        h.clock.set(_at(day, t))
        h.broker.advance(_at(day, t))
    assert sym not in h.broker.positions()
    h.restart()
    h.account.recon.check("session_start")
    assert sym not in h.account.held(s.name)  # the sale is booked
    h.clock.set(_at(day, time(15, 50)))
    note = h.live._plan(h.live.rules[0], day, RID)["carry"]
    assert note["skipped"][sym] == "sold since the decision" and sym not in {c[0] for c in note["carried"]}
    assert note["carried"]  # the names the venue left short are still carried


def test_nothing_is_carried_without_the_decisions_own_send_or_without_last_evenings_record(data, tmp_path):
    """Engine down on the decision's fill day (no `md_send` of it): nothing is carried on the days after; since
    M9 1.4 the decision itself is sent late on the next session (tests/unit/test_m9_late_send.py), sized then, and
    not as a carry-over of share counts nobody journalled.
    Engine down in the evening (no record for the next session): since M9 1.3 the
    record is taken late, in the session (tests/unit/test_m9_late_decision.py), and the carry-over runs as after an
    on-time start; a session that still has no record carries nothing."""
    (tmp_path / "a").mkdir()
    s, h = _guarded(data, tmp_path / "a")
    day = _to_the_carry_day(h, expire=False, sessions={FILL_DAY: {"send": False}})
    assert h.broker.orders == {}
    planned = h.live._plan(h.live.rules[0], day, RID)
    assert planned["late_of"] == str(data.day(FILL_DAY)) and "carry" not in planned and "equity" in planned
    assert [t for t in planned["targets"] if t[2] == "buy"] and not [t for t in planned["targets"] if t[2] == "carry"]
    (tmp_path / "b").mkdir()
    s, h = _guarded(data, tmp_path / "b")
    day = _to_the_carry_day(h, sessions={FILL_DAY: {"decide": False}})
    planned = h.live._plan(h.live.rules[0], day, RID)
    rec = h.live._decision_for(s.name, day)
    assert rec["late"] is True and rec["target"] is None and rec["note"] == "not a decision day"
    assert len(planned["carry"]["carried"]) == 2 and planned.get("note") is None
    (tmp_path / "c").mkdir()
    s, h = _guarded(data, tmp_path / "c")
    day = _to_the_carry_day(h, sessions={FILL_DAY: {"decide": False}})
    h.live.after_close = lambda *a, **k: []  # the late step takes no record (as when the daily store is not ready)
    planned = h.live._plan(h.live.rules[0], day, RID)
    assert planned["targets"] == [] and planned.get("note") == "no decision" and "carry" not in planned


def test_once_a_newer_decision_exists_nothing_of_the_older_plan_is_carried(data, tmp_path):
    """The next decision ends the carry-over, also when that decision's own send never happened (engine down in its
    window): the older plan's short buys are not picked up again; they are the newer decision's business (since
    M9 1.4 the newer decision is then sent late, and nothing of the older plan with it)."""
    s, h = _guarded(data, tmp_path)
    _to_the_carry_day(h, at=None)
    _session(h, CARRY_DAY, send=False)  # nothing was carried that day: both names are still short
    day, rule = data.day(CARRY_DAY + 1), h.live.rules[0]
    h.account.recon.check("session_start")
    h.clock.set(_at(day, time(15, 50)))
    assert len(h.live._plan(rule, day, RID)["carry"]["carried"]) == 2  # the latest decision's plan: carried
    first = next(b for *_, b in h.journal.entries(kinds=("md_decision",)) if b["target"] is not None)
    h.journal.append("md_decision", {**first, "session": str(data.day(FILL_DAY)),
                                     "fill_session": str(data.day(CARRY_DAY))})  # a newer decision, never sent
    planned = h.live._plan(rule, day, RID)
    assert "carry" not in planned and not [t for t in planned["targets"] if t[2] == "carry"]
    assert planned["late_of"] == str(data.day(CARRY_DAY))


def test_a_stock_the_rule_could_not_buy_at_the_last_close_is_not_carried(data):
    """Section 2's "Not buyable" (below its 100-day average, a 15% move), read at the previous close: the reason is
    in the note. Found on the fixture: a session where one stock is buyable and another is not."""
    from kaibot.engine import daily

    s = mom.VERSIONS["trend_momentum"]
    for k in range(I0, I1):
        sell_if = s.signals(data.view(k))[2]
        ok = [x for x in data.symbols[:60] if np.isfinite(data.raw_c[k - 1, data.col(x)])]
        bad = [x for x in ok if sell_if(data.col(x)) is not None]
        good = [x for x in ok if sell_if(data.col(x)) is None]
        if bad and good and daily.market_on(data.view(k).close(daily.SMA_MARKET)[:, data.view(k).spy]):
            break
    else:
        pytest.fail("the fixture has no such session")
    out, note = daily.carry_targets(s, s.name, [(good[0], 7, "buy"), (bad[0], 7, "buy")], {}, data, k - 1, k, {})
    assert [(t.symbol, t.shares, t.purpose) for t in out] == [(good[0], 7, "carry")]
    assert note["skipped"] == {bad[0]: f"not buyable: {sell_if(data.col(bad[0]))}"}
    assert note["carried"] == [[good[0], 7, 0.0]] and note["risk_off"] is False


@pytest.mark.parametrize("version", ["trend_momentum", "mom_12_1_band"])
def test_a_stock_outside_the_rules_ranked_universe_at_the_last_close_is_not_carried(data, version):
    """Backend-quant's review of #366 (trend_momentum Amendment 13, mom_12_1_band Amendment 11). Section 2 lets a rule
    buy only a stock of its ranked universe: one that passes the universe filter (price, liquidity, point-in-time
    membership) and has a score. A carried buy is a buy of its own session, so both are read at the previous close.
    On 3bdcac9 and b1ada35 the carry-over read only trend_momentum's 100-day average and 15% move, and nothing at
    all for mom_12_1_band (which has neither). Found on the fixture: a session where one stock is in the ranked
    universe and another, with a bar on the fill day, is outside the universe filter; a stock without a score is
    made by a strategy that hides one score."""
    from kaibot.engine import daily

    s = mom.VERSIONS[version]
    for k in range(I0, I1):
        v = data.view(k)
        score, ok, sell_if = s.signals(v)
        if getattr(s, "market_filter", False) and not daily.market_on(v.close(daily.SMA_MARKET)[:, v.spy]):
            continue
        cols = [int(j) for j in data.stocks if np.isfinite(data.raw_c[k - 1, j]) and sell_if(int(j)) is None]
        good = [data.symbols[j] for j in cols if ok[j] and np.isfinite(score[j])]
        outside = [data.symbols[j] for j in cols if not ok[j]]
        if len(good) >= 2 and outside:
            break
    else:
        pytest.fail("the fixture has no such session")
    keep, hidden, out_of = good[0], good[1], outside[0]

    class HidesOneScore:
        market_filter = getattr(s, "market_filter", False)

        def signals(self, view):
            sc, okay, sell = s.signals(view)
            sc = np.array(sc, dtype=float)
            sc[data.col(hidden)] = np.nan
            return sc, okay, sell

    plan = [(keep, 7, "buy"), (out_of, 7, "buy"), (hidden, 7, "buy")]
    out, note = daily.carry_targets(HidesOneScore(), s.name, plan, {}, data, k - 1, k, {})
    assert [(t.symbol, t.shares, t.purpose) for t in out] == [(keep, 7, "carry")]
    assert note["skipped"] == {out_of: "not buyable: universe", hidden: "not buyable: no score"}
    assert note["carried"] == [[keep, 7, 0.0]]
    out, note = daily.carry_targets(s, s.name, plan, {}, data, k - 1, k, {})  # the rule itself scores `hidden`
    assert [t.symbol for t in out] == [keep, hidden] and note["skipped"] == {out_of: "not buyable: universe"}


def test_a_carried_buy_must_still_fit_the_cash_and_the_books_caps_at_the_last_close(data, tmp_path):
    """A carried buy keeps the plan's shares, sized at the decision's prices against that day's cash, and the paper
    account lends rather than refuses. So at the previous close it must fit: the cash less the 3% reserve, the
    book's exposure cap, the per-name cap, the sector cap. Best-ranked first; what does not fit is journalled with
    its reason and tried again at the next session."""
    s, h = _guarded(data, tmp_path)
    day = _to_the_carry_day(h)
    rule = h.live.rules[0]
    base = h.live._plan(rule, day, RID)["carry"]
    (a, qa, ha), (b, qb, hb) = base["carried"]
    k = CARRY_DAY - 1
    cost = {x: (q - held) * float(data.raw_c[k, data.col(x)]) for x, q, held in base["carried"]}
    eq, real_cash = h.account.equity(), h.account.cash
    try:
        # 1. cash: room for the first name only (the intraday rule holds the rest at 15:50, or prices rose)
        h.account.cash = lambda: 0.03 * eq + cost[a] + 1.0
        note = h.live._plan(rule, day, RID)["carry"]
        assert [c[0] for c in note["carried"]] == [a]
        assert note["skipped"] == {b: "no cash room at the last close (the cash reserve is kept)"}
        # 2. no cash at all: nothing is carried, both are journalled, and the event says so
        h.account.cash = lambda: 0.0
        n0 = len(h.events)
        planned = h.live._plan(rule, day, RID)
        assert planned["targets"] == [] and set(planned["carry"]["skipped"]) == {a, b}
        assert [body["skipped"] for kind, body in h.events[n0:] if kind == "multi_day_carry"] == [
            planned["carry"]["skipped"]]
    finally:
        h.account.cash = real_cash
    # 3. the caps, on the same book: per-name, sector, exposure
    lim = rule.builder.limits
    held = h.account.held(s.name)
    carry = [Target(s.name, a, qa, "carry"), Target(s.name, b, qb, "carry")]
    assert h.live._carry_room(rule, data.day(k), carry, held) == {}
    worth_of = {x: q * float(data.raw_c[k, data.col(x)]) for x, q in ((a, qa), (b, qb))}
    big = max(worth_of, key=worth_of.get)
    tight = ld.Rule(s, dataclasses.replace(rule.builder, limits=replace(
        lim, max_name_pct=sum(worth_of.values()) / 2 / eq * 100)))  # between the two positions' worth
    assert h.live._carry_room(tight, data.day(k), carry, held) == {big: "above the per-name cap at the last close"}
    worth = sum(q * m for q, m in held.values())
    full = ld.Rule(s, dataclasses.replace(rule.builder, limits=replace(
        lim, max_gross_pct=(worth + cost[a]) / eq * 100 + 0.01)))
    assert h.live._carry_room(full, data.day(k), carry, held) == {
        b: "the book is at its exposure cap at the last close"}
    sector = replace(data, sector={x: "one" for x in data.symbols})
    h.live._data = {data.day(k): ld.as_of(sector, k)}
    one = ld.Rule(s, dataclasses.replace(rule.builder, limits=replace(
        lim, max_sector_pct=(worth + cost[a]) / eq * 100 + 0.01)))
    assert h.live._carry_room(one, data.day(k), carry, held) == {
        b: "its sector is at the sector cap at the last close"}
    # 4. a book that cannot be valued carries nothing
    h.account.equity = lambda: float("nan")
    assert set(h.live._carry_room(rule, data.day(k), carry, held)) == {a, b}


def test_a_failing_carry_never_holds_back_the_sessions_sales_and_is_one_event(data, tmp_path):
    """Whatever goes wrong inside the carry-over, the session's send is still planned (its pending sales go out),
    nothing is carried, and the failure is an event and in the journalled send."""
    s, h = _guarded(data, tmp_path)
    day = _to_the_carry_day(h)
    rule = h.live.rules[0]
    sale = Target(s.name, "ZZZ", 0, "sell", {"why": "pending"})
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(ld, "plan_session", lambda *a, **k: (None, [sale]))
        monkey.setattr(h.live, "_carry_room", lambda *a, **k: 1 / 0)
        planned = h.live._plan(rule, day, RID)
    finally:
        monkey.undo()
    assert planned["targets"] == [["ZZZ", 0, "sell", "pending"]]
    assert planned["carry"]["carried"] == [] and "ZeroDivisionError" in planned["carry"]["error"]
    assert [body["step"] for kind, body in h.events if kind == "multi_day_error"] == ["carry"]


def test_a_carried_buy_has_its_row_for_the_fail_safes_before_the_venue_gets_the_order(data, tmp_path):
    """#342 for a carried buy: the account's `before_send` hook (the engine writes today's ledger_position rows
    from it) gets each carried buy at its target shares before the first carried order reaches the venue."""
    s, h = _guarded(data, tmp_path)
    seen: list = []
    h.account.before_send = lambda rule, session, planned: seen.append(
        (str(session), dict(planned), len(_carries(h))))
    h.broker.script.hold(None, bars=None)
    h.broker.script.fills(None, [1.0])
    h.live.after_close(data.day(I0 - 1))
    for i in range(I0, CARRY_DAY + 1):
        _session(h, i)
    (fill_day, plan, _), (carry_day, carried, sent_before) = seen
    x, y = _first_orders(h)
    assert fill_day == str(data.day(FILL_DAY)) and carry_day == str(data.day(CARRY_DAY))
    assert carried == {x["symbol"]: plan[x["symbol"]], y["symbol"]: plan[y["symbol"]]} and sent_before == 0
    assert len(_carries(h)) == 2
