"""M20 9.6 (#730): two hosts, one lease. The Mac and the cloud VM both run an engine on one paper account, side by
side, as they will during the January cut-over (docs/ops/cloud_move.md); only the one the lease names may send.

The deterministic simulation's own world (tests/dst/world.py: the same venue, the same Supabase, the same clock),
with two engine processes instead of one. Each host has its own data folder (the VM's journal is a copy of the
Mac's, as the runbook moves it), its own lease holder id, its own machine (`lease.machine_key`) and its own order
fence (`engine/fence.FencedBroker`) over the one venue. Every venue write is attributed to the host that made it.

    two_hosts_stall   the VM is started at noon on D1 while the Mac runs: every start of it is refused (LeaseHeld,
                      exit 3) and it sends nothing. On D2 the Mac freezes at 15:45 ET, inside the minutes before the
                      send window; the owner copies its journal to the VM; the VM takes the lease once it has
                      expired and sends D2's orders in the window. The Mac wakes at 15:52 with the same orders due:
                      its fence finds the lease gone, nothing reaches the venue, it stops (LeaseLost, exit 3) and
                      every later start is refused (LeaseHeld, exit 3).
    two_hosts_clean   the runbook's cut-over: the Mac stops cleanly after D2's close (the lease is given back), the
                      journal is copied, the VM starts and takes the lease; the Mac's launcher comes back the next
                      morning (the owner forgot to unload it): every start is refused and it sends nothing.
    the roll-back     the VM is stopped cleanly; the Mac's launcher is still loaded with its old journal: the lease
                      is free, but its journal is stale (engine/__main__.start_with_lease: `verify_identity`), so every
                      start gives the lease back and exits 4, until the VM's journal is copied back.

Two planted bugs show the scenario reaches the fence: with the loop's renewal before the tick taken away, the woken
Mac goes to its due sale and the fence alone refuses it; with the fence taken away too, the sale reaches the venue
on the VM's lease and the one-sender check fails.

The world's passes are minutes apart outside the send window; a running engine renews every 10 s. With two hosts that
gap would let a live lease expire between passes, so every live, awake engine renews first at each pass
(`TwoHosts.renew_between`), as its heartbeat would have.

What must hold, besides every invariant of the simulation (checked on the engine that holds the lease):
    - every venue write is made by the host the lease row names at that moment (one sender at any time);
    - the VM writes nothing before it took the lease, the Mac nothing after it lost it;
    - every refused start is the lease's (exit 3), and none of them writes to the venue;
    - no decision is sent twice (one working order per decision; the client order ids of the venue are unique).
"""

from __future__ import annotations

import contextlib
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from scenarios import quiet_in
from world import MARCH_GAP, World, et

from kaibot.engine.__main__ import EXIT_JOURNAL
from kaibot.engine.fence import FencedBroker
from kaibot.engine.journal import BootstrapError, IdentityError
from kaibot.engine.lease import EXIT_LEASE, LeaseError, LeaseLost

D0, D1, D2, D3 = MARCH_GAP.evenings
# D2 sells B: a sale is an order the engine sends with its lease unverified (a reduction goes out in a Supabase
# outage, docs/ops/failsafe.md), so the woken Mac of two_hosts_stall reaches the venue's door with it; only the fence
# (a renewal with its tenure's epoch) can stop it there
PLAN = {D1: ("B", "C"), D2: ("C",), D3: ("C", "D")}
# what one engine process keeps of its own (World's attributes for "the" engine); the rest is the world's
SLOT = ("eng", "rep", "lease", "gaps", "beat", "_stop", "down_until", "asleep_until", "jpath", "broker", "_down_for",
        "host")
WRITES = ("submit", "replace", "cancel", "close_position")


class Wire:
    """The venue as one host reaches it: every write is recorded with the host's name, then passed on."""

    def __init__(self, inner, name: str, world: TwoHosts) -> None:
        self.inner, self.name, self.world = inner, name, world

    def __getattr__(self, attr: str):
        if attr.startswith("__"):
            raise AttributeError(attr)
        got = getattr(self.inner, attr)
        if attr in WRITES:
            def write(*a, **kw):
                self.world.wrote(self.name, attr, a)
                return got(*a, **kw)
            return write
        return got


class Host:
    def __init__(self, name: str, state: dict, enabled: bool = True) -> None:
        self.name, self.state, self.enabled = name, state, enabled
        self.refused: list[tuple[datetime, int, str]] = []  # (when, exit code, exception) of every refused start
        self.lost: list[datetime] = []  # when its lease was found lost (LeaseLost, exit 3)
        self.ups: list[datetime] = []  # successful starts


class TwoHosts(World):
    """The simulation with a Mac and a VM. `events`: [(when, fn(world))], the owner's steps, run before the pass at
    or after their time."""

    def __init__(self, scenario, events, root: Path | None = None) -> None:
        super().__init__(scenario, root)
        faulty = self.broker.inner  # World wraps the FaultyBroker in one FencedBroker
        self.writes: list[tuple[datetime, str, str, str | None]] = []  # (when, host, call, lease holder then)
        mac = {k: getattr(self, k, None) for k in SLOT}
        mac.update(broker=FencedBroker(Wire(faulty, "mac", self)), host=("kaibot-mac", "H-mac", "UUID-MAC"),
                   jpath=self.root / "mac" / "journal.sqlite")
        vm = {**dict.fromkeys(SLOT), "broker": FencedBroker(Wire(faulty, "vm", self)),
              "host": ("kaibot-engine-vm", "H-vm", "GCE-VM-ID"), "jpath": self.root / "vm" / "journal.sqlite"}
        for st in (mac, vm):
            st["jpath"].parent.mkdir(parents=True, exist_ok=True)
        self.mac, self.vm = Host("mac", mac), Host("vm", vm, enabled=False)
        self.hosts = (self.mac, self.vm)
        self.cur: Host | None = None
        self.events = sorted(events, key=lambda e: e[0])
        self.seat(self.mac)

    # ---------------------------------------------------------------- one process at a time in World's seat
    def seat(self, h: Host) -> None:
        for k in SLOT:
            setattr(self, k, h.state[k])
        self.cur = h

    def unseat(self, h: Host) -> None:
        for k in SLOT:
            h.state[k] = getattr(self, k, None)

    @contextlib.contextmanager
    def as_host(self, h: Host):
        self.seat(h)
        try:
            yield
        finally:
            self.unseat(h)

    def wrote(self, host: str, call: str, args) -> None:
        row = self.replica.lease or {}
        what = getattr(args[0], "client_order_id", None) if args else None
        self.writes.append((self.clock(), host, f"{call} {what or (args[0] if args else '')}", row.get("holder")))

    # ---------------------------------------------------------------- the loop: both machines, one after the other
    def one_pass(self) -> None:
        while self.events and self.events[0][0] <= self.clock():
            when, fn = self.events.pop(0)
            fn(self)
            self.say(f"owner: {fn.__name__}")
        self.renew_between()
        for h in self.hosts:
            if h.enabled:
                with self.as_host(h):
                    super().one_pass()

    def beat_alive(self) -> None:
        for h in self.hosts:
            if h.enabled:
                with self.as_host(h):
                    super().beat_alive()

    def renew_between(self) -> None:
        """A running engine renews its lease every 10 s (its heartbeat), also between the world's passes, which are
        minutes apart outside the send window. With one engine that gap does not matter; with two, a lease left to
        expire between passes would hand the account to the other host, which the real loop never does. So every
        live, awake engine renews first, as its heartbeat thread would have (a renewal that finds the lease gone
        marks it lost; that engine's pass then stops on it)."""
        for h in self.hosts:
            st = h.state
            if h.enabled and st["eng"] is not None and st["lease"] is not None and st["asleep_until"] is None:
                with contextlib.suppress(LeaseLost):
                    st["lease"].renew(force=True)

    def boot(self) -> None:
        h = self.cur
        try:
            super().boot()
        except (LeaseError, IdentityError, BootstrapError) as e:
            # engine/__main__ start_with_lease: no lease -> exit 3; a stale or restored journal -> exit 4
            code = EXIT_JOURNAL if isinstance(e, (IdentityError, BootstrapError)) else EXIT_LEASE
            h.refused.append((self.clock(), code, type(e).__name__))
            self.down_until = self.clock() + timedelta(minutes=1)  # launchd / systemd: again a minute later
            raise
        h.ups.append(self.clock())

    def _start_live(self, eng) -> None:
        """World's start plus what engine/__main__.start_with_lease does between the lease and the start-up: the
        journal must be the newest one Supabase has seen (`verify_identity`), else the lease is given back and the
        process exits with code 4. A journal copied to the other host and run there makes the original stale."""
        real = eng.startup

        def startup():
            try:
                eng.store.bootstrap(eng.process)
                eng.store.verify_identity(eng.process)
            except (BootstrapError, IdentityError):
                eng.lease.release()
                raise
            real()

        eng.startup = startup
        try:
            super()._start_live(eng)
        finally:
            eng.startup = real

    def die(self, why: str) -> None:
        if why.startswith(LeaseLost.__name__):
            self.cur.lost.append(self.clock())
        super().die(why)

    def run(self):
        try:
            return super().run()
        finally:
            for h in self.hosts:  # World.run closes only the engine in its seat
                eng = h.state.get("eng")
                if eng is not None:
                    with contextlib.suppress(Exception):
                        eng.journal.close()

    # ---------------------------------------------------------------- the owner's steps
    def copy_journal(self, back: bool = False) -> None:
        """The data folder moved to the VM (`back`: to the Mac, the roll-back): its journal (sqlite's online
        backup), never the holder file (`engine_holder_id` stays on its machine, docs/ops/cloud_move.md)."""
        src, dst = self.mac.state["jpath"], self.vm.state["jpath"]
        if back:
            src, dst = dst, src
        with contextlib.closing(sqlite3.connect(src)) as a, contextlib.closing(sqlite3.connect(dst)) as b:
            a.backup(b)


def start_vm(w: TwoHosts) -> None:
    w.copy_journal()
    w.vm.enabled = True


def freeze_mac(w: TwoHosts, until: datetime) -> None:
    w.mac.state["asleep_until"] = until


def stop_mac_cleanly(w: TwoHosts, back: datetime) -> None:
    """`scripts/service.sh stop` on the Mac: the engine gives the lease back (engine/__main__.main's end) and
    stops; its launcher stays unloaded until `back` (here: the owner forgot to unload it, a reboot loads it)."""
    st = w.mac.state
    if st["lease"] is not None:
        st["lease"].release()
    with w.as_host(w.mac):
        w.die("clean stop (service.sh stop)")
        w.down_until = back


def _writes_by(w: TwoHosts, host: str) -> list:
    return [x for x in w.writes if x[1] == host]


def _check_one_sender(w: TwoHosts) -> list[str]:
    bad = []
    holder = {"mac": "H-mac", "vm": "H-vm"}
    for t, host, what, named in w.writes:
        if named != holder[host]:
            bad.append(f"{host} wrote {what} at {t} while the lease named {named!r}")
    coids = [str(o.get("client_order_id") or "") for o in w.sim.orders.values()]
    dup = sorted({c for c in coids if c and coids.count(c) > 1})
    if dup:
        bad.append(f"client order ids at the venue twice: {dup}")
    return bad


def _codes(h: Host, since: datetime, until: datetime | None = None) -> set[tuple[int, str]]:
    return {(c, e) for t, c, e in h.refused if t >= since and (until is None or t < until)}


FREEZE, WAKE = et(D2, "15:45"), et(D2, "15:52")


def stall(tmp_path) -> tuple[TwoHosts, object]:
    """two_hosts_stall (the module's docstring)."""
    freeze, wake = FREEZE, WAKE

    def mac_freezes(w):
        freeze_mac(w, wake)

    def owner_copies_the_journal(w):  # the Mac writes nothing while it is frozen
        w.copy_journal()

    events = [(et(D1, "12:00"), start_vm), (freeze, mac_freezes),
              (freeze + timedelta(seconds=1), owner_copies_the_journal)]
    w = TwoHosts(quiet_in(MARCH_GAP, PLAN, lease=True, heal=D3), events, tmp_path)
    return w, w.run()


def no_renewal_before_the_tick(mp) -> None:
    """Planted: the loop no longer proves the lease before a tick (engine/__main__.before_tick's renewal), so the
    woken Mac goes straight to its due orders: only the order fence stands between it and the venue."""
    from kaibot.engine import __main__ as main

    real = main.before_tick

    def before_tick(eng, lease, *a, **kw):
        renew = lease.renew
        lease.renew = lambda force=False: lease.ok
        try:
            return real(eng, lease, *a, **kw)
        finally:
            lease.renew = renew

    mp.setattr(main, "before_tick", before_tick)


def no_fence(mp) -> None:
    """Planted: the order fence trusts the engine's own view of its lease (no renewal with the epoch per order)."""
    from kaibot.engine.lease import EngineLease

    mp.setattr(EngineLease, "fence", lambda self, reducing: None)


def test_two_hosts_one_lease_the_mac_stalls_and_the_vm_takes_over(tmp_path):
    freeze, wake = FREEZE, WAKE
    w, res = stall(tmp_path)
    assert not res.violations, res.violations
    assert not _check_one_sender(w), _check_one_sender(w)
    took = w.vm.ups[0]  # the VM's first successful start: it took the lease
    assert freeze < took < wake
    # side by side from D1 noon: the VM was refused (LeaseHeld, exit 3) at every try before the freeze, sent nothing
    before = [r for r in w.vm.refused if r[0] < freeze]
    assert len(before) > 10 and _codes(w.vm, et(D1, "12:00"), freeze) == {(EXIT_LEASE, "LeaseHeld")}
    assert all(t >= took for t, *_ in _writes_by(w, "vm"))
    assert any(t < freeze for t, *_ in _writes_by(w, "mac")), "the Mac traded D1 itself"
    # the Mac woke with D2's orders due and sent none of them: its fence found the lease gone; it stopped (exit 3)
    assert not [x for x in _writes_by(w, "mac") if x[0] >= freeze]
    assert w.mac.lost and w.mac.lost[0] >= wake
    assert len([r for r in w.mac.refused if r[0] > w.mac.lost[0]]) > 10
    assert _codes(w.mac, w.mac.lost[0]) == {(EXIT_LEASE, "LeaseHeld")}
    assert not [u for u in w.mac.ups if u > freeze], "the Mac started again while the VM held the lease"
    # D2's decision went out once, from the VM, in its window
    d2 = D2.replace("-", "")
    sent = [x for x in _writes_by(w, "vm") if x[2].startswith("submit kb") and f"-{d2}-" in x[2]]
    assert sent and all(et(D2, "15:50") <= t < et(D2, "16:00") for t, *_ in sent)
    assert (w.replica.lease or {}).get("holder") == "H-vm"


def test_two_hosts_the_fence_alone_stops_the_woken_mac(tmp_path, monkeypatch):
    """Without the loop's renewal before the tick, the woken Mac reaches its due orders of D2: the fence renews with
    its tenure's epoch, finds the lease gone and sends nothing (the order is refused locally, the Mac stops)."""
    no_renewal_before_the_tick(monkeypatch)
    w, res = stall(tmp_path)
    assert not _check_one_sender(w), _check_one_sender(w)
    assert w.mac.state["broker"].refused >= 1  # its due sale of B, refused at the fence
    assert not [x for x in _writes_by(w, "mac") if x[0] >= FREEZE]
    assert w.mac.lost and w.mac.lost[0] >= WAKE


def test_two_hosts_without_the_fence_the_mac_sends_and_the_scenario_sees_it(tmp_path, monkeypatch):
    """The scenario's power: with both the loop's renewal and the fence planted away, the woken Mac sends D2's
    orders on a lease the VM holds, and the one-sender check finds it."""
    no_renewal_before_the_tick(monkeypatch)
    no_fence(monkeypatch)
    w, _ = stall(tmp_path)
    late = [x for x in _writes_by(w, "mac") if x[0] >= FREEZE]
    assert late, "the planted Mac sent nothing: the scenario does not reach the fence"
    assert any("while the lease named 'H-vm'" in b for b in _check_one_sender(w))


def test_two_hosts_one_lease_the_runbooks_clean_cutover(tmp_path):
    stop = et(D2, "18:00")
    back = et(D3, "09:00")

    def stop_mac(w):
        stop_mac_cleanly(w, back)

    events = [(stop, stop_mac), (stop + timedelta(minutes=2), start_vm)]
    w = TwoHosts(quiet_in(MARCH_GAP, PLAN, lease=True, heal=D3), events, tmp_path)
    res = w.run()

    assert not res.violations, res.violations
    assert not _check_one_sender(w), _check_one_sender(w)
    assert w.vm.ups and w.vm.ups[0] < back and not w.vm.refused  # the lease was free: taken at once
    assert all(t < stop for t, *_ in _writes_by(w, "mac"))
    assert all(t >= w.vm.ups[0] for t, *_ in _writes_by(w, "vm"))
    # the Mac's launcher came back at 09:00 on D3: every start refused (exit 3), nothing sent, never up again
    later = [r for r in w.mac.refused if r[0] >= back]
    assert len(later) > 10 and _codes(w.mac, back) == {(EXIT_LEASE, "LeaseHeld")}
    assert not [u for u in w.mac.ups if u >= stop]
    d3 = D3.replace("-", "")
    assert [x for x in _writes_by(w, "vm") if x[2].startswith("submit kb") and f"-{d3}-" in x[2]]


def test_two_hosts_one_lease_the_rollback_needs_the_vms_journal(tmp_path):
    """The roll-back of the runbook: the VM traded D2 and is stopped after the close (lease given back). The Mac's
    launcher is still loaded: the lease is free, but the Mac's journal is stale (the VM's copy ran on after it), so
    every start takes the lease, finds the journal is not the newest (exit 4), gives the lease back and sends
    nothing. Once the owner copies the VM's journal back, the Mac starts and trades D3."""
    vm_up = et(D1, "18:00")
    vm_stop = et(D2, "18:00")
    copy_back = et(D3, "08:00")

    def mac_hands_over(w):  # the clean cut-over the evening before
        stop_mac_cleanly(w, back=vm_stop + timedelta(minutes=5))  # the Mac's launcher comes back after the VM stops

    def stop_vm(w):
        st = w.vm.state
        st["lease"].release()
        with w.as_host(w.vm):
            w.die("clean stop (systemctl stop kaibot-engine)")
        w.vm.enabled = False

    def owner_copies_back(w):
        w.copy_journal(back=True)

    events = [(vm_up, mac_hands_over), (vm_up + timedelta(minutes=2), start_vm), (vm_stop, stop_vm),
              (copy_back, owner_copies_back)]
    w = TwoHosts(quiet_in(MARCH_GAP, PLAN, lease=True, heal=D3), events, tmp_path)
    res = w.run()

    assert not res.violations, res.violations
    assert not _check_one_sender(w), _check_one_sender(w)
    d2 = D2.replace("-", "")
    assert [x for x in _writes_by(w, "vm") if x[2].startswith("submit kb") and f"-{d2}-" in x[2]]
    stale = _codes(w.mac, vm_stop, copy_back)
    assert stale == {(EXIT_JOURNAL, "IdentityError")}, stale
    assert len([r for r in w.mac.refused if vm_stop <= r[0] < copy_back]) > 10
    assert not [x for x in _writes_by(w, "mac") if vm_up <= x[0] < copy_back]
    assert w.mac.ups[-1] >= copy_back  # the Mac runs again on the VM's journal
    d3 = D3.replace("-", "")
    assert [x for x in _writes_by(w, "mac") if x[2].startswith("submit kb") and f"-{d3}-" in x[2]]
    assert (w.replica.lease or {}).get("holder") == "H-mac"
