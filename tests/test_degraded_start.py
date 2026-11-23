"""M10 1.25 (#450): a start while Supabase is down. When the journal's last lease record is this machine's own and
nothing at the broker contradicts it, the engine comes up "reducing only" (the lease counts as unverified: exits,
stops, sales and fills go on, nothing is bought, no command is applied, no heartbeat and no row is sent) until the
lease is taken and the journal's identity is verified; then it is fully active. Two engines never both trade:
docs/ops/failsafe.md, "Degraded start". The interleavings below are that argument, run."""

from __future__ import annotations

import shutil
from datetime import timedelta

import pandas as pd
import pytest
from fakes import FakeStore
from test_engine_lease import Time
from test_outage_drill_multiday import D1, HELD, MD, NEW, SEND, Drill, Keeps, _open

from kaibot.engine import __main__ as engine_main
from kaibot.engine import live_daily as ld
from kaibot.engine.core import Engine
from kaibot.engine.journal import Journal, JournalStore
from kaibot.engine.lease import EXIT_LEASE, EngineLease
from kaibot.engine.market import LiveMarket
from kaibot.engine.store import StoreError
from kaibot.risk.profiles import load_profiles
from kaibot.strategies.base import TargetList


class Supabase(FakeStore):
    """The simulated store with a switch: `down` makes every call fail the way an unreachable Supabase does."""

    def __init__(self):
        super().__init__()
        self.down = False
        self.writes = 0

    def _gate(self, write=False):
        if self.down:
            raise StoreError("POST: <urlopen error [Errno 111] Connection refused>")
        self.writes += write

    def _lease_guard(self):
        self._gate()
        super()._lease_guard()

    def insert(self, table, row, returning=True):
        self._gate(True)
        return super().insert(table, row, returning)

    def upsert(self, table, row, on_conflict):
        self._gate(True)
        return super().upsert(table, row, on_conflict)

    def update(self, table, match, values):
        self._gate(True)
        return super().update(table, match, values)

    def select(self, table, params):
        self._gate()
        return super().select(table, params)

    def heartbeat(self, process, detail):
        self._gate(True)
        super().upsert("heartbeat", {"process": process, "detail": detail}, "process")
        return super().heartbeat(process, detail)

    def pending_commands(self):
        self._gate()
        return super().pending_commands()

    def active_run(self):
        self._gate()
        return super().active_run()


class Broker:
    """What the evidence check reads: open orders, fills since a time, an order by its id."""

    def __init__(self, open_=(), fills=(), orders=None, fail=False):
        self.open, self.fills, self.by_id, self.fail = list(open_), list(fills), dict(orders or {}), fail
        self.asked_after = None

    def open_orders(self):
        if self.fail:
            raise ConnectionError("alpaca unreachable")
        return self.open

    def activities(self, after=None, types=("FILL",)):
        self.asked_after = after
        return self.fills

    def order(self, order_id):
        return self.by_id[order_id]


class Stub:
    """Just enough of Engine for the start: a store on a journal, a broker, and a record of what ran."""

    process = "engine"

    def __init__(self, store, broker=None):
        self.store, self.journal, self.broker = store, store.journal, broker or Broker()
        self.lease, self.started, self.commands = None, 0, 0

    def startup(self):
        self.started += 1

    def apply_commands(self):
        self.commands += 1

    def _rid(self):
        return None


class World:
    """One machine's data folder (journal, holder id) and one Supabase, on one clock."""

    def __init__(self, tmp_path):
        self.t, self.db, self.path = Time(), Supabase(), tmp_path / "journal.sqlite"
        self.db.lease_now = self.t.db

    def engine(self, broker=None, path=None) -> Stub:
        return Stub(JournalStore(Journal(path or self.path), self.db, live=True), broker)

    def lease(self, eng, name="mac") -> EngineLease:
        return EngineLease(eng.store, f"holder-{name}", f"host-{name}", mono=self.t.mono, wall=self.t.wall,
                           journal=eng.journal)

    def ran_once(self, stop="crash") -> None:
        """This machine ran the engine with Supabase up, then stopped (a crash leaves the lease to expire)."""
        eng = self.engine()
        lease = self.lease(eng)
        assert engine_main.start_with_lease(eng, lease) == "active"
        eng.store.heartbeat("engine", {"state": "trading"})
        if stop == "clean":
            lease.release()
        eng.journal.close()

    def records(self, eng) -> list[str]:
        return [b["state"] for _, _, _, b in eng.journal.entries(kinds=("lease",))]


def start(w: World, eng, name="mac"):
    lease = w.lease(eng, name)
    return lease, engine_main.start_with_lease(eng, lease)


# ======================================================================= when a degraded start is allowed
@pytest.mark.parametrize("stop", ["crash", "clean"])
def test_supabase_down_at_start_and_the_last_lease_was_ours_the_engine_comes_up_reducing_only(tmp_path, stop, caplog):
    w = World(tmp_path)
    w.ran_once(stop)
    w.db.down = True
    eng = w.engine()
    with caplog.at_level("WARNING"):
        lease, mode = start(w, eng)
    assert mode == "degraded" and eng.started == 1 and eng.lease is lease
    assert lease.degraded and not lease.ok and not lease.held and lease.status() == "degraded"
    assert lease.reducing_only  # the hook for the trading state of ticket 1.21
    assert "REDUCING ONLY" in caplog.text and "failsafe.md" in caplog.text
    assert w.records(eng)[-1] == "degraded_start"
    ev = eng.store.tables["event"][-1]
    assert ev["type"] == "engine_degraded_start" and "last lease" in ev["payload"]["why"]


def test_a_second_degraded_start_in_the_same_outage_is_allowed_too(tmp_path):
    w = World(tmp_path)
    w.ran_once()
    w.db.down = True
    for _ in range(2):
        eng = w.engine()
        assert start(w, eng)[1] == "degraded"
        eng.journal.close()


def refused(w, eng, name="mac") -> int:
    with pytest.raises(SystemExit) as e:
        start(w, eng, name)
    assert eng.started == 0 and eng.lease is None
    return e.value.code


def test_without_a_lease_record_of_its_own_the_engine_does_not_start_as_before(tmp_path, caplog):
    """A journal from before this change, or one that never held the lease: exit code 3, nothing done."""
    w = World(tmp_path)
    eng = w.engine()
    eng.store.bootstrap()
    eng.journal.close()
    w.db.down = True
    eng = w.engine()
    with caplog.at_level("ERROR"):
        assert refused(w, eng) == EXIT_LEASE
    assert "could not be checked" in caplog.text and "no lease record" in caplog.text


def test_a_new_journal_never_starts_degraded(tmp_path):
    w = World(tmp_path)
    w.db.down = True
    assert refused(w, w.engine()) == EXIT_LEASE


def test_a_journal_and_holder_file_of_another_machine_do_not_start_degraded(tmp_path, caplog):
    w = World(tmp_path)
    w.ran_once()
    w.db.down = True
    with caplog.at_level("ERROR"):
        assert refused(w, w.engine(), name="vm") == EXIT_LEASE  # the journal was copied; the holder id is the VM's
    assert "another machine" in caplog.text


def test_a_data_folder_copied_whole_to_another_machine_does_not_start_degraded(tmp_path, caplog):
    """QA-Ops (#536): the journal AND its holder file copied to another machine (the holder id is this install's,
    the host is not): no degraded start there. The lease record names the host it was written on."""
    w = World(tmp_path)
    w.ran_once()
    w.db.down = True
    eng = w.engine()
    lease = EngineLease(eng.store, "holder-mac", "host-vm", mono=w.t.mono, wall=w.t.wall, journal=eng.journal)
    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as e:
        engine_main.start_with_lease(eng, lease)
    assert e.value.code == EXIT_LEASE and eng.started == 0 and eng.lease is None
    assert "data folder was copied" in caplog.text


@pytest.mark.parametrize("record_machine", ["other", "none"])
def test_a_data_folder_copied_to_a_machine_with_the_same_host_name_does_not_start_degraded(tmp_path, caplog,
                                                                                           record_machine):
    """#562: the host name can be the same on two machines (or a record was written before #562 and names no
    machine): the machine key in the lease record tells them apart; fail closed."""
    w = World(tmp_path)
    if record_machine == "other":
        w.ran_once()  # written with this test host's machine key
        machine = "a-different-machine-key"
    else:
        eng0 = w.engine()
        eng0.journal.append("lease", {"state": "acquired", "holder": "holder-mac", "host": "host-mac"})
        eng0.journal.close()
        machine = None
    w.db.down = True
    eng = w.engine()
    lease = EngineLease(eng.store, "holder-mac", "host-mac", mono=w.t.mono, wall=w.t.wall, journal=eng.journal,
                        machine=machine)
    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as e:
        engine_main.start_with_lease(eng, lease)
    assert e.value.code == EXIT_LEASE and eng.started == 0
    assert ("another machine with this host name" if record_machine == "other" else "names no machine") in caplog.text


def test_the_lease_record_names_the_machine(tmp_path):
    w = World(tmp_path)
    w.ran_once()
    eng = w.engine()
    rec = [b for *_, b in eng.journal.entries(kinds=("lease",))][-1]
    assert rec["machine"] == w.lease(eng).machine and rec["machine"]


@pytest.mark.parametrize("how", ["lost", "held_by_other"])
def test_a_record_that_another_engine_took_over_is_newer_evidence_and_no_degraded_start(tmp_path, how, caplog):
    w = World(tmp_path)
    eng = w.engine()
    lease, _ = start(w, eng)
    if how == "lost":  # this engine ran, slept, and found the lease taken when it woke
        w.t.s += 600
        w.lease(w.engine(path=tmp_path / "b.sqlite"), "vm").acquire()
        with pytest.raises(Exception, match="no longer ours"):
            lease.renew(force=True)
    else:  # this engine stopped cleanly; a later start found another engine holding the lease
        lease.release()
        w.lease(w.engine(path=tmp_path / "b.sqlite"), "vm").acquire()
        eng.journal.close()
        assert refused(w, w.engine()) == EXIT_LEASE
    assert w.records(w.engine())[-1] == how
    w.db.down = True
    with caplog.at_level("ERROR"):
        assert refused(w, w.engine()) == EXIT_LEASE
    assert "another engine" in caplog.text


# ---------------------------------------------------------------- the broker's word
def known_order(eng) -> None:
    eng.journal.append("om_intent", {"coid": "kb3-trend_momentum-B-20260804-target-1", "symbol": "B", "side": "sell"})
    eng.journal.append("om_state", {"coid": "kb3-trend_momentum-B-20260804-target-1", "state": "submitted",
                                    "order_id": "o-1", "client_id": "kb3-trend_momentum-B-20260804-stop-2"})


def o(coid, oid="o-9", **kw):
    return {"id": oid, "client_order_id": coid, "symbol": "B", "side": "buy", **kw}


@pytest.mark.parametrize("case, broker", [
    ("an open order of another engine", Broker(open_=[o("kb3-trend_momentum-D-20260804-target-1")])),
    ("its leg", Broker(open_=[o("x", legs=[o("kb-3-17-1", "o-10")])])),
    ("a fill on an order of another engine", Broker(fills=[{"id": "a1", "order_id": "o-9"}],
                                                    orders={"o-9": o("kb3-trend_momentum-D-20260804-target-1")})),
    ("the broker cannot be read", Broker(fail=True)),
    ("a fill whose order cannot be read", Broker(fills=[{"id": "a1", "order_id": "o-404"}])),
])
def test_an_engine_order_this_journal_does_not_know_refuses_the_degraded_start(tmp_path, case, broker, caplog):
    w = World(tmp_path)
    w.ran_once()
    w.db.down = True
    eng = w.engine(broker)
    known_order(eng)
    with caplog.at_level("ERROR"):
        assert refused(w, eng) == EXIT_LEASE
    assert "another engine" in caplog.text or "cannot be read" in caplog.text
    assert w.records(eng)[-1] == "contradicted"
    eng.journal.close()
    assert refused(w, w.engine()) == EXIT_LEASE  # and it stays refused until a lease is taken from Supabase
    w.db.down = False
    eng = w.engine()
    assert start(w, eng)[1] == "active" and w.records(eng)[-1] == "acquired"


def test_its_own_orders_the_fail_safes_stops_and_strangers_are_not_another_engine(tmp_path):
    w = World(tmp_path)
    w.ran_once()
    w.db.down = True
    broker = Broker(
        open_=[o("kb3-trend_momentum-B-20260804-target-1", "o-1"),  # journalled before it was sent
               o("kb3-trend_momentum-B-20260804-stop-2", "o-2"),  # the client id a replace gave it
               o("anything", "o-1"),  # a venue id the journal knows
               o("kbwd-trend_momentum-B-20260804T1401-wdstop", "o-3"),  # the watchdog's stop
               o("kbdm-trend_momentum-C-20260804T1401-dmstop", "o-4"),  # the dead-man's
               o("probe-idem-1", "o-5"), o(None, "o-6")],  # not an engine's id: the account check's business
        fills=[{"id": "a1", "order_id": "o-1"}, {"id": "a2", "order_id": "o-7"}],
        orders={"o-7": o("3f0e-alpaca-generated", "o-7")})
    eng = w.engine(broker)
    known_order(eng)
    assert start(w, eng)[1] == "degraded"
    assert broker.asked_after is not None  # fills since shortly before the journal's last entry


# ======================================================================= what a degraded engine does not do
def test_reducing_only_means_no_heartbeat_no_renewal_no_command_and_no_row_to_supabase(tmp_path):
    w = World(tmp_path)
    w.ran_once()
    w.db.down = True
    eng = w.engine()
    lease, _ = start(w, eng)
    w.db.down = False  # Supabase is back, but the lease RPC still fails (say): nothing is confirmed yet
    w.db.lease_fail = True
    calls, writes, stop = w.db.lease_calls, w.db.writes, {"now": False}
    beat = engine_main.make_beat(eng, lease, "host-mac", stop)
    for _ in range(3):
        w.t.s += 10
        assert engine_main.before_tick(eng, lease) is False
        beat({"state": "trading"})
    assert eng.commands == 0  # a command applied here would be taken from the engine that holds the lease
    assert w.db.writes == writes and w.db.beats.get("engine", {}).get("state") == "trading"  # the old beat stays
    assert "lease" not in w.db.beats["engine"]
    assert w.db.lease_calls == calls + 3  # one acquire attempt a pass, no renewal
    assert not lease.ok and not stop["now"]


# ======================================================================= Supabase is back
def degraded(tmp_path, stop="crash"):
    w = World(tmp_path)
    w.ran_once(stop)
    w.db.down = True
    eng = w.engine()
    lease, mode = start(w, eng)
    assert mode == "degraded"
    return w, eng, lease


def test_supabase_back_and_the_lease_is_still_ours_the_engine_becomes_active(tmp_path, caplog):
    w, eng, lease = degraded(tmp_path)
    assert engine_main.before_tick(eng, lease) is False and eng.commands == 0  # still down
    w.t.s += 300
    w.db.down = False
    with caplog.at_level("INFO"):
        assert engine_main.before_tick(eng, lease) is True
    assert lease.ok and lease.held and not lease.degraded and lease.status() == "ok" and eng.commands == 1
    assert w.db.lease["holder"] == "holder-mac"
    assert w.records(eng)[-2:] == ["degraded_start", "acquired"]
    assert eng.store.tables["event"][-1]["type"] == "engine_lease_confirmed"
    assert "fully active" in caplog.text
    assert engine_main.before_tick(eng, lease) is False and eng.commands == 2  # a normal pass from here on


def test_supabase_back_and_another_engine_holds_the_lease_this_one_stops(tmp_path, caplog):
    w, eng, lease = degraded(tmp_path)
    w.t.s += 300
    other = w.engine(path=tmp_path / "b.sqlite")
    w.db.down = False
    w.lease(other, "vm").acquire()  # the VM took the lease while this machine was cut off
    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as e:
        engine_main.before_tick(eng, lease)
    assert e.value.code == EXIT_LEASE and "Another Kaibot engine holds the lease" in caplog.text
    assert not lease.ok and eng.commands == 0 and w.db.lease["holder"] == "holder-vm"
    assert w.records(eng)[-1] == "held_by_other"


@pytest.mark.parametrize("how", ["released", "expired"])
def test_supabase_back_and_another_journal_ran_the_engine_meanwhile_this_one_stops(tmp_path, how, caplog):
    """The lease is free again (the other engine stopped, or died and its lease ran out), so it is taken; the
    journal check then finds that another journal ran the engine after this one: stale, exit code 4."""
    w, eng, lease = degraded(tmp_path)
    w.t.s += 300
    w.db.down = False
    vm = w.engine(path=tmp_path / "b.sqlite")
    vm_lease = w.lease(vm, "vm")
    vm_lease.acquire()
    vm.store.bootstrap()
    vm.store.heartbeat("engine", {"state": "trading", "host": "host-vm"})
    if how == "released":
        vm_lease.release()
    else:
        w.t.s += 200
    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as e:
        engine_main.before_tick(eng, lease)
    assert e.value.code == engine_main.EXIT_JOURNAL == 4 and "stale standby" in caplog.text
    assert not lease.ok and eng.commands == 0
    assert w.db.lease is None  # given back: this engine does not keep a lease it may not use


def test_the_lease_is_taken_but_the_journal_cannot_be_verified_yet_stays_reducing_only(tmp_path):
    w, eng, lease = degraded(tmp_path)
    w.db.down = False
    real = w.db.select
    w.db.select = lambda table, params: (_ for _ in ()).throw(StoreError("GET heartbeat -> 503: unavailable"))
    assert engine_main.before_tick(eng, lease) is False
    assert lease.held and lease.degraded and not lease.ok and eng.commands == 0
    w.db.select = real
    assert engine_main.before_tick(eng, lease) is True and lease.ok


def test_a_normal_start_is_what_it_was_and_records_the_lease(tmp_path):
    w = World(tmp_path)
    eng = w.engine()
    lease, mode = start(w, eng)
    assert mode == "active" and lease.ok and not lease.degraded and not lease.reducing_only
    assert w.records(eng) == ["acquired"]
    assert engine_main.before_tick(eng, lease) is False and eng.commands == 1
    lease.release()
    assert w.records(eng) == ["acquired", "released"]


# ======================================================================= the drill, with the real engine
def sells_c(self, ctx):
    """The drill rule's decision, which also sells C."""
    want = ["B", NEW]
    return TargetList(self.name, ctx.fill_session, {s: 1.0 for s in want},
                      features={"keep": [s for s in want if s in ctx.held],
                                "buy": [s for s in want if s not in ctx.held], "sell": {"C": "rank"}}, resize=False)


class Machine(Drill):
    """The outage drill's engine as the paper engine is built: its store is the journal, Supabase is behind it."""

    def __init__(self, tmp_path, monkeypatch, sells=False):
        self.db, self.t = Supabase(), Time()
        self.db.lease_now = self.t.db
        if sells:
            monkeypatch.setattr(Keeps, "on_event", sells_c)
        super().__init__(tmp_path, monkeypatch, laptop=True)

    def engine(self, path=None, name="mac") -> Engine:
        self.store = self.db  # the drill's own writes (the new-run command, the heartbeat) go to Supabase
        market = LiveMarket(self.data, ["A", "B", "C", NEW], {s: "XLK" for s in ("A", "B", "C", NEW)}, "SPY",
                            self.tmp / "hist", lambda: pd.Series(18.0, index=pd.date_range("2025-01-01", "2026-12-31")))
        store = JournalStore(Journal(path or self.jpath), self.db, live=True, mono=self.t.mono)
        eng = Engine(self.broker, market, store, load_profiles(), set(), now=self.clock)
        eng.market.candidates_at = lambda m: pd.DataFrame()
        eng.daily = ld.for_engine(eng, self.load, rules=[self.rule])
        self.lease = EngineLease(store, f"holder-{name}", f"host-{name}", mono=self.t.mono, wall=self.t.wall,
                                 journal=store.journal)
        return eng

    def boot(self, eng=None, lease=None) -> str:
        return engine_main.start_with_lease(eng or self.eng, lease or self.lease)

    def minute(self, eng, lease, day, k) -> None:
        """One pass of the main loop at minute k of the session."""
        bar = _open(day) + timedelta(minutes=k)
        self.broker.advance(pd.Timestamp(bar))
        self.clock.t = bar + timedelta(minutes=1, seconds=7)
        self.t.s += 60
        engine_main.before_tick(eng, lease)
        eng.tick()

    def orders(self, side, sym=None):
        return [x for x in self.broker.orders.values() if x["side"] == side and x["type"] == "market"
                and (sym is None or x["symbol"] == sym)]


def machine(tmp_path, monkeypatch, sells=False) -> Machine:
    """D0 on a healthy machine: the book holds B and C, the evening's decision is journalled, the engine dies."""
    m = Machine(tmp_path, monkeypatch, sells)
    m.eng.lease = m.lease
    m.lease.acquire()
    m.d0()
    m.eng.journal.close()
    m.writes_d0 = m.db.writes
    return m


def test_drill_supabase_down_at_start_reducing_only_then_supabase_back_and_active(tmp_path, monkeypatch):
    m = machine(tmp_path, monkeypatch)
    m.db.down = True  # Supabase is unreachable when the engine is started at 09:00 ET on D1
    m.clock.t = _open(D1) - timedelta(minutes=30)
    eng = m.engine()
    assert m.boot(eng) == "degraded"
    assert eng.entry_block() == "lease unverified" and set(eng._md_sleeves()) == {(MD, s) for s in HELD}
    for _ in range(3):  # loop passes before the open: still down, still reducing only
        m.t.s += 60
        assert engine_main.before_tick(eng, m.lease) is False
        eng.tick()
    assert not m.lease.ok and m.db.writes == m.writes_d0  # nothing reached Supabase: no heartbeat, no row
    m.db.down = False  # Supabase is back at 09:20
    m.t.s += 60
    assert engine_main.before_tick(eng, m.lease) is True
    assert m.lease.ok and m.db.lease["holder"] == "holder-mac"
    for k in range(SEND + 4):  # the session, to 15:54 ET
        m.minute(eng, m.lease, D1, k)
        if k < SEND:
            assert not m.orders("buy")
    assert eng.entry_block() is None and eng.kill is None
    assert [x["symbol"] for x in m.orders("buy")] == [NEW]  # fully active: the rule's buy goes out, once
    assert float(m.broker.pos[NEW]["qty"]) > 0 and {s: float(m.broker.pos[s]["qty"]) for s in HELD} == HELD
    types = [e["type"] for e in eng.store.tables["event"]]
    assert types.index("engine_degraded_start") < types.index("engine_lease_confirmed")


def degraded_session(tmp_path, monkeypatch, start_k=None):
    """D0 healthy; on D1 Supabase is down and the engine starts degraded: before the open (`start_k` None) or at
    minute `start_k` of the session (a restart, test-eng's drill on #536: killed 09:31, back up at 09:41)."""
    m = machine(tmp_path, monkeypatch)
    m.db.down = True
    m.clock.t = _open(D1) - timedelta(minutes=30) if start_k is None else _open(D1) + timedelta(minutes=start_k)
    eng = m.engine()
    eng.monotonic = m.t.mono  # the config retry's minute on the drill's clock
    assert m.boot(eng) == "degraded"
    return m, eng


def run(m, eng, ks) -> None:
    for k in ks:
        m.minute(eng, m.lease, D1, k)


def test_drill_supabase_back_inside_the_send_window_the_config_is_read_again_and_the_buy_goes_out(tmp_path,
                                                                                                   monkeypatch):
    m, eng = degraded_session(tmp_path, monkeypatch)
    run(m, eng, range(SEND + 2))  # the whole session up to 15:52 ET, two minutes into the 15:50-15:57 send window
    assert not m.orders("buy") and not m.lease.ok  # the decision buys D: nothing was bought
    assert {s: float(p["qty"]) for s, p in m.broker.pos.items()} == HELD  # and the book was kept
    assert eng.kill is None and m.db.writes == m.writes_d0
    assert eng.day.refused.startswith("config unreadable")  # the session began without the owner's config
    m.db.down = False  # Supabase is back at 15:52: the lease is confirmed and the config read again (#536)
    m.minute(eng, m.lease, D1, SEND + 2)
    assert m.lease.ok and eng.day.refused is None and eng.entry_block() is None
    run(m, eng, range(SEND + 3, SEND + 6))
    assert [x["symbol"] for x in m.orders("buy")] == [NEW]  # still inside the send window: the buy goes out, once
    types = [e["type"] for e in eng.store.tables["event"]]
    assert types.index("engine_lease_confirmed") < len(types) and "halt_cleared" in types


def test_drill_supabase_back_after_the_send_window_nothing_is_bought_that_day(tmp_path, monkeypatch):
    m, eng = degraded_session(tmp_path, monkeypatch)
    run(m, eng, range(SEND + 8))  # to 15:58 ET: the send window has closed
    m.db.down = False
    run(m, eng, range(SEND + 8, SEND + 10))
    assert m.lease.ok and eng.entry_block() is None  # entries are allowed again ...
    assert not m.orders("buy")  # ... but the window is gone: nothing is sent after it
    assert {s: float(p["qty"]) for s, p in m.broker.pos.items()} == HELD


def test_drill_restart_with_supabase_down_then_back_at_15_20_the_days_buy_still_goes_out(tmp_path, monkeypatch,
                                                                                          caplog):
    """Test-eng's finding on #536: a restart at 09:41 with Supabase down since 08:55, Supabase back at 15:20. The
    session began reducing only and could not read the owner's config; once the lease is confirmed it is read
    again, the log says "fully active" only then, and the 15:50 buy goes out."""
    m, eng = degraded_session(tmp_path, monkeypatch, start_k=11)
    back = 350  # 15:20 ET
    run(m, eng, range(11, back))
    assert not m.orders("buy") and eng.day.refused.startswith("config unreadable")
    m.db.down = False
    with caplog.at_level("INFO"):
        run(m, eng, range(back, SEND + 4))
    assert "fully active (entries allowed)" in caplog.text and "entries stay OFF" not in caplog.text
    ev = [e for e in eng.store.tables["event"] if e["type"] == "engine_lease_confirmed"]
    assert ev and ev[-1]["payload"]["entries"] is True
    assert eng.entry_block() is None and eng.kill is None
    assert [x["symbol"] for x in m.orders("buy")] == [NEW]
    assert float(m.broker.pos[NEW]["qty"]) > 0


class ConfigDown(Supabase):
    """Supabase up, but the `config` table cannot be read."""

    config_down = True

    def select(self, table, params):
        if table == "config" and self.config_down:
            raise StoreError("GET config: 503")
        return super().select(table, params)


def test_drill_config_still_unreadable_when_the_lease_is_confirmed_entries_stay_off_and_it_says_so(
        tmp_path, monkeypatch, caplog):
    m, eng = degraded_session(tmp_path, monkeypatch, start_k=11)
    run(m, eng, range(11, 300))
    m.db.__class__ = ConfigDown  # Supabase is back, but the owner's config still cannot be read
    m.db.down = False
    with caplog.at_level("INFO"):
        m.minute(eng, m.lease, D1, 300)
    assert m.lease.ok and not m.lease.degraded
    assert "entries stay OFF: config refused: config unreadable" in caplog.text
    assert "fully active (entries allowed)" not in caplog.text
    ev = [e for e in eng.store.tables["event"] if e["type"] == "engine_lease_confirmed"][-1]["payload"]
    assert ev["entries"] is False and ev["entries_off"].startswith("config refused: config unreadable")
    halts = len(eng.store.tables["halt"])
    run(m, eng, range(301, 320))  # asked again every minute: still unreadable, still refused, no new halt row
    assert eng.entry_block().startswith("config refused: config unreadable") and len(eng.store.tables["halt"]) == halts
    m.db.config_down = False  # readable again: lifted at the next retry, and the 15:50 buy goes out
    run(m, eng, range(320, SEND + 4))
    assert eng.day.refused is None and [x["symbol"] for x in m.orders("buy")] == [NEW]


def test_a_config_read_late_and_invalid_is_refused_as_at_the_open(tmp_path, monkeypatch):
    from kaibot.engine import core

    m, eng = degraded_session(tmp_path, monkeypatch, start_k=11)
    run(m, eng, range(11, 20))
    monkeypatch.setattr(eng, "_applied_config", lambda run_id, open_utc: {"version": 7, "body": {}})

    def bad(*a, **k):
        raise core.ConfigError("max_positions above the level's ceiling")

    monkeypatch.setattr(core, "tighten", bad)
    m.db.down = False
    run(m, eng, range(20, SEND + 4))
    assert eng.day.refused.startswith("config v7 rejected") and not m.orders("buy")
    assert eng.retry_rules(force=True) == eng.day.refused  # a rejected config is not asked again
    assert eng.store.tables["halt"][-1]["kind"] == "config"


def test_drill_a_degraded_engine_sells_what_its_rule_sells_and_buys_nothing(tmp_path, monkeypatch):
    m = machine(tmp_path, monkeypatch, sells=True)
    m.db.down = True
    m.clock.t = _open(D1) - timedelta(minutes=30)
    eng = m.engine()
    assert m.boot(eng) == "degraded"
    for k in range(SEND + 4):
        m.minute(eng, m.lease, D1, k)
    assert [(x["symbol"], float(x["qty"])) for x in m.orders("sell")] == [("C", 30.0)]  # the sale went out
    assert "C" not in m.broker.pos and not m.orders("buy")
    assert (MD, "C") not in eng._md_sleeves()  # and its fill is booked


@pytest.mark.parametrize("first", ["degraded", "holder"])
def test_two_engines_with_the_same_book_never_sell_a_position_twice_and_only_the_holder_buys(tmp_path, monkeypatch,
                                                                                               first):
    """The worst interleaving: this machine (A) is cut off from Supabase and starts degraded; meanwhile another
    machine (B) took the lease with the SAME book (the journal as it was) and trades. Both rules sell C at 15:50.
    Whoever sends first, the venue holds one sale of 30 shares: the second engine's order has the same client id and
    is refused. Only B, the lease holder, buys D."""
    m = machine(tmp_path, monkeypatch, sells=True)
    shutil.copy(m.jpath, tmp_path / "b.sqlite")
    m.t.s += 3600  # A's lease has long expired
    m.clock.t = _open(D1) - timedelta(minutes=30)
    b = m.engine(path=tmp_path / "b.sqlite", name="vm")
    b_lease = m.lease
    assert m.boot(b, b_lease) == "active" and m.db.lease["holder"] == "holder-vm"

    def cut_off(eng, lease, k):  # A's network cannot reach Supabase; B's can
        m.db.down = True
        try:
            m.minute(eng, lease, D1, k)
        finally:
            m.db.down = False

    m.db.down = True
    a = m.engine()
    a_lease = m.lease
    assert m.boot(a, a_lease) == "degraded"
    m.db.down = False
    for k in range(SEND + 5):
        steps = [lambda k=k: cut_off(a, a_lease, k), lambda k=k: m.minute(b, b_lease, D1, k)]
        for step in (steps if first == "degraded" else steps[::-1]):
            step()
            m.t.s -= 60  # one minute for both
        m.t.s += 60
        for sym, p in m.broker.pos.items():
            assert float(p["qty"]) >= 0, sym  # never short
    sells = m.orders("sell", "C")
    assert len(sells) == 1 and float(sells[0]["qty"]) == 30.0 and "C" not in m.broker.pos
    buys = m.orders("buy")
    assert [x["symbol"] for x in buys] == [NEW]
    assert not a_lease.ok and a_lease.degraded and b_lease.ok
    # A cannot buy for more than one reason: the lease, and soon the drift check (B's D is not in A's book)
    assert a.entry_block() in ("lease unverified", "halted: drift")
    assert sorted(m.broker.pos) == ["B", NEW] and float(m.broker.pos["B"]["qty"]) == HELD["B"]  # nothing else sold
    # B bought D; A never knew a buy: every buy at the venue is in B's journal and none is in A's
    coid = buys[0]["client_order_id"]
    assert [x for *_, x in b.journal.entries(kinds=("om_intent",)) if x["coid"] == coid]
    assert not [x for *_, x in a.journal.entries(kinds=("om_intent",)) if x["side"] == "buy"]
    # and when A reaches Supabase again it finds B's lease and stops
    with pytest.raises(SystemExit) as e:
        engine_main.before_tick(a, a_lease)
    assert e.value.code == EXIT_LEASE


def test_a_late_config_read_is_the_one_of_the_sessions_open_never_a_newer_one(tmp_path, monkeypatch):
    """QA-Ops (#536): the retry asks for the config as of the session's open (point-in-time), however late it runs."""
    m, eng = degraded_session(tmp_path, monkeypatch, start_k=11)
    run(m, eng, range(11, 20))
    asked = []
    monkeypatch.setattr(eng, "_applied_config", lambda run_id, open_utc: asked.append((run_id, open_utc)))
    m.db.down = False
    run(m, eng, range(20, 25))
    assert eng.day.refused is None and asked and set(asked) == {(eng.day.run_id, eng.day.open_utc)}


def test_a_config_read_and_rejected_is_never_read_again_that_session(tmp_path, monkeypatch):
    """QA-Ops (#536): only an UNREADABLE config is asked again; a rejected one is final for the day (no new read,
    no new halt row), so the minute retry cannot turn a refusal into an accepted config."""
    from kaibot.engine import core

    m, eng = degraded_session(tmp_path, monkeypatch, start_k=11)
    run(m, eng, range(11, 20))
    asked = []

    def applied(run_id, open_utc):
        asked.append(open_utc)
        return {"version": 7, "body": {}}

    def bad(*a, **k):
        raise core.ConfigError("max_positions above the level's ceiling")

    monkeypatch.setattr(eng, "_applied_config", applied)
    monkeypatch.setattr(core, "tighten", bad)
    m.db.down = False
    run(m, eng, range(20, 23))
    assert eng.day.refused.startswith("config v7 rejected") and len(asked) == 1
    halts = len(eng.store.tables["halt"])
    run(m, eng, range(23, 30))
    assert eng.retry_rules(force=True) == eng.day.refused
    assert len(asked) == 1 and len(eng.store.tables["halt"]) == halts


def test_the_config_retry_runs_only_while_the_lease_is_ours():
    """QA-Ops (#536): a pass on which the lease is not ok (lapsed, lost) never re-reads the owner's config."""

    class Lease:
        degraded, ok = None, False

        def renew(self, force=False):
            return None

    class Eng:
        tried = 0

        def retry_rules(self, force=False):
            Eng.tried += 1

        def apply_commands(self):
            return None

    lease, eng = Lease(), Eng()
    assert engine_main.before_tick(eng, lease) is False and Eng.tried == 0
    lease.ok = True
    engine_main.before_tick(eng, lease)
    assert Eng.tried == 1
