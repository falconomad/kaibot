"""#562 (migration 0041): the engine lease is bound to the holder AND the machine.

A data folder copied to a second machine carries the holder id (data/live/engine_holder_id). Before 0041 that machine
was the SAME holder: its start with Supabase up was "already ours" and both engines renewed one lease. Now the machine
key (host name + OS machine id, hashed, never read from the data folder) must match too: the copy is refused while
the lease is live, can never renew or release it, and takes it over only after it expired (the first engine then
stops). The fake store mirrors 0041; the last block runs the same story on the real SQL (KAIBOT_TEST_PG)."""

from __future__ import annotations

import shutil

import pytest
from fakes import FakeStore
from test_engine_lease import PG, PgStore, StubEngine, Time

from kaibot.engine import __main__ as engine_main
from kaibot.engine import lease as lease_mod
from kaibot.engine.config import ROOT
from kaibot.engine.lease import EXIT_LEASE, TTL_S, EngineLease, LeaseHeld, LeaseLost, holder_id, machine_key

SQL = (ROOT / "migrations" / "0041_engine_lease_machine.sql").read_text()


def world():
    t, store = Time(), FakeStore()
    store.lease_now = t.db
    return t, store


def machine(store, t, data_dir, host, os_id):
    """One machine: its data folder gives the holder id, the OS gives the machine key."""
    return EngineLease(store, holder_id(data_dir, host), host, mono=t.mono, wall=t.wall,
                       machine=machine_key(host, os_id))


def copied(tmp_path):
    a_dir, c_dir = tmp_path / "a" / "data", tmp_path / "c" / "data"
    holder_id(a_dir, "mac")
    shutil.copytree(a_dir, c_dir)  # the whole data folder, holder file included
    assert holder_id(c_dir, "vm") == holder_id(a_dir, "mac")
    return a_dir, c_dir


# ---------- the machine key ----------
def test_the_machine_key_depends_on_host_and_os_id_and_never_on_the_data_folder(tmp_path):
    k = machine_key("mac", "UUID-1")
    assert k == machine_key("mac", "UUID-1") and len(k) == 24
    assert machine_key("mac", "UUID-2") != k  # another Mac with the same name
    assert machine_key("vm", "UUID-1") != k  # a baked /etc/machine-id in an image: the host name still differs
    assert machine_key("mac", "") and machine_key("mac", "") != k  # no OS id: the host name alone, never empty
    d = tmp_path / "data"
    hid = holder_id(d, "mac")
    assert k not in (d / lease_mod.HOLDER_FILE).read_text() and hid not in k  # the folder holds only the holder id


def test_the_machine_key_is_computed_once_at_start(monkeypatch):
    calls = []
    monkeypatch.setattr(lease_mod, "os_machine_id", lambda: calls.append(1) or "UUID-1")
    t, store = world()
    lease = EngineLease(store, "h", "mac", mono=t.mono, wall=t.wall)
    assert lease.machine == machine_key("mac", "UUID-1") and calls == [1]
    lease.acquire()
    for _ in range(3):
        t.s += 10
        assert lease.renew(force=True)
    assert calls == [1]  # a host name that changes while it runs does not take its own lease away
    assert store.lease["machine"] == lease.machine


def test_an_unreadable_os_id_falls_back_to_the_host_name(monkeypatch):
    monkeypatch.setattr(lease_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(lease_mod, "MACHINE_ID_FILES", ("/nonexistent/machine-id",))
    assert lease_mod.os_machine_id() == ""
    with pytest.raises(ValueError):
        EngineLease(FakeStore(), "h", "mac", machine="")


# ---------- the copied data folder (the issue) ----------
def test_a_copied_data_folder_is_refused_while_the_first_engine_holds_the_lease(tmp_path, caplog):
    t, store = world()
    a_dir, c_dir = copied(tmp_path)
    eng_a, a = StubEngine(store), machine(store, t, a_dir, "mac", "UUID-A")
    engine_main.start_with_lease(eng_a, a)
    eng_c, c = StubEngine(store), machine(store, t, c_dir, "vm", "UUID-C")
    assert c.holder == a.holder and c.machine != a.machine
    t.s += 5
    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as e:
        engine_main.start_with_lease(eng_c, c)
    assert e.value.code == EXIT_LEASE and eng_c.started == 0 and eng_c.lease is None
    assert "THIS install's holder id on another machine" in caplog.text and "'mac'" in caplog.text
    assert store.lease["machine"] == a.machine and store.lease["host"] == "mac"  # A's lease, untouched
    # the copy can neither renew nor release A's lease, even by calling the store directly with the holder id
    assert store.lease_renew(c.holder, TTL_S, c.machine) is False
    assert store.lease_release(c.holder, c.machine) is False and store.lease is not None
    t.s += 10
    assert a.renew(force=True) and a.ok


def test_two_engines_with_one_holder_id_never_both_hold_the_lease(tmp_path):
    """The two-engines test: A runs; C (a copied data folder) keeps trying to start as Docker would. At no moment do
    both hold a lease that renews; when A dies, C takes over after the TTL and A, waking up, stops."""
    t, store = world()
    a_dir, c_dir = copied(tmp_path)
    a = machine(store, t, a_dir, "mac", "UUID-A")
    a.acquire()
    c = None
    for _ in range(30):  # 5 minutes: A renews every 10 s, C retries every 10 s
        t.s += 10
        assert a.renew(force=True)
        c = machine(store, t, c_dir, "vm", "UUID-C")
        with pytest.raises(LeaseHeld):
            c.acquire()
        assert not c.ok and not c.held
    t.s += TTL_S - 1  # A's laptop sleeps: not yet expired, C still refused
    with pytest.raises(LeaseHeld):
        c.acquire()
    t.s += 2
    out = c.acquire()  # expired: C takes over, recorded as a takeover like any other machine
    assert out["acquired"] and out["taken_over"] and out["previous_host"] == "mac"
    assert c.ok and store.lease["machine"] == c.machine
    with pytest.raises(LeaseLost):  # A wakes up: not ours (same holder, other machine) -> it stops
        a.renew(force=True)
    assert not a.ok and a.release() is False and store.lease["machine"] == c.machine
    t.s += 10
    assert c.renew(force=True)


def test_the_same_machine_restarts_at_once_as_before(tmp_path):
    t, store = world()
    d = tmp_path / "data"
    first = machine(store, t, d, "mac", "UUID-A")
    first.acquire()
    started = store.lease["started_at"]
    t.s += 3  # killed, restarted: no release
    out = machine(store, t, d, "mac", "UUID-A").acquire()
    assert out["acquired"] and not out["taken_over"] and store.lease["started_at"] == started


def test_a_renamed_machine_waits_out_its_old_lease(tmp_path):
    """Fail closed: a host name that changed between two starts is another machine key; the new start waits for the
    old lease to expire (or a clean stop released it)."""
    t, store = world()
    d = tmp_path / "data"
    machine(store, t, d, "mac", "UUID-A").acquire()
    with pytest.raises(LeaseHeld):
        machine(store, t, d, "mac-2", "UUID-A").acquire()
    t.s += TTL_S + 1
    assert machine(store, t, d, "mac-2", "UUID-A").acquire()["acquired"]


def test_a_clean_stop_releases_with_the_machine_so_the_copy_could_start_at_once(tmp_path):
    t, store = world()
    a_dir, c_dir = copied(tmp_path)
    a = machine(store, t, a_dir, "mac", "UUID-A")
    a.acquire()
    assert a.release() is True and store.lease is None
    assert machine(store, t, c_dir, "vm", "UUID-C").acquire()["acquired"]


# ---------- the migration ----------
def test_migration_0041_matches_on_holder_and_machine_and_stays_service_role_only():
    assert len(list((ROOT / "migrations").glob("0041_*"))) == 1  # the number is not shared
    assert "add column if not exists machine text not null default ''" in SQL
    assert "where (l.holder = excluded.holder and l.machine = excluded.machine) or l.expires_at <= now()" in SQL
    assert SQL.count("holder = p_holder and machine = coalesce(p_machine, '')") == 2  # renew and release
    for old in ("engine_lease_acquire(text, text, integer)", "engine_lease_renew(text, integer)",
                "engine_lease_release(text)"):
        assert f"drop function if exists kaibot.{old};" in SQL  # no 0013 overload left that matches on holder only
    assert "from public, anon, authenticated;" in SQL and "to service_role;" in SQL
    assert SQL.strip().startswith("--") and "begin;" in SQL and SQL.rstrip().endswith("commit;")


# ---------- the same story on real SQL (KAIBOT_TEST_PG; CI builds one) ----------
@pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migrations 0013 and 0041 applied")
def test_real_sql_a_copied_holder_id_on_another_machine_is_refused_and_cannot_renew():
    store = PgStore()
    store._q("delete from kaibot.engine_lease")
    a = EngineLease(store, "H", "mac", machine=machine_key("mac", "UUID-A"))
    c = EngineLease(store, "H", "vm", machine=machine_key("vm", "UUID-C"))
    assert a.acquire()["acquired"]
    got = store.lease_acquire("H", "vm", TTL_S, c.machine)
    assert got["acquired"] is False and got["reason"] == "holder_on_other_machine" and got["host"] == "mac"
    with pytest.raises(LeaseHeld):
        c.acquire()
    assert store.lease_renew("H", TTL_S, c.machine) is False and store.lease_release("H", c.machine) is False
    assert a.renew(force=True)
    # an engine of a release before 0041 (no p_machine: machine '') cannot touch the new lease either
    assert store._q(f"select to_jsonb(kaibot.engine_lease_renew('H', {TTL_S}))") is False
    assert EngineLease(store, "H", "mac", machine=a.machine).acquire()["taken_over"] is False  # restart at once
    store.age(TTL_S + 1)
    out = c.acquire()
    assert out["taken_over"] and out["previous_host"] == "mac"
    with pytest.raises(LeaseLost):
        a.renew(force=True)
    assert c.release() is True
    store._q("delete from kaibot.engine_lease")


@pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migrations 0013 and 0041 applied")
def test_real_sql_rollout_an_older_engine_keeps_its_lease_and_the_new_release_waits():
    """QA-Ops (#567): during the rollout an engine of a release before 0041 calls the functions without p_machine
    (machine ''). It still acquires, renews and releases its own lease; the new release on the same holder id is
    refused while that lease is live and takes it only once it is released or expired (fail closed)."""
    store = PgStore()
    store._q("delete from kaibot.engine_lease")
    old = store._q(f"select kaibot.engine_lease_acquire('H', 'mac', {TTL_S})")
    assert old["acquired"] is True
    assert store._q(f"select to_jsonb(kaibot.engine_lease_renew('H', {TTL_S}))") is True
    assert store._q("select to_jsonb(machine) from kaibot.engine_lease") == ""
    new = EngineLease(store, "H", "mac", machine=machine_key("mac", "UUID-A"))
    with pytest.raises(LeaseHeld):
        new.acquire()
    assert store._q(f"select to_jsonb(kaibot.engine_lease_renew('H', {TTL_S}))") is True  # still the old one's
    assert store._q("select to_jsonb(kaibot.engine_lease_release('H'))") is True  # a clean stop of the old release
    assert new.acquire()["acquired"] is True and new.renew(force=True)
    assert store._q(f"select to_jsonb(kaibot.engine_lease_renew('H', {TTL_S}))") is False  # '' can't touch it now
    store._q("delete from kaibot.engine_lease")
