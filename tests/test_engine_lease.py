"""Cross-machine engine lease (issue #81): only one engine may trade the paper account.

The fake store mirrors `kaibot.engine_lease_*` (migration 0013). Covered: two engines racing, renewal, takeover
after expiry, clean release, and fail closed (lease cannot be checked -> no start, no entries). The last block
runs the same story against the real SQL when a scratch Postgres is given (KAIBOT_TEST_PG, 0013 applied)."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeStore
from test_engine import fake_candidate, make, run_day, synth_frames

from kaibot.engine import __main__ as engine_main
from kaibot.engine import lease as lease_mod
from kaibot.engine.config import ROOT
from kaibot.engine.lease import EXIT_LEASE, TTL_S, EngineLease, LeaseHeld, LeaseLost, LeaseUnavailable, holder_id

T0 = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)


class Time:
    """One clock for the fake database and for an engine's own monotonic / wall clocks."""

    def __init__(self):
        self.s = 0.0

    def db(self):
        return T0 + timedelta(seconds=self.s)

    def mono(self):
        return self.s

    def wall(self):
        return 1_000_000.0 + self.s


def world():
    t, store = Time(), FakeStore()
    store.lease_now = t.db
    return t, store


def lease_for(store, t, name):
    return EngineLease(store, f"holder-{name}", f"host-{name}", mono=t.mono, wall=t.wall)


class StubEngine:
    """Just enough of Engine for start_with_lease / make_beat: records whether start-up ran."""

    process = "engine"

    def __init__(self, store):
        self.store, self.lease, self.started = store, None, 0

    def startup(self):
        self.started += 1


# ---------- two engines race: exactly one starts ----------
def test_two_engines_on_different_machines_race_and_exactly_one_starts():
    for _ in range(25):
        t, store = world()
        gate = threading.Barrier(2)
        engines = [StubEngine(store) for _ in range(2)]
        codes: list = [None, None]

        def boot(i, gate=gate, engines=engines, codes=codes, store=store, t=t):
            gate.wait()
            try:
                engine_main.start_with_lease(engines[i], lease_for(store, t, f"m{i}"))
                codes[i] = 0
            except SystemExit as stopped:
                codes[i] = stopped.code

        th = [threading.Thread(target=boot, args=(i,)) for i in range(2)]
        [x.start() for x in th]
        [x.join() for x in th]
        assert sorted(codes) == [0, EXIT_LEASE]
        winner = codes.index(0)
        assert [e.started for e in engines] == [1 if i == winner else 0 for i in range(2)]
        assert engines[1 - winner].lease is None  # the loser never got as far as a lease object on the engine
        assert store.lease["holder"] == f"holder-m{winner}"


def test_many_engines_race_one_winner():
    t, store = world()
    n = 12
    gate, got = threading.Barrier(n), []

    def boot(i):
        gate.wait()
        try:
            lease_for(store, t, i).acquire()
            got.append(i)
        except LeaseHeld:
            pass

    th = [threading.Thread(target=boot, args=(i,)) for i in range(n)]
    [x.start() for x in th]
    [x.join() for x in th]
    assert len(got) == 1


def test_second_engine_is_refused_with_a_clear_message_and_does_nothing(caplog):
    t, store = world()
    lease_for(store, t, "mac").acquire()
    vm = StubEngine(store)
    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as e:
        engine_main.start_with_lease(vm, lease_for(store, t, "vm"))
    assert e.value.code == EXIT_LEASE == 3
    msg = caplog.text
    assert "Another Kaibot engine holds the lease" in msg and "host-mac" in msg and "not starting" in msg
    assert "run_engine.md" in msg
    assert vm.started == 0 and "event" not in store.tables and not store.beats
    assert store.lease["holder"] == "holder-mac"  # untouched


# ---------- renewal ----------
def test_renewal_extends_the_lease_and_keeps_its_start():
    t, store = world()
    a = lease_for(store, t, "a")
    a.acquire()
    started = store.lease["started_at"]
    for _ in range(30):  # five minutes of 10 s heartbeats
        t.s += 10
        assert a.renew() is True and a.ok
    assert store.lease["started_at"] == started
    assert store.lease["expires_at"] == t.db() + timedelta(seconds=TTL_S)
    with pytest.raises(LeaseHeld):  # so another machine is still refused long after the first 90 s
        lease_for(store, t, "b").acquire()


def test_renewal_is_throttled_for_rapid_progress_callbacks():
    t, store = world()
    a = lease_for(store, t, "a")
    a.acquire()
    n = store.lease_calls
    for _ in range(50):
        t.s += 0.05
        assert a.renew() is True
    assert store.lease_calls == n
    t.s += lease_mod.RENEW_MIN_GAP_S
    a.renew()
    assert store.lease_calls == n + 1


def test_heartbeat_renews_and_reports_the_lease():
    t, store = world()
    eng, a, stop = StubEngine(store), lease_for(store, t, "a"), {"now": False}
    engine_main.start_with_lease(eng, a)
    beat = engine_main.make_beat(eng, a, "host-a", stop)
    t.s += 10
    beat({"state": "trading"})
    assert store.beats["engine"]["lease"] == "ok" and store.beats["engine"]["host"] == "host-a"
    assert store.lease["renewed_at"] == t.db() and not stop["now"]


# ---------- takeover after expiry ----------
def test_dead_holder_is_taken_over_only_after_the_lease_expires():
    t, store = world()
    a, b = lease_for(store, t, "a"), lease_for(store, t, "b")
    a.acquire()
    t.s += TTL_S - 1  # A died right after acquiring; 89 s later it still blocks B
    with pytest.raises(LeaseHeld) as e:
        b.acquire()
    assert "about 1 s more" in str(e.value)
    t.s += 1
    out = b.acquire()
    assert out["acquired"] and out["taken_over"] and out["previous_host"] == "host-a"
    assert store.lease["holder"] == "holder-b" and store.lease["started_at"] == t.db()


def test_takeover_is_recorded_as_an_event():
    t, store = world()
    lease_for(store, t, "a").acquire()
    t.s += TTL_S + 5
    eng = StubEngine(store)
    engine_main.start_with_lease(eng, lease_for(store, t, "b"))
    ev = [e for e in store.tables["event"] if e["type"] == "engine_lease_takeover"]
    assert ev and ev[0]["payload"] == {"host": "host-b", "previous_host": "host-a"} and eng.started == 1


def test_old_holder_that_comes_back_after_a_takeover_stops_and_leaves_the_new_lease_alone(caplog):
    t, store = world()
    eng_a, a, stop = StubEngine(store), lease_for(store, t, "a"), {"now": False}
    engine_main.start_with_lease(eng_a, a)
    beat = engine_main.make_beat(eng_a, a, "host-a", stop)
    t.s += 10
    beat({"state": "trading"})
    t.s += 600  # A's laptop sleeps for ten minutes; B starts meanwhile
    assert not a.ok  # A may not open anything from the moment it wakes, before it has even asked
    b = lease_for(store, t, "b")
    b.acquire()
    b_beat = dict(state="trading", host="host-b")
    store.heartbeat("engine", b_beat)
    with caplog.at_level("ERROR"):
        beat({"state": "trading"})
    assert stop == {"now": True, "lease_lost": True} and "no longer ours" in caplog.text
    assert a.status() == "lost" and not a.ok
    assert store.beats["engine"] is b_beat  # A did not overwrite B's heartbeat
    assert a.release() is False and store.lease["holder"] == "holder-b"  # nor release B's lease
    with pytest.raises(LeaseLost):
        a.lost, a.held = False, True  # even if asked again, the database answer is the same
        a.renew(force=True)
    assert b.renew(force=True) is True


def test_same_machine_restart_after_a_crash_is_not_locked_out(tmp_path):
    t, store = world()
    hid = holder_id(tmp_path, "mac")
    assert hid.startswith("mac-") and holder_id(tmp_path, "mac") == hid  # stable across restarts
    assert holder_id(tmp_path / "other", "mac") != hid  # another install is another holder
    EngineLease(store, hid, "mac", mono=t.mono, wall=t.wall).acquire()
    started = store.lease["started_at"]
    t.s += 3  # SIGKILL, restart 3 s later: no clean release happened
    out = EngineLease(store, holder_id(tmp_path, "mac"), "mac", mono=t.mono, wall=t.wall).acquire()
    assert out["acquired"] and not out["taken_over"] and store.lease["started_at"] == started


def test_holder_id_survives_an_unwritable_data_dir(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert holder_id(blocker / "sub", "vm").startswith("vm-")  # cannot create: falls back to a fresh id
    assert holder_id(None, "vm").startswith("vm-")


# ---------- clean release ----------
def test_clean_stop_releases_so_another_machine_can_start_at_once():
    t, store = world()
    a = lease_for(store, t, "a")
    a.acquire()
    assert a.release() is True and store.lease is None and not a.ok
    out = lease_for(store, t, "b").acquire()
    assert out["acquired"] and not out["taken_over"]
    assert a.release() is False and store.lease["holder"] == "holder-b"  # a second release cannot hurt B
    assert a.renew(force=True) is False


def test_release_failure_is_swallowed_and_the_lease_just_expires():
    t, store = world()
    a = lease_for(store, t, "a")
    a.acquire()
    store.lease_fail = True
    assert a.release() is False
    store.lease_fail = False
    with pytest.raises(LeaseHeld):
        lease_for(store, t, "b").acquire()
    t.s += TTL_S
    assert lease_for(store, t, "b").acquire()["acquired"]


# ---------- fail closed: the lease cannot be checked ----------
def test_lease_check_failure_at_startup_means_no_start(caplog):
    t, store = world()
    store.lease_fail = True
    eng = StubEngine(store)
    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as e:
        engine_main.start_with_lease(eng, lease_for(store, t, "a"))
    assert e.value.code == EXIT_LEASE
    assert "could not be checked" in caplog.text and "migration 0013" in caplog.text
    assert eng.started == 0 and eng.lease is None and store.lease is None


@pytest.mark.parametrize("answer", [None, [], {}, {"acquired": "yes"}, {"acquired": 1}, True])
def test_an_odd_answer_is_not_a_lease(answer):
    t, store = world()
    store.lease_acquire = lambda *a: answer
    with pytest.raises((LeaseUnavailable, LeaseHeld)):
        lease_for(store, t, "a").acquire()


@pytest.mark.parametrize("answer", [None, 1, "true", {}])
def test_an_odd_renewal_answer_counts_as_lost(answer):
    t, store = world()
    a = lease_for(store, t, "a")
    a.acquire()
    store.lease_renew = lambda *x: answer
    with pytest.raises(LeaseLost):
        a.renew(force=True)
    assert not a.ok


def test_failed_renewal_blocks_until_one_succeeds_again():
    t, store = world()
    a = lease_for(store, t, "a")
    a.acquire()
    store.lease_fail = True
    t.s += 10
    assert a.renew() is False and not a.ok and a.status() == "unverified"
    store.lease_fail = False
    t.s += 10
    assert a.renew() is True and a.ok and a.status() == "ok"


def test_a_lease_not_renewed_for_the_ttl_is_not_trusted_on_either_clock():
    t, store = world()
    a = lease_for(store, t, "a")
    a.acquire()
    t.s += TTL_S - 1
    assert a.ok
    t.s += 1
    assert not a.ok
    a.renew(force=True)
    assert a.ok
    a.mono = lambda: t.s - 500  # a sleeping laptop: the monotonic clock stood still, the wall clock did not
    t.s += TTL_S
    assert not a.ok


def _engine_day(tmp_path, lease_fail_from=None, heal_at=None):
    """The synthetic day of test_engine with a candidate at minute 54 and 64, a lease, and a heartbeat per tick."""
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    t = Time()
    store.lease_now = t.db
    lease = lease_for(store, t, "a")
    lease.acquire()
    eng.lease = lease
    beat = engine_main.make_beat(eng, lease, "host-a", {"now": False})
    fake_candidate(eng, at=(54, 64))

    def hook(k, e):
        t.s += 60
        if lease_fail_from is not None and k == lease_fail_from:
            store.lease_fail = True
        if heal_at is not None and k == heal_at:
            store.lease_fail = False
        beat({"state": "trading"})

    run_day(eng, broker, clock, open_utc, until=80, hook=hook)
    return eng, store


def test_engine_with_a_healthy_lease_trades_as_before(tmp_path):
    eng, store = _engine_day(tmp_path)
    assert len(store.tables["trade"]) == 2 and eng.lease.ok  # the candidates at minutes 54 and 64


def test_lease_check_failure_means_no_entries(tmp_path):
    eng, store = _engine_day(tmp_path, lease_fail_from=10)
    assert "trade" not in store.tables and "order" not in store.tables
    assert not eng.lease_ok() and store.beats["engine"]["lease"] == "unverified"
    assert not eng.broker.orders  # nothing reached the broker


def test_entries_come_back_when_the_lease_is_verified_again(tmp_path):
    eng, store = _engine_day(tmp_path, lease_fail_from=10, heal_at=58)
    trades = store.tables["trade"]
    assert len(trades) == 1 and eng.lease.ok  # minute 54 was skipped, minute 64 traded
    sig = next(s for s in store.tables["signal"] if s["id"] == trades[0]["signal_id"])
    assert sig["features"]["m"] == 64


def test_gate_sees_the_kill_switch_when_the_lease_is_unverified(tmp_path):
    """Second lock: even if a caller skipped _may_enter, the risk gate refuses while the lease is not ours."""
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    t = Time()
    store.lease_now = t.db
    eng.lease = lease_for(store, t, "a")
    eng.lease.acquire()
    fake_candidate(eng)
    eng._may_enter = lambda m: True
    store.lease_fail = True
    t.s += 10
    eng.lease.renew()
    run_day(eng, broker, clock, open_utc, until=80)
    assert "trade" not in store.tables
    gates = store.tables["gate_run"]
    assert gates and all(not g["approved"] for g in gates)


def test_engine_without_a_lease_object_is_unchanged(tmp_path):
    """Replays and older tests build an Engine with no lease: nothing is enforced there."""
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    assert eng.lease is None and eng.lease_ok()


# ---------- the migration and the store agree ----------
SQL = (ROOT / "migrations" / "0013_engine_lease.sql").read_text()


def test_store_calls_match_the_migration(monkeypatch):
    from kaibot.engine.store import Store

    sent = []
    monkeypatch.setattr(Store, "_call", lambda self, method, path, **kw: sent.append((method, path, kw)) or True)
    s = Store("https://example.supabase.co", "k")
    s.lease_acquire("h", "host", 90)
    s.lease_renew("h", 90)
    s.lease_release("h")
    for method, path, kw in sent:
        fn = path.removeprefix("rpc/")
        assert method == "POST" and path.startswith("rpc/engine_lease_")
        sig = re.search(rf"function kaibot\.{fn}\(([^)]*)\)", SQL).group(1)
        params = [p.split()[0] for p in sig.split(",")]
        assert sorted(kw["body"]) == sorted(params)
        assert kw["attempts"] * kw["timeout"] < TTL_S / 2  # a lease call answers well inside the lease


def test_migration_is_service_role_only_and_single_row():
    assert "primary key default true check (id)" in SQL
    assert "enable row level security" in SQL
    assert re.search(r"revoke all on function .*from public, anon, authenticated;", SQL, re.S)
    assert re.search(r"grant execute on function .*to service_role;", SQL, re.S)
    assert "revoke insert, update, delete on kaibot.engine_lease from service_role" in SQL
    assert "0013_engine_lease.sql" in {p.name for p in (ROOT / "migrations").glob("0013_*")}
    assert len(list((ROOT / "migrations").glob("0013_*"))) == 1  # the number is not shared


# ---------- the same story on real SQL (skipped in CI) ----------
PG = os.environ.get("KAIBOT_TEST_PG")


class PgStore:
    """Calls the real functions through psql, with the Store's lease method names."""

    def _q(self, sql: str):
        out = subprocess.run(["psql", *shlex.split(PG), "-qAt", "-v", "ON_ERROR_STOP=1", "-c", sql],
                             capture_output=True, text=True, check=True).stdout.strip()
        return json.loads(out) if out else None

    def lease_acquire(self, holder, host, ttl_s):
        return self._q(f"select kaibot.engine_lease_acquire('{holder}', '{host}', {ttl_s})")

    def lease_renew(self, holder, ttl_s):
        return self._q(f"select to_jsonb(kaibot.engine_lease_renew('{holder}', {ttl_s}))")

    def lease_release(self, holder):
        return self._q(f"select to_jsonb(kaibot.engine_lease_release('{holder}'))")

    def age(self, seconds):  # pretend time passed: move the row into the past
        self._q(f"update kaibot.engine_lease set expires_at = expires_at - interval '{seconds} s'")


@pytest.mark.skipif(not PG, reason="set KAIBOT_TEST_PG to a scratch Postgres with migration 0013 applied")
def test_real_sql_race_renew_takeover_release():
    store = PgStore()
    store._q("delete from kaibot.engine_lease")
    for _ in range(10):
        gate, got = threading.Barrier(4), []

        def boot(i, gate=gate, got=got):
            gate.wait()
            try:
                EngineLease(store, f"h{i}", f"host{i}").acquire()
                got.append(i)
            except LeaseHeld:
                pass

        th = [threading.Thread(target=boot, args=(i,)) for i in range(4)]
        [x.start() for x in th]
        [x.join() for x in th]
        assert len(got) == 1
        store._q("delete from kaibot.engine_lease")
    a, b = EngineLease(store, "A", "mac"), EngineLease(store, "B", "vm")
    assert a.acquire()["acquired"] and a.renew(force=True)
    with pytest.raises(LeaseHeld) as e:
        b.acquire()
    assert "'mac'" in str(e.value)
    assert EngineLease(store, "A", "mac").acquire()["taken_over"] is False  # same holder restarts at once
    store.age(TTL_S - 5)
    with pytest.raises(LeaseHeld):
        b.acquire()
    store.age(6)
    out = b.acquire()
    assert out["taken_over"] and out["previous_host"] == "mac"
    with pytest.raises(LeaseLost):
        a.renew(force=True)
    assert a.release() is False and b.release() is True
    assert EngineLease(store, "C", "x").acquire()["taken_over"] is False
    with pytest.raises(LeaseUnavailable):
        EngineLease(store, "D", "x", ttl_s=5).acquire()  # the function refuses a silly TTL
    store._q("delete from kaibot.engine_lease")
