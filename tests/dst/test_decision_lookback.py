"""Audit 2, ticket 5.11 (#691): a decision whose daily prices arrive only after its fill session is taken late, not
dropped; one that will never be taken is journalled once as lost.

The weekly rebalance of the scenario is decided after D1's close for D2 (the world's rule decides only then). The
daily store is refreshed 30 hours late: D1's bars are in at 22:10 on D2, after D2's session. D2's evening journals
"not a decision day" for D3, so before the fix D3's open found a record one session back and the rebalance was gone.
Now D3 looks back, takes D1's decision (marked late), and sends it in D3's window (M9 1.4: the due plan, at D3's
prices)."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from scenarios import ON_TIME, quiet
from world import D0, D1, D2, D3, et, run_scenario

from kaibot.engine import live_daily as ld

LATE = et(D1, "16:10") + timedelta(hours=30)  # 22:10 ET on D2: after D2's session has closed
DELAYED = {**ON_TIME, D1: LATE, D2: LATE}  # the next refresh brings both evenings at once
NEVER = et(D3, "23:59") + timedelta(days=30)


def _journal(res, kind):
    return [t for t in res.trace if " journal " in t and f" {kind} " in t]


def test_a_30_hour_fetch_delay_takes_the_weekly_rebalance_late_not_dropped():
    res = run_scenario(quiet({D2: ("B",)}, ready=DELAYED))
    assert res.ok, res.report()
    decisions = [t for t in _journal(res, "md_decision") if f"md_decision session={D1} " in t]
    assert len(decisions) == 1 and "late=True" in decisions[0] and f"fill_session={D2}" in decisions[0], res.report()
    assert decisions[0].startswith(f"{D3[5:]} "), decisions  # taken on D3, once the prices were in
    sends = [t for t in _journal(res, "md_send") if f"session={D3}" in t]
    assert len(sends) == 1 and f"late_of={D2}" in sends[0] and "'B'" in sends[0], res.report()
    assert any(" fill " in t and "side=buy" in t and "-B-" in t for t in res.trace), res.report()
    assert not _journal(res, "md_decision_lost")


def test_without_the_look_back_the_rebalance_is_dropped(monkeypatch):
    """The teeth: the same world with the look-back taken away holds nothing at the end (no decision for D2 was
    ever journalled, so the oracle expects nothing: the test checks the book itself)."""
    monkeypatch.setattr(ld.LiveDaily, "_lookback", lambda self, session: {})
    res = run_scenario(quiet({D2: ("B",)}, ready=DELAYED))
    assert not [t for t in _journal(res, "md_decision") if f"md_decision session={D1} " in t]
    assert not any(" fill " in t and "side=buy" in t for t in res.trace), res.report()


def test_a_decision_a_newer_decision_day_replaced_is_journalled_lost_once():
    """D1's prices never come; D2's decision (for D3) is on time and replaces it. At D3's open the missed D1 is
    journalled lost once (not again at later tries), and D3 trades D2's decision as before."""
    res = run_scenario(quiet({D2: ("B",), D3: ("C",)}, ready={**ON_TIME, D1: NEVER}))
    assert res.ok, res.report()
    lost = _journal(res, "md_decision_lost")
    assert len(lost) == 1 and f"session={D1}" in lost[0] and "replaced" in lost[0], res.report()
    assert not [t for t in _journal(res, "md_decision") if f"md_decision session={D1} " in t]


def test_a_quiet_week_on_time_never_looks_back_or_loses_anything():
    res = run_scenario(quiet({D1: ("B",), D2: ("B", "C")}))
    assert res.ok, res.report()
    assert not _journal(res, "md_decision_lost")
    assert not [t for t in _journal(res, "md_decision") if "late=True" in t and f"session={D0}" not in t]


# ---------------------------------------------------------------- the bound, on the look-back alone
class _Journal:
    def __init__(self, decisions):
        self.rows = {"md_decision": list(decisions), "md_decision_lost": []}

    def find(self, kind, **eq):
        return [b for b in self.rows.get(kind, []) if all(b.get(k) == v for k, v in eq.items())]

    def append(self, kind, body):
        self.rows.setdefault(kind, []).append(body)
        return len(self.rows[kind])


def _daily(decisions, on=(date(2026, 8, 4),)):
    """A stand-in with what `_lookback` reads: weekday sessions, one rule deciding after the closes `on` only."""

    def prev(d):
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    def nxt(d):
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d

    rule = SimpleNamespace(name="r", strategy=SimpleNamespace(decision_day=lambda p, f: p in on))
    events = []
    j = _Journal(decisions)
    self = SimpleNamespace(account=SimpleNamespace(run_id=lambda: 7, book_ended=lambda: False), journal=j,
                           prev_session=prev, next_session=nxt, active=lambda: [rule], events=events,
                           event=lambda k, b: events.append((k, b)), now=lambda: et(D3, "10:00"))
    self.decision = lambda name, c: (j.find("md_decision", strategy=name, session=str(c)) or [None])[-1]
    self._decision_lost = lambda *a: ld.LiveDaily._decision_lost(self, *a)
    self._entries = lambda kind: j.rows.get(kind, [])
    return self


def _rec(day: str, run: int = 7):
    return {"run_id": run, "strategy": "r", "session": day, "target": None}


def test_the_look_back_takes_the_latest_missed_decision_day_within_the_bound():
    # 2026-08-04 is the decision day and has no record; 08-03 was decided in this run; 08-04 is 5 sessions back
    d = _daily([_rec(x) for x in ("2026-08-03", "2026-08-05", "2026-08-06", "2026-08-07", "2026-08-10")])
    assert ld.LiveDaily._lookback(d, date(2026, 8, 11)) == {date(2026, 8, 4): {"r"}}
    assert not d.journal.rows["md_decision_lost"]


def test_beyond_the_bound_the_missed_decision_is_lost_once():
    recs = [_rec(x) for x in ("2026-08-03", "2026-08-05", "2026-08-06", "2026-08-07", "2026-08-10", "2026-08-11")]
    d = _daily(recs)
    assert ld.LiveDaily._lookback(d, date(2026, 8, 12)) == {}  # 08-04 is now the sixth session back
    assert ld.LiveDaily._lookback(d, date(2026, 8, 12)) == {}
    (lost,) = d.journal.rows["md_decision_lost"]
    assert lost["session"] == "2026-08-04" and "within 5 sessions" in lost["why"]
    assert [k for k, _ in d.events] == ["multi_day_decision_lost"]


def test_no_record_of_this_run_before_the_gap_means_nothing_to_make_up():
    # a new run (or a new journal) since: the older decision day was never this run's
    assert ld.LiveDaily._lookback(_daily([_rec("2026-08-05"), _rec("2026-08-06")]), date(2026, 8, 7)) == {}
    assert ld.LiveDaily._lookback(_daily([_rec("2026-08-03", run=6), _rec("2026-08-06")]), date(2026, 8, 7)) == {}
    assert ld.LiveDaily._lookback(_daily([]), date(2026, 8, 7)) == {}


def test_a_failing_look_back_never_costs_the_previous_sessions_late_decision(monkeypatch):
    """Second-key review: the look-back raising (a calendar or journal read) is one error event, and the M9 1.3 late
    decision of the previous session is still taken as before."""

    def broken(self, session):
        raise RuntimeError("calendar unreadable")

    monkeypatch.setattr(ld.LiveDaily, "_lookback", broken)
    res = run_scenario(quiet({D1: ("B",)}))  # the run starts after D0's close: D1's decision is the late one
    assert res.ok, res.report()
    assert any(" fill " in t and "side=buy" in t and "-B-" in t for t in res.trace), res.report()
