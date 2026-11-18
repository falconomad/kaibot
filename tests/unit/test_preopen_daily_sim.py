"""Issue #131 (M7·S91): the pre-open review on the live daily path, driven on SimBroker exactly as the parity test
drives the engine (tests/unit/test_live_daily.py), with the deterministic replay reviewer: no key, no network, no
spend. What it shows:

- a review that allows everything sends the SAME orders and gets the SAME fills as the research runner (the review
  is only a filter: with nothing vetoed nothing changes);
- a veto drops that buy on every session and is journalled with its reason (`md_review`, `md_send.review`, events);
- the review runs before the session (it ends at 09:31; the send window is 15:50-15:57 since #333) and its record
  precedes the send in the journal;
- the counterfactual (the rules' plan alone) is measured through SimBroker, not a private simulator: both arms are
  full SimBroker runs of the same core;
- the journalled reviews are stored candidate lists the offline evals read back (contract violations: none).
"""

from __future__ import annotations

from datetime import datetime, time

import pytest
from test_live_daily import EQ0, I0, I1, POL, RID, Harness, _at, reference, venue_fills, venue_orders

from kaibot.agents import preopen
from kaibot.agents.evalset.preopen import ON_ENV, evaluate, from_journal, load_lists, offline_gateway, write_lists
from kaibot.agents.preopen_daily import DeterministicReviewer, PreopenDesk
from kaibot.broker.sim import NY
from kaibot.engine import live_daily as ld
from kaibot.engine.daily import momentum_limits
from kaibot.engine.ledger import LEDGER_KINDS, Ledger
from kaibot.strategies import momentum as mom

S = mom.VERSIONS["trend_momentum"]


def desk(reviewer, inline=True):
    return PreopenDesk(reviewer, offline_gateway(), env=ON_ENV, inline=inline, needs_key=False)


class ReviewHarness(Harness):
    """The parity harness with the review wired, and the engine's pre-open tick at 08:45 ET."""

    def __init__(self, *a, review=None, **kw):
        self._review = review
        super().__init__(*a, **kw)

    def build(self) -> None:
        super().build()
        self.live.review = self._review

    def run(self) -> None:
        d = self.d
        self.live.after_close(d.day(I0 - 1))
        for i in range(I0, I1 + 1):
            day = d.day(i)
            self.account.recon.check("session_start")
            self.clock.set(_at(day, time(8, 45)))
            self.live.preopen()  # the engine's closed-market tick before the open
            self.clock.set(_at(day, time(9, 30)))
            self.broker.advance(_at(day, time(9, 30)))
            for minute in (49, 50, 51, 52, 53, 55, 56, 57, 58):  # the near-close send window (#333)
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
            self.live.after_close(day)


def equity(h) -> float:
    led = Ledger.fold(h.journal.entries(kinds=LEDGER_KINDS)).book(RID)
    return led.cash + sum(x.qty * x.mark for x in led.sleeves.values())


@pytest.fixture(scope="module")
def runs(data, tmp_path_factory):
    lim = momentum_limits(S, POL, 100.0)
    ref = reference(data, S, ld.live_builder(S, lim))
    buys = [o for o in ref.broker.orders.values() if o["side"] == "buy"]
    victim = buys[0]["symbol"]
    allow = ReviewHarness(data, S, ld.live_builder(S, lim), tmp_path_factory.mktemp("allow"),
                          review=desk(DeterministicReviewer()))
    allow.run()
    veto = ReviewHarness(data, S, ld.live_builder(S, lim), tmp_path_factory.mktemp("veto"),
                         review=desk(DeterministicReviewer(veto={victim})))
    veto.run()
    return {"ref": ref, "allow": allow, "veto": veto, "victim": victim}


@pytest.fixture(scope="module")
def data():
    from kaibot.engine import daily_report as dr
    from kaibot.engine.daily import DailyData

    m, _ = dr.load_fixture(dr.FIXTURE)
    return DailyData.from_market(m)


def entries(h, kind):
    return [(seq, b) for seq, _, _, b in h.journal.entries(kinds=(kind,))]


def test_a_review_that_allows_everything_changes_nothing(runs):
    ref, h = runs["ref"], runs["allow"]
    assert venue_orders(h.broker) == venue_orders(ref.broker) and len(venue_orders(h.broker)) > 20
    assert venue_fills(h.broker) == venue_fills(ref.broker)
    reviews = [b for _, b in entries(h, "md_review")]
    assert reviews and all(b["status"] == "done" and not b["vetoed"] and not b["downgraded"] for b in reviews)


def test_a_veto_drops_that_buy_on_every_session_and_is_journalled(runs):
    h, victim = runs["veto"], runs["victim"]
    assert not [o for o in h.broker.orders.values() if o["symbol"] == victim and o["side"] == "buy"]
    assert any(o["symbol"] == victim for o in runs["ref"].broker.orders.values())
    reviews = [b for _, b in entries(h, "md_review")]
    hit = [b for b in reviews if victim in b["vetoed"]]
    assert hit and all(b["verdicts"][f"{S.name}:{victim}"] == "veto" for b in hit)
    assert all(any(v["candidate_id"] == f"{S.name}:{victim}" and v["reason"] == "scenario veto"
                   for v in b["answer"]["verdicts"]) for b in hit)
    sends = [b for _, b in entries(h, "md_send")]
    assert all("review" in b for b in sends)
    assert any(victim in b["review"]["vetoed"] for b in sends)
    assert not any(t[0] == victim and t[1] > 0 for b in sends for t in b["targets"])
    kinds = [k for k, _ in h.events]
    assert "multi_day_review" in kinds and "multi_day_review_applied" in kinds
    ev = next(b for k, b in h.events if k == "multi_day_review" and victim in b["vetoed"])
    assert ev["reasons"][victim].startswith("veto:")


def test_the_review_is_journalled_before_the_send_window(runs):
    h = runs["veto"]
    reviews = {(b["strategy"], b["session"]): (seq, b) for seq, b in entries(h, "md_review")}
    for seq, b in entries(h, "md_send"):
        rseq, rb = reviews[(b["strategy"], b["session"])]
        assert rseq < seq
        assert datetime.fromisoformat(rb["as_of"]).astimezone(NY).time() == time(8, 45)  # before 09:31


def test_the_counterfactual_is_two_simbroker_runs_of_the_same_core(runs):
    rules, reviewed = equity(runs["allow"]), equity(runs["veto"])
    assert rules != pytest.approx(EQ0) and reviewed != pytest.approx(EQ0)
    assert rules == pytest.approx(equity(runs["allow"]))  # deterministic
    # every journalled review carries both arms, so the rules' arm can be replayed beside it
    for _, b in entries(runs["veto"], "md_review"):
        for arm in b["arms"].values():
            assert arm["current"] <= arm["reviewed"] <= arm["rules"]


def test_heartbeat_shows_the_review_and_is_unchanged_when_off(runs, data):
    h = runs["veto"]
    day = data.day(I1)
    st = h.live.status(day)
    assert st["preopen_review"] == "on"
    rv = st["rules"][S.name]["review"]
    assert rv["session"] == str(day) and rv["note"].startswith("pre-open review")
    # the same journal read by a daily path with the review off (None): no review field anywhere
    lv = ld.LiveDaily(None, h.journal, h.clock, [ld.Rule(S, None)], None, h.account,
                      next_session=lambda d: d, prev_session=lambda d: d)
    assert lv.review is None and lv.preopen() == []
    st_off = lv.status(day)
    assert "preopen_review" not in st_off and "review" not in st_off["rules"][S.name]


def test_journalled_reviews_are_stored_lists_for_the_offline_evals(runs, tmp_path):
    cases = from_journal([b for _, b in entries(runs["veto"], "md_review")])
    assert cases
    p = tmp_path / "stored.jsonl"
    assert write_lists(cases, p) == len(cases)
    rep = evaluate(load_lists(p), reviewer=DeterministicReviewer(veto={runs["victim"]}))
    assert rep["contract_violations"] == [] and rep["expected_match"] == 1.0


def test_an_unreviewed_growing_key_is_vetoed():
    from kaibot.agents.preopen_daily import reviewed_plan

    planned = {"strategy": "r", "session": "2026-11-17", "targets": [["A", 10, "target", None],
                                                                     ["B", 5, "target", None]]}
    rec = {"session": "2026-11-17", "status": "done", "verdicts": {"r:A": "allow"}, "cost_usd": 0.0}
    out, block = reviewed_plan("r", planned, {}, rec)
    assert [t[0] for t in out["targets"]] == ["A"] and block["vetoed"] == ["B"] and block["unseen"] == ["B"]


def test_off_by_default_nothing_is_built():
    from kaibot.engine.m3glue import make_preopen

    assert make_preopen(offline_gateway(), None, env={}) is None
    assert make_preopen(offline_gateway(), None, env={"KAIBOT_ROOM": "on"}) is None
    assert make_preopen(offline_gateway(), None, env={"KAIBOT_PREOPEN_REVIEW": "on"}) is None
    assert not preopen.enabled({})
    assert isinstance(make_preopen(offline_gateway(), None, env=ON_ENV), PreopenDesk)


# ---------------------------------------------------------------- QA-Ops second key (#248): sales never wait


class _Hang:
    """A reviewer that does not answer (a hung model call); `gate.set()` lets the worker end."""

    def __init__(self):
        import threading

        self.gate, self.calls = threading.Event(), 0

    def __call__(self, session, cands):
        self.calls += 1
        self.gate.wait(30)


class LateHarness(ReviewHarness):
    """Sessions before `switch` as the engine runs them with an allowing review. From `switch` on, the engine is
    (re)started at 15:55 ET, in the last minutes before the send window closes (15:57), and the review never
    answers."""

    def __init__(self, *a, switch: int, hang, **kw):
        self.switch, self.hang = switch, hang
        super().__init__(*a, **kw)

    def run(self) -> None:
        d = self.d
        self.live.after_close(d.day(I0 - 1))
        for i in range(I0, self.switch + 1):
            day = d.day(i)
            self.account.recon.check("session_start")
            if i < self.switch:
                self.clock.set(_at(day, time(8, 45)))
                self.live.preopen()
                ticks = [time(15, m) for m in (49, 50, 51, 52, 53, 55, 56, 57, 58)]
            else:
                self.live.review = PreopenDesk(self.hang, offline_gateway(), env=ON_ENV, needs_key=False,
                                               clock=lambda: self.clock().timestamp())
                ticks = [time(15, 55, 7), time(15, 56, 7)]  # one tick a minute: the last one inside the window
            self.clock.set(_at(day, time(9, 30)))
            self.broker.advance(_at(day, time(9, 30)))
            for t in ticks:
                self.clock.set(_at(day, t))
                self.live.sync()
                self.live.send(day)
            self.clock.set(_at(day, time(15, 59)))
            self.broker.advance(_at(day, time(15, 59)))
            self.clock.set(_at(day, time(16, 30)))
            self.live.sync()
            self.live.sync(force=True)
            self.account.ledger.apply("mark", self.mark(i))
            self.journal.append("mark", self.mark(i))
            self.live.after_close(day)


def test_a_review_that_cannot_finish_before_the_cutoff_never_holds_back_the_sales(runs, data, tmp_path):
    """Room ON, the review hung, the engine's only tick left in the send window: no review is started
    (no model call), it is journalled as a timeout, the session's sales go out exactly as the rules decided on that
    tick, and no buy does."""
    ref = runs["ref"]
    sold = {}
    for day, sym, side, qty, _ in venue_orders(ref.broker):
        if side == "sell":
            sold.setdefault(day, []).append((sym, qty))
    idx = next(i for i in range(I0 + 5, I1) if str(data.day(i)) in sold
               and any(o[0] == str(data.day(i)) and o[2] == "buy" for o in venue_orders(ref.broker)))
    day = str(data.day(idx))
    hang = _Hang()
    lim = momentum_limits(S, POL, 100.0)
    h = LateHarness(data, S, ld.live_builder(S, lim), tmp_path, review=desk(DeterministicReviewer()), switch=idx,
                    hang=hang)
    try:
        h.run()
    finally:
        hang.gate.set()
    got = [(o[1], o[3]) for o in venue_orders(h.broker) if o[0] == day and o[2] == "sell"]
    assert sorted(got) == sorted(sold[day])  # every sale of the session, as the rules decided
    assert not [o for o in venue_orders(h.broker) if o[0] == day and o[2] == "buy"]
    rec = [b for _, b in entries(h, "md_review") if b["session"] == day]
    assert len(rec) == 1 and rec[0]["status"] == "timeout"
    send = [b for _, b in entries(h, "md_send") if b["session"] == day]
    assert send and send[0]["review"]["status"] == "timeout"
    assert hang.calls == 0  # nothing started that could not be waited for
