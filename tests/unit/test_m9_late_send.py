"""M9 ticket 1.4 (audit finding M2): a decision whose send window was missed is sent on the next session, once.

Before the fix the 15:50-15:57 ET send of a decision happened on its fill session or never: without that session's
`md_send` the carry-over had no plan to read, so neither the decision's sales nor its buys went out until the next
decision (a week for trend_momentum, a month for mom_12_1_band). The live daily path on `SimBroker` and `SimClock`
(the harness of test_m9_late_decision.py), with the process down over a fill session's window.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import numpy as np
import pytest
from test_live_daily import EQ0, RID, _at
from test_m9_late_decision import WINDOW, Days, S, first_buy_day

from kaibot.broker.sim import NY
from kaibot.engine import daily
from kaibot.engine import daily_report as dr
from kaibot.engine.daily import DailyData


@pytest.fixture(scope="module")
def data() -> DailyData:
    m, _ = dr.load_fixture(dr.FIXTURE)
    return DailyData.from_market(m)


@pytest.fixture(scope="module")
def fill(data, tmp_path_factory) -> int:
    return first_buy_day(data, tmp_path_factory.mktemp("probe"))


def orders_on(h, day) -> list[dict]:
    return [o for o in h.broker.orders.values()
            if datetime.fromisoformat(o["submitted_at"]).astimezone(NY).date() == day]


def late_sends(h) -> list[dict]:
    return [b for b in h.sends() if b.get("late_of")]


def decision_of(h, day) -> dict:
    (dec,) = [b for b in h.decisions() if b["fill_session"] == str(day) and b["target"] is not None]
    return dec


def to_the_window(h, i: int) -> None:
    """Session i up to 15:49 ET (nothing sent yet); the sessions before it as `Days.run` drives them."""
    h.run(i - 1)
    day = h.d.day(i)
    h.account.recon.check("session_start")
    h.clock.set(_at(day, time(9, 30)))
    h.broker.advance(_at(day, time(9, 30)))
    h.tick(day, time(12, 0))


def window(h, i: int) -> None:
    for minute in WINDOW:
        h.tick(h.d.day(i), time(15, minute))


def test_a_missed_window_on_the_fill_day_is_sent_on_the_next_session_once(data, fill, tmp_path):
    day, nxt = data.day(fill), data.day(fill + 1)
    on_time, h = Days(data, tmp_path / "a"), Days(data, tmp_path / "b", no_window={fill})
    on_time.run(fill + 3)
    h.run(fill + 3)
    dec = decision_of(h, day)
    assert orders_on(h, day) == [] and not [b for b in h.sends() if b["session"] == str(day)]
    (late,) = late_sends(h)  # exactly one late send, on the next session
    assert (late["session"], late["late_of"], late["decided"]) == (str(nxt), str(day), dec["session"])
    assert late["run_id"] == RID and "equity" in late  # sized by the builder at that session's prices and cash
    sent = orders_on(h, nxt)
    bought = {o["symbol"] for o in sent if o["side"] == "buy"}
    wanted = {s for s, _ in dec["target"]["weights"]}
    assert bought and bought <= wanted
    assert bought == {s for s, q, p, _ in late["targets"] if p == "buy"}
    assert all("-buy-" in o["client_order_id"] and nxt.strftime("%Y%m%d") in o["client_order_id"]
               for o in sent if o["side"] == "buy")
    # the same names an on-time engine bought a session earlier (the share counts are that session's sizing)
    assert bought == {o["symbol"] for o in orders_on(on_time, day) if o["side"] == "buy"}
    # once: the sessions after it send nothing of the decision again
    per_symbol = [o["symbol"] for o in h.broker.orders.values() if o["side"] == "buy"]
    assert len(per_symbol) == len(set(per_symbol))
    assert not orders_on(h, data.day(fill + 2)) and not orders_on(h, data.day(fill + 3))
    held = {s: q for s, (q, _) in h.account.held(S.name).items()}
    assert held == {s: float(q) for s, q, p, _ in late["targets"] if p == "buy"}
    assert abs(h.account.cash() - on_time.account.cash()) < 0.05 * EQ0 and h.account.cash() < 0.8 * EQ0
    kinds = [k for k, _ in h.events]
    assert "multi_day_error" not in kinds
    plan = [b for k, b in h.events if k == "multi_day_plan" and b["session"] == str(nxt)]
    assert len(plan) == 1 and plan[0]["late_of"] == str(day)


def test_two_missed_windows_send_the_decision_on_the_third_session(data, fill, tmp_path):
    h = Days(data, tmp_path, no_window={fill, fill + 1})
    h.run(fill + 3)
    (late,) = late_sends(h)
    assert (late["session"], late["late_of"]) == (str(data.day(fill + 2)), str(data.day(fill)))
    assert not orders_on(h, data.day(fill)) and not orders_on(h, data.day(fill + 1))
    assert orders_on(h, data.day(fill + 2)) and not orders_on(h, data.day(fill + 3))


def test_a_restart_between_the_late_send_record_and_its_orders_sends_them_once(data, fill, tmp_path):
    h = Days(data, tmp_path, no_window={fill})
    to_the_window(h, fill + 1)
    day, rule = data.day(fill + 1), h.live.rules[0]
    h.clock.set(_at(day, time(15, 50)))
    h.journal.append("md_send", h.live._plan(rule, day, RID))  # the record is written; the process dies before an order
    assert len(late_sends(h)) == 1 and not orders_on(h, day)
    h.restart()
    for minute in (50, 51):
        h.tick(day, time(15, minute))
    h.restart()
    for minute in (52, 53, 55, 56, 57, 58):
        h.tick(day, time(15, minute))
    assert len(late_sends(h)) == 1
    buys = [o["symbol"] for o in orders_on(h, day) if o["side"] == "buy"]
    assert buys and len(buys) == len(set(buys))
    assert sorted(buys) == sorted(s for s, q, p, _ in late_sends(h)[0]["targets"] if p == "buy")


def test_a_late_send_buys_nothing_while_the_market_filter_is_off_and_the_buys_stay_due(data, fill, tmp_path,
                                                                                    monkeypatch):
    """The carry-over's first skip rule (Amendment 11, item 2): a buy is a buy of its own session, so none goes out
    while SPY closed below its 200-day average at the previous close. The late send's buys are then still due: the
    carry-over sends them on the first session the filter is on again, checked against cash and caps as any carry."""
    h = Days(data, tmp_path, no_window={fill})
    to_the_window(h, fill + 1)
    real = daily.market_on
    monkeypatch.setattr(daily, "market_on", lambda spy: False)
    window(h, fill + 1)
    monkeypatch.setattr(daily, "market_on", real)
    (late,) = late_sends(h)
    assert late["late"]["risk_off"] is True and not [t for t in late["targets"] if t[2] in ("buy", "resize")]
    assert late["late"]["due"] and set(late["late"]["skipped"].values()) == {"market filter off"}
    assert not orders_on(h, data.day(fill + 1))
    h.clock.set(_at(data.day(fill + 1), time(16, 30)))
    h.live.sync()
    h.account.ledger.apply("mark", h.mark(fill + 1))
    h.journal.append("mark", h.mark(fill + 1))
    h.live.after_close(data.day(fill + 1))
    to_the_window_from = fill + 2
    day = data.day(to_the_window_from)
    h.account.recon.check("session_start")
    h.clock.set(_at(day, time(9, 30)))
    h.broker.advance(_at(day, time(9, 30)))
    window(h, fill + 2)
    assert len(late_sends(h)) == 1  # the late send is not built a second time
    sent = orders_on(h, day)
    due = {s: q for s, q, _ in late["late"]["due"]}
    assert sent and all("-carry-" in o["client_order_id"] and o["side"] == "buy" for o in sent)
    assert all(float(o["qty"]) == due[o["symbol"]] for o in sent)
    carry = [b for b in h.sends() if b["session"] == str(day)][0]["carry"]
    assert carry["of"] == str(data.day(fill + 1)) and {c[0] for c in carry["carried"]} == {o["symbol"] for o in sent}


def test_a_late_send_leaves_out_a_stock_the_rule_could_not_buy_at_the_last_close(data, fill, tmp_path, monkeypatch):
    """Amendment 13's rule for a carried buy holds for a late one: outside the rule's ranked universe at the
    previous close it is not bought that session; the other buys go out."""
    h = Days(data, tmp_path, no_window={fill})
    to_the_window(h, fill + 1)
    dec = decision_of(h, data.day(fill))
    out = dec["target"]["weights"][0][0]
    j, k, real = data.col(out), fill, type(S).signals

    def signals(self, v):
        score, ok, sell_if = real(self, v)
        if v.k == k:  # the previous close of the late session
            ok = np.array(ok, copy=True)
            ok[j] = False
        return score, ok, sell_if

    monkeypatch.setattr(type(S), "signals", signals)
    window(h, fill + 1)
    (late,) = late_sends(h)
    assert late["late"]["skipped"] == {out: "not buyable: universe"}
    bought = {o["symbol"] for o in orders_on(h, data.day(fill + 1)) if o["side"] == "buy"}
    assert bought and out not in bought and out in {d[0] for d in late["late"]["due"]}
    events = [b for kind, b in h.events if kind == "multi_day_carry" and b["session"] == str(data.day(fill + 1))]
    assert len(events) == 1 and events[0]["late_of"] == str(data.day(fill)) and out in events[0]["skipped"]


def test_a_late_send_keeps_the_corporate_action_guard(data, fill, tmp_path):
    """Amendment 6: no buy of a stock with an announced corporate action, on a late send as on any send."""
    from types import SimpleNamespace

    h = Days(data, tmp_path, no_window={fill})
    to_the_window(h, fill + 1)
    dec = decision_of(h, data.day(fill))
    out = dec["target"]["weights"][0][0]
    action = SimpleNamespace(action="forward_split", on="2023-02-10", skip_text=lambda: "split announced")
    h.live.ca = SimpleNamespace(lookup=lambda prev, session, symbols: ({out: action}, None))
    window(h, fill + 1)
    (late,) = late_sends(h)
    assert [x[0] for x in late["ca_skipped"]] == [out]
    bought = {o["symbol"] for o in orders_on(h, data.day(fill + 1)) if o["side"] == "buy"}
    assert bought and out not in bought


def test_a_newer_decision_replaces_one_that_was_never_sent(data, fill, tmp_path):
    """Standing targets: the latest decision's targets are due until a newer decision exists. A session that is the
    fill session of a newer decision sends that one, by the usual path; the older one is not sent late."""
    h = Days(data, tmp_path, no_window={fill})
    to_the_window(h, fill + 1)
    day, rule = data.day(fill + 1), h.live.rules[0]
    old = decision_of(h, data.day(fill))
    one = old["target"]["weights"][0]
    newer = {**old, "session": str(data.day(fill)), "fill_session": str(day),
             "target": {**old["target"], "weights": [one]}}
    h.journal.append("md_decision", newer)  # a newer decision for this session, with one name
    h.clock.set(_at(day, time(15, 50)))
    planned = h.live._plan(rule, day, RID)
    assert "late_of" not in planned and [t[0] for t in planned["targets"] if t[2] == "buy"] == [one[0]]


def test_another_runs_unsent_decision_is_not_sent_late(data, fill, tmp_path):
    """Amendment 12, item 1: only a decision of the run that is running. An unsent decision of an earlier run stays
    unsent."""
    h = Days(data, tmp_path, no_window={fill})
    to_the_window(h, fill + 1)
    day, rule = data.day(fill + 1), h.live.rules[0]
    h.clock.set(_at(day, time(15, 50)))
    planned = h.live._plan(rule, day, RID + 1)
    assert "late_of" not in planned and planned["targets"] == []


# ---------------------------------------------------------------- QA-Ops second key on #518


def open_day(h, i: int) -> None:
    day = h.d.day(i)
    h.account.recon.check("session_start")
    h.clock.set(_at(day, time(9, 30)))
    h.broker.advance(_at(day, time(9, 30)))
    h.tick(day, time(12, 0))


def close_day(h, i: int) -> None:
    day = h.d.day(i)
    h.clock.set(_at(day, time(15, 59)))
    h.broker.advance(_at(day, time(15, 59)))
    h.clock.set(_at(day, time(16, 30)))
    h.live.sync()
    h.account.ledger.apply("mark", h.mark(i))
    h.journal.append("mark", h.mark(i))
    h.live.after_close(day)


def test_an_outage_from_before_the_window_to_ten_the_next_morning_sends_the_decision_that_day(data, fill, tmp_path):
    """The outage of 20 Nov 2026 a day earlier: the process is down from before the fill session's window until
    10:00 ET of the next session. That session's record is taken late (M9 1.3, "not a decision day") and the
    missed decision goes out in its window (M9 1.4), once."""
    on_time, h = Days(data, tmp_path / "a"), Days(data, tmp_path / "b", late={fill + 1}, no_window={fill})
    on_time.run(fill)
    h.run(fill + 3)
    day, nxt = data.day(fill), data.day(fill + 1)
    (rec,) = [b for b in h.decisions() if b["fill_session"] == str(nxt)]
    assert rec["late"] is True and rec["target"] is None
    (late,) = late_sends(h)
    assert (late["session"], late["late_of"]) == (str(nxt), str(day))
    buys = [o["symbol"] for o in orders_on(h, nxt) if o["side"] == "buy"]
    assert buys and sorted(buys) == sorted(o["symbol"] for o in orders_on(on_time, day) if o["side"] == "buy")
    assert not orders_on(h, day) and not orders_on(h, data.day(fill + 2)) and not orders_on(h, data.day(fill + 3))
    assert "multi_day_error" not in [k for k, _ in h.events]


def test_of_two_decisions_that_were_never_sent_only_the_newer_one_is_sent_late(data, fill, tmp_path):
    """Two fill sessions in a row lost their windows. The session after sends the newer decision only: nothing of
    the older target list goes out, that day or later."""
    h = Days(data, tmp_path, no_window={fill, fill + 1})
    to_the_window(h, fill + 2)
    old = decision_of(h, data.day(fill))
    one = old["target"]["weights"][1]
    h.journal.append("md_decision", {**old, "session": str(data.day(fill)), "fill_session": str(data.day(fill + 1)),
                                     "target": {**old["target"], "weights": [one]}})  # the newer one, one name
    assert len(old["target"]["weights"]) > 1
    window(h, fill + 2)
    close_day(h, fill + 2)
    open_day(h, fill + 3)
    window(h, fill + 3)
    close_day(h, fill + 3)
    (late,) = late_sends(h)
    assert (late["session"], late["late_of"]) == (str(data.day(fill + 2)), str(data.day(fill + 1)))
    assert [o["symbol"] for o in h.broker.orders.values()] == [one[0]]
    assert set(h.account.held(S.name)) == {one[0]}


def test_a_fill_session_that_sent_only_a_pending_sale_is_sent_late_and_no_sale_goes_out_twice(data, fill, tmp_path):
    """The daily view was gone in the fill session's window, so that session sent only a sale still pending from
    before (a send without the builder's plan). The decision is then still due: the next session sends it late.
    The pending sale that filled is not sent again, its stock is not bought back (it lost shares since the
    decision: Amendment 12, item 2), and the decision's own sale and buy go out once each."""
    f2 = fill + 5  # the fill session of the next weekly decision: it sells one holding and buys a new name
    h = Days(data, tmp_path)
    h.run(f2 - 1)
    dec = decision_of(h, data.day(f2))
    held = {s: q for s, (q, _) in h.account.held(S.name).items()}
    sells = set(dec["target"]["features"].get("sell") or {}) or set(held) - {s for s, _ in dec["target"]["weights"]}
    news = [s for s, _ in dec["target"]["weights"] if s not in held]
    x = next(s for s, _ in dec["target"]["weights"] if s in held and s not in sells)
    assert sells and news
    h.journal.append("md_send", {"run_id": RID, "strategy": S.name, "session": str(data.day(f2 - 1)),
                                 "decided": str(data.day(f2 - 2)), "targets": [[x, 0, "sell", "pending (test)"]]})
    h.restart()
    h.unready = {f2 - 1}  # the evening's view is gone on the fill session
    open_day(h, f2)
    window(h, f2)
    (first,) = [b for b in h.sends() if b["session"] == str(data.day(f2))]
    assert "equity" not in first and [t[:3] for t in first["targets"]] == [[x, 0, "sell"]]
    close_day(h, f2)
    h.unready = set()
    for i in (f2 + 1, f2 + 2, f2 + 3):
        open_day(h, i)
        window(h, i)
        close_day(h, i)
    (late,) = late_sends(h)
    assert (late["session"], late["late_of"]) == (str(data.day(f2 + 1)), str(data.day(f2))) and "equity" in late
    assert late["late"]["skipped"].get(x) == "sold since the decision"
    after = [o for o in h.broker.orders.values()
             if datetime.fromisoformat(o["submitted_at"]).astimezone(NY).date() >= data.day(f2)]
    sold = [(o["symbol"], float(o["qty"])) for o in after if o["side"] == "sell"]
    assert sorted(sold) == sorted([(x, held[x]), *[(s, held[s]) for s in sells]])  # each sale once, never more
    assert [o["symbol"] for o in orders_on(h, data.day(f2)) if o["side"] == "sell"] == [x]
    assert sorted(o["symbol"] for o in after if o["side"] == "buy") == sorted(news)  # once each; x not bought back
    assert x not in h.account.held(S.name) and not (sells & set(h.account.held(S.name)))
    assert "multi_day_error" not in [k for k, _ in h.events]


class QuotedDays(Days):
    """`Days` with the pre-send gate's quote source (M9 1.16): every stock quotes at the previous close a second
    ago, except (session, symbol) pairs in `stale`, whose quote is an hour old (halted, not trading)."""

    stale: set = set()

    def build(self) -> None:
        super().build()
        self.live.quotes = self.quotes

    def quotes(self, symbols):
        now, d = self.clock(), self.d
        day = now.astimezone(NY).date()
        i, out = d.index_of(day), {}
        for s in symbols:
            px = float(d.raw_c[i - 1, d.col(s)])
            if np.isfinite(px):
                age = timedelta(hours=1) if (str(day), s) in self.stale else timedelta(seconds=1)
                out[s] = {"bp": round(px - 0.01, 2), "ap": round(px + 0.01, 2),
                          "t": (now - age).isoformat().replace("+00:00", "Z")}
        return out


def test_a_late_buy_the_pre_send_check_withholds_is_carried_from_the_late_send_once(data, fill, tmp_path):
    """The pre-send gate (M9 1.16) and the late send together: a late buy whose quote is stale has no order that
    session; the session after carries it at the late send's own shares (the late send is the decision's plan), and
    no other buy of the decision goes out a second time."""
    day, nxt, after = data.day(fill), data.day(fill + 1), data.day(fill + 2)
    probe = Days(data, tmp_path / "a", no_window={fill})
    probe.run(fill + 1)
    halted = sorted(o["symbol"] for o in orders_on(probe, nxt) if o["side"] == "buy")[0]
    h = QuotedDays(data, tmp_path / "b", no_window={fill})
    h.stale = {(str(nxt), halted)}
    h.run(fill + 3)
    (late,) = late_sends(h)
    plan = {s: q for s, q, p, _ in late["targets"] if p == "buy"}
    assert late["late_of"] == str(day) and halted in plan
    held_back = [b for *_, b in h.journal.entries(kinds=("md_withheld",))]
    assert [(b["session"], b["symbol"], b["side"]) for b in held_back] == [(str(nxt), halted, "buy")]
    assert sorted(o["symbol"] for o in orders_on(h, nxt)) == sorted(set(plan) - {halted})
    (o,) = orders_on(h, after)
    assert (o["symbol"], float(o["qty"]), o["status"]) == (halted, float(plan[halted]), "filled")
    assert "-carry-" in o["client_order_id"]
    carry = [b for b in h.sends() if b["session"] == str(after)][0]["carry"]
    assert carry["of"] == str(nxt) and [c[0] for c in carry["carried"]] == [halted]
    assert not orders_on(h, data.day(fill + 3))
    bought = [x["symbol"] for x in h.broker.orders.values() if x["side"] == "buy"]
    assert sorted(bought) == sorted(plan)  # every name of the late send once
    assert {s: q for s, (q, _) in h.account.held(S.name).items()} == {s: float(q) for s, q in plan.items()}
