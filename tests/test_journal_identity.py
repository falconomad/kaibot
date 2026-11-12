"""QA-Ops review of PR #160 (#117): journal identity (B1), idempotent replication (B2), every broker call after its
intent (should-fix 2). Each test is the reviewer's case."""

from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest
from test_engine import synth_frames
from test_journal import Remote
from test_journal_pre0018 import PreRemote
from test_ledger_engine import D2, build, calm, offer, session

from kaibot.engine import __main__ as engine_main
from kaibot.engine.journal import (
    EPOCHS,
    ID_SPAN,
    SCHEMA_PRE_0018,
    IdentityError,
    Journal,
    JournalStore,
    Replicator,
    same_row,
)


def journal_with_run(path, remote):
    st = JournalStore(Journal(path), remote, live=True)
    st.bootstrap()
    st.verify_identity()
    run = st.insert("run", {"amount_usd": 5000, "status": "running", "level": "balanced"})
    sig = st.insert("signal", {"run_id": run["id"], "symbol": "A", "mode": "live"})
    order = st.insert("order", {"signal_id": sig["id"], "symbol": "A", "side": "buy",
                                "client_order_id": f"kb-{run['id']}-{sig['id']}-1"})
    return st, run, order


def fills(st, order, n, px=10.0):
    for k in range(n):
        st.insert("fill", {"order_id": order["id"], "qty": 1, "price": px + k}, returning=False)


# ---------------------------------------------------------------- B1: identity
def test_every_journal_has_its_own_identity_and_key_range(tmp_path):
    a, b = Journal(tmp_path / "a.sqlite"), Journal(tmp_path / "b.sqlite")
    assert a.journal_id != b.journal_id and a.epoch != b.epoch
    assert Journal(tmp_path / "a.sqlite").journal_id == a.journal_id, "stored in the file"
    for j in (a, b):
        assert 1 <= j.epoch <= EPOCHS and j.id_base == j.epoch * ID_SPAN
        assert j.jseq(1) == j.id_base + 1 and j.id_base + ID_SPAN < 2 ** 53, "exact in the dashboard's JavaScript"


def test_two_journals_on_one_remote_lose_no_rows(tmp_path):
    """The reviewer's repro: journal A replicates 10 fills; journal B (a standby taking over) bootstraps and
    replicates 3. Supabase must hold all 13, and B's status must be honest."""
    remote = Remote()
    a, run, order_a = journal_with_run(tmp_path / "a.sqlite", remote)
    fills(a, order_a, 10)
    a.heartbeat("engine", {"state": "trading"})
    ra = Replicator(a.journal, remote)
    assert ra.pump() and ra.pending() == 0
    trade_a = a.insert("trade", {"run_id": run["id"], "symbol": "A", "qty": 10})
    ra.pump()

    b = JournalStore(Journal(tmp_path / "b.sqlite"), remote, live=True)
    assert b.bootstrap()["run_id"] == run["id"]
    b.verify_identity()
    sig_b = b.insert("signal", {"run_id": run["id"], "symbol": "B", "mode": "live"})
    order_b = b.insert("order", {"signal_id": sig_b["id"], "symbol": "B", "side": "buy",
                                 "client_order_id": f"kb-{run['id']}-{sig_b['id']}-1"})
    fills(b, order_b, 3, px=20.0)
    trade_b = b.insert("trade", {"run_id": run["id"], "symbol": "B", "qty": 3})
    b.update("trade", {"id": trade_b["id"]}, {"exit_reason": "time"})
    rb = Replicator(b.journal, remote)
    rb.pump()
    assert rb.status()["pending"] == 0 and rb.status()["error"] is None and rb.status()["conflicts"] == 0

    assert len(remote.tables["fill"]) == 13
    assert sorted(f["order_id"] for f in remote.tables["fill"]) == sorted([order_a["id"]] * 10 + [order_b["id"]] * 3)
    assert len({f["jseq"] for f in remote.tables["fill"]}) == 13
    assert sig_b["id"] != order_a["id"] and trade_b["id"] != trade_a["id"], "ids never shared between journals"
    trades = {t["id"]: t for t in remote.tables["trade"]}
    assert trades[trade_b["id"]]["exit_reason"] == "time" and trades[trade_a["id"]].get("exit_reason") is None, \
        "B's update reaches B's trade, not A's"


def test_a_stale_standby_refuses_to_start_unless_adopted(tmp_path):
    """A ran, B took over with a fresh journal and traded; A (the old journal) starts again."""
    remote = Remote()
    a, run, order_a = journal_with_run(tmp_path / "a.sqlite", remote)
    fills(a, order_a, 2)
    a.heartbeat("engine", {"state": "trading"})
    Replicator(a.journal, remote).pump()
    a.journal.close()

    b = JournalStore(Journal(tmp_path / "b.sqlite"), remote, live=True)
    b.bootstrap()
    b.verify_identity()
    fills(b, b.insert("order", {"symbol": "B", "side": "buy", "client_order_id": "kb-b-1"}), 2)
    b.heartbeat("engine", {"state": "trading"})
    Replicator(b.journal, remote).pump()

    a2 = JournalStore(Journal(tmp_path / "a.sqlite"), remote, live=True)
    assert a2.bootstrap() == {"imported": 0}
    with pytest.raises(IdentityError, match="stale standby"):
        a2.verify_identity()
    out = a2.verify_identity(adopt=True)
    assert out["adopted"] and a2.tables["event"][-1]["type"] == "journal_adopted"
    a2.verify_identity()  # adopted once: what it saw is now known

    fills(b, b.insert("order", {"symbol": "B", "side": "buy", "client_order_id": "kb-b-2"}), 1)
    b.heartbeat("engine", {"state": "trading"})  # B carries on afterwards: A is behind again
    with pytest.raises(IdentityError, match="stale standby"):
        JournalStore(Journal(tmp_path / "a.sqlite"), remote, live=True).verify_identity()


def test_rows_of_a_newer_journal_refuse_a_start_even_without_its_heartbeat(tmp_path):
    remote = Remote()
    a, _, order_a = journal_with_run(tmp_path / "a.sqlite", remote)
    a.heartbeat("engine", {"state": "trading"})
    Replicator(a.journal, remote).pump()
    b = JournalStore(Journal(tmp_path / "b.sqlite"), remote, live=True)
    b.bootstrap()
    remote.tables.pop("heartbeat")  # B never managed a heartbeat; its rows still reached Supabase
    fills(b, order_a, 1)
    Replicator(b.journal, remote).pump()
    with pytest.raises(IdentityError, match="newest fill row"):
        JournalStore(Journal(tmp_path / "a.sqlite"), remote, live=True).verify_identity()


def test_a_restored_backup_refuses_to_start_and_adopting_it_reuses_no_key(tmp_path):
    """#121 backup restored: the file is older than what Supabase holds of the same journal."""
    remote = Remote()
    path = tmp_path / "journal.sqlite"
    a, run, order = journal_with_run(path, remote)
    fills(a, order, 2)
    Replicator(a.journal, remote).pump()
    a.journal.close()
    shutil.copy(path, tmp_path / "backup.sqlite")
    a = JournalStore(Journal(path), remote, live=True)
    fills(a, order, 5)
    a.insert("trade", {"run_id": run["id"], "symbol": "A", "qty": 7})
    a.heartbeat("engine", {"state": "trading"})
    Replicator(a.journal, remote).pump()
    a.journal.close()

    shutil.copy(tmp_path / "backup.sqlite", path)  # the restore
    r = JournalStore(Journal(path), remote, live=True)
    with pytest.raises(IdentityError, match="restored backup"):
        r.verify_identity()
    remote.tables.pop("heartbeat")
    with pytest.raises(IdentityError, match="restored backup"):  # the rows alone say so too
        JournalStore(Journal(path), remote, live=True).verify_identity()

    r = JournalStore(Journal(path), remote, live=True)
    r.verify_identity(adopt=True)
    fills(r, order, 3, px=30.0)
    t = r.insert("trade", {"run_id": run["id"], "symbol": "A", "qty": 3})
    rep = Replicator(r.journal, remote)
    rep.pump()
    assert rep.status()["conflicts"] == 0 and rep.pending() == 0
    assert len(remote.tables["fill"]) == 10 and len(remote.tables["trade"]) == 2
    assert t["id"] in {x["id"] for x in remote.tables["trade"]}
    r.verify_identity()


def test_a_fresh_journal_on_a_new_machine_starts(tmp_path):
    remote = Remote()
    a, _, _ = journal_with_run(tmp_path / "a.sqlite", remote)
    a.heartbeat("engine", {"state": "trading"})
    Replicator(a.journal, remote).pump()
    vm = JournalStore(Journal(tmp_path / "vm.sqlite"), remote, live=True)
    vm.bootstrap()
    assert vm.verify_identity()["ok"]


def test_the_engine_exits_with_the_reason_and_adopts_with_the_flag(tmp_path, caplog):
    remote = Remote()
    a, _, _ = journal_with_run(tmp_path / "a.sqlite", remote)
    a.journal.close()
    b = JournalStore(Journal(tmp_path / "b.sqlite"), remote, live=True)
    b.bootstrap()
    b.heartbeat("engine", {"state": "trading"})

    released = []
    lease = SimpleNamespace(acquire=lambda: {}, release=lambda: released.append(1), host="mac", ttl_s=60)
    started = []
    eng = SimpleNamespace(store=JournalStore(Journal(tmp_path / "a.sqlite"), remote, live=True), process="engine",
                          startup=lambda: started.append(1))
    with pytest.raises(SystemExit) as e:
        engine_main.start_with_lease(eng, lease)
    assert e.value.code == engine_main.EXIT_JOURNAL and released and not started
    assert "stale standby" in caplog.text and "--adopt-journal" in caplog.text
    engine_main.start_with_lease(eng, lease, adopt_journal=True)
    assert started


# ---------------------------------------------------------------- B2: a lost answer
@pytest.mark.parametrize("schema", ["pre-0018", "0018"])
def test_a_lost_answer_on_a_natural_key_is_settled_and_replication_goes_on(tmp_path, schema):
    """The reviewer's repro: the first ledger / order / room_session insert commits, the answer is lost. Before the
    fix: `cursor 1, pending 2, error 23505` on every later pump."""
    remote = PreRemote() if schema == "pre-0018" else Remote()
    st = JournalStore(Journal(tmp_path / "j.sqlite"), remote, live=True)
    run = st.insert("run", {"amount_usd": 5000, "status": "running", "level": "balanced"})
    sig = st.insert("signal", {"run_id": run["id"], "symbol": "A", "mode": "live"})
    rep = Replicator(st.journal, remote)
    assert rep.pump() == 2
    st.insert("ledger", {"run_id": run["id"], "ts": "2026-11-12T15:00:00+00:00", "cash": 5000.0,
                         "positions": {"A": 3}, "equity_venue": 5000.0, "equity_shadow": 4999.9}, returning=False)
    order = st.insert("order", {"signal_id": sig["id"], "symbol": "A", "side": "buy",
                                "client_order_id": f"kb-{run['id']}-{sig['id']}-1"})
    st.insert("room_session", {"signal_id": sig["id"], "verdict": "go"}, returning=False)
    st.insert("fill", {"order_id": order["id"], "qty": 3, "price": 10.0}, returning=False)
    for _ in range(3):  # each of the three inserts loses its answer once
        remote.lose = 1
        rep.pump()
    rep.pump()
    s = rep.status()
    assert s["pending"] == 0 and s["error"] is None and s["conflicts"] == 0, s
    for t in ("ledger", "order", "room_session", "fill"):
        assert len(remote.tables[t]) == 1, t
    db_order = remote.tables["order"][0]["id"]
    assert remote.tables["fill"][0]["order_id"] == db_order, "the fill points at the order that exists"
    if schema == "pre-0018":
        assert rep.schema == SCHEMA_PRE_0018 and rep.ids[("order", order["id"])] == db_order


@pytest.mark.parametrize("table", ["fill", "trade"])
def test_after_0018_a_lost_answer_on_jseq_or_an_engine_id_is_settled(table):
    remote = Remote()
    st = JournalStore(Journal(), remote, live=True)
    rep = Replicator(st.journal, remote)
    st.insert("run", {"amount_usd": 5000, "status": "running", "level": "balanced"})
    rep.pump()
    st.insert(table, {"qty": 3, "price": 10.0} if table == "fill" else {"symbol": "A", "qty": 3}, returning=False)
    st.insert("halt", {"kind": "drift", "reason": "x"}, returning=False)
    remote.lose = 1
    assert rep.pump() == 0 and rep.last_error
    assert rep.pump() == 2 and rep.status()["conflicts"] == 0 and rep.last_error is None
    assert len(remote.tables[table]) == 1 and len(remote.tables["halt"]) == 1


@pytest.mark.parametrize("schema", ["pre-0018", "0018"])
def test_a_different_row_under_the_same_key_is_a_conflict_not_a_freeze(tmp_path, schema):
    remote = PreRemote() if schema == "pre-0018" else Remote()
    st = JournalStore(Journal(tmp_path / "j.sqlite"), remote, live=True)
    run = st.insert("run", {"amount_usd": 5000, "status": "running", "level": "balanced"})
    rep = Replicator(st.journal, remote)
    rep.pump()
    db_run = remote.tables["run"][0]["id"]
    remote.tables.setdefault("ledger", []).append({"run_id": db_run, "ts": "2026-11-12T15:00:00+00:00",
                                                   "cash": 1.0, "positions": {}, "equity_venue": 1.0,
                                                   "equity_shadow": 1.0})  # someone else's row
    st.insert("ledger", {"run_id": run["id"], "ts": "2026-11-12T15:00:00+00:00", "cash": 5000.0, "positions": {},
                         "equity_venue": 5000.0, "equity_shadow": 5000.0}, returning=False)
    st.insert("halt", {"run_id": run["id"], "kind": "drift", "reason": "after"}, returning=False)
    assert rep.pump() == 2, "never stuck on one entry"
    assert rep.status()["conflicts"] == 1 and rep.pending() == 0 and len(remote.tables["halt"]) == 1
    (c,) = rep.take_conflicts()
    assert c["table"] == "ledger" and c["reason"] == "a different row holds this key"
    assert rep.take_conflicts() == []
    assert [e for e in st.journal.entries(kinds=("replica_conflict",))], "recorded in the journal"

    eng = SimpleNamespace(store=st, _rid=lambda: run["id"])
    rep._conflicts.append(c)
    engine_main.report_conflicts(eng, rep)
    assert st.tables["event"][-1]["type"] == "replica_conflict"
    assert st.tables["halt"][-1]["kind"] == "replica_conflict"


def test_same_row_reads_values_as_postgres_returns_them():
    sent = {"ts": "2026-11-12T15:00:00+00:00", "cash": 4999.996, "positions": {"A": [1, 2.0]}, "ok": True,
            "note": None}
    assert same_row(sent, {"ts": "2026-11-12T15:00:00Z", "cash": 5000.00, "positions": {"A": [1, 2]}, "ok": True,
                           "note": None, "extra": 1})
    assert not same_row(sent, {**sent, "cash": 4990.0})
    assert not same_row(sent, {**sent, "positions": {"A": [1]}})
    assert not same_row(sent, {**sent, "ts": "2026-11-12T15:00:01+00:00"})


# ---------------------------------------------------------------- should-fix 2: intent before every broker call
BROKER_WRITES = ("submit", "cancel", "replace", "close_position", "submit_bracket", "submit_oto_stop",
                 "submit_moc_sell", "replace_stop", "replace_limit", "cancel_order")


def test_every_broker_call_is_preceded_by_its_intent_in_the_journal(tmp_path):
    store = JournalStore(Journal(tmp_path / "j.sqlite"), live=True)
    eng, broker, clock, _ = build(tmp_path, calm(synth_frames()), D2, store=store)
    calls: list[str] = []

    def spy(name, fn):
        def call(*a, **k):
            seq, _, kind, body = store.journal.entries(after=store.journal.last_seq() - 1)[-1]
            assert kind == "intent", f"broker.{name} called without an intent (last entry: {kind} {body})"
            assert not store.journal.entries(after=seq, kinds=("ack",)), f"broker.{name}: its intent is answered"
            calls.append(name)
            return fn(*a, **k)
        return call

    real_bracket = broker.submit_bracket
    # legs a few cents off the prices the fill gives (the fill is not the reference price): both are re-priced
    broker.submit_bracket = lambda coid, s, q, stop, target: real_bracket(coid, s, q, stop - 0.05, target + 0.05)
    for name in BROKER_WRITES:
        if hasattr(broker, name):
            setattr(broker, name, spy(name, getattr(broker, name)))
    offer(eng, D2)
    session(eng, broker, clock, D2)
    assert store.tables["trade"] and store.tables["trade"][0]["exit_reason"] == "time"
    assert {"submit_bracket", "replace_limit", "replace_stop"} <= set(calls), calls
    intents = [b["action"] for _, _, _, b in store.journal.entries(kinds=("intent",))]
    assert {"entry", "replace_limit", "replace_stop"} <= set(intents)


# ---------------------------------------------------------------- dividends: the guard #118 replaces
def test_multi_day_holding_on_pay_date_dividends_is_refused_until_118(tmp_path, monkeypatch):
    """Live dividends are booked at the pay date (Alpaca DIV activities), which is exact for intraday rules (never
    held over an ex-date). A rule held across sessions needs ex-date accrual: #118 must replace this guard."""
    from kaibot.engine.core import MultiDayDividendsError
    from kaibot.strategies import registry
    from kaibot.strategies.base import Holding

    eng, *_ = build(tmp_path, calm(synth_frames()), D2)
    names = eng.level_strategies()
    held = registry.live(names)[0]
    multi = SimpleNamespace(**{k: getattr(held, k) for k in ("name", "mode")}, holding=Holding.MULTI_DAY)
    monkeypatch.setattr(registry, "STRATEGIES", tuple(multi if s is held else s for s in registry.STRATEGIES))
    with pytest.raises(MultiDayDividendsError, match="#118"):
        eng.live_strategies()
    eng.corporate_actions = lambda day: []  # ex-date actions given (research: the daily store): allowed
    assert eng.live_strategies()
