"""Issue #311: the daily note on the live daily path, driven on SimBroker as the parity test drives the engine. It is
written once per session after 17:00 ET from the journal's own records, and the orders, fills and every other
journal entry are byte for byte the same with it on and off. Template mode (no key): no model, no network."""

from __future__ import annotations

import json
from datetime import time

import pytest
from test_live_daily import I0, I1, POL, Harness, _at, venue_fills, venue_orders

from kaibot.agents import daily_note as dn
from kaibot.agents.evalset.preopen import offline_gateway
from kaibot.engine import live_daily as ld
from kaibot.engine.daily import momentum_limits
from kaibot.strategies import momentum as mom

S = mom.VERSIONS["trend_momentum"]


class NoteHarness(Harness):
    def __init__(self, *a, note=None, **kw):
        self._note = note
        super().__init__(*a, **kw)

    def build(self) -> None:
        super().build()
        self.live.note = self._note

    def run(self) -> None:  # the parity run, plus the engine's evening tick at 17:05 ET
        d = self.d
        self.live.after_close(d.day(I0 - 1))
        for i in range(I0, I1 + 1):
            day = d.day(i)
            self.account.recon.check("session_start")
            self.clock.set(_at(day, time(9, 30)))
            self.broker.advance(_at(day, time(9, 30)))
            for minute in (49, 50, 51, 52, 53, 55, 56, 57, 58):  # #333: near-close orders, 15:50-15:57 ET
                self.clock.set(_at(day, time(15, minute)))
                self.live.sync()
                self.live.send(day)
            self.clock.set(_at(day, time(15, 59)))
            self.broker.advance(_at(day, time(15, 59)))
            self.clock.set(_at(day, time(16, 30)))
            self.live.sync()
            self.live.sync(force=True)
            self.account.ledger.apply("mark", self.mark(i))
            self.journal.append("mark", self.mark(i))
            self.clock.set(_at(day, time(17, 5)))
            self.live.sync()
            self.live.after_close(day)


@pytest.fixture(scope="module")
def data():
    from kaibot.engine import daily_report as dr
    from kaibot.engine.daily import DailyData

    m, _ = dr.load_fixture(dr.FIXTURE)
    return DailyData.from_market(m)


@pytest.fixture(scope="module")
def runs(data, tmp_path_factory):
    lim = momentum_limits(S, POL, 100.0)
    off = NoteHarness(data, S, ld.live_builder(S, lim), tmp_path_factory.mktemp("off"))
    off.run()
    gw = offline_gateway()
    gw.client = None  # no key: the template
    on = NoteHarness(data, S, ld.live_builder(S, lim), tmp_path_factory.mktemp("on"),
                     note=dn.NoteDesk(gw, inline=True))
    on.run()
    return off, on


def test_the_note_changes_nothing(runs):
    off, on = runs
    assert venue_orders(on.broker) == venue_orders(off.broker) and venue_fills(on.broker) == venue_fills(off.broker)
    strip = [json.dumps([k, b], sort_keys=True, default=str) for _, _, k, b in on.journal.entries() if k != dn.KIND]
    assert strip == [json.dumps([k, b], sort_keys=True, default=str) for _, _, k, b in off.journal.entries()]


def test_one_note_per_session_from_the_days_records(runs):
    _, on = runs
    notes = [b for _, _, _, b in on.journal.entries(kinds=(dn.KIND,))]
    sends = {b["session"] for _, _, _, b in on.journal.entries(kinds=("md_send",))}
    assert len(notes) == len({n["session"] for n in notes}) == len(sends)
    assert all(n["source"] == "template" and n["text"] for n in notes)
    bought = [n for n in notes if n["facts"]["rules"].get(S.name, {}).get("bought")]
    assert bought and all("bought " in n["text"] for n in bought)
    assert [k for k, _ in on.events if k == dn.EVENT] and on.live.status(on.d.day(I1))["daily_note"]["text"]
