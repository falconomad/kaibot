"""M13 ticket 4.3 (#477): a fencing token on the lease, checked before every order the engine sends.

Every acquire is a new tenure with a rising `epoch` (migration 0042); renew and release match it. Every write to
the broker goes through `engine/fence.FencedBroker`, which asks `EngineLease.fence`: a renewal with the epoch first.
The ticket's check: a paused engine that resumes after expiry cannot place an order. The last block models the
lease protocol as a small state machine and checks every interleaving of two engine processes up to a depth: an
order passes the fence only for the tenure the database names, while it is live.
"""

from __future__ import annotations

import itertools

import pytest
from fakes import FakeStore
from test_engine_lease import PG, PgStore, Time

from kaibot.broker.protocol import OrderRequest
from kaibot.engine.config import ROOT
from kaibot.engine.fence import Fenced, FencedBroker
from kaibot.engine.lease import TTL_S, EngineLease, LeaseLost, machine_key
from kaibot.engine.orders import REFUSED

SQL = (ROOT / "migrations" / "0042_engine_lease_epoch.sql").read_text()


def world():
    t, store = Time(), FakeStore()
    store.lease_now = t.db
    return t, store


def proc(store, t, holder="H", host="mac", os_id="UUID-A"):
    """One engine process: its lease (holder from the data folder, machine from the OS)."""
    return EngineLease(store, holder, host, mono=t.mono, wall=t.wall, machine=machine_key(host, os_id))


class Venue:
    def __init__(self):
        self.calls = []

    def submit(self, order):
        self.calls.append(("submit", order.side, order.symbol))
        return {"id": f"o{len(self.calls)}", "client_order_id": order.client_order_id, "status": "accepted"}

    def cancel(self, order_id):
        self.calls.append(("cancel", order_id))

    def replace(self, order_id, **kw):
        self.calls.append(("replace", order_id))
        return {"id": order_id}

    def order(self, order_id):
        if order_id == "gone":
            raise ConnectionError("venue unreachable")
        return {"id": order_id, "side": "sell" if order_id.startswith("stop") else "buy"}

    def close_position(self, symbol):
        self.calls.append(("close", symbol))

    def positions(self):
        return {"B": {"qty": "10"}}


def buy(sym="B"):
    return OrderRequest(sym, "buy", 10, "market", "day", f"kb1-x-{sym}-20260803-target-1")


def sell(sym="B"):
    return OrderRequest(sym, "sell", 10, "market", "day", f"kb1-x-{sym}-20260803-exit-1")


# ---------------------------------------------------------------- the epoch
def test_every_acquire_is_a_new_tenure_and_the_older_process_of_the_same_install_is_fenced_out():
    t, store = world()
    p1 = proc(store, t)
    p1.acquire()
    t.s += 5
    p2 = proc(store, t)  # the same install on the same machine starts again while p1 still runs
    p2.acquire()
    assert p2.epoch == p1.epoch + 1 and store.lease["epoch"] == p2.epoch
    t.s += 5
    with pytest.raises(LeaseLost):
        p1.renew(force=True)  # before 0042 this was "still ours": two processes renewing one lease
    assert p2.renew(force=True) and p1.release() is False and store.lease is not None
    assert p2.release() is True


def test_the_epoch_only_rises_also_across_a_release():
    t, store = world()
    seen = []
    for host in ("mac", "vm", "mac"):
        p = proc(store, t, holder=host, host=host)
        p.acquire()
        seen.append(p.epoch)
        p.release()
    assert seen == sorted(seen) and len(set(seen)) == 3


# ---------------------------------------------------------------- the fence
def test_every_order_is_preceded_by_a_renewal_with_the_epoch():
    t, store = world()
    lease = proc(store, t)
    lease.acquire()
    fb = FencedBroker(Venue(), lease)
    calls = store.lease_calls
    t.s += 1
    fb.submit(buy())
    fb.submit(sell())
    fb.cancel("o1")
    assert store.lease_calls == calls + 3 and lease.ok and [c[0] for c in fb.inner.calls] == ["submit"] * 2 + ["cancel"]


def test_a_new_process_of_the_same_install_fences_the_old_one_out_at_its_next_order():
    """What the model check below found with a "fresh enough" shortcut: the new process's tenure begins at once,
    so the old process's proof of a second ago is already stale. Every order asks."""
    t, store = world()
    p1 = proc(store, t)
    p1.acquire()
    f1 = FencedBroker(Venue(), p1)
    proc(store, t).acquire()
    with pytest.raises(Fenced):
        f1.submit(sell())
    assert f1.inner.calls == [] and p1.lost


@pytest.mark.parametrize("taken_by", ["another machine", "another process of this install"])
@pytest.mark.parametrize("write", ["buy", "sell", "cancel", "replace", "close"])
def test_a_paused_engine_that_resumes_after_expiry_cannot_place_an_order(taken_by, write):
    """The ticket's check. A pauses past the TTL; B takes the lease; A wakes and tries to write: refused before the
    venue, the lease is marked lost (the main loop stops on it), B's tenure is untouched."""
    t, store = world()
    a = proc(store, t)
    a.acquire()
    fa = FencedBroker(Venue(), a)
    t.s += TTL_S + 30  # A's laptop sleeps
    b = proc(store, t, host="vm", os_id="UUID-B") if taken_by == "another machine" else proc(store, t)
    b.acquire()
    call = {"buy": lambda: fa.submit(buy()), "sell": lambda: fa.submit(sell()), "cancel": lambda: fa.cancel("o1"),
            "replace": lambda: fa.replace("o1", stop_price=9.0), "close": lambda: fa.close_position("B")}[write]
    with pytest.raises(Fenced) as e:
        call()
    assert e.value.status in REFUSED and "no longer ours" in str(e.value)
    assert fa.inner.calls == [] and a.lost and fa.refused == 1
    assert store.lease["epoch"] == b.epoch and b.renew(force=True)


def test_a_paused_engine_that_nobody_replaced_renews_and_sends():
    t, store = world()
    a = proc(store, t)
    a.acquire()
    fa = FencedBroker(Venue(), a)
    t.s += TTL_S + 30  # expired, but no other engine came: still this tenure (0013's rule)
    fa.submit(buy())
    assert fa.inner.calls == [("submit", "buy", "B")] and a.ok


def test_supabase_unreachable_after_a_pause_refuses_entries_and_lets_reductions_out():
    """The fail-safe design for an outage (docs/ops/failsafe.md): stops and sales go on; nothing is opened."""
    t, store = world()
    a = proc(store, t)
    a.acquire()
    fa = FencedBroker(Venue(), a)
    t.s += 5
    store.lease_fail = True
    with pytest.raises(Fenced, match="no new entry"):
        fa.submit(buy())
    fa.submit(sell())
    fa.cancel("o1")
    fa.replace("o1", stop_price=9.0)
    fa.replace("stop-1", stop_price=9.0, qty=20)  # QA-Ops: protect.py resizes a protective stop: a sale, it goes on
    with pytest.raises(Fenced):
        fa.replace("o1", qty=20)  # a buy's quantity change is fenced like an entry
    with pytest.raises(Fenced):
        fa.replace("gone", qty=20)  # and so is one whose side cannot be read
    assert [c[0] for c in fa.inner.calls] == ["submit", "cancel", "replace", "replace"] and not a.lost


def test_qa_an_engine_of_an_older_release_is_fenced_out_once_this_release_starts():
    """QA-Ops on #584: an engine of a release before #477 sends no epoch. Its tenure has epoch 0 and stays its own
    (the running engine during the rollout, or a rollback), but once an engine of this release has started on the
    same install its renewals fail: never two engines, also not during the rollout."""
    t, store = world()
    m = machine_key("mac", "UUID-A")
    store.lease_acquire("H", "mac", TTL_S, m, fenced=False)  # the older release
    assert store.lease["epoch"] == 0 and store.lease_renew("H", TTL_S, m) is True
    p = proc(store, t)
    p.acquire()
    assert p.epoch >= 1
    assert store.lease_renew("H", TTL_S, m) is False and store.lease_release("H", m) is False
    assert p.renew(force=True)
    store.lease_acquire("H", "mac", TTL_S, m, fenced=False)  # the older one starts again: it is the tenure now
    with pytest.raises(LeaseLost):
        p.renew(force=True)


def test_reads_pass_through_and_no_lease_means_no_fence():
    fb = FencedBroker(Venue())
    assert fb.positions() == {"B": {"qty": "10"}}
    fb.submit(buy())
    assert fb.inner.calls == [("submit", "buy", "B")]


def test_a_fenced_order_is_a_refusal_the_order_manager_never_looks_for():
    """status 403 without "client_order_id" in the body: engine/orders.OrderManager marks it rejected, not unknown."""
    e = Fenced("the engine lease is no longer ours")
    assert e.status in REFUSED and "client_order_id" not in e.body


def test_the_engine_s_broker_is_fenced_and_the_lease_is_set_at_start_up():
    from kaibot.engine import __main__ as engine_main

    src = (ROOT / "src" / "kaibot" / "engine" / "__main__.py").read_text()
    assert "broker = FencedBroker(AlpacaPaper(" in src
    t, store = world()

    class Eng:
        process, lease, started = "engine", None, 0

        def __init__(self):
            self.store, self.broker = store, FencedBroker(Venue())

        def startup(self):
            assert self.broker.lease is self.lease  # fenced before the start-up's first order call
            self.started += 1

    eng = Eng()
    engine_main.start_with_lease(eng, proc(store, t))
    assert eng.started == 1 and eng.broker.lease is eng.lease


# ---------------------------------------------------------------- the protocol as a state machine
ACTIONS = ("acq1", "acq2", "renew1", "renew2", "write1", "write2", "tick", "pause")


def explore(same_install: bool, depth: int) -> int:
    """Every sequence of `depth` actions for two processes (1 and 2) on one simulated database. Invariant: an order
    that passes the fence is sent by the tenure the database names, while that row is live; and a process that
    was told "not ours" never sends again. Returns the number of sequences checked."""
    n = 0
    for seq in itertools.product(ACTIONS, repeat=depth):
        t, store = world()
        ps = {1: proc(store, t), 2: proc(store, t) if same_install else proc(store, t, "H2", "vm", "UUID-B")}
        lost_once = set()
        for act in seq:
            if act == "tick":
                t.s += 10
            elif act == "pause":
                t.s += TTL_S + 5
            else:
                i = int(act[-1])
                p = ps[i]
                try:
                    if act.startswith("acq"):
                        if p.held or p.lost:
                            continue  # one acquire per process (a restart is a new process)
                        try:
                            p.acquire()
                        except Exception:  # noqa: BLE001 - LeaseHeld: this process did not start
                            p.lost = True
                    elif act.startswith("renew"):
                        p.renew(force=True)
                    else:
                        if p.fence(reducing=False) is None:
                            row = store.lease
                            assert i not in lost_once, (seq, "a fenced-out process sent")
                            assert row is not None and row["epoch"] == p.epoch, (seq, row, p.epoch)
                            assert row["expires_at"] > t.db(), (seq, "sent on an expired row")
                            assert row["holder"] == p.holder and row["machine"] == p.machine, seq
                except LeaseLost:
                    lost_once.add(i)
        n += 1
    return n


@pytest.mark.parametrize("same_install", [True, False])
def test_every_interleaving_of_two_processes_keeps_one_tenure_sending(same_install):
    assert explore(same_install, depth=6) == len(ACTIONS) ** 6


# ---------------------------------------------------------------- the migration and the real SQL
def test_migration_0042_rises_the_epoch_and_matches_it():
    assert len(list((ROOT / "migrations").glob("0042_*"))) == 1
    assert "nextval('kaibot.engine_lease_epoch_seq')" in SQL
    assert SQL.count("and epoch = coalesce(p_epoch, 0);") == 2  # renew and release: no epoch matches only epoch 0
    assert "p_epoch is null or" not in SQL  # QA-Ops: a missing epoch never matches a fenced tenure
    assert "case when coalesce(p_fenced, false) then nextval('kaibot.engine_lease_epoch_seq') else 0 end" in SQL
    # QA-Ops: the acquires are serialised before the epoch is drawn, also with no row to lock
    lock = SQL.index("perform pg_advisory_xact_lock(hashtext('kaibot.engine_lease_acquire'));")
    assert lock < SQL.index("select * into prev from kaibot.engine_lease where id for update;") < SQL.index("nextval(")
    assert "kaibot.engine_lease_acquire(text, text, integer, text, boolean)" in SQL
    for old in ("engine_lease_acquire(text, text, integer, text)", "engine_lease_renew(text, integer, text)",
                "engine_lease_release(text, text)"):
        assert f"drop function if exists kaibot.{old};" in SQL
    assert "from public, anon, authenticated;" in SQL and "to service_role;" in SQL
    assert "begin;" in SQL and SQL.rstrip().endswith("commit;")


@pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migrations 0013-0042 applied")
def test_real_sql_the_epoch_fences_an_older_process_of_the_same_install():
    store = PgStore()
    store._q("delete from kaibot.engine_lease")
    m = machine_key("mac", "UUID-A")
    p1, p2 = EngineLease(store, "H", "mac", machine=m), EngineLease(store, "H", "mac", machine=m)
    p1.acquire()
    p2.acquire()
    assert p2.epoch > p1.epoch
    assert store.lease_renew("H", TTL_S, m, p1.epoch) is False and store.lease_release("H", m, p1.epoch) is False
    assert store.lease_renew("H", TTL_S, m, p2.epoch) is True
    assert store.lease_renew("H", TTL_S, m, None) is False  # QA-Ops: no epoch never matches a fenced tenure
    with pytest.raises(LeaseLost):
        p1.renew(force=True)
    assert p2.release() is True
    p3 = EngineLease(store, "H", "mac", machine=m)
    p3.acquire()
    assert p3.epoch > p2.epoch  # the sequence goes on after a release
    assert p3.release() is True
    # QA-Ops on #584: an engine of a release before #477 (no p_fenced, no p_epoch) keeps an unfenced tenure (epoch 0;
    # the running engine during the rollout, or a rollback), and loses it once this release starts
    old = store._q(f"select kaibot.engine_lease_acquire('H', 'mac', {TTL_S}, '{m}')")
    assert old["acquired"] and old["epoch"] == 0
    assert store._q(f"select to_jsonb(kaibot.engine_lease_renew('H', {TTL_S}, '{m}'))") is True
    p4 = EngineLease(store, "H", "mac", machine=m)
    p4.acquire()
    assert p4.epoch > p3.epoch
    assert store._q(f"select to_jsonb(kaibot.engine_lease_renew('H', {TTL_S}, '{m}'))") is False
    assert store._q(f"select to_jsonb(kaibot.engine_lease_release('H', '{m}'))") is False
    assert p4.release() is True
