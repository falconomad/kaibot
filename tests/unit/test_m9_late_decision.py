"""M9 ticket 1.3 (audit finding M1): an engine that first comes up after the open of a fill day still takes the
session's decision.

Before the fix the after-close decision was only taken while the market was closed. An engine started at 10:00 ET
(the Mac did start late on 20 Nov 2026) found no `md_decision` for the session, journalled a "no decision" send and
the weekly rule skipped its rebalance. The live daily path on `SimBroker` and `SimClock` (the harness of
test_live_daily.py), with the process off from one session's close to 10:00 ET of the next.
"""

from __future__ import annotations

from datetime import datetime, time

import pytest
from test_live_daily import EQ0, I0, POL, RID, Harness, _at, venue_fills, venue_orders

from kaibot.broker.sim import NY
from kaibot.engine import daily_report as dr
from kaibot.engine import live_daily as ld
from kaibot.engine.daily import DailyData, momentum_limits
from kaibot.strategies import momentum as mom

S = mom.VERSIONS["trend_momentum"]
WINDOW = (49, 50, 51, 52, 53, 55, 56, 57, 58)


@pytest.fixture(scope="module")
def data() -> DailyData:
    m, _ = dr.load_fixture(dr.FIXTURE)
    return DailyData.from_market(m)


class Days(Harness):
    """The harness over sessions I0..last, with the process off on chosen evenings and send windows.

    `late`: sessions the engine first comes up for at 10:00 ET (nothing ran after the previous close: no
    `after_close`, a new process at 10:00). `no_window`: sessions whose 15:49-15:58 ticks never happen (the process
    is down from 15:45 and comes back after the close)."""

    def __init__(self, data, tmp_path, late=(), no_window=(), unready=()):
        self.late, self.no_window, self.unready = set(late), set(no_window), set(unready)
        super().__init__(data, S, ld.live_builder(S, momentum_limits(S, POL, 100.0)), tmp_path)

    def build(self) -> None:
        super().build()
        d, real = self.d, self.live.load

        def load(k, now):  # the daily store was not refreshed for the evenings in `unready`
            if d.index_of(k) in self.unready:
                raise ld.DataNotReady(f"the daily store has no SPY bar for {k}")
            return real(k, now)

        self.live.load = load

    def tick(self, day, t: time) -> None:
        self.clock.set(_at(day, t))
        self.live.sync()
        self.live.send(day)

    def run(self, last: int) -> None:
        d = self.d
        if I0 not in self.late:
            self.live.after_close(d.day(I0 - 1))
        for i in range(I0, last + 1):
            day = d.day(i)
            self.account.recon.check("session_start")
            self.clock.set(_at(day, time(9, 30)))
            self.broker.advance(_at(day, time(9, 30)))
            if i in self.late:
                self.clock.set(_at(day, time(10, 0)))
                self.restart()  # the process starts at 10:00 ET
                self.tick(day, time(10, 0))
            else:
                for minute in (31, 45):
                    self.tick(day, time(9, minute))
            self.tick(day, time(12, 0))
            if i not in self.no_window:
                for minute in WINDOW:
                    self.tick(day, time(15, minute))
            self.clock.set(_at(day, time(15, 59)))
            self.broker.advance(_at(day, time(15, 59)))
            self.clock.set(_at(day, time(16, 30)))
            if i in self.no_window:
                self.restart()  # back after the close
            self.live.sync()
            self.account.ledger.apply("mark", self.mark(i))
            self.journal.append("mark", self.mark(i))
            if i + 1 not in self.late:
                self.live.after_close(day)

    def decisions(self) -> list[dict]:
        return [b for *_, b in self.journal.entries(kinds=("md_decision",))]

    def sends(self) -> list[dict]:
        return [b for *_, b in self.journal.entries(kinds=("md_send",))]


def first_buy_day(data, tmp_path) -> int:
    """The first fill session (index) of the window whose decision buys something."""
    h = Days(data, tmp_path / "probe")
    h.run(I0 + 12)
    days = sorted({data.index_of(datetime.fromisoformat(o["submitted_at"]).astimezone(NY).date())
                   for o in h.broker.orders.values() if o["side"] == "buy"})
    assert days
    return days[0]


def same(b: dict) -> dict:
    return {k: v for k, v in b.items() if k not in ("decided_at", "late")}


def test_a_start_at_ten_on_a_fill_day_journals_the_decision_an_on_time_start_would(data, tmp_path):
    fill = first_buy_day(data, tmp_path)
    on_time, late = Days(data, tmp_path / "a"), Days(data, tmp_path / "b", late={fill})
    on_time.run(fill + 1)
    late.run(fill + 1)
    want, got = on_time.decisions(), late.decisions()
    assert [same(b) for b in got] == [same(b) for b in want]  # the same records: targets, order, held, sessions
    rec = [b for b in got if b["fill_session"] == str(data.day(fill))]
    assert len(rec) == 1 and rec[0]["target"] is not None and rec[0]["late"] is True
    decided = datetime.fromisoformat(rec[0]["decided_at"]).astimezone(NY)
    assert (decided.date(), decided.time()) == (data.day(fill), time(10, 0))  # at the first tick, not at 15:50
    assert not any(b.get("late") for b in want)
    # and so the same orders at the same time and the same fills: the rebalance is not skipped
    assert venue_orders(late.broker) == venue_orders(on_time.broker)
    assert venue_fills(late.broker) == venue_fills(on_time.broker) and len(venue_fills(late.broker)) > 3
    assert [same(b) for b in late.sends()] == [same(b) for b in on_time.sends()]
    kinds = [k for k, _ in late.events]
    assert "multi_day_no_decision" not in kinds and "multi_day_error" not in kinds
    ev = [b for k, b in late.events if k == "multi_day_decision" and b["fill_session"] == str(data.day(fill))]
    assert len(ev) == 1 and ev[0]["late"] is True
    assert late.account.cash() == pytest.approx(on_time.account.cash(), abs=0.01) and late.account.cash() < EQ0
    assert rec[0]["run_id"] == RID


def test_a_late_start_on_a_day_that_is_no_decision_day_gets_its_record_too(data, tmp_path):
    """The session's record is what the carry-over and the pending sales read: an engine up late on a day after a
    non-decision close journals "not a decision day", late, exactly as it would have the evening before."""
    fill = first_buy_day(data, tmp_path)
    on_time, late = Days(data, tmp_path / "a"), Days(data, tmp_path / "b", late={fill + 1})
    on_time.run(fill + 2)
    late.run(fill + 2)
    assert [same(b) for b in late.decisions()] == [same(b) for b in on_time.decisions()]
    rec = [b for b in late.decisions() if b["fill_session"] == str(data.day(fill + 1))]
    assert len(rec) == 1 and rec[0]["target"] is None and rec[0]["late"] is True
    assert venue_orders(late.broker) == venue_orders(on_time.broker)


def test_a_late_decision_waits_for_the_daily_store_and_is_taken_once(data, tmp_path):
    """The store was not refreshed for the previous close: no decision, the plain note, no order (fail closed, as
    before). The try is repeated at most every five minutes before the window and on every tick inside it."""
    fill = first_buy_day(data, tmp_path)
    h = Days(data, tmp_path / "a", late={fill}, unready={fill - 1})
    h.run(fill)
    day = str(data.day(fill))
    assert not [b for b in h.decisions() if b["fill_session"] == day]
    assert not [o for o in h.broker.orders.values()
                if datetime.fromisoformat(o["submitted_at"]).astimezone(NY).date() == data.day(fill)]
    assert [b["note"] for b in h.sends() if b["session"] == day] == ["no decision"]
    assert any(k == "multi_day_no_decision" and "daily store not refreshed" in b["note"] for k, b in h.events)
    waits = [b for k, b in h.events if k == "multi_day_waiting" and b["session"] == str(data.day(fill - 1))]
    assert len(waits) == 1  # said once, not at every try

    calls = []
    g = Days(data, tmp_path / "b", late={fill})
    g.run(fill - 1)
    day = data.day(fill)
    g.clock.set(_at(day, time(9, 30)))
    g.broker.advance(_at(day, time(9, 30)))
    g.restart()
    real = g.live.load
    g.live.load = lambda k, now: (calls.append(now), real(k, now))[1]
    g.unready = {fill - 1}
    for minute in range(0, 12):  # 10:00 ... 10:11: tried at 10:00, 10:05, 10:10
        g.tick(day, time(10, minute))
    assert len(calls) == 3 and not [b for b in g.decisions() if b["fill_session"] == str(day)]
    g.unready = set()  # the store is refreshed
    g.tick(day, time(10, 15))
    g.tick(day, time(10, 16))
    g.tick(day, time(12, 0))
    assert len([b for b in g.decisions() if b["fill_session"] == str(day)]) == 1 and len(calls) == 4
