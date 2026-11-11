"""Cross-machine engine lease (issue #81): only one engine may trade the paper account, whatever machine it is on.

One row in Supabase (`kaibot.engine_lease`, migration 0013) names the engine that may trade. The engine takes it
at start-up with one atomic SQL function, renews it with every heartbeat and gives it back on a clean stop.

- Another engine holds a live lease  -> `LeaseHeld`: this engine must not start.
- The lease cannot be checked         -> `LeaseUnavailable` at start-up (do not start); while running, `ok` turns
                                         False and the engine opens nothing until a renewal succeeds again.
- A renewal says the lease is not ours any more (it expired and another engine took it) -> `LeaseLost`: stop.
- A holder that dies stops renewing; after `TTL_S` seconds the next engine to start takes the lease over.

The holder id is one random id per engine install, kept in the data directory, so a restart on the same machine
gets its own lease back at once (a crash does not lock the machine out for `TTL_S`). Two engines on the SAME
machine are `scripts/engine_guard.sh`'s job. Never copy the holder file to another machine.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

log = logging.getLogger("kaibot.engine")

TTL_S = 90  # a dead holder's lease can be taken over after this long (same figure as the watchdog's stale limit)
RENEW_MIN_GAP_S = 5.0  # progress callbacks can fire many times a second; renew at most this often
HOLDER_FILE = "engine_holder_id"
EXIT_LEASE = 3  # process exit code when the lease stops this engine


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


class EngineLease:
    def __init__(self, store, holder: str, host: str, ttl_s: int = TTL_S, mono=time.monotonic, wall=time.time):
        self.store, self.holder, self.host, self.ttl_s, self.mono, self.wall = store, holder, host, ttl_s, mono, wall
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
        return self.held and not self.lost and self.last_error is None and self.age_s() < self.ttl_s

    def age_s(self) -> float:
        if self.last_ok is None or self.last_ok_wall is None:
            return float("inf")
        return max(self.mono() - self.last_ok, self.wall() - self.last_ok_wall)

    def _mark_ok(self) -> None:
        self.last_error, self.last_ok, self.last_ok_wall = None, self.mono(), self.wall()

    def status(self) -> str:
        if self.lost:
            return "lost"
        if not self.held:
            return "none"
        return "ok" if self.ok else "unverified"

    # ---------- start-up ----------
    def acquire(self) -> dict:
        try:
            out = self.store.lease_acquire(self.holder, self.host, self.ttl_s)
        except Exception as e:
            raise LeaseUnavailable(
                f"The engine lease could not be checked ({str(e)[:200]}). Not starting: without the lease this "
                "engine cannot know it is the only one. Check Supabase and that migration 0013 is applied "
                "(docs/ops/run_engine.md, 'Engine lease').") from None
        if not isinstance(out, dict) or "acquired" not in out:
            raise LeaseUnavailable(f"The engine lease answered something unexpected ({str(out)[:120]}). Not starting.")
        if out["acquired"] is not True:
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
        return out

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
            ours = self.store.lease_renew(self.holder, self.ttl_s)
        except Exception as e:
            if self.last_error is None:
                log.error("engine lease renewal failed (%s): no new entries until it works again", str(e)[:200])
            self.last_error = str(e)[:200]
            return False
        if ours is not True:  # False, None or anything odd: not provably ours
            self.lost, self.held = True, False
            raise LeaseLost(
                "The engine lease is no longer ours: another engine took it over after ours expired, or it was "
                "released by hand. Stopping so that two engines never trade one account. Open positions keep "
                "their broker-held stops (docs/ops/run_engine.md, 'Engine lease').")
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
        try:
            return self.store.lease_release(self.holder) is True
        except Exception:
            log.exception("engine lease release failed: it expires by itself in %d s", self.ttl_s)
            return False
