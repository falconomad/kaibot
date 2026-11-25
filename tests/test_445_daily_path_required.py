"""M10 1.20 step 2 (#445, Product ruling 24 Nov, option 2): the engine refuses to run multi-day sleeves without the
daily path. When the run's ledger holds multi-day sleeves and KAIBOT_MULTIDAY is off (or no multi-day rule is
enabled), `Engine.startup` fails closed with a message that names the switch, before any order call, start event or
heartbeat; engine/__main__ gives the lease back and exits with code 5. With the daily path, or with no sleeve, the
start is as before."""

from __future__ import annotations

import pytest
from test_failsafe_drills import DAY1, MD, Account
from test_live_daily_engine import DailyAccount

from kaibot.engine import __main__ as entry
from kaibot.engine.core import DailyPathMissing, Engine


def _hold_b(a) -> None:
    """40 shares of B in the multi-day book (booked as the order manager's daily path books them)."""
    a.ticks(a.open1, 0, 30)
    px = float(a.eng.market.last_close("B"))
    a.broker.pos["B"] = {"qty": 40, "avg_entry_price": px}
    a.eng._book("fill", {"run_id": a.rid, "strategy": MD, "symbol": "B", "side": "buy", "qty": 40, "px": px,
                         "fees": 0.0, "coid": f"kb{a.rid}-{MD}-B-20260803-buy-1", "session": DAY1})


def _without_daily_path(a) -> Engine:
    """A new engine process on the same journal and store, built as the live process builds it with
    KAIBOT_MULTIDAY off: no daily path."""
    a.eng.journal.close()
    eng = Account.engine(a)
    Account.quiet(eng)
    assert eng.daily is None
    return eng


@pytest.mark.parametrize("flag", [None, "off", "1", "true"])
def test_sleeves_and_the_switch_off_refuse_the_start_naming_the_switch(tmp_path, monkeypatch, flag):
    a = DailyAccount(tmp_path, monkeypatch)
    _hold_b(a)
    if flag is None:
        monkeypatch.delenv("KAIBOT_MULTIDAY", raising=False)
    else:
        monkeypatch.setenv("KAIBOT_MULTIDAY", flag)
    eng = _without_daily_path(a)
    orders, events = dict(a.broker.orders), len(a.store.tables["event"])
    seq = eng.journal.last_seq()
    with pytest.raises(DailyPathMissing, match=r"KAIBOT_MULTIDAY=on") as e:
        eng.startup()
    assert e.value.symbols == ["B"] and e.value.run_id == a.rid
    assert "B" in str(e.value) and "refusing to start" in str(e.value)
    assert a.broker.orders == orders  # nothing sent, changed or cancelled
    assert len(a.store.tables["event"]) == events  # no engine_start (no event at all)
    assert eng.journal.last_seq() == seq  # nothing journalled
    assert not [x for x in a.store.tables.get("heartbeat", [])]  # the engine wrote no heartbeat


def test_the_switch_on_without_an_enabled_rule_is_refused_too(tmp_path, monkeypatch):
    """KAIBOT_MULTIDAY=on but no multi-day rule enabled (`daily_path()` is None): still no daily path, so no start;
    the message says which half is missing."""
    a = DailyAccount(tmp_path, monkeypatch)
    _hold_b(a)
    eng = _without_daily_path(a)  # the flag is on (DailyAccount sets it)
    with pytest.raises(DailyPathMissing, match=r"no multi-day rule is enabled"):
        eng.startup()


def test_with_the_daily_path_the_same_book_starts(tmp_path, monkeypatch):
    a = DailyAccount(tmp_path, monkeypatch)
    _hold_b(a)
    eng = a.restart()  # DailyAccount.engine attaches the daily path
    assert eng.daily is not None and eng.run["id"] == a.rid
    assert "engine_start" in [e["type"] for e in a.store.tables["event"]]


def test_no_sleeve_and_no_daily_path_starts_as_before(tmp_path, monkeypatch):
    monkeypatch.delenv("KAIBOT_MULTIDAY", raising=False)
    a = Account(tmp_path)  # a run, an empty book, no daily path
    a.ticks(a.open1, 0, 30)
    eng = a.restart()
    assert eng.daily is None and eng.run["id"] == a.rid


def test_a_sold_out_sleeve_does_not_block_the_start(tmp_path, monkeypatch):
    """Only sleeves with shares count: a book that sold its last share starts without the daily path."""
    a = DailyAccount(tmp_path, monkeypatch)
    _hold_b(a)
    px = float(a.eng.market.last_close("B"))
    a.eng._book("fill", {"run_id": a.rid, "strategy": MD, "symbol": "B", "side": "sell", "qty": 40, "px": px,
                         "fees": 0.0, "coid": f"kb{a.rid}-{MD}-B-20260803-sell-1", "session": DAY1})
    a.broker.pos.pop("B", None)
    monkeypatch.delenv("KAIBOT_MULTIDAY", raising=False)
    eng = _without_daily_path(a)
    eng.startup()
    assert eng.run["id"] == a.rid


class _Lease:
    def __init__(self):
        self.released = 0

    def release(self):
        self.released += 1
        return True


def test_main_gives_the_lease_back_and_exits_with_code_5(monkeypatch):
    lease = _Lease()

    def refuse(eng, lease, adopt_journal=False):
        raise DailyPathMissing(7, ["B"], False)

    monkeypatch.setattr(entry, "start_with_lease", refuse)
    with pytest.raises(SystemExit) as e:
        entry.start_or_refuse(object(), lease)
    assert e.value.code == entry.EXIT_DAILY_PATH == 5 and lease.released == 1


def test_main_passes_a_normal_start_through(monkeypatch):
    lease = _Lease()
    monkeypatch.setattr(entry, "start_with_lease", lambda eng, lease, adopt_journal=False: "active")
    assert entry.start_or_refuse(object(), lease) == "active" and lease.released == 0
