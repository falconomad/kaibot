"""#551 on the loops of M10 1.19 (#444): the multi-day book's own loop (engine/book_loop.py, `Engine._tick`) and the
intraday path (engine/intraday.IntradayEngine, which overrides `_tick`) both run under `Engine.tick`'s check, so a
damaged journal entry read by the daily path stops the pass at that step (JournalCorrupt: the main loop writes
`engine_error`, stops and exits with code 4), whichever loop runs, and nothing after it in that pass runs."""

from __future__ import annotations

import sqlite3

import pytest
from test_live_daily_engine import DailyAccount
from test_m10_book_loop import LoopAccount

from kaibot.engine.core import Engine
from kaibot.engine.intraday import IntradayEngine
from kaibot.engine.journal import JournalCorrupt


def test_no_loop_bypasses_the_check():
    """`tick` is defined once (Engine): a subclass that overrode it would run without the journal-damage check."""
    assert "tick" not in IntradayEngine.__dict__ and "_tick" in IntradayEngine.__dict__
    assert "_journal_intact" in Engine.tick.__code__.co_names


def _damage_last(a) -> int:
    seq = a.eng.journal.entries()[-1][0]
    db = sqlite3.connect(a.jpath)
    db.execute("drop trigger entry_no_update")  # a damaged disk, not SQL
    db.execute("update entry set body = replace(body, '\"', '''') where seq = ?", (seq,))
    db.commit()
    db.close()
    return seq


@pytest.mark.parametrize("cls", [LoopAccount, DailyAccount], ids=["book_loop", "intraday"])
def test_a_damaged_entry_read_by_the_daily_path_stops_the_pass(cls, tmp_path, monkeypatch):
    a = cls(tmp_path, monkeypatch)
    assert isinstance(a.eng, IntradayEngine) is (cls is DailyAccount)
    a.ticks(a.open1, 0, 30)
    seq = _damage_last(a)
    eng, after = a.eng, []
    for name in ("_check_drift", "_risk_halts", "_ledger", "_recheck_reconcile"):
        real = getattr(eng, name)

        def spy(*x, _real=real, _name=name, **k):
            if eng.journal.damage is not None:
                after.append(_name)
            return _real(*x, **k)

        monkeypatch.setattr(eng, name, spy)
    real_sync = eng.daily.sync
    monkeypatch.setattr(eng.daily, "sync", lambda *x, **k: (eng.journal.entries(),
                                                             real_sync(*x, **k)))
    with pytest.raises(JournalCorrupt, match=str(seq)):
        a.ticks(a.open1, 30, 31)
    assert after == []
    with pytest.raises(JournalCorrupt):  # and on every later pass, whatever it reads
        a.ticks(a.open1, 31, 32)


@pytest.mark.parametrize("cls", [LoopAccount, DailyAccount], ids=["book_loop", "intraday"])
def test_a_daily_step_that_catches_the_damage_itself_stops_the_pass(cls, tmp_path, monkeypatch):
    """QA-Ops on #564: the daily sync catches the error itself and returns normally (no raise reaches
    `_daily_step`): the pass stops at that step all the same, on both loops."""
    a = cls(tmp_path, monkeypatch)
    a.ticks(a.open1, 0, 30)
    _damage_last(a)
    eng, after = a.eng, []
    for name in ("_check_drift", "_risk_halts", "_ledger", "_recheck_reconcile"):
        real = getattr(eng, name)

        def spy(*x, _real=real, _name=name, **k):
            if eng.journal.damage is not None:
                after.append(_name)
            return _real(*x, **k)

        monkeypatch.setattr(eng, name, spy)

    def sync_that_swallows(*x, **k):
        try:
            eng.journal.entries()
        except JournalCorrupt:
            return None

    monkeypatch.setattr(eng.daily, "sync", sync_that_swallows)
    with pytest.raises(JournalCorrupt):
        a.ticks(a.open1, 30, 31)
    assert after == []
