"""The deterministic simulation of M12 ticket 3.2 (#464): whole engine sessions under one seed.

One seed gives one run of the REAL engine (`Engine.tick`, the live daily path, the order manager, the journal, the
journal store and its replicator) against the simulated venue behind `FaultyBroker`, a faulty market-data client and
a faulty Supabase (`kaibot.broker.faulty`). Nothing in the engine is edited or patched; the simulation only owns
what the engine does not: the clock, the venue, the replica, the owner, the watchdog, and the power switch.

The run (`Scenario.from_seed`) covers four evenings and three sessions:

    D0 evening   a paper run starts; the rule decides for D1 once the daily view of D0 is there
    D1, D2       a send window each (15:50-15:57 ET), fills, the close, the evening's decision (or no decision day)
    D3           the world is healed before the open: no more faults. The last window carries, or sends late, what
                 the earlier sessions lost, and the run must end where the latest decision wanted it

and on the way, drawn from the seed: which evenings decide and what they want; when each evening's daily view
arrives (sometimes only the next morning); the faults of `FaultProfile`; an owner who may press "Flatten and stop";
outages (the process is killed at a journal step or a venue call, counted from a moment of the run, and comes back
after seconds, minutes, a missed window or a missed night) and pauses (the process sleeps and wakes); and the real
watchdog (`engine/watchdog.Watchdog`), which acts on a stale heartbeat whether the engine is dead or only paused.

What is checked (`Checker`), after EVERY pass of the loop and once more at the end, are the nine invariants of the
engine audit (docs/research/engine_audit/report.md, "What to build so the engine keeps finding its own edge
cases"), in the form this engine gives them:

    1 the ledger's cash and shares are the fold of the journalled fills, to the cent
    2 every execution is booked at most once, and none is booked that the venue does not have
    3 at most one working venue order per decision (client order id without its attempt), across restarts
    4 an order's state never leaves a final state; its filled quantity never falls
    5 no buy beyond the target: shares held plus working buys never exceed the largest journalled target
    6 no short position, no negative cash
    7 at quiescence the ledger equals the venue, no order is unconfirmed, the replica has every row
    8 journal sequence numbers are gapless; every order at the venue had its intent journalled first
    9 no order outside its window, and no daily view from the future

and one liveness check at the end: after the healed last session the book holds exactly the names the latest
decision wanted (or nothing, when the owner flattened).

A failure names the seed; `python tests/dst/run_seed.py <seed>` runs it again and prints the trace.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import random
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
from fakes import FakeStore

from kaibot.broker.faulty import DEFAULT_FAULTS, FaultProfile, FaultyBroker, FaultyData, FaultyStore
from kaibot.broker.sim import NY, WORKING, SimBroker
from kaibot.engine import failsafe
from kaibot.engine import live_daily as ld
from kaibot.engine.clock import SimClock
from kaibot.engine.core import Engine
from kaibot.engine.daily import DailyData
from kaibot.engine.journal import (
    ID_TABLES,
    JSEQ_TABLES,
    Journal,
    JournalCorrupt,
    JournalStore,
    Replicator,
    canon,
    select_rows,
)
from kaibot.engine.market import LiveMarket
from kaibot.engine.orders import TERMINAL, UNCONFIRMED, St
from kaibot.engine.store import StoreError
from kaibot.engine.watchdog import Watchdog, book_reader
from kaibot.replay.history import HistoricalData
from kaibot.risk.profiles import load_profiles, rule_book_limits
from kaibot.strategies import momentum as mom
from kaibot.strategies.base import TargetList

D0, D1, D2, D3 = "2026-07-31", "2026-08-03", "2026-08-04", "2026-08-05"
D4 = "2026-08-06"  # the venue's calendar goes on: D3's evening has a next session to decide for
SESSIONS = (D1, D2, D3)
EVENINGS = (D0, D1, D2, D3)
STOCKS = ("B", "C", "D")
MD = "trend_momentum"
AMOUNT = 50_000.0
VENUE_CASH = 100_000.0
HISTORY_DAYS = 6  # sessions of minute bars before D0 (the market's history load; few, see daily_from)
SEND, CLOSE = 380, 390  # the minute bars at 15:50 and (one past the last) at 16:00
MARKET_PURPOSES = ("buy", "sell", "resize", "carry", "target")  # the daily path's near-close orders
log = logging.getLogger("kaibot.dst")
SAID = {  # the journal entries the trace shows, with the fields that tell the story
    "run": ("run_id", "amount"), "om_intent": ("coid", "side", "qty", "type"), "om_state": ("coid", "state", "reason"),
    "fill": ("coid", "activity", "side", "qty", "px"), "md_decision": ("session", "fill_session", "late", "note"),
    "md_send": ("session", "late_of", "targets"), "md_sent": ("session", "sent", "skipped"),
    "md_cancel": ("day", "sides", "reason"), "md_withheld": ("session", "symbol", "why"),
    "md_book_end": ("drawdown_pct",), "reconcile": ("when", "ok"),
}


# Engine defects the simulation found that are still open: issue -> what the generator leaves out so that every
# seed does not trip over the same one. Each has a strict xfail in test_dst.py; remove both when the issue is fixed.
KNOWN: dict[int, str] = {
    529: "no fill activity is delayed past the next session's window (faulty.DROP_MINUTES ends at 300 minutes): "
         "a fill booked a day late is not sold by the decision taken meanwhile, and a watchdog flatten of the "
         "unbooked shares leaves a difference only the owner can accept",
}


class Kill(BaseException):
    """The process dies here. Not an `Exception`: the engine must not be able to catch it."""


def et(day: str, hhmm: str, sec: int = 0) -> datetime:
    return (pd.Timestamp(f"{day} {hhmm}", tz=NY) + pd.Timedelta(seconds=sec)).tz_convert(UTC).to_pydatetime()


def session_open(day: str) -> datetime:
    return et(day, "09:30")


# ---------------------------------------------------------------- the scenario of a seed
@dataclass(frozen=True)
class Outage:
    """The engine goes away. `kill`: at the `effects`-th effect (a journal append, a venue write, a replica write;
    with `orders_only`, only those of the order path) counted from `arm`, before or after that effect; it is back
    `down` minutes later. Not `kill` (a pause): the process sleeps from `arm` for `down` minutes and wakes."""

    arm: datetime
    down: int
    kill: bool = True
    effects: int = 1
    orders_only: bool = False
    after: bool = False


@dataclass(frozen=True)
class Scenario:
    seed: int
    plan: dict  # fill session -> the names the rule wants to hold (a tuple), or None: not a decision day
    ready: dict  # evening -> when its daily view is in the daily store
    outages: tuple = ()
    flatten: datetime | None = None  # the owner presses "Flatten and stop"
    watchdog: bool = False
    profile: FaultProfile = DEFAULT_FAULTS
    sparse: bool = True  # skip quiet minutes of the session (the loop runs, the tick is not due)
    forced: tuple = ()  # faults a hand-written scenario names (kaibot.broker.faulty.Forced)
    heal: str = D3  # the session before whose open the faults stop

    @classmethod
    def from_seed(cls, seed: int, profile: FaultProfile = DEFAULT_FAULTS) -> Scenario:
        r = random.Random(f"kaibot-dst-{seed}")
        names = list(STOCKS)
        first = tuple(sorted(r.sample(names, r.choice((1, 1, 2)))))
        plan: dict = {D1: first, D2: None, D3: None}
        held, dropped = set(first), set()
        for day, p in ((D2, 0.5), (D3, 0.3)):
            if r.random() < p:
                # a name a decision dropped is not wanted again: its sale, if it did not fill whole, stays a sale
                # on the later sessions whatever a newer list says (engine/daily.builder_sizing, pre-registered),
                # so "the book holds what the latest decision wanted" would not be the rule's own promise there
                free = [s for s in names if s not in dropped]
                want = set(r.sample(free, min(r.choice((0, 1, 1, 2, 2)), len(free))))
                dropped |= held - want
                held = want
                plan[day] = tuple(sorted(want))
        ready = {}
        for ev, nxt in ((D0, D1), (D1, D2), (D2, D3)):
            if r.random() < 0.12:  # the daily store is refreshed only the next morning: a late decision
                ready[ev] = et(nxt, "09:30") + timedelta(minutes=r.choice((5, 40, 200)))
            else:
                ready[ev] = et(ev, "16:00") + timedelta(minutes=r.choice((3, 12, 25, 70, 200)))
        ready[D3] = et(D3, "16:10")
        outages = []
        for _ in range(r.choice((0, 1, 1, 2, 2, 3, 4))):
            day = r.choice((D1, D1, D2, D2, D0))
            where = r.random()
            if day == D0 or where < 0.2:  # an evening: the decision's journal steps
                arm = et(day, "16:00") + timedelta(minutes=r.randrange(0, 300))
            elif where < 0.75:  # around the send window and the fills
                arm = session_open(day) + timedelta(minutes=r.randrange(SEND - 8, CLOSE + 3))
            else:
                arm = session_open(day) + timedelta(minutes=r.randrange(-20, SEND - 8))
            down = r.choice((0, 0, 1, 1, 2, 3, 5, 9, 15, 45, 400, 1100))
            if r.random() < 0.25:
                outages.append(Outage(arm, max(down, 2), kill=False))
            else:
                outages.append(Outage(arm, down, effects=r.randrange(1, 25), orders_only=r.random() < 0.6,
                                      after=r.random() < 0.5))
        flatten = None
        if r.random() < 0.15:
            day = r.choice((D2, D2, D3))
            minute = r.choice((r.randrange(5, SEND), r.randrange(SEND, CLOSE)))
            flatten = session_open(day) + timedelta(minutes=minute)
        scale = r.choice((0.0, 0.5, 1.0, 1.0, 1.0, 2.0))
        profile = profile.scaled(scale)
        return cls(seed, plan, ready, tuple(sorted(outages, key=lambda o: o.arm)), flatten,
                   watchdog=r.random() < 0.5, profile=profile)

    def wanted(self, decidable: set[str] | None = None) -> tuple:
        """What the book must hold at the end: the latest decision's names. `decidable`: the fill sessions whose
        decision the engine had a chance to take (None: all of them)."""
        last = None
        for day in SESSIONS:
            if self.plan[day] is not None and (decidable is None or day in decidable):
                last = self.plan[day]
        return () if self.flatten is not None else tuple(last or ())


# ---------------------------------------------------------------- the rule, the bars, the venue, the replica
@dataclass(frozen=True)
class Scripted(mom.TrendMomentum):
    """trend_momentum's name, holding policy, sizing and carry-over conditions; its decisions are the scenario's."""

    plan: tuple = ()  # ((fill session, names or None), ...): hashable, so the rule stays a frozen dataclass

    def decision_day(self, prev, fill) -> bool:
        return dict(self.plan).get(str(fill)) is not None

    def on_event(self, ctx):
        want = dict(self.plan).get(str(ctx.fill_session))
        if want is None:
            return None
        # always a list on a decision day, as the real rule gives one: the book the rule reads here can lack a fill
        # the ledger has not booked yet, and it is the list (a held name missing from it is sold), not this
        # moment's holdings, that the send works from
        held = set(ctx.held)
        return TargetList(self.name, ctx.fill_session, {s: 1.0 for s in want},
                          features={"keep": sorted(held & set(want)), "buy": sorted(set(want) - held),
                                    "sell": {s: "not wanted" for s in sorted(held - set(want))}}, resize=False)


def minute_frames(seed: int) -> dict[str, pd.DataFrame]:
    """Minute bars of the three stocks, XLK and SPY from HISTORY_DAYS sessions before D0 to D4: gentle random walks,
    different for every seed."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end=D4, periods=HISTORY_DAYS + 5)
    opens = np.array([pd.Timestamp(f"{d.date()} 09:30", tz=NY).tz_convert(UTC).value for d in days])
    ts = pd.to_datetime((opens[:, None] + np.arange(390)[None, :] * 60_000_000_000).ravel(), utc=True)
    out = {}
    for s, p0, vol in (("B", 100.0, 2.5e-4), ("C", 60.0, 3e-4), ("D", 150.0, 2e-4), ("XLK", 200.0, 2e-4),
                       ("SPY", 500.0, 1.5e-4)):
        c = p0 * np.exp(np.cumsum(rng.normal(2e-6, vol, len(ts))))
        o = np.r_[p0, c[:-1]]
        h, lo = np.maximum(o, c) * 1.0003, np.minimum(o, c) * 0.9997
        out[s] = pd.DataFrame({"ts": ts, "o": o, "h": h, "l": lo, "c": c, "v": 2e5, "vw": (h + lo + c) / 3})
    return out


def daily_from(frames: dict[str, pd.DataFrame]) -> DailyData:
    """300 sessions of daily bars up to D4: the last ones are the minute bars' own days, the earlier ones a steady
    rise to them: enough history for the rule's averages, and steep enough against the few gentle days of minute bars
    that every stock stays above its 100-day average and SPY above its 200-day average. The carry-over and the late
    send skip a buy the rule could not make at the last close ("not buyable: trend", the market filter): by design,
    and not what this simulation is about, so the bars never give them a reason."""
    n = 300
    dates = np.busday_offset(np.datetime64(D4), -np.arange(n)[::-1], roll="backward").astype("datetime64[D]")
    symbols = [*STOCKS, "SPY"]
    c = np.zeros((n, len(symbols)))
    o = np.zeros_like(c)
    tail = HISTORY_DAYS + 5
    for j, s in enumerate(symbols):
        f = frames[s]
        day_c = f["c"].to_numpy().reshape(-1, 390)[:, -1]
        day_o = f["o"].to_numpy().reshape(-1, 390)[:, 0]
        ramp = np.linspace(day_c[0] * 0.6, day_c[0], n - tail + 1)[:-1]
        c[:, j] = np.r_[ramp, day_c]
        o[:, j] = np.r_[ramp, day_o]
    h, lo, v = np.maximum(o, c) * 1.01, np.minimum(o, c) * 0.99, np.full(c.shape, 2e6)
    return DailyData(dates=dates, symbols=symbols, sig_c=c.copy(), sig_h=h, sig_l=lo, raw_o=o, raw_c=c.copy(),
                     raw_h=h, raw_l=lo, v_first=v * 0.02, v_last=v * 0.02, raw_v=v,
                     stocks=np.arange(len(STOCKS)), spy=len(STOCKS), sig_unfilled_c=c.copy())


class Venue(SimBroker):
    """The simulated paper account: cost model v1, a cash book, Alpaca's wash-trade rule, shorting off, and
    positions marked as Alpaca marks them (`current_price`)."""

    name = "dst"

    def __init__(self, history, clock) -> None:
        super().__init__(history, clock, cash=VENUE_CASH)
        self.wash_check = True
        self.shorting_enabled = False

    def positions(self):
        now = self.clk()
        out = {}
        for s, p in self.pos.items():
            px = self.bars.last_close(s, now)
            out[s] = {**p, "current_price": None if px is None else str(px)}
        return out


class Replica(FakeStore):
    """Supabase as the engine and the watchdog see it: a second insert of a row with the same id or jseq is refused
    as PostgREST refuses it (409, 23505); the heartbeat is a row with the database's time."""

    def __init__(self, clock) -> None:
        super().__init__()
        self.clock = clock
        self.lease_now = clock

    def insert(self, table, row, returning=True):
        for r in row if isinstance(row, list) else [row]:
            for key in ("id", "jseq"):
                if key in r and any(x.get(key) == r[key] for x in self.tables.get(table, [])):
                    raise StoreError(f'POST {table} -> 409: {{"code":"23505","details":"Key ({key})=({r[key]}) '
                                     f'already exists.","message":"duplicate key value violates unique constraint"}}')
        return super().insert(table, row, returning)

    def select(self, table, params):
        return select_rows(self.tables.get(table, []), params)

    def heartbeat(self, process, detail):
        self.upsert("heartbeat", {"process": process, "ts": self.clock(), "detail": detail}, "process")


class Fuse:
    """Counts the effects and kills at the armed one."""

    ORDER_PATH = ("venue.", "journal.om_", "journal.fill", "journal.md_")

    def __init__(self) -> None:
        self.left = 0
        self.orders_only = self.after = self._pending = False
        self.fired: list[str] = []

    def arm(self, o: Outage) -> None:
        self.left, self.orders_only, self.after, self._pending = o.effects, o.orders_only, o.after, False

    def disarm(self) -> None:
        self.left, self._pending = 0, False

    def __call__(self, phase: str, what: str) -> None:
        if phase == "before":
            if self.left <= 0 or (self.orders_only and not what.startswith(self.ORDER_PATH)):
                return
            self.left -= 1
            if self.left:
                return
            if self.after:
                self._pending = True
                return
            self.fired.append(f"before {what}")
            raise Kill(what)
        if self._pending:
            self._pending = False
            self.fired.append(f"after {what}")
            raise Kill(what)


class FusedJournal(Journal):
    """The engine's journal with a crash point before and after every append."""

    def __init__(self, path, now, fuse: Fuse) -> None:
        super().__init__(path, now)
        self.fuse = fuse
        # the kill is simulated inside one process, so the operating system never loses a write: no fsync per entry
        # (tests/crash/test_crash_sigkill.py keeps the engine's own settings and really kills)
        self.db.execute("pragma synchronous=off")

    def append(self, kind: str, body: dict) -> int:
        what = f"journal.{kind}"
        self.fuse("before", what)
        seq = super().append(kind, body)
        self.fuse("after", what)
        return seq


# ---------------------------------------------------------------- the world
@dataclass
class Result:
    scenario: Scenario
    violations: list[str]
    trace: list[str]
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.violations

    def report(self, tail: int = 60) -> str:
        s = self.scenario
        lines = [f"seed {s.seed}: {len(self.violations)} violation(s)", *[f"  - {v}" for v in self.violations[:12]],
                 f"plan {s.plan}  flatten {s.flatten}  watchdog {s.watchdog}",
                 f"outages {[(str(o.arm), o.down, 'kill' if o.kill else 'pause') for o in s.outages]}",
                 f"run it again: PYTHONHASHSEED=0 PYTHONPATH=src python tests/dst/run_seed.py {s.seed}",
                 f"the last {tail} lines of the trace:", *[f"  {t}" for t in self.trace[-tail:]]]
        return "\n".join(lines)


class World:
    """Everything a crash does not touch, and the loop that drives the engine through the scenario."""

    def __init__(self, scenario: Scenario, root: Path | None = None) -> None:
        self.s = scenario
        self.own_root = root is None
        self.root = Path(tempfile.mkdtemp(prefix="kb-dst-")) if root is None else root
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = SimClock(et(D0, "16:01", 7))
        frames = minute_frames(scenario.seed)
        self.history = HistoricalData(frames, self.clock)
        self.daily_all = daily_from(frames)
        self.fuse = Fuse()
        self.sim = Venue(self.history.history, self.clock)
        self.broker = FaultyBroker(self.sim, scenario.seed, scenario.profile, on_write=self._venue_write,
                                   forced=scenario.forced)
        self.broker.on_fault = lambda fault, what: self.say(f"fault {fault} {what}")
        self.data = FaultyData(self.history, scenario.seed, scenario.profile)
        self.replica = Replica(self.clock)
        self.remote = FaultyStore(self.replica, scenario.seed, scenario.profile, now=self.clock, on_write=self.fuse)
        self.rule_s = Scripted(plan=tuple(sorted(scenario.plan.items())))
        pol = load_profiles()["balanced"]
        self.rule = ld.Rule(self.rule_s, ld.live_builder(self.rule_s, ld.live_limits(
            self.rule_s, pol, rule_book_limits(MD, "balanced", research=True))))
        self.jpath = self.root / "journal.sqlite"
        self.eng: Engine | None = None
        self.rep: Replicator | None = None
        self.down_until: datetime | None = None  # the engine is dead until then
        self.asleep_until: datetime | None = None  # the engine is paused until then
        self.outages = list(scenario.outages)
        self.healed = False
        self.trace: list[str] = []
        self.violations: list[str] = []
        self.stats = {"passes": 0, "kills": 0, "pauses": 0, "boots": 0, "boot_failures": 0, "tick_errors": 0,
                      "watchdog_acts": 0}
        self.check = Checker(self)
        self.dog = Watchdog(self.sim, self.replica, now=self.clock,
                            book=book_reader(self.jpath, self.replica, now=self.clock),
                            cancel_log=self.root / failsafe.CANCELS_FILE) if scenario.watchdog else None
        self.flatten_sent = False
        # The fill sessions whose decision the engine had a chance to take: it ran, without an error, between the
        # arrival of the previous evening's daily view and the session's close (before the open at any moment;
        # during the session over at least LATE_EVERY, the pace of the late decision's tries). An engine that was
        # down from before the view came until after the fill session closed has decided nothing for it, and no
        # later session makes that decision up: an outage of a whole session, not a defect of the engine.
        self.decidable: set[str] = set()
        self._first_ok: dict[str, datetime] = {}
        self._run_seen: datetime | None = None  # the first pass that saw the run (see _could_decide)
        self._first_due: dict[str, datetime] = {}
        self.replica.insert("command", {"kind": "new_run", "payload": {"amount_usd": AMOUNT, "level": "balanced"},
                                        "idempotency_key": "dst-run", "applied_at": None})

    # ------------------------------------------------------------ small things
    def say(self, what: str) -> None:
        self.trace.append(f"{self.clock().astimezone(NY):%m-%d %H:%M:%S} {what}")

    def bad(self, what: str) -> None:
        if what not in self.violations:
            self.violations.append(what)
            self.say(f"VIOLATION {what}")

    def _venue_write(self, phase: str, what: str) -> None:
        if phase == "before":
            self.check.on_venue_write(what)
        self.fuse(phase, what)

    def load(self, k: date, now: datetime):
        """The daily store: the view of the evening of session k, once it has been refreshed."""
        if et(str(k), "16:00") > now:
            self.bad(f"the engine asked for the daily view of {k} before that session's close (at {now})")
        ready = self.s.ready.get(str(k))
        if ready is None or now < ready:
            raise ld.DataNotReady(f"the daily store has no SPY bar for {k}")
        return ld.as_of(self.daily_all, self.daily_all.index_of(k))

    # ------------------------------------------------------------ the engine process
    def boot(self) -> None:
        """A new engine process on the journal file and the replica as they are."""
        self.stats["boots"] += 1
        # the journal's identity (a uuid4, drawn once when the file is made) decides the run's id and so every
        # client order id: it comes from the seed, like everything else
        jid = uuid.UUID(int=int.from_bytes(hashlib.blake2b(f"kaibot-dst-{self.s.seed}".encode(),
                                                           digest_size=16).digest(), "big"))
        with mock.patch.object(uuid, "uuid4", lambda: jid):
            journal = FusedJournal(self.jpath, self.clock, self.fuse)
        try:
            store = JournalStore(journal, self.remote, live=True, mono=lambda: self.clock().timestamp())
            market = LiveMarket(self.data, list(STOCKS), {s: "XLK" for s in STOCKS}, "SPY", self.root / "hist",
                                lambda: pd.Series(18.0, index=pd.date_range("2025-01-01", "2026-12-31")))
            eng = Engine(self.broker, market, store, load_profiles(), set(), now=self.clock,
                         daily=lambda e: ld.for_engine(e, self.load, rules=[self.rule]))
            eng.market.candidates_at = lambda m: pd.DataFrame()  # no intraday candidates: the daily path only
            eng.sleep = lambda s: None
            eng.monotonic = lambda: self.clock().timestamp()
            rep = Replicator(journal, self.remote)
            rep.clock = lambda: self.clock().timestamp()
            eng.startup()
        except BaseException:
            journal.close()
            raise
        self.eng, self.rep = eng, rep
        self.check.attach(eng)
        self.say("engine up")

    def die(self, why: str) -> None:
        if self.eng is not None:
            with contextlib.suppress(Exception):
                self.eng.journal.close()
        self.eng = self.rep = None
        self.fuse.disarm()
        self.say(f"engine down: {why}")

    def one_pass(self) -> None:
        """One iteration of the engine's main loop (engine/__main__.main): commands, the tick, the heartbeat,
        the replicator. A Kill ends the process; anything else is what the loop itself survives."""
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
            if self.down_until is not None and now < self.down_until:
                return
            try:
                self.boot()
            except Kill as k:
                self._killed(str(k))
                return
            except JournalCorrupt as e:
                self.bad(f"the journal is damaged: {e}")
                return
            except Exception as e:  # the launcher starts the engine again
                self.stats["boot_failures"] += 1
                self.say(f"start failed ({type(e).__name__}: {str(e)[:100]}): the launcher tries again")
                return
        eng = self.eng
        self.stats["passes"] += 1
        try:
            status: dict = {"state": "starting"}
            try:
                eng.apply_commands()
                status = eng.tick()
                eng.on_tick_ok()
            except JournalCorrupt as e:
                self.bad(f"the journal is damaged: {e}")
                self.die("journal corrupt")
                return
            except Exception as e:
                self.stats["tick_errors"] += 1
                self.say(f"tick error {type(e).__name__}: {str(e)[:120]}")
                with contextlib.suppress(Exception):
                    eng.on_tick_error(e)
                status = {"state": "error", "error": f"{type(e).__name__}: {e}"[:300]}
                with contextlib.suppress(Exception):
                    eng.store.event("engine_error", {"error": status["error"]}, eng._rid())
            if status.get("state") != "error":
                self._could_decide(now)
            eng.store.heartbeat(eng.process, status)
            self.rep.pump()
        except Kill as k:
            self._killed(str(k))
            return
        self.check.after_pass()

    def _could_decide(self, now: datetime) -> None:
        # #577: a run the owner starts after a close is "scheduled" until the next open, and a scheduled run decides
        # nothing (engine/core._daily_after_close); its first decision is the late one at that open (M9 1.3). A pass
        # with no running run is therefore no chance to decide.
        # The run's status is the engine's own word, so it is checked against the world's: a run seen first before a
        # session's open must be running by that session (the first open tick starts it, core._start_day). Else an
        # engine that never starts its run would decide nothing and the oracle would expect nothing.
        run = self.eng.run if self.eng is not None else None
        if run and self._run_seen is None:
            self._run_seen = now
        if run and run.get("status") == "scheduled":
            for day in SESSIONS:  # a session in progress that opened after the run was first seen
                if self._run_seen <= session_open(day) <= now < et(day, "16:00"):
                    first = self._first_due.setdefault(day, now)
                    if now - first >= ld.LATE_EVERY + timedelta(minutes=1):
                        self.bad(f"the run is still scheduled in the session of {day}, an open after it was started")
        if not run or run.get("status") not in ("running", "paused"):
            return
        for prev, fill in zip(EVENINGS, SESSIONS, strict=False):
            if fill in self.decidable or not (self.s.ready[prev] <= now < et(fill, "16:00")):
                continue
            first = self._first_ok.setdefault(fill, now)
            if now < session_open(fill) or now - max(first, session_open(fill)) >= ld.LATE_EVERY + timedelta(minutes=1):
                self.decidable.add(fill)

    def _killed(self, what: str) -> None:
        self.stats["kills"] += 1
        self.down_until = self.clock() + timedelta(minutes=getattr(self, "_down_for", 0))
        self.die(f"killed at {self.fuse.fired[-1] if self.fuse.fired else what}; back in "
                 f"{getattr(self, '_down_for', 0)} min")
        self.check.after_kill()

    # ------------------------------------------------------------ the owner and the watchdog
    def owner(self) -> None:
        if self.s.flatten is not None and not self.flatten_sent and self.clock() >= self.s.flatten:
            self.flatten_sent = True
            self.replica.insert("command", {"kind": "flatten_and_stop", "payload": {},
                                            "idempotency_key": "dst-flatten", "applied_at": None})
            self.say("owner: flatten and stop")

    def beat_alive(self) -> None:
        """A live, unpaused loop beats every ten seconds whether a tick is due or not."""
        if self.eng is not None and self.asleep_until is None:
            with contextlib.suppress(Exception):
                self.replica.heartbeat("engine", {"state": "alive"})

    def watchdog(self) -> None:
        if self.dog is None or self.healed:
            return
        try:
            out = self.dog.step()
        except Exception as e:
            self.say(f"watchdog error {type(e).__name__}: {str(e)[:100]}")
            return
        if out.get("canceled") or out.get("closed") or out.get("stops"):
            self.stats["watchdog_acts"] += 1
            self.say(f"watchdog: {json.dumps({k: v for k, v in out.items() if v}, default=str)[:200]}")

    # ------------------------------------------------------------ time
    def heal(self) -> None:
        self.healed = True
        self.broker.heal()
        self.data.armed = self.remote.armed = False
        self.fuse.disarm()
        self.asleep_until = None
        if self.down_until is not None and self.down_until > self.clock():
            late = et(D3, "15:20")  # an engine that was to stay down returns before the last window at the latest
            self.down_until = min(self.down_until, late)
        self.say("the world is healed: no more faults")

    def at(self, t: datetime, run: bool = True) -> None:
        self.clock.set(t)
        self.owner()
        if run:
            self.one_pass()
        else:
            self.beat_alive()

    def session(self, day: str, rng: random.Random) -> None:
        o = session_open(day)
        for hhmm in ("09:00", "09:25"):
            self.at(et(day, hhmm, 7))
        due = self._due(rng)
        for k in range(CLOSE):
            self.broker.advance(pd.Timestamp(o + timedelta(minutes=k)))
            t = o + timedelta(minutes=k + 1, seconds=7)
            busy = self.eng is None or self.asleep_until is not None or bool(self.fuse.left) \
                or bool(self.eng.daily and self.eng.daily.om and self.eng.daily.om.live)
            self.at(t, run=k in due or busy or not self.s.sparse)
            self.watchdog()

    def _due(self, rng: random.Random) -> set[int]:
        due, k = {0, 1, 2}, 2
        while k < SEND - 10:
            k += rng.choice((1, 2, 3, 5, 8, 13, 21, 34))
            due.add(k)
        return due | set(range(SEND - 10, CLOSE))

    def evening(self, day: str) -> None:
        marks = {5, 20, 45, 90, 180, 300}
        ready = self.s.ready.get(day)
        if ready is not None and ready < et(day, "23:00"):
            marks |= {int((ready - et(day, "16:00")).total_seconds() // 60) + m for m in (1, 2)}
        for m in sorted(marks):
            self.at(et(day, "16:00", 7) + timedelta(minutes=m))

    def run(self) -> Result:
        os.environ["KAIBOT_MULTIDAY"] = "on"
        rng = random.Random(f"kaibot-dst-ticks-{self.s.seed}")
        try:
            self.evening(D0)
            for day in SESSIONS:
                if day == self.s.heal:
                    self.clock.set(et(day, "08:00"))
                    self.heal()
                self.session(day, rng)
                self.evening(day)
            for m in range(1, 4):  # quiescence: the loop runs on, nothing new happens
                self.at(et(D3, "21:10", 7) + timedelta(minutes=m))
            self.check.at_quiescence()
        finally:
            if self.eng is not None:
                with contextlib.suppress(Exception):
                    self.eng.journal.close()
            if self.own_root:
                shutil.rmtree(self.root, ignore_errors=True)
        faults = [*self.broker.log, *self.data.log, *self.remote.log]
        self.stats["faults"] = len(faults)
        self.stats["fault_kinds"] = sorted({f[1] for f in faults})
        return Result(self.s, self.violations, self.trace, self.stats)


# ---------------------------------------------------------------- the invariants
class Checker:
    """The nine invariants, checked incrementally: the journal's new entries after every pass, the venue's books
    against the engine's, and at the end the quiescence and liveness checks."""

    def __init__(self, world: World) -> None:
        self.w = world
        self.eng: Engine | None = None
        self.seq = 0  # the last journal entry checked
        self.intents: set[str] = set()
        self.booked: set[str] = set()
        self.state: dict[str, str] = {}  # coid -> its last journalled state
        self.targets: dict[str, int] = {}  # symbol -> the largest journalled target
        self.fills_cash = 0.0  # the cash the journalled fills moved
        self.fills_qty: dict[str, float] = {}
        self.cum: dict[str, float] = {}  # venue order id -> filled quantity last seen
        self.rid: int | None = None

    def attach(self, eng: Engine) -> None:
        self.eng = eng
        self.journal_tail()

    # --- 8, 2, 4, 5 (the journal's side)
    def journal_tail(self) -> None:
        w = self.w
        for seq, _ts, kind, b in self.eng.journal.entries(after=self.seq):
            if seq != self.seq + 1:
                w.bad(f"journal sequence numbers have a gap: {self.seq} is followed by {seq}")
            self.seq = seq
            if kind in SAID and not (kind == "om_state" and self.state.get(b["coid"]) == str(b.get("state"))):
                w.say(f"journal {seq} {kind} " + " ".join(f"{k}={b[k]}" for k in SAID[kind] if b.get(k) is not None))
            if kind == "run":
                self.rid = b.get("run_id")
            elif kind == "om_intent":
                self.intents.add(b["coid"])
                self.state[b["coid"]] = "intent"
            elif kind == "om_state":
                was, now = self.state.get(b["coid"]), str(b.get("state"))
                if was in TERMINAL and now != was and not (was == "rejected" and now in ("accepted", "filled",
                                                                                         "partially_filled")):
                    w.bad(f"{b['coid']} left its final state {was} for {now}")
                self.state[b["coid"]] = now
            elif kind == "fill":
                aid = str(b.get("activity"))
                if aid in self.booked:
                    w.bad(f"execution {aid} was booked twice")
                self.booked.add(aid)
                sign = 1.0 if b["side"] == "buy" else -1.0
                self.fills_cash += -sign * float(b["qty"]) * float(b["px"]) - float(b.get("fees") or 0.0)
                self.fills_qty[b["symbol"]] = self.fills_qty.get(b["symbol"], 0.0) + sign * float(b["qty"])
            elif kind == "md_send":
                for sym, shares, *_ in b.get("targets") or []:
                    self.targets[sym] = max(self.targets.get(sym, 0), int(shares))

    # --- 8 (intent before send), 9 (the window)
    def on_venue_write(self, what: str) -> None:
        w = self.w
        if not what.startswith("venue.submit "):
            return
        _, sym, side, coid = what.split(" ", 3)
        if not coid.startswith("kb"):
            return
        if self.eng is not None:
            self.journal_tail()
        if coid not in self.intents:
            w.bad(f"{coid} was sent before its intent was journalled")
        parts = coid.split("-")
        purpose, day = parts[-2], parts[-3]
        now = w.clock().astimezone(NY)
        if purpose in MARKET_PURPOSES:
            if day != f"{now:%Y%m%d}" or not (now.hour == 15 and 50 <= now.minute <= 59):
                w.bad(f"{coid} left outside its window, at {now:%Y-%m-%d %H:%M:%S} New York")
            if w.broker._skew is not None and w.broker.armed:
                w.bad(f"{coid} was sent while the host clock and the venue's disagreed")
        elif not (now.hour * 60 + now.minute >= 570 and now.hour < 16):
            w.bad(f"{coid} ({purpose}) was sent while the market was closed, at {now:%H:%M:%S}")

    # --- after every pass
    def after_pass(self) -> None:
        self.journal_tail()
        self.venue_side()
        self.ledger_side()

    def after_kill(self) -> None:
        self.venue_side()

    def venue_side(self) -> None:
        """3, 4, 5, 6 on the venue's own books (the truth the engine cannot see all of)."""
        w, sim = self.w, self.w.sim
        alive: dict[str, int] = {}
        buying: dict[str, float] = {}
        for oid, o in sim.orders.items():
            done = float(o.get("filled_qty") or 0.0)
            if done < self.cum.get(oid, 0.0) - 1e-9:
                w.bad(f"venue order {oid}: filled quantity fell from {self.cum[oid]:g} to {done:g}")
            self.cum[oid] = done
            coid = str(o.get("client_order_id") or "")
            if o["status"] in WORKING and coid.startswith("kb") and o.get("type") == "market":
                base = coid.rsplit("-", 1)[0]
                alive[base] = alive.get(base, 0) + 1
                if o["side"] == "buy":
                    buying[o["symbol"]] = buying.get(o["symbol"], 0.0) + float(o["qty"]) - done
        for base, n in alive.items():
            if n > 1:
                w.bad(f"{n} working venue orders for one decision ({base})")
        for s in STOCKS:
            have = float((sim.pos.get(s) or {}).get("qty") or 0.0)
            if have < -1e-9:
                w.bad(f"the venue holds a short position in {s}: {have:g}")
            if have + buying.get(s, 0.0) > self.targets.get(s, 0) + 1e-9:
                w.bad(f"a buy beyond the target in {s}: {have:g} held and {buying.get(s, 0.0):g} working, the "
                      f"largest journalled target is {self.targets.get(s, 0)}")
        if sim.cash is not None and sim.cash < -0.005:
            w.bad(f"the venue's cash is negative: {sim.cash:.2f}")

    def ledger_side(self) -> None:
        """1, 2, 6: the engine's ledger against the fold of its own journalled fills, and against the venue."""
        w, eng = self.w, self.eng
        if eng is None or self.rid is None:
            return
        book = eng.ledger.book(self.rid)
        if book is None:
            return
        if abs(book.cash - (book.amount + self.fills_cash)) > 0.005:
            w.bad(f"the ledger's cash {book.cash:.4f} is not the run's amount plus its journalled fills "
                  f"{book.amount + self.fills_cash:.4f}")
        mine = {s: q for s, q in book.qty_by_symbol().items() if abs(q) > 1e-9}
        want = {s: q for s, q in self.fills_qty.items() if abs(q) > 1e-9}
        if mine != want:
            w.bad(f"the ledger's shares {mine} are not the fold of its journalled fills {want}")
        if book.cash < -0.005:
            w.bad(f"the ledger's cash is negative: {book.cash:.2f}")
        for s, q in mine.items():
            if q < -1e-9:
                w.bad(f"the ledger holds a short position in {s}: {q:g}")
        ghost = self.booked - {str(f["id"]) for f in w.sim.fills}
        if ghost:
            w.bad(f"booked executions the venue does not have: {sorted(ghost)}")

    # --- 7 and the liveness check
    def at_quiescence(self) -> None:
        w, eng, sim = self.w, self.eng, self.w.sim
        if eng is None:
            w.bad("the engine is not running at the end")
            return
        self.after_pass()
        if self.rid is None:
            w.bad("no paper run was ever started")
            return
        book = eng.ledger.book(self.rid)
        mine = {s: q for s, q in book.qty_by_symbol().items() if abs(q) > 1e-9}
        theirs = {s: float(p["qty"]) for s, p in sim.pos.items()}
        if mine != theirs:
            w.bad(f"at quiescence the ledger's shares {mine} are not the venue's {theirs}")
        if abs((book.cash - book.amount) - (sim.cash - VENUE_CASH)) > 0.005:
            w.bad(f"at quiescence the ledger's cash moved {book.cash - book.amount:.4f}, the venue's "
                  f"{sim.cash - VENUE_CASH:.4f}")
        executed = {str(f["id"]) for f in sim.fills
                    if str((sim.orders.get(f["order_id"]) or {}).get("client_order_id") or "").startswith("kb")}
        if executed - self.booked:
            w.bad(f"at quiescence {len(executed - self.booked)} executions are not booked: "
                  f"{sorted(executed - self.booked)[:4]}")
        om = eng.daily.om if eng.daily is not None else None
        for r in (om.orders.values() if om is not None else ()):
            if r.state in UNCONFIRMED or r.state in (St.PENDING_CANCEL, St.PENDING_REPLACE):
                w.bad(f"at quiescence {r.coid} is still {r.state}")
        for o in sim.orders.values():
            if o["status"] in WORKING and str(o.get("client_order_id") or "").startswith("kb") \
                    and o.get("type") == "market":
                w.bad(f"at quiescence the venue still works {o['client_order_id']}")
        rec = eng.recon.check("dst")
        if rec.diffs:
            w.bad(f"at quiescence the engine's reconciliation finds differences: {rec.summary()['diffs']}")
        w.rep.pump()
        if w.rep.pending():
            w.bad(f"at quiescence {w.rep.pending()} rows have not reached the replica ({w.rep.last_error})")
        for table in sorted(ID_TABLES | JSEQ_TABLES):
            local = eng.store.tables.get(table, [])
            remote = [r for r in w.replica.tables.get(table, [])  # the watchdog's own rows have no jseq
                      if table in ID_TABLES or r.get("jseq") is not None]
            if len(remote) != len(local):
                w.bad(f"at quiescence the replica's table {table} has {len(remote)} rows, the journal's "
                      f"{len(local)}")
            elif table in ID_TABLES:
                a = sorted(json.dumps(canon(r), sort_keys=True, default=str) for r in local)
                b = sorted(json.dumps({k: v for k, v in canon(r).items() if k != "jseq"}, sort_keys=True,
                                      default=str) for r in remote)
                if a != b:
                    w.bad(f"at quiescence the replica's table {table} differs from the journal's")
        held = tuple(sorted(mine))
        wanted = w.s.wanted(w.decidable)
        if held != tuple(sorted(wanted)):
            w.bad(f"at the end the book holds {held}; the latest decision wanted {tuple(sorted(wanted))}"
                  + (" (the owner flattened)" if w.s.flatten is not None else ""))
        if w.s.flatten is not None and eng.run is not None:
            w.bad("the owner pressed Flatten and stop and the run has not ended")


def run_seed(seed: int, profile: FaultProfile = DEFAULT_FAULTS, root: Path | None = None) -> Result:
    return run_scenario(Scenario.from_seed(seed, profile), root)


def run_scenario(scenario: Scenario, root: Path | None = None) -> Result:
    was = os.environ.get("KAIBOT_MULTIDAY")
    level = logging.getLogger().level
    try:
        return World(scenario, root).run()
    finally:
        logging.getLogger().setLevel(level)
        if was is None:
            os.environ.pop("KAIBOT_MULTIDAY", None)
        else:
            os.environ["KAIBOT_MULTIDAY"] = was
