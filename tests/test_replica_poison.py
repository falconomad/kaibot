"""M9 ticket 1.12 (#437, audit D1): a row Supabase permanently refuses no longer blocks every later row, and a
backlog that grows for ten minutes raises an alarm."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from test_journal import Remote

from kaibot.engine.journal import (
    BACKLOG_ALARM_S,
    POISON_AFTER_N,
    POISON_AFTER_S,
    Journal,
    JournalStore,
    Replicator,
)
from kaibot.engine.store import StoreError

CHECK_400 = ('POST {t} -> 400: {{"code":"23514","details":"Failing row contains (...).","hint":null,'
             '"message":"new row for relation \\"{t}\\" violates check constraint \\"{t}_amount_check\\""}}')
COLUMN_400 = ('POST {t} -> 400: {{"code":"PGRST204","details":null,"hint":null,'
              '"message":"Could not find the \'brand_new\' column of \'{t}\' in the schema cache"}}')


class Refusing(Remote):
    """Supabase that refuses some inserts for good, the way PostgREST answers."""

    def __init__(self):
        super().__init__()
        self.refuse: dict = {}  # table -> (predicate on the row, the error text)

    def insert(self, table, row, returning=True):
        if table in self.refuse:
            bad, text = self.refuse[table]
            if bad(row):
                self._gate()
                raise StoreError(text.format(t=table))
        return super().insert(table, row, returning)


def _setup(tmp_path):
    remote = Refusing()
    js = JournalStore(Journal(tmp_path / "j.sqlite"), remote, live=True)
    run = js.insert("run", {"amount_usd": 5000, "status": "running", "level": "balanced"})
    rep = Replicator(js.journal, remote)
    t = SimpleNamespace(now=1000.0)
    rep.clock = lambda: t.now
    assert rep.pump() == 1
    return remote, js, run, rep, t


def _refuse_for_good(rep, t, passes=POISON_AFTER_N, over=POISON_AFTER_S):
    """`passes` replication passes spread over `over` seconds."""
    for _ in range(passes):
        t.now += over / (passes - 1) if passes > 1 else over
        rep.pump()


def test_a_row_supabase_refuses_for_good_is_skipped_recorded_and_the_rows_after_it_replicate(tmp_path):
    remote, js, run, rep, t = _setup(tmp_path)
    remote.refuse["halt"] = (lambda r: r["kind"] == "bad", CHECK_400)
    js.insert("halt", {"run_id": run["id"], "kind": "bad", "reason": "violates a constraint"}, returning=False)
    js.insert("halt", {"run_id": run["id"], "kind": "drift", "reason": "fine"}, returning=False)
    js.insert("event", {"run_id": run["id"], "type": "after", "detail": {}}, returning=False)

    assert rep.pump() == 0 and "23514" in rep.last_error and rep.pending() == 3  # what main did, for ever
    assert rep.take_poisons() == [] and not js.journal.entries(kinds=("replica_poison",))

    _refuse_for_good(rep, t)
    assert rep.pending() == 0 and rep.last_error is None
    assert [h["reason"] for h in remote.tables["halt"]] == ["fine"]
    assert [e["type"] for e in remote.tables["event"]] == ["after"]
    (p,) = rep.take_poisons()
    assert p["table"] == "halt" and p["op"] == "insert" and "23514" in p["error"]
    assert p["attempts"] >= POISON_AFTER_N and rep.take_poisons() == []
    (e,) = js.journal.entries(kinds=("replica_poison",))
    assert e[3]["seq"] == p["seq"] and e[3]["row"]["kind"] == "bad"  # the journal keeps the row and says so
    assert rep.status()["poisons"] == 1


def test_a_refusal_is_not_poison_before_both_the_count_and_the_time_are_reached(tmp_path):
    remote, js, run, rep, t = _setup(tmp_path)
    remote.refuse["halt"] = (lambda r: True, CHECK_400)
    js.insert("halt", {"run_id": run["id"], "kind": "bad", "reason": "x"}, returning=False)
    for _ in range(POISON_AFTER_N * 3):  # many refusals inside a few seconds (PostgREST's schema cache reloading)
        t.now += 1
        rep.pump()
    assert rep.pending() == 1 and not rep.take_poisons()
    t.now += POISON_AFTER_S
    remote.refuse.clear()  # it was a passing state: the row goes out
    assert rep.pump() == 1 and not rep.take_poisons()
    assert [h["kind"] for h in remote.tables["halt"]] == ["bad"]


@pytest.mark.parametrize("text", [
    "POST {t}: <urlopen error [Errno 111] Connection refused>",  # no answer at all
    "POST {t}: The read operation timed out",
    'POST {t} -> 503: {{"message":"upstream connect error"}}',
    'POST {t} -> 500: {{"message":"internal"}}',
    'POST {t} -> 401: {{"message":"JWT expired"}}',  # the key, not the row: every row would be dropped
    'POST {t} -> 403: {{"message":"permission denied for table {t}"}}',
    'POST {t} -> 429: {{"message":"rate limit"}}',
    # QA-Ops (#516): a 4xx that refuses EVERY row is not a row's fault; skipping would empty the whole backlog
    'POST {t} -> 405: {{"code":"25006","message":"cannot execute INSERT in a read-only transaction"}}',
    'POST {t} -> 402: {{"message":"Service for this project is restricted due to exceeding usage limits"}}',
    'POST {t} -> 404: <html><body>Not Found</body></html>',
    'POST {t} -> 400: Bad Request',
    'POST {t} -> 406: {{"code":"PGRST106","message":"The schema must be one of the following: public"}}',
    'POST {t} -> 409: {{"code":"55P03","message":"could not obtain lock on relation"}}',
    'POST {t} -> 409: {{"code":"40001","message":"could not serialize access due to concurrent update"}}',
])
def test_an_outage_or_a_refused_key_never_makes_a_row_poison(tmp_path, text):
    remote, js, run, rep, t = _setup(tmp_path)
    remote.refuse["halt"] = (lambda r: True, text)
    js.insert("halt", {"run_id": run["id"], "kind": "drift", "reason": "x"}, returning=False)
    _refuse_for_good(rep, t, passes=POISON_AFTER_N * 4, over=POISON_AFTER_S * 20)
    assert rep.pending() == 1 and not rep.take_poisons() and rep.last_error
    remote.refuse.clear()
    assert rep.pump() == 1 and [h["kind"] for h in remote.tables["halt"]] == ["drift"]


def test_rows_refused_for_the_same_reason_follow_at_once_and_are_reported_once(tmp_path):
    """A column added in code before its migration: every row of the table is refused. The first one waits out the
    count and the time; the ones behind it must not each wait again, or the replica falls two minutes behind per
    row. One alarm, every row journalled."""
    remote, js, run, rep, t = _setup(tmp_path)
    remote.refuse["gate_run"] = (lambda r: True, COLUMN_400)
    for i in range(4):
        js.insert("gate_run", {"run_id": run["id"], "brand_new": i}, returning=False)
    js.insert("event", {"run_id": run["id"], "type": "after", "detail": {}}, returning=False)
    _refuse_for_good(rep, t)
    assert rep.pending() == 0 and [e["type"] for e in remote.tables["event"]] == ["after"]
    assert len(js.journal.entries(kinds=("replica_poison",))) == 4
    assert len(rep.take_poisons()) == 1 and rep.status()["poisons"] == 4
    # the migration is applied: the next row goes out, nothing is skipped
    remote.refuse.clear()
    js.insert("gate_run", {"run_id": run["id"], "brand_new": 9}, returning=False)
    assert rep.pump() == 1 and len(js.journal.entries(kinds=("replica_poison",))) == 4


def test_the_count_starts_again_for_another_row(tmp_path):
    remote, js, run, rep, t = _setup(tmp_path)
    remote.refuse["halt"] = (lambda r: r["kind"] == "a", CHECK_400)
    js.insert("halt", {"run_id": run["id"], "kind": "a", "reason": "x"}, returning=False)
    _refuse_for_good(rep, t, passes=POISON_AFTER_N - 1)
    remote.refuse.clear()
    assert rep.pump() == 1  # it went out after all
    remote.refuse["segment"] = (lambda r: True, CHECK_400)
    js.insert("segment", {"run_id": run["id"], "level": "balanced"}, returning=False)
    t.now += POISON_AFTER_S
    rep.pump()
    assert rep.pending() == 1 and not rep.take_poisons(), "one refusal of a new row is one refusal"


def test_a_backlog_that_grows_for_ten_minutes_raises_one_alarm_and_rearms_when_it_clears(tmp_path):
    remote, js, run, rep, t = _setup(tmp_path)
    assert rep.backlog_alarm() is None
    remote.down = True
    for i in range(11):  # a row a minute, none sent
        js.insert("event", {"run_id": run["id"], "type": f"e{i}", "detail": {}}, returning=False)
        rep.pump()
        got = rep.backlog_alarm()
        if i < 10:
            assert got is None, i
        t.now += 60
    assert got["pending"] == 11 and got["minutes"] >= 10 and "Connection refused" in got["error"]
    assert rep.status()["backlog_alarm"] is True
    t.now += 600
    assert rep.backlog_alarm() is None, "one alarm per stall"
    remote.down = False
    assert rep.pump() == 11
    assert rep.backlog_alarm() is None and rep.status()["backlog_alarm"] is False
    remote.down = True  # a second stall alarms again
    js.insert("event", {"run_id": run["id"], "type": "again", "detail": {}}, returning=False)
    assert rep.backlog_alarm() is None
    t.now += BACKLOG_ALARM_S
    assert rep.backlog_alarm()["pending"] == 1


def test_a_backlog_that_is_being_worked_off_is_not_an_alarm(tmp_path):
    remote, js, run, rep, t = _setup(tmp_path)
    for i in range(40):
        js.insert("event", {"run_id": run["id"], "type": f"e{i}", "detail": {}}, returning=False)
    for _ in range(20):  # slow, but every look finds fewer rows waiting
        rep.pump(max_n=2)
        assert rep.backlog_alarm() is None
        t.now += 60


def test_the_engine_turns_a_poison_row_and_a_backlog_into_events_a_halt_row_and_log_lines(tmp_path, caplog):
    from kaibot.engine.__main__ import report_conflicts

    remote, js, run, rep, t = _setup(tmp_path)
    eng = SimpleNamespace(store=js, _rid=lambda: run["id"])
    remote.refuse["halt"] = (lambda r: r["kind"] == "bad", CHECK_400)
    js.insert("halt", {"run_id": run["id"], "kind": "bad", "reason": "x"}, returning=False)
    _refuse_for_good(rep, t)
    with caplog.at_level(logging.ERROR):
        report_conflicts(eng, rep)
    ev = [e for e in js.tables["event"] if e["type"] == "replica_poison"]
    assert len(ev) == 1 and ev[0]["payload"]["table"] == "halt" and "23514" in ev[0]["payload"]["error"]
    assert [h["kind"] for h in js.tables["halt"]][-1] == "replica_poison"
    assert "replica poison" in caplog.text
    rep.pump()
    assert any(e["type"] == "replica_poison" for e in remote.tables["event"]), "the alarm itself reaches Supabase"

    remote.down = True
    js.insert("event", {"run_id": run["id"], "type": "x", "detail": {}}, returning=False)
    report_conflicts(eng, rep)
    t.now += BACKLOG_ALARM_S
    caplog.clear()
    with caplog.at_level(logging.ERROR):
        report_conflicts(eng, rep)
        report_conflicts(eng, rep)
    ev = [e for e in js.tables["event"] if e["type"] == "replica_backlog"]
    assert len(ev) == 1 and ev[0]["payload"]["pending"] >= 1
    assert caplog.text.count("replica backlog") == 1


def test_a_cause_that_still_skips_rows_an_hour_later_is_announced_again(tmp_path):
    """QA-Ops (#516): the engine runs for days. Rows of a known cause follow at once, with no second wait; they
    must not also follow in silence for ever. One alarm per cause per hour while rows are being skipped."""
    from kaibot.engine.journal import POISON_REALARM_S

    remote, js, run, rep, t = _setup(tmp_path)
    remote.refuse["gate_run"] = (lambda r: True, COLUMN_400)
    js.insert("gate_run", {"run_id": run["id"], "brand_new": 0}, returning=False)
    _refuse_for_good(rep, t)
    assert len(rep.take_poisons()) == 1
    t.now += POISON_REALARM_S / 2
    js.insert("gate_run", {"run_id": run["id"], "brand_new": 1}, returning=False)
    assert rep.pump() == 1 and rep.take_poisons() == [], "inside the hour: skipped at once, journalled, no alarm"
    t.now += POISON_REALARM_S
    js.insert("gate_run", {"run_id": run["id"], "brand_new": 2}, returning=False)
    js.insert("gate_run", {"run_id": run["id"], "brand_new": 3}, returning=False)
    assert rep.pump() == 2
    (again,) = rep.take_poisons()
    assert again["table"] == "gate_run" and again["first_of_its_kind"] is False
    assert len(js.journal.entries(kinds=("replica_poison",))) == 4 and rep.status()["poisons"] == 4


def test_the_documented_rebuild_sends_the_skipped_row_once_the_cause_is_fixed_and_duplicates_nothing(tmp_path):
    """QA-Ops (#516): docs/ops/ledger.md says "fix the cause, stop the engine, run --rebuild". Proven here."""
    remote, js, run, rep, t = _setup(tmp_path)
    remote.refuse["halt"] = (lambda r: r["kind"] == "bad", CHECK_400)
    js.insert("halt", {"run_id": run["id"], "kind": "bad", "reason": "was refused"}, returning=False)
    js.insert("halt", {"run_id": run["id"], "kind": "drift", "reason": "fine"}, returning=False)
    _refuse_for_good(rep, t)
    assert [h["kind"] for h in remote.tables["halt"]] == ["drift"]
    remote.refuse.clear()  # the migration was applied
    again = Replicator(js.journal, remote)  # `python -m kaibot.engine.journal --rebuild`, the engine stopped
    again.rebuild()
    assert sorted(h["kind"] for h in remote.tables["halt"]) == ["bad", "drift"]
    assert len(remote.tables["run"]) == 1 and again.pending() == 0 and again.status()["poisons"] == 0
