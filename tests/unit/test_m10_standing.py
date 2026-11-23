"""M10 1.19 (#444), standing targets (engine/standing.py): one record per decision says what it still owes. The
lookups of M9 1.3 and 1.4 (`LiveDaily._latest`, `_sent_plan`, `_unsent`) are views of it; these tests pin the
record itself, on journal entries written out by hand."""

from __future__ import annotations

from kaibot.engine import standing as st

RID = 7


def dec(session, fill, target=True, run=RID, rule="trend_momentum", **kw):
    return {"run_id": run, "strategy": rule, "session": session, "fill_session": fill,
            "target": {"weights": [["B", 1.0]]} if target else None, "held": {}, **kw}


def send(session, targets, run=RID, rule="trend_momentum", equity=10_000.0, **kw):
    body = {"run_id": run, "strategy": rule, "session": session, "targets": targets, **kw}
    if equity is not None:
        body["equity"] = equity
    return body


D1 = dec("2026-08-03", "2026-08-04")
PLAN = [["B", 40, "buy", "ranked 1"], ["C", 0, "sell", "left the top"], ["D", 25, "resize", "resize"]]


def test_a_decision_is_due_until_its_plan_is_sent():
    s = st.latest([D1], [], "trend_momentum", RID)
    assert (s.decided, s.fill_session, s.state(), s.sent_on, s.plan()) == ("2026-08-03", "2026-08-04", st.DUE,
                                                                           None, [])
    assert s.summary() == {"decided": "2026-08-03", "fill_session": "2026-08-04", "state": "due", "sent_on": None}


def test_sent_then_met_when_no_buy_is_short():
    s = st.latest([D1], [send("2026-08-04", PLAN)], "trend_momentum", RID)
    assert s.state() == st.SENT and s.sent_on == "2026-08-04" and s.late_of is None
    assert s.short({}) == {"B": 40.0, "D": 25.0}  # a sale is never "short"
    assert s.short({"B": 40, "D": 10}) == {"D": 15.0} and s.state({"B": 40, "D": 10}) == st.SENT
    assert s.state({"B": 40, "D": 25, "C": 3}) == st.MET
    assert s.summary({"B": 40})["short"] == ["D"]


def test_a_send_without_the_builders_plan_is_not_the_decisions_send():
    """The view had gone away on the fill session: only pending sales went out (no `equity`). Still due."""
    only_sales = send("2026-08-04", [["C", 0, "sell", "pending"]], equity=None)
    assert st.latest([D1], [only_sales], "trend_momentum", RID).state() == st.DUE


def test_a_late_send_is_the_plan_and_its_left_out_buys_are_still_owed():
    late = send("2026-08-05", [["B", 38, "buy", "ranked 1"]], late_of="2026-08-04",
                late={"due": [["E", 12, "buy"]], "skipped": {"E": "market filter off"}})
    s = st.latest([D1], [late], "trend_momentum", RID)
    assert (s.state(), s.sent_on, s.late_of) == (st.SENT, "2026-08-05", "2026-08-04")
    assert s.plan() == [("B", 38, "buy"), ("E", 12, "buy")] and s.short({"B": 38}) == {"E": 12.0}
    assert s.summary({"B": 38}) == {"decided": "2026-08-03", "fill_session": "2026-08-04", "state": "sent",
                                    "sent_on": "2026-08-05", "late_of": "2026-08-04", "short": ["E"]}


def test_a_newer_decision_replaces_an_older_one_sent_or_not():
    d2 = dec("2026-08-10", "2026-08-11")
    old, new = st.fold([D1, d2], [], "trend_momentum", RID)
    assert (old.state(), old.replaced_by, new.state(), new.replaced_by) == (st.REPLACED, "2026-08-10", st.DUE, None)
    assert st.latest([D1, d2], [send("2026-08-04", PLAN)], "trend_momentum", RID).decision is d2
    # "not a decision day" records replace nothing: they carry no target list
    quiet = dec("2026-08-04", "2026-08-05", target=False)
    assert st.latest([D1, quiet], [], "trend_momentum", RID).decision is D1
    assert [s.decided for s in st.fold([D1, quiet, d2], [], "trend_momentum", RID)] == ["2026-08-03", "2026-08-10"]


def test_only_this_runs_and_this_rules_entries_count_and_only_fills_before_the_session():
    other_run, other_rule = dec("2026-08-03", "2026-08-04", run=8), dec("2026-08-03", "2026-08-04", rule="x")
    assert st.latest([other_run, other_rule], [], "trend_momentum", RID) is None
    s = st.latest([D1], [send("2026-08-04", PLAN, run=8), send("2026-08-04", PLAN, rule="x")], "trend_momentum", RID)
    assert s.state() == st.DUE  # another run's send of the same day is not this decision's
    # on its own fill session a decision is not yet "the latest before": that session's plan comes from `_plan`
    assert st.latest([D1], [], "trend_momentum", RID, before="2026-08-04") is None
    assert st.latest([D1], [], "trend_momentum", RID, before="2026-08-05").decision is D1


def test_the_last_matching_send_wins_as_the_journal_orders_them():
    a, b = send("2026-08-04", PLAN), send("2026-08-04", [["B", 41, "buy", "again"]])
    assert st.latest([D1], [a, b], "trend_momentum", RID).send is b
