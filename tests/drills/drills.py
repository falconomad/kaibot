"""Scripted fail-safe drills against the simulated venue (M12 ticket 3.7, #469).

Each drill plays one bad day end to end with the real engine (`Engine.tick`, `LiveDaily`, `OrderManager`, `Journal`,
the lease, the start-up of `engine/__main__.py`), the real watchdog, and, where it acts, the real SQL dead-man
(migration 0019 in a throwaway local Postgres, `tests/pg_harness.py`), against the deterministic simulation of
tests/dst (`SimBroker` behind `FaultyBroker`, a simulated Supabase, `SimClock`). It then checks the outcome the
operations documents promise. Each drill names the sentences of docs/ops it holds the engine to (`DOCS`), and
test_drills.py fails when a sentence is no longer in its document, so a changed promise changes its drill.

    watchdog takeover          the engine dies in the send window with a buy working
    dead-man stop placement    the engine is silent all session with a multi-day sleeve and no stop: the watchdog and
                               the SQL dead-man each place the same protective stop, and neither sells the sleeve
    lease lost                 renewals fail through the send window (no entries until one works); later the engine
                               sleeps past the lease, another engine takes it, and this one stops and writes nothing
    supabase down at start     every start while Supabase is unreachable is refused (exit code 3) before the broker is
                               touched; once it is back the engine starts and the session's buy still goes out
    supabase down at a restart an engine that held the lease restarts while Supabase is down (D1 morning to D2 15:20):
                               it comes up reducing only; D1's buy reaches its send window and nothing is bought, no
                               heartbeat is attempted; once the lease is confirmed and the owner config read, entries
                               are allowed again and the buy goes out once in D2's window (#536)
    journal damaged            at start: exit code 4 before anything else; while running: the engine stops (exit code 4)

A drill returns its checks (what was expected, what happened, pass or fail); scripts/run_drills.py writes the record.
Nothing here edits or patches the engine: the drills own only the world around it.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (ROOT / "tests" / "dst", ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pandas as pd  # noqa: E402
from world import D0, D1, D2, D3, EVENINGS, STOCKS, Kill, Outage, Scenario, World, et  # noqa: E402

from kaibot.broker.faulty import NO_FAULTS, FaultProfile, Forced  # noqa: E402
from kaibot.broker.sim import NY  # noqa: E402
from kaibot.engine import __main__ as engine_main  # noqa: E402
from kaibot.engine import failsafe  # noqa: E402
from kaibot.engine import live_daily as ld  # noqa: E402
from kaibot.engine.core import Engine  # noqa: E402
from kaibot.engine.journal import JournalCorrupt, JournalStore, Replicator  # noqa: E402
from kaibot.engine.lease import EngineLease, LeaseLost  # noqa: E402
from kaibot.engine.market import LiveMarket  # noqa: E402
from kaibot.engine.watchdog import book_reader  # noqa: E402
from kaibot.risk.profiles import load_profiles  # noqa: E402

ON_TIME = {d: et(d, "16:10") for d in EVENINGS}
MD = "trend_momentum"
FIXED_HOLDER = "drill-mac-holder"


@dataclass
class Check:
    what: str  # the documented outcome, in a few words
    ok: bool
    seen: str  # what happened

    def __str__(self) -> str:
        return f"{'PASS' if self.ok else 'FAIL'} {self.what}: {self.seen}"


@dataclass
class Record:
    name: str
    checks: list[Check] = field(default_factory=list)
    seconds: float = 0.0
    skipped: str | None = None
    error: str | None = None
    known: int | None = None  # an open engine issue the drill is expected to fail on (strict xfail)

    @property
    def ok(self) -> bool:
        return self.error is None and self.skipped is None and bool(self.checks) and all(c.ok for c in self.checks)


def check(out: list[Check], what: str, ok: bool, seen) -> None:
    out.append(Check(what, bool(ok), str(seen)[:300]))


# ---------------------------------------------------------------- the world of a drill
class StartRefused(RuntimeError):
    """The engine process exited at start (SystemExit) with this code; the launcher starts it again."""

    def __init__(self, code) -> None:
        super().__init__(f"exit code {code}")
        self.code = code


class DrillWorld(World):
    """The simulation, with what the drills need on top. `lease`: the engine is started as engine/__main__.py
    starts it (`start_with_lease`: the lease first, then the journal's bootstrap and identity, then `startup`) and
    every pass is the main loop's: a lease that is not verified is renewed first; `LeaseLost` and `JournalCorrupt`
    end the process; the heartbeat is `make_beat`'s (which renews the lease). `hooks`: (time, function) run once
    when the clock reaches the time."""

    def __init__(self, scenario: Scenario, root: Path, *, lease: bool = False) -> None:
        super().__init__(scenario, root)
        self.use_lease = lease
        self.lease: EngineLease | None = None
        self.hooks: list[tuple[datetime, object]] = []
        self.watches: list = []  # functions of the world called at every step until one returns True
        self.refused: list[tuple[datetime, object, str]] = []  # (when, exit code, the log's last error)
        self.stopped: tuple[datetime, str] | None = None  # the process ended by itself, and why
        self.stop: dict = {"now": False}
        self.beat = None
        self.writes: list[tuple[datetime, str]] = []  # every venue write, with its time
        self.log = _Capture()
        self.log.attach()
        self.confirmed_at: datetime | None = None  # a reducing-only engine's lease confirmed (before_tick)
        self.beat_calls: list[tuple[datetime, str]] = []  # every heartbeat write the engine attempts, even failed
        sent = self.remote.heartbeat

        def heartbeat(process, detail):
            self.beat_calls.append((self.clock(), process))
            return sent(process, detail)

        self.remote.heartbeat = heartbeat

    def _venue_write(self, phase: str, what: str) -> None:
        if phase == "before":
            self.writes.append((self.clock(), what))
        super()._venue_write(phase, what)

    def at(self, t: datetime, run: bool = True) -> None:
        self.clock.set(t)
        self.watches = [f for f in self.watches if not f(self)]
        for h in sorted(self.hooks, key=lambda h: h[0]):
            if t >= h[0]:
                self.hooks.remove(h)
                h[1](self)  # at the first step at or after its time
        super().at(t, run)

    def boot(self) -> None:
        if not self.use_lease:
            return super().boot()
        if self.stopped is not None:  # the process ended for good (lease lost): nobody starts it in the drill
            raise StartRefused("stopped")
        self.stats["boots"] += 1
        import hashlib
        import uuid

        from kaibot.engine.__main__ import make_beat, start_with_lease

        jid = uuid.UUID(int=int.from_bytes(hashlib.blake2b(f"kaibot-dst-{self.s.seed}".encode(),
                                                           digest_size=16).digest(), "big"))
        from world import FusedJournal

        with mock.patch.object(uuid, "uuid4", lambda: jid):
            journal = FusedJournal(self.jpath, self.clock, self.fuse)
        try:
            store = JournalStore(journal, self.remote, live=True, mono=lambda: self.clock().timestamp())
            market = LiveMarket(self.data, list(STOCKS), {s: "XLK" for s in STOCKS}, "SPY", self.root / "hist",
                                lambda: pd.Series(18.0, index=pd.date_range("2025-01-01", "2026-12-31")))
            eng = Engine(self.broker, market, store, load_profiles(), set(), now=self.clock,
                         daily=lambda e: ld.for_engine(e, self.load, rules=[self.rule]))
            eng.market.candidates_at = lambda m: pd.DataFrame()
            eng.sleep = lambda s: None
            eng.monotonic = lambda: self.clock().timestamp()
            ts = lambda: self.clock().timestamp()  # noqa: E731
            # built as engine/__main__.main builds it (M10 1.25, #536, adds `journal=`: the lease is recorded there)
            kw = {"journal": eng.journal} if "journal" in inspect.signature(EngineLease).parameters else {}
            lease = EngineLease(eng.store, FIXED_HOLDER, "drill-mac", mono=ts, wall=ts, **kw)
            try:
                mode = start_with_lease(eng, lease)
            except SystemExit as e:
                self.refused.append((self.clock(), e.code, self.log.last_error()))
                raise StartRefused(e.code) from None
            rep = Replicator(journal, self.remote)
            rep.clock = ts
        except BaseException:
            journal.close()
            raise
        self.eng, self.rep, self.lease = eng, rep, lease
        self.stop = {"now": False}
        self.beat = make_beat(eng, lease, "drill-mac", self.stop)
        self.check.attach(eng)
        self.say("engine up (reducing only)" if mode == "degraded" else "engine up (with the lease)")

    def one_pass(self) -> None:
        if not self.use_lease:
            return super().one_pass()
        now = self.clock()
        if self.asleep_until is not None:
            if now < self.asleep_until:
                return
            self.asleep_until = None
            self.say("engine awake")
        while self.outages and self.outages[0].arm <= now and not self.healed:
            o = self.outages.pop(0)
            if self.eng is None:
                continue
            if o.kill:
                self.fuse.arm(o)
                self._down_for = o.down
            else:
                self.stats["pauses"] += 1
                self.asleep_until = now + timedelta(minutes=o.down)
                self.say(f"engine paused for {o.down} min")
                return
        if self.eng is None:
            if self.stopped is not None or (self.down_until is not None and now < self.down_until):
                return
            try:
                self.boot()
            except Kill as k:
                self._killed(str(k))
            except StartRefused as e:
                self.say(f"start refused ({e}): the launcher tries again")
            except JournalCorrupt as e:
                self.bad(f"the journal is damaged: {e}")
            return
        eng = self.eng
        self.stats["passes"] += 1
        status: dict = {"state": "starting"}
        try:
            try:
                before = getattr(engine_main, "before_tick", None)  # M10 1.25 (#536): the pass's start, one seam
                if before is not None:
                    if before(eng, self.lease) and self.confirmed_at is None:  # the pass it became fully active
                        self.confirmed_at = now
                else:  # engine/__main__.main before #536: the same steps inline
                    if not self.lease.ok:  # prove the lease is ours before acting
                        self.lease.renew(force=True)
                    eng.apply_commands()
                status = eng.tick()
                eng.on_tick_ok()
            except LeaseLost as e:
                self._end("lease lost", str(e))
                return
            except SystemExit as e:  # #536: a degraded engine whose lease turns out to be another's exits
                self._end(f"exit code {e.code}", "SystemExit in the loop")
                return
            except JournalCorrupt as e:
                with contextlib.suppress(Exception):
                    eng.store.event("engine_error", {"error": f"JournalCorrupt: {e}"[:300]}, eng._rid())
                self._end("journal corrupt", str(e))
                return
            except Exception as e:
                self.stats["tick_errors"] += 1
                self.say(f"tick error {type(e).__name__}: {str(e)[:120]}")
                with contextlib.suppress(Exception):
                    eng.on_tick_error(e)
                status = {"state": "error", "error": f"{type(e).__name__}: {e}"[:300]}
            if status.get("state") != "error":
                self._could_decide(now)
            self.beat(status)
            if self.stop.get("lease_lost"):
                self._end("lease lost", "the heartbeat's renewal found the lease taken")
                return
            if not getattr(self.lease, "degraded", False):  # #536: nothing is sent to Supabase while degraded
                self.rep.pump()
        except Kill as k:
            self._killed(str(k))
            return
        self.check.after_pass()

    def _end(self, why: str, detail: str) -> None:
        self.stopped = (self.clock(), why)
        self.say(f"engine stopped: {why} ({detail[:100]})")
        if why == "journal corrupt":  # main: the replicator sends what it can, then the process exits with code 4
            with contextlib.suppress(Exception):
                self.rep.pump()
        with contextlib.suppress(Exception):
            self.eng.journal.close()
        self.eng = self.rep = None
        self.check.eng = None  # the checker reads the journal no more: the process is gone

    def beat_alive(self) -> None:
        if not self.use_lease:
            return super().beat_alive()
        if self.eng is not None and self.asleep_until is None and self.beat is not None:
            with contextlib.suppress(Exception):
                self.beat({"state": "alive"})
            if self.stop.get("lease_lost") and self.eng is not None:
                self._end("lease lost", "the heartbeat's renewal found the lease taken")


class _Capture(logging.Handler):
    """The engine's log lines of one drill, whatever the root logger's level (scripts/run_drills.py silences it)."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: list[tuple[int, str]] = []
        self._was: tuple | None = None

    def attach(self) -> None:
        lg = logging.getLogger("kaibot")
        self._was = (lg.level, lg.propagate)
        lg.setLevel(logging.INFO)
        lg.propagate = False  # the drill keeps the lines; the terminal stays quiet
        lg.addHandler(self)

    def detach(self) -> None:
        lg = logging.getLogger("kaibot")
        lg.removeHandler(self)
        if self._was is not None:
            lg.setLevel(self._was[0])
            lg.propagate = self._was[1]
            self._was = None

    def emit(self, record) -> None:
        with contextlib.suppress(Exception):
            self.lines.append((record.levelno, record.getMessage()))

    def last_error(self) -> str:
        return next((m for lv, m in reversed(self.lines) if lv >= logging.ERROR), "")


@contextlib.contextmanager
def _env():
    was = os.environ.get("KAIBOT_MULTIDAY")
    level = logging.getLogger().level
    try:
        yield
    finally:
        logging.getLogger().setLevel(level)
        if was is None:
            os.environ.pop("KAIBOT_MULTIDAY", None)
        else:
            os.environ["KAIBOT_MULTIDAY"] = was


def play(w: DrillWorld):
    with _env():
        try:
            return w.run()
        finally:
            w.log.detach()


def _ny(t: datetime) -> str:
    return f"{t.astimezone(NY):%m-%d %H:%M:%S}"


def _orders(w: World, side: str, sym: str, day: str) -> list[dict]:
    d = day.replace("-", "")
    return [o for o in w.sim.orders.values() if o.get("side") == side and o.get("symbol") == sym
            and str(o.get("client_order_id") or "").startswith("kb") and f"-{d}-" in str(o["client_order_id"])]


def _beats(w: World) -> list[datetime]:
    return [r["ts"] for r in w.replica.tables.get("heartbeat", []) if r.get("process") == "engine"]


# ---------------------------------------------------------------- 1. watchdog takeover
def watchdog_takeover(tmp: Path) -> list[Check]:
    out: list[Check] = []
    s = Scenario(31, {D1: ("B",), D2: None, D3: None}, ON_TIME, profile=NO_FAULTS, watchdog=True, heal=D2,
                 forced=(Forced("hold", "|B|buy", "never"),),
                 outages=(Outage(et(D1, "15:51"), 30, effects=1),))
    w = DrillWorld(s, tmp)
    res = play(w)
    kill = next((ln for ln in w.trace if "engine down: killed" in ln), None)
    acted = next((ln for ln in w.trace if ln[15:].startswith("watchdog:") and "canceled" in ln), None)
    check(out, "the engine was killed in the send window with its buy working", kill is not None, kill)
    buy = _orders(w, "buy", "B", D1)
    canceled = [o for o in buy if o["status"] == "canceled"]
    check(out, "the watchdog cancelled the open kb buy", canceled and acted, acted or [o["status"] for o in buy])
    if acted:
        age = json.loads(acted.split("watchdog: ", 1)[1]).get("age_s")
        check(out, "only once the heartbeat was over 90 s old (one 60 s simulated poll late at most)",
              age is not None and 90 < float(age) <= 90 + 60, f"heartbeat {age} s old when it acted")
    halts = [h for h in w.replica.tables.get("halt", []) if h.get("kind") == "watchdog"]
    events = sorted({e.get("type") for e in w.replica.tables.get("event", []) if str(e.get("type")).startswith(
        "watchdog")})
    check(out, "a halt row and an event", halts and events, f"halt rows {len(halts)}, events {events}")
    logged = failsafe.watchdog_cancels(w.root / failsafe.CANCELS_FILE)
    check(out, "the cancel was written to watchdog_cancels.jsonl before it was made",
          {o["client_order_id"] for o in canceled} <= logged and logged, sorted(logged))
    again = [o for o in _orders(w, "buy", "B", D2)]
    check(out, "the cancelled buy is sent again at the next session", again,
          [(o["client_order_id"], o["status"]) for o in again])
    check(out, "the simulation's invariants hold and the book ends holding B", res.ok, res.violations or "ok")
    return out


# ---------------------------------------------------------------- 2. dead-man stop placement
def _silent_day(seed: int, watchdog: bool) -> Scenario:
    # B is bought on D1; on D2 the engine is gone from 09:40 to after the close: a multi-day sleeve with no stop
    # (trend_momentum has none by design) under a silent engine
    return Scenario(seed, {D1: ("B",), D2: None, D3: None}, ON_TIME, profile=NO_FAULTS, watchdog=watchdog, heal=D3,
                    outages=(Outage(et(D2, "09:40"), 420, effects=1),))


def _stop_check(out: list[Check], who: str, w: World, prefix: str) -> dict | None:
    held = float((w.sim.pos.get("B") or {}).get("qty") or 0)
    stops = [o for o in w.sim.orders.values() if str(o.get("client_order_id") or "").startswith(prefix)
             and o.get("type") == "stop"]
    check(out, f"{who}: one protective stop for the sleeve", len(stops) == 1,
          [(o["client_order_id"], o["qty"], o.get("stop_price"), o.get("time_in_force")) for o in stops])
    if len(stops) != 1:
        return None
    st = stops[0]
    qty, px = float(st["qty"]), float(st["stop_price"])
    check(out, f"{who}: GTC, a sell, whole shares, never more than held",
          st.get("time_in_force") == "gtc" and st["side"] == "sell" and qty.is_integer() and 0 < qty <= held,
          f"{st.get('time_in_force')} {st['side']} {qty:g} of {held:g}")
    t = pd.Timestamp(st.get("submitted_at") or st.get("created_at"))
    last = w.sim.bars.last_close("B", t.to_pydatetime())
    want = int(last * 0.9 * 100) / 100 if last else None
    check(out, f"{who}: no stop known, so 10% below the last price", want is not None and abs(px - want) <= 0.011,
          f"stop {px:.2f}, last {last}, 10% below {want}")
    return {"qty": qty, "stop": px}


def deadman_stop_placement(tmp: Path) -> list[Check]:
    out: list[Check] = []
    w = DrillWorld(_silent_day(32, watchdog=True), tmp / "watchdog")
    res = play(w)
    py = _stop_check(out, "watchdog", w, "kbdm-")  # #585: the id both fail-safes send
    held_d2 = float((w.sim.pos.get("B") or {}).get("qty") or 0)
    sold = [o for o in w.sim.orders.values() if o.get("symbol") == "B" and o["side"] == "sell"
            and o.get("type") != "stop"]
    check(out, "watchdog: the multi-day sleeve is not sold at 15:55", not sold and held_d2 > 0,
          f"sales {[(o['client_order_id'], o['status']) for o in sold]}, held {held_d2:g}")
    check(out, "watchdog run: the simulation's invariants hold", res.ok, res.violations or "ok")
    sq = _deadman_run(out, tmp / "deadman")
    if py is not None and sq is not None:
        # each is 10% below the last price it read (a minute apart here): the same rule, the same shares
        check(out, "the SQL dead-man and the watchdog place the same stop (shares; the same 10% rule, each at its "
              "own last price)", py["qty"] == sq["qty"], f"watchdog {py}, dead-man {sq}")
    return out


class DeadmanWorld(DrillWorld):
    """The silent day with no watchdog: the SQL dead-man (0019) runs every minute in a local Postgres, reading the
    simulated Supabase (heartbeat, run, the published ledger positions) and answering Alpaca from the venue."""

    def __init__(self, scenario, root, db) -> None:
        super().__init__(scenario, root)
        self.db = db
        self.sim.down = False  # pg_harness.DeadmanDb.pump expects the flag of tests/fakes.FakeBroker

    def watchdog(self) -> None:
        now = self.clock()
        if self.healed or now.astimezone(NY).date() != pd.Timestamp(D2).date():
            return
        rid = self.check.rid
        if rid is None:
            return
        beats = _beats(self)
        self.db.sql("delete from kaibot.heartbeat; delete from kaibot.ledger_position")
        if beats:
            self.db.beat(max(beats))
        self.db.run(rid)
        rows = [r.get("row", r) for r in self.replica.tables.get("ledger_position", [])]
        latest = max((str(r["session"]) for r in rows), default=None)
        mine = [(r["strategy"], r["symbol"], float(r["qty"]), r.get("stop_px"), r.get("opened_session"))
                for r in rows if str(r["session"]) == latest and float(r.get("qty") or 0) > 0]
        if mine:
            self.db.ledger(rid, latest, mine)
        self.db.check(now)
        self.db.pump(self.sim)
        self.db.check(now + timedelta(seconds=20))
        self.db.pump(self.sim)


def _deadman_run(out: list[Check], root: Path) -> dict | None:
    import pytest
    from pg_harness import DeadmanDb

    try:
        db = DeadmanDb(root.parent / "pg")
    except pytest.skip.Exception as e:  # no PostgreSQL binaries here: the weekly CI job has them
        if os.environ.get("KAIBOT_DRILLS_REQUIRE_PG") == "1":
            check(out, "the SQL dead-man drill can run (PostgreSQL)", False, str(e))
        else:
            check(out, "the SQL dead-man part (skipped: no PostgreSQL binaries)", True, str(e))
        return None
    try:
        w = DeadmanWorld(_silent_day(32, watchdog=False), root, db)
        res = play(w)
        sq = _stop_check(out, "dead-man", w, "kbdm-")
        held = float((w.sim.pos.get("B") or {}).get("qty") or 0)
        sold = [o for o in w.sim.orders.values() if o.get("symbol") == "B" and o["side"] == "sell"
                and o.get("type") != "stop"]
        check(out, "dead-man: the multi-day sleeve is not sold in the 15:56 window", not sold and held > 0,
              f"sales {len(sold)}, held {held:g}")
        halts = db.one("select count(*) from kaibot.halt where kind = 'deadman'") if _has_halt(db) else "?"
        check(out, "dead-man: one `deadman` halt for the session", halts in ("1", "?"), f"halt rows {halts}")
        check(out, "dead-man run: the simulation's invariants hold", res.ok, res.violations or "ok")
        return sq
    finally:
        db.stop()


def _has_halt(db) -> bool:
    return db.one("select to_regclass('kaibot.halt') is not null") == "t"


# ---------------------------------------------------------------- 3. lease lost
def lease_lost(tmp: Path) -> list[Check]:
    out: list[Check] = []
    s = Scenario(33, {D1: ("B",), D2: ("B", "C"), D3: None}, ON_TIME, profile=NO_FAULTS, heal="2026-08-06",
                 outages=(Outage(et(D2, "10:00"), 4, kill=False),))
    w = DrillWorld(s, tmp, lease=True)
    fail = (et(D1, "15:49"), et(D1, "15:53"))
    taken: dict = {}

    def down(world):
        world.replica.lease_fail = True

    def up(world):
        world.replica.lease_fail = False

    def take_over(world) -> bool:  # another machine's engine starts while this one sleeps past its lease
        if world.asleep_until is None or world.lease is None or world.lease.age_s() <= 95:
            return False
        other = EngineLease(world.replica, "other-machine", "vm", mono=lambda: world.clock().timestamp(),
                            wall=lambda: world.clock().timestamp())
        taken.update(other.acquire(), at=world.clock())
        return True

    w.hooks += [(fail[0], down), (fail[1], up)]
    w.watches.append(take_over)
    res = play(w)
    d1 = _orders(w, "buy", "B", D1)
    during = [(t, x) for t, x in w.writes if fail[0] <= t < fail[1] and x.startswith("venue.submit")]
    unverified = [ln for lv, ln in w.log.lines if "engine lease renewal failed" in ln]
    check(out, "renewal failing: the lease is unverified and logged", unverified, unverified[:1])
    check(out, "renewal failing: no new entries while it fails (15:49-15:53)", not during,
          [x for _, x in during] or "none sent")
    check(out, "renewed again: the session's buy still goes out in its window", d1 and all(
        fail[1] <= pd.Timestamp(o.get("submitted_at") or o.get("created_at")).to_pydatetime() for o in d1),
        [(o["client_order_id"], o["status"]) for o in d1])
    check(out, "another engine took the expired lease over", taken.get("acquired") and taken.get("taken_over"),
          {k: taken.get(k) for k in ("acquired", "taken_over", "previous_host")})
    stopped = w.stopped
    check(out, "this engine stops at its next renewal: lease lost", stopped and stopped[1] == "lease lost",
          stopped and f"{stopped[1]} at {_ny(stopped[0])}")
    if stopped:
        after = [x for t, x in w.writes if t >= taken["at"]]
        check(out, "it sends nothing after the takeover", not after, after[:3] or "nothing")
        beats = [b for b in _beats(w) if b > taken["at"]]
        check(out, "it writes no heartbeat after the takeover (the row is the new engine's)", not beats,
              [str(b) for b in beats[:2]] or "none")
        # the scenario's D2 decision (B and C) is the new engine's to make: this one is gone
        left = [v for v in res.violations if "not running at the end" not in v and "latest decision wanted" not in v]
        check(out, "the simulation's invariants hold up to the stop", not left, left or "ok")
    return out


# ---------------------------------------------------------------- 4. Supabase down at start
def _supabase_world(tmp: Path, start: datetime, outages: tuple = (), back: datetime | None = None,
                    heal: str = D2) -> tuple[DrillWorld, datetime]:
    """Supabase unreachable from `start` until `back` (15:20 on D1); D1 has a decided buy of B for the 15:50 window."""
    s = Scenario(34, {D1: ("B",), D2: None, D3: None}, ON_TIME, profile=NO_FAULTS, heal=heal, outages=outages)
    w = DrillWorld(s, tmp, lease=True)
    back = back or et(D1, "15:20")
    full = FaultProfile(write_fail=1.0, read_fail=1.0)

    def outage(world):
        world.replica.lease_fail = True
        world.remote.profile, world.remote.armed = full, True

    def recover(world):
        world.replica.lease_fail = False
        world.remote.profile = NO_FAULTS

    w.hooks += [(start, outage), (back, recover)]
    return w, back


def supabase_down_at_start(tmp: Path) -> list[Check]:
    """The first start ever (no lease record in the journal): no degraded start is allowed, so every start is refused
    until Supabase is back."""
    out: list[Check] = []
    start = et(D0, "16:00")
    w, back = _supabase_world(tmp, start)
    res = play(w)
    refused = [r for r in w.refused if start <= r[0] < back]
    check(out, "every start while Supabase was down was refused with exit code 3", refused and all(
        r[1] == 3 for r in refused), f"{len(refused)} refusals, codes {sorted({r[1] for r in refused})}")
    msg = refused[0][2] if refused else ""
    check(out, "the log says the lease could not be checked", "lease could not be checked" in msg, msg[:160])
    early = [x for t, x in w.writes if start <= t < back]
    check(out, "nothing was done at the broker before the lease", not early, early[:3] or "nothing")
    beats = [b for b in _beats(w) if start <= b < back]
    check(out, "no heartbeat and no start before the lease", not beats, len(beats))
    started = next((ln for ln in w.trace if "engine up" in ln and ln[:11] >= f"{start.astimezone(NY):%m-%d %H:%M}"),
                   None)
    check(out, "once Supabase is back the engine starts", started and started[:5] == "08-03", started)
    buy = _orders(w, "buy", "B", D1)
    check(out, "and the session's buy still goes out (a late decision, M9 1.3)", buy,
          [(o["client_order_id"], o["status"]) for o in buy])
    check(out, "the simulation's invariants hold and the book ends holding B", res.ok, res.violations or "ok")
    return out


def supabase_down_at_a_restart(tmp: Path) -> list[Check]:
    """The engine ran since D0 (its journal holds its own lease record) and is killed on the morning of D1, with
    Supabase down from 08:55 on D1 to 15:20 on D2. M10 1.25 (#536): the restart comes up reducing only instead of
    exiting. D1's decided buy of B reaches its 15:50 send window while the engine is reducing only: nothing may be
    bought and no heartbeat attempted until the lease is confirmed. Once Supabase answers, the lease is confirmed,
    the journal verified and D2's owner config (unreadable at D2's open) read again, and the buy of B goes out once,
    in D2's 15:50-15:57 send window."""
    out: list[Check] = []
    back = et(D2, "15:20")
    w, _ = _supabase_world(tmp, et(D1, "08:55"), outages=(Outage(et(D1, "09:00"), 10, effects=1),), back=back,
                           heal=D3)
    res = play(w)
    kill = next((ln for ln in w.trace if "engine down: killed" in ln), None)
    check(out, "the engine ran and was killed with Supabase already down", kill and kill.startswith("08-03"), kill)
    if not kill:
        return out
    start = et(D1, kill[6:11], int(kill[12:14]))  # from here on the process is the restart's
    at = w.confirmed_at
    until = at or et(D3, "09:30")  # the degraded window: from the kill to the lease's confirmation
    refused = [r for r in w.refused if start <= r[0] < back]
    check(out, "the restart is not refused (no exit code 3 while Supabase is down)", not refused,
          [(_ny(t), c, m[:80]) for t, c, m in refused] or "none")
    up = next((ln for ln in w.trace if "engine up" in ln and start.astimezone(NY).strftime("%m-%d %H:%M") <= ln[:11]),
              None)
    check(out, "it comes up reducing only while Supabase is still down", up and "reducing only" in up
          and up[:5] == "08-03", up)
    warned = [m for lv, m in w.log.lines if lv == logging.WARNING and "Starting REDUCING ONLY" in m]
    check(out, "the log says it starts REDUCING ONLY", warned, warned[0][:160] if warned else "no such line")
    # the buy signal inside the degraded window: D1's decided buy of B reaches its send step at 15:50
    sends = _journal_kinds(w, "md_send", et(D1, "15:50"), et(D1, "15:57"))
    check(out, "D1's buy of B reaches its 15:50 send while the engine is reducing only",
          sends and at is not None and et(D1, "15:57") <= at, sends[:1] or "no md_send in D1's window")
    venue = [(o["client_order_id"], o["symbol"], _ny(t)) for o in w.sim.orders.values() if o.get("side") == "buy"
             for t in [_submitted(o)] if start <= t < until]
    check(out, "the venue received no buy while reducing only (its own orders, by submit time)", not venue,
          venue[:3] or "none")
    beats = [(_ny(t), pr) for t, pr in w.beat_calls if pr == "engine" and start <= t < until]
    later = [t for t, pr in w.beat_calls if pr == "engine" and at is not None and t >= at]
    check(out, "no heartbeat attempted while reducing only (every write the engine tried, failed or not)",
          not beats and later, beats[:3] or f"none; {len(later)} after the confirmation")
    check(out, "the lease is confirmed once Supabase is back", at is not None and at >= back, at and _ny(at))
    lines = [m for lv, m in w.log.lines if m.startswith("engine lease confirmed and journal verified")]
    check(out, "the log says fully active (entries allowed)",
          lines and lines[0] == "engine lease confirmed and journal verified: fully active (entries allowed)",
          lines[:1] or "no confirmation line")
    ev = [e.get("payload") or {} for e in w.replica.tables.get("event", [])
          if e.get("type") == "engine_lease_confirmed"]
    check(out, "the engine_lease_confirmed event says entries: true", ev and ev[0].get("entries") is True, ev[:1])
    buy = [o for o in w.sim.orders.values() if o.get("side") == "buy" and o.get("symbol") == "B"
           and str(o.get("client_order_id") or "").startswith("kb")]
    sent = [_submitted(o) for o in buy]
    check(out, "the buy of B goes out once, after the confirmation, in D2's 15:50-15:57 window",
          len(buy) == 1 and at is not None and all(at <= t and et(D2, "15:50") <= t < et(D2, "15:57") for t in sent),
          [(o["client_order_id"], o["status"], _ny(t)) for o, t in zip(buy, sent, strict=True)])
    check(out, "the simulation's invariants hold and the book ends holding B", res.ok, res.violations or "ok")
    return out


def _submitted(o: dict) -> datetime:
    return pd.Timestamp(o.get("submitted_at") or o.get("created_at")).to_pydatetime()


def _journal_kinds(w: World, kind: str, t0: datetime, t1: datetime) -> list[dict]:
    """The journal's entries of `kind` written between t0 and t1 (the journal outlives the processes)."""
    from kaibot.engine.journal import Journal

    j = Journal(w.jpath)
    try:
        return [b for _, ts, _k, b in j.entries(kinds=(kind,)) if t0 <= datetime.fromisoformat(ts) < t1]
    finally:
        j.close()


# ---------------------------------------------------------------- 5. journal damaged
def _damage(path: Path, kind: str, which: int = -1) -> int:
    """Change one entry's text on disk, outside SQLite's own checks (a damaged disk or a bad copy)."""
    db = sqlite3.connect(path)
    try:
        seqs = [r[0] for r in db.execute("select seq from entry where kind = ? order by seq", (kind,))]
        seq = seqs[which]
        db.execute("drop trigger if exists entry_no_update")
        db.execute("update entry set body = replace(body, '\"', '''') where seq = ?", (seq,))
        db.commit()
    finally:
        db.close()
    return seq


def journal_damaged_at_start(tmp: Path) -> list[Check]:
    from kaibot.engine.__main__ import EXIT_JOURNAL, open_journal

    out: list[Check] = []
    w = DrillWorld(Scenario(35, {D1: ("B",), D2: None, D3: None}, ON_TIME, profile=NO_FAULTS), tmp)
    res = play(w)
    check(out, "a clean run first", res.ok, res.violations or "ok")
    seq = _damage(w.jpath, "fill")
    cap = _Capture()
    cap.attach()
    try:
        code = None
        try:
            open_journal(w.jpath)
        except SystemExit as e:
            code = e.code
    finally:
        cap.detach()
    check(out, "the start exits with code 4", code == EXIT_JOURNAL, f"exit code {code}")
    line = cap.last_error()
    check(out, "the log line names the entry", f"journal entry {seq} fails its checksum" in line, line[:120])
    try:
        open_journal(w.jpath)
        again = None
    except SystemExit as e:
        again = e.code
    check(out, "every later start refuses the file the same way", again == EXIT_JOURNAL, f"exit code {again}")
    book = book_reader(w.jpath, w.replica, now=w.clock)()
    check(out, "the watchdog reads the ledger rows in Supabase instead", book.source == "store",
          f"book source {book.source}, {[(s.symbol, s.qty) for s in book.sleeves]}")
    return out


def journal_damaged_while_running(tmp: Path) -> list[Check]:
    out: list[Check] = []
    s = Scenario(36, {D1: ("B",), D2: None, D3: None}, ON_TIME, profile=NO_FAULTS)
    w = DrillWorld(s, tmp, lease=True)
    hit: dict = {}

    def damage(world):
        hit["seq"] = _damage(world.jpath, "md_decision")
        hit["at"] = world.clock()

    w.hooks.append((et(D1, "11:00"), damage))
    play(w)
    stopped = w.stopped
    check(out, "the engine stops once it reads the damaged entry", stopped and stopped[1] == "journal corrupt",
          stopped and f"{stopped[1]} at {_ny(stopped[0])}" or f"still running; trace: {w.trace[-1][:80]}")
    ev = [e for e in w.replica.tables.get("event", []) if e.get("type") == "engine_error"
          and "JournalCorrupt" in json.dumps(e.get("payload"))]
    check(out, "an engine_error event with the line reaches Supabase", ev, len(ev))
    return out


DRILLS: dict[str, tuple] = {
    # name: (function, the documents' sentences it holds the engine to, an open engine issue it fails on)
    "watchdog takeover": (watchdog_takeover, [
        ("docs/ops/failsafe.md", "cancel open Kaibot buys (`kb...`) not yet completely filled, halt row + event"),
        ("docs/ops/failsafe.md", "Before each entry cancel the watchdog appends the order's ids to "
                                 "`watchdog_cancels.jsonl`"),
        ("docs/ops/failsafe.md", "a buy the watchdog cancelled is sent again at the next session like one that never "
                                 "reached the venue"),
    ], None),
    "dead-man stop placement": (deadman_stop_placement, [
        ("docs/ops/failsafe.md", "a multi-day sleeve whose shares no resting stop of its own covers gets one: GTC "
                                 "stop sell, whole shares"),
        ("docs/ops/failsafe.md", "if none is known, or it is not below the last price, 10% below the last price"),
        ("docs/ops/failsafe.md", "Since S80 (decision 15 sections 4 and 6) none of them sells a **multi-day** sleeve"),
        ("docs/ops/failsafe.md", "One plan, two implementations"),
    ], None),
    "lease lost": (lease_lost, [
        ("docs/ops/failsafe.md", "renewal failing: no new entries; lease taken by another engine: this one stops"),
        ("docs/ops/run_engine.md", "This one stops and writes nothing more"),
        ("docs/ops/run_engine.md", "**No new entries**; open positions are still managed"),
    ], None),
    "supabase down at start": (supabase_down_at_start, [
        ("docs/ops/run_engine.md", "Without the lease the engine cannot know it is the only one, so it does not "
                                   "start."),
        ("docs/ops/run_engine.md", "| Start-up, before it touches the broker | Takes the lease"),
    ], None),
    "supabase down at a restart": (supabase_down_at_a_restart, [
        ("docs/ops/run_engine.md", "Exits, stops, sales and fills are managed; **nothing is bought**, no dashboard "
                                   "command is applied and the heartbeat is not written until `engine lease "
                                   "confirmed and journal verified`."),
        ("docs/ops/run_engine.md", "so the day's buys still go out if the 15:50-15:57 send window has not closed."),
        ("docs/ops/failsafe.md", "Only `entries: true` is logged as `fully active (entries allowed)`."),
        ("docs/ops/failsafe.md", "a buy whose window has closed waits for the next session"),
    ], None),
    "journal damaged at start": (journal_damaged_at_start, [
        ("docs/ops/ledger.md", "At start: the engine logs the line and exits with **code 4** before it takes the lease "
                               "or reads the broker."),
        ("docs/ops/ledger.md", "every later start refuses the file the same way"),
        ("docs/ops/ledger.md", "The watchdog, on a journal it cannot trust, reads the ledger rows in Supabase "
                               "instead."),
    ], None),
    "journal damaged while running": (journal_damaged_while_running, [
        ("docs/ops/ledger.md", "While running: the engine writes an `engine_error` event with the line, stops"),
    ], None),
}


def run(name: str, tmp: Path) -> Record:
    fn, _docs, known = DRILLS[name]
    rec = Record(name, known=known)
    t0 = time.monotonic()
    try:
        rec.checks = fn(tmp)
    except Exception as e:  # a drill that cannot finish is a failed drill, with the reason
        import traceback

        rec.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"
    rec.seconds = round(time.monotonic() - t0, 1)
    return rec
