"""Cross-machine engine lease (issue #81): only one engine may trade the paper account, whatever machine it is on.

One row in Supabase (`kaibot.engine_lease`, migration 0013) names the engine that may trade. The engine takes it
at start-up with one atomic SQL function, renews it with every heartbeat and gives it back on a clean stop.

- Another engine holds a live lease  -> `LeaseHeld`: this engine must not start.
- The lease cannot be checked         -> `LeaseUnavailable` at start-up (do not start); while running, `ok` turns
                                         False and the engine opens nothing until a renewal succeeds again.
- A renewal says the lease is not ours any more (it expired and another engine took it) -> `LeaseLost`: stop.
- A holder that dies stops renewing; after `TTL_S` seconds the next engine to start takes the lease over.

Degraded start (M10 1.25, #450; the argument is in docs/ops/failsafe.md, "Degraded start"). Every change of the
lease is also written to the local journal (`lease` entries: acquired, released, lost, held_by_other,
degraded_start, contradicted; never sent to Supabase). When the lease cannot be CHECKED at start-up and the last
of those records is this install's own "acquired", "released" or "degraded_start", the engine may start
"reducing only" (`start_degraded`): `ok` stays False, so nothing is bought, while exits, stops, sales and fills go
on exactly as they do for a running engine whose renewals fail. `acquire` is tried again on every loop pass; when
it works and the journal's identity is verified, `activate` makes the engine fully active. Until ticket 1.21's
trading state lands this rides on the existing entry block (`Engine.lease_ok` -> `entry_block`: "lease
unverified"); `reducing_only` is the hook that state should read.

The holder id is one random id per engine install, kept in the data directory, so a restart on the same machine
gets its own lease back at once (a crash does not lock the machine out for `TTL_S`). Two engines on the SAME
machine are `scripts/engine_guard.sh`'s job. Never copy the holder file to another machine.

#562 (migration 0041): the lease is bound to the holder AND the machine (`machine_key`: the host name and the
operating system's machine id, hashed, read once at start and never from the data folder). A data folder copied to
another machine carries the holder id but not the machine: that machine's start is refused while the lease is live
(`LeaseHeld`, exit code 3), and it can never renew or release this machine's lease.
"""

from __future__ import annotations

import hashlib
import logging
import platform
import re
import subprocess
import time
import uuid
from pathlib import Path

log = logging.getLogger("kaibot.engine")

TTL_S = 90  # a dead holder's lease can be taken over after this long (same figure as the watchdog's stale limit)
RENEW_MIN_GAP_S = 5.0  # progress callbacks can fire many times a second; renew at most this often
HOLDER_FILE = "engine_holder_id"
LEASE_KIND = "lease"  # the journal's record of the lease (local only)
# the last record after which a degraded start is allowed, when it is this install's own
OWN_STATES = ("acquired", "released", "degraded_start")
EXIT_LEASE = 3  # process exit code when the lease stops this engine
MACHINE_ID_FILES = ("/etc/machine-id", "/var/lib/dbus/machine-id")  # Linux (systemd, dbus)


class LeaseError(RuntimeError):
    pass


class LeaseHeld(LeaseError):
    """Another engine holds a live lease."""


class LeaseUnavailable(LeaseError):
    """The lease could not be checked (Supabase unreachable, migration 0013 missing, bad answer)."""


class LeaseLost(LeaseError):
    """The lease was ours and now is not: another engine took it over, or it was released by hand."""


def holder_id(data_dir: Path | str | None, host: str) -> str:
    """This install's stable id: read from `<data_dir>/engine_holder_id`, created on first use. If the file cannot
    be written the id is new on every start: still safe, but a quick restart then waits out the old lease."""
    fresh = f"{host}-{uuid.uuid4().hex[:12]}"
    if not data_dir:
        return fresh
    path = Path(data_dir) / HOLDER_FILE
    try:
        if path.exists():
            got = path.read_text().strip()
            if got:
                return got
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fresh + "\n")
    except OSError:
        log.warning("cannot keep the lease holder id in %s: a restart will wait for the old lease to expire", path)
    return fresh


def os_machine_id() -> str:
    """The operating system's own id of this machine: macOS IOPlatformUUID, Linux /etc/machine-id; "" when none can
    be read (then the host name alone tells machines apart)."""
    try:
        if platform.system() == "Darwin":
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True,
                                 timeout=5).stdout
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
            return m.group(1) if m else ""
        for f in MACHINE_ID_FILES:
            p = Path(f)
            if p.exists():
                got = p.read_text().strip()
                if got:
                    return got
    except Exception:  # noqa: BLE001 - unknown: the host name alone
        log.warning("the machine id could not be read: the engine lease is bound to the host name alone")
    return ""


def machine_key(host: str, os_id: str | None = None) -> str:
    """#562: what binds the lease to this machine besides the holder id. The host name AND the OS machine id (a
    Docker image may carry a baked /etc/machine-id, but every container has its own host name), hashed. Never read
    from the data folder, so a copied folder does not carry it. Never empty."""
    os_id = os_machine_id() if os_id is None else os_id
    return hashlib.sha256(f"kaibot-engine\0{host}\0{os_id}".encode()).hexdigest()[:24]


class EngineLease:
    def __init__(self, store, holder: str, host: str, ttl_s: int = TTL_S, mono=time.monotonic, wall=time.time,
                 journal=None, machine: str | None = None):
        """`journal`: the engine's local journal (append, entries); None: the lease is not recorded and a degraded
        start is never allowed. `machine`: `machine_key(host)` when None, computed ONCE here (#562): a host name
        that changes while the engine runs does not take its lease away (the next start then waits out the old
        lease, fail closed)."""
        self.store, self.holder, self.host, self.ttl_s, self.mono, self.wall = store, holder, host, ttl_s, mono, wall
        self.journal = journal
        self.degraded: str | None = None  # why this engine is reducing only (M10 1.25); None: not degraded
        self.machine = machine_key(host) if machine is None else machine
        if not self.machine:
            raise ValueError("engine lease: the machine key must not be empty")
        self.held = False
        self.lost = False
        # Time of the last successful acquire / renewal on both clocks. The monotonic clock stops while a laptop
        # sleeps and the wall clock can be set back, so the age is the larger of the two.
        self.last_ok: float | None = None
        self.last_ok_wall: float | None = None
        self.last_try: float | None = None
        self.last_error: str | None = None
        self.info: dict = {}

    # ---------- state ----------
    @property
    def ok(self) -> bool:
        """True only while the lease is provably ours: held, the last renewal worked, and it is younger than the
        TTL on both clocks. Anything else blocks new entries (fail closed)."""
        return (self.held and not self.lost and self.degraded is None and self.last_error is None
                and self.age_s() < self.ttl_s)

    @property
    def reducing_only(self) -> bool:
        """M10 1.25: started without a confirmed lease. The hook for ticket 1.21's trading state ("reducing")."""
        return self.degraded is not None

    def age_s(self) -> float:
        if self.last_ok is None or self.last_ok_wall is None:
            return float("inf")
        return max(self.mono() - self.last_ok, self.wall() - self.last_ok_wall)

    def _mark_ok(self) -> None:
        self.last_error, self.last_ok, self.last_ok_wall = None, self.mono(), self.wall()

    def status(self) -> str:
        if self.lost:
            return "lost"
        if self.degraded is not None:
            return "degraded"
        if not self.held:
            return "none"
        return "ok" if self.ok else "unverified"

    # ---------- start-up ----------
    def acquire(self) -> dict:
        try:
            out = self.store.lease_acquire(self.holder, self.host, self.ttl_s, self.machine)
        except Exception as e:
            raise LeaseUnavailable(
                f"The engine lease could not be checked ({str(e)[:200]}). Not starting: without the lease this "
                "engine cannot know it is the only one. Check Supabase and that migration 0013 is applied "
                "(docs/ops/run_engine.md, 'Engine lease').") from None
        if not isinstance(out, dict) or "acquired" not in out:
            raise LeaseUnavailable(f"The engine lease answered something unexpected ({str(out)[:120]}). Not starting.")
        if out["acquired"] is not True:
            self._record("held_by_other", other_host=out.get("host"))
            if out.get("reason") == "holder_on_other_machine" or out.get("holder") == self.holder:
                raise LeaseHeld(
                    f"The engine lease is held by THIS install's holder id on another machine or an older release "
                    f"(host {out.get('host')!r}, started {out.get('started_at')}, live for about "
                    f"{out.get('expires_in_s', '?')} s more). The data folder (its engine_holder_id file) was copied "
                    "to this machine, or this machine's name changed, or an engine of a release before #562 still "
                    "runs here. Two engines with one holder id would both trade, so this one is not starting. Stop "
                    "the other engine; if it is this machine after a rename or an upgrade, wait 90 s and start "
                    "again. On a machine that got a copied data folder, delete data/live/engine_holder_id there "
                    "(docs/ops/run_engine.md, 'Engine lease').")
            raise LeaseHeld(
                f"Another Kaibot engine holds the lease: host {out.get('host')!r}, started {out.get('started_at')}, "
                f"lease live for about {out.get('expires_in_s', '?')} s more (it renews every 10 s while that "
                "engine runs). Two engines on one paper account would trade twice, so this one is not starting. "
                "Stop the other engine first; if it is already dead, wait 90 s and start again "
                "(docs/ops/run_engine.md, 'Engine lease').")
        self.held, self.lost = True, False
        self._mark_ok()
        self.last_try = self.last_ok
        self.info = out
        self._record("acquired", taken_over=bool(out.get("taken_over")), previous_host=out.get("previous_host"))
        return out

    # ---------- the local record and the degraded start (M10 1.25) ----------
    def _record(self, state: str, **kw) -> None:
        """One `lease` entry in the local journal. A journal that cannot be written is the engine's larger
        problem and is raised where the engine writes its own entries; the lease goes on without the record."""
        if self.journal is None:
            return
        try:
            self.journal.append(LEASE_KIND, {"state": state, "holder": self.holder, "host": self.host,
                                             "machine": self.machine,  # #562: the record names the machine too
                                             **{k: v for k, v in kw.items() if v is not None}})
        except Exception:
            log.exception("lease record %r not journalled", state)

    def last_record(self) -> dict | None:
        if self.journal is None:
            return None
        got = self.journal.entries(kinds=(LEASE_KIND,))
        return got[-1][3] if got else None

    def degraded_allowed(self) -> tuple[bool, str]:
        """May this engine start reducing only, by the journal's word alone? (The broker's word is asked next:
        engine/__main__.py `other_engine_evidence`.)"""
        last = self.last_record()
        if last is None:
            return False, ("no lease record in this journal (it has not held the lease since this release, or it "
                           "is new)")
        if last.get("holder") != self.holder:
            return False, (f"the journal's last lease record is of another machine (host {last.get('host')!r}): "
                           "this journal or its holder file was copied")
        if last.get("host") != self.host:
            # QA-Ops (#536): the whole data folder (journal AND holder file) copied to another machine carries
            # this install's holder id; the host name it was recorded on still tells them apart (fail closed: a
            # host name that changed refuses the degraded start, never allows one)
            return False, (f"the journal's last lease record was written on host {last.get('host')!r}, this is "
                           f"{self.host!r}: the data folder was copied, or the host name changed")
        if last.get("machine") != self.machine:
            # #562: the same host name on another machine (or a record written before #562, which names none): the
            # machine key (host name and OS machine id) tells them apart; fail closed, as for the host
            return False, ("the journal's last lease record was written on another machine with this host name"
                           if last.get("machine") else "the journal's last lease record names no machine (written "
                           "before #562): a degraded start needs one lease taken by this release first")
        if last.get("state") not in OWN_STATES:
            return False, (f"the journal's last lease record is {last.get('state')!r}: another engine took the "
                           "lease after this one, or traded the account")
        return True, f"the last lease recorded here was this machine's own ({last.get('state')})"

    def contradicted(self, evidence: list[str]) -> None:
        """Local evidence against a degraded start (orders of another engine at the broker): recorded, so every
        later start is refused too until a lease is taken from Supabase."""
        self._record("contradicted", evidence=[str(e)[:200] for e in evidence[:10]])

    def start_degraded(self, why: str) -> None:
        self.held, self.lost, self.degraded = False, False, why
        self._record("degraded_start", why=why[:200])

    def activate(self) -> None:
        """The lease is taken AND the journal's identity is verified: fully active from here on."""
        self.degraded = None

    # ---------- every heartbeat ----------
    def renew(self, force: bool = False) -> bool:
        """Extend the lease. Returns `ok`. Raises LeaseLost when the database says it is no longer ours. A failed
        call (network, Supabase down) does not raise: `ok` goes False and the next heartbeat tries again."""
        if not self.held or self.lost:
            return False
        now = self.mono()
        if not force and self.ok and self.last_try is not None and now - self.last_try < RENEW_MIN_GAP_S:
            return self.ok
        self.last_try = now
        try:
            ours = self.store.lease_renew(self.holder, self.ttl_s, self.machine)
        except Exception as e:
            if self.last_error is None:
                log.error("engine lease renewal failed (%s): no new entries until it works again", str(e)[:200])
            self.last_error = str(e)[:200]
            return False
        if ours is not True:  # False, None or anything odd: not provably ours
            self.lost, self.held = True, False
            self._record("lost")
            raise LeaseLost(
                "The engine lease is no longer ours: another engine took it over after ours expired, or it was "
                "released by hand, or it is this holder id's on another machine. Stopping so that two engines "
                "never trade one account. Open positions keep their broker-held stops "
                "(docs/ops/run_engine.md, 'Engine lease').")
        if self.last_error is not None:
            log.info("engine lease renewed again: entries allowed")
        self._mark_ok()
        return True

    # ---------- clean stop ----------
    def release(self) -> bool:
        """Give the lease back so another machine can start at once. Never raises; never releases a lost lease."""
        if not self.held or self.lost:
            return False
        self.held = False
        self._record("released")
        try:
            return self.store.lease_release(self.holder, self.machine) is True
        except Exception:
            log.exception("engine lease release failed: it expires by itself in %d s", self.ttl_s)
            return False
