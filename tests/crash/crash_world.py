"""The crash-point harness of M12 ticket 3.3 (#465): every write the engine's order path makes is a numbered crash
point; the process is killed at each one, restarted from what is on disk, and the invariants are checked.

The idea is ALICE's (crash-consistency testing by enumeration), in its cheap form. Nothing in the engine is edited:
the harness wraps the three places a write can go.

    journal   Journal.append, Journal.set_cursor, Journal.map_id   (the local SQLite file)
    store     insert / upsert / update at the replica               (Supabase; here an in-memory fake)
    venue     submit / cancel / replace                             (SimBroker)

Each write is one numbered effect and two crash points: `before` (the process dies just before the write: it did
not happen) and `after` (the write happened, and the process dies before anything else runs). The kill is a
`BaseException`, so the engine cannot catch it.

What dies and what survives. The engine side (`Engine`: journal connection, journal store, order manager,
replicator, everything in memory) is thrown away. The world (`World`: the clock, the venue, the replica) survives,
as the real ones do. The restart does not use the dead process's SQLite connection: it copies the journal's files
as they are on disk at the kill (the database and its write-ahead log) into a new directory, a disk image, and
opens that. A variant can damage the image first: `torn` cuts the tail of the write-ahead log (a last record that
was not completely written), `flipped` flips one byte in the last record's frame.

The scenario (`STEPS`) is a fixed two-session script through the real order manager, target loop, journal store,
ledger and replicator: a near-close buy with a partial fill, a marketable limit that is re-priced, a resting order
that is cancelled, a sale whose answer times out, the session marks and the replication of every row. Every step
is written as the engine's own steps are: idempotent, so that the restart simply runs the interrupted step again.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd
from fakes import FakeStore

from kaibot.broker.protocol import OrderRequest
from kaibot.broker.sim import NY, WORKING, SimBroker
from kaibot.engine.clock import SimClock
from kaibot.engine.execution import ExecutionPolicy
from kaibot.engine.journal import ID_TABLES, Journal, JournalStore, Replicator, canon, select_rows
from kaibot.engine.ledger import LEDGER_KINDS, Ledger, price_key
from kaibot.engine.orders import UNCONFIRMED, VENUE, OrderManager, St, client_order_id
from kaibot.engine.reconcile import compare
from kaibot.engine.store import StoreError
from kaibot.engine.targets import Target, reconcile_targets

DAYS = ("2026-09-15", "2026-09-16")
SYMBOLS = ("A", "B")
START_CASH = 100_000.0
REPLICA = "supabase"


class Kill(BaseException):
    """The process dies here. Not an `Exception`: the engine must not be able to catch it."""


@dataclass
class Fuse:
    """Numbers the effects and kills at the armed crash point. `log` names every effect seen, in order."""

    at: int | None = None  # the effect number to die at (1-based); None: never
    phase: str = "before"
    n: int = 0
    log: list[str] = field(default_factory=list)
    fired: str | None = None

    def before(self, what: str) -> None:
        self.n += 1
        self.log.append(what)
        if self.at == self.n and self.phase == "before":
            self._fire(what)

    def after(self, what: str) -> None:
        if self.at == self.n and self.phase == "after":
            self._fire(what)

    def _fire(self, what: str) -> None:
        self.at, self.fired = None, what
        raise Kill(what)


class CrashJournal(Journal):
    def __init__(self, path, now, fuse: Fuse) -> None:
        super().__init__(path, now)
        self.fuse = fuse
        # the kill is simulated inside one process, so the operating system never loses a write: no need to pay
        # an fsync per entry here. test_crash_sigkill.py keeps the engine's own settings and really kills.
        self.db.execute("pragma synchronous=off")
        self.db.execute("pragma wal_autocheckpoint=0")  # the image keeps its write-ahead log: the variants cut it

    def append(self, kind: str, body: dict) -> int:
        what = f"journal.append {kind}" + (f" {body.get('table')}" if kind == "row" else "") + (
            f" {body.get('state')}" if kind == "om_state" else "")
        self.fuse.before(what)
        seq = super().append(kind, body)
        self.fuse.after(what)
        return seq

    def set_cursor(self, name: str, seq: int) -> None:
        self.fuse.before("journal.set_cursor")
        super().set_cursor(name, seq)
        self.fuse.after("journal.set_cursor")

    def map_id(self, name: str, table: str, engine_id: int, db_id: int) -> None:
        self.fuse.before("journal.map_id")
        super().map_id(name, table, engine_id, db_id)
        self.fuse.after("journal.map_id")


class Replica(FakeStore):
    """Supabase as the replicator sees it: a second insert of a row with the same id or jseq is refused as
    PostgREST refuses it (409, 23505), which is what makes a re-send after a lost answer harmless."""

    def __init__(self, fuse: Fuse) -> None:
        super().__init__()
        self.fuse = fuse

    def insert(self, table, row, returning=True):
        for key in ("id", "jseq"):
            if key in row and any(r.get(key) == row[key] for r in self.tables.get(table, [])):
                raise StoreError(f'POST {table} -> 409: {{"code":"23505","details":"Key ({key})=({row[key]}) already '
                                 f'exists.","message":"duplicate key value violates unique constraint"}}')
        self.fuse.before(f"store.insert {table}")
        out = super().insert(table, row, returning)
        self.fuse.after(f"store.insert {table}")
        return out

    def upsert(self, table, row, on_conflict):
        self.fuse.before(f"store.upsert {table}")
        super().upsert(table, row, on_conflict)
        self.fuse.after(f"store.upsert {table}")

    def update(self, table, match, values):
        self.fuse.before(f"store.update {table}")
        super().update(table, match, values)
        self.fuse.after(f"store.update {table}")

    def select(self, table, params):
        return select_rows(self.tables.get(table, []), params)


class CrashVenue:
    """`SimBroker` with crash points on its writes, and the scenario's venue faults keyed by client order id (so a
    step that runs again after a restart meets the same venue)."""

    def __init__(self, sim: SimBroker, fuse: Fuse) -> None:
        self.sim, self.fuse = sim, fuse
        self.faults: dict[str, tuple] = {}  # decision (client order id without its attempt number) -> script call
        self.sent_without_intent: list[str] = []
        self.journal: Journal | None = None

    def __getattr__(self, name: str):
        return getattr(self.sim, name)

    def submit(self, order: OrderRequest) -> dict:
        coid = str(order.client_order_id)
        if coid not in {b["coid"] for _, _, _, b in self.journal.entries(kinds=("om_intent",))}:
            self.sent_without_intent.append(coid)
        what = f"venue.submit {order.symbol} {order.side}"
        self.fuse.before(what)
        fault = self.faults.pop(coid.rsplit("-", 1)[0], None)  # by decision: whichever attempt arrives first
        if fault is not None:
            getattr(self.sim.script, fault[0])(*fault[1:])
        try:
            return self.sim.submit(order)
        finally:  # also when the venue's answer is an error or never comes: the process can die right there
            self.sim.script._plans.clear()
            self.fuse.after(what)

    def cancel(self, order_id: str) -> None:
        self.fuse.before("venue.cancel")
        self.sim.cancel(order_id)
        self.fuse.after("venue.cancel")

    def replace(self, order_id: str, **kw) -> dict:
        self.fuse.before("venue.replace")
        out = self.sim.replace(order_id, **kw)
        self.fuse.after("venue.replace")
        return out


def et(day: str, hhmm: str, sec: int = 0) -> datetime:
    return (pd.Timestamp(f"{day} {hhmm}", tz=NY) + pd.Timedelta(seconds=sec)).tz_convert(UTC).to_pydatetime()


def bars(day: str, shift: int) -> pd.DataFrame:
    m = np.arange(390)
    px = 100.0 + ((m * 7 + shift) % 5) - 2.0
    ts = pd.Timestamp(f"{day} 09:30", tz=NY).tz_convert(UTC) + pd.to_timedelta(m, unit="m")
    return pd.DataFrame({"ts": ts, "o": px, "h": px + 0.05, "l": px - 0.05, "c": px, "v": 10_000.0, "vw": px})


class World:
    """What a crash does not touch: the clock, the venue, the replica, and the directory the disk images go in."""

    def __init__(self, fuse: Fuse | None = None) -> None:
        from kaibot.replay.history import BarHistory

        self.fuse = fuse or Fuse()
        self.root = Path(tempfile.mkdtemp(prefix="kb-crash-"))
        self.clock = SimClock(et(DAYS[0], "15:40", 7))
        frames = {s: pd.concat([bars(d, i + j) for j, d in enumerate(DAYS)], ignore_index=True)
                  for i, s in enumerate(SYMBOLS)}
        self.history = BarHistory(frames)
        self.sim = SimBroker(self.history, self.clock, cash=START_CASH, replace_new_id=True, near_close=time(15, 50))
        self.venue = CrashVenue(self.sim, self.fuse)
        self.replica = Replica(self.fuse)
        self.images = 0
        self.targets: dict[tuple[str, str], int] = {}
        self.buys_planned = self.sales_planned = False  # the venue's faults are planned once, crash or no crash

    def image_dir(self) -> Path:
        self.images += 1
        d = self.root / f"image{self.images}"
        d.mkdir()
        return d

    def to(self, day: str, hhmm: str) -> None:
        """Trade every minute up to this time (a time already passed: nothing happens, the step ran before)."""
        end = et(day, hhmm, 7)
        while self.clock() < end:
            self.sim.advance(self.clock().replace(second=0, microsecond=0))
            self.clock.advance(minutes=1)

    def tick(self, minutes: int = 1) -> None:
        for _ in range(minutes):
            self.sim.advance(self.clock().replace(second=0, microsecond=0))
            self.clock.advance(minutes=1)

    def session(self) -> str:
        return str(self.clock().astimezone(NY).date())

    def prices(self) -> dict[str, float]:
        return {s: self.history.last_close(s, self.clock()) or 100.0 for s in SYMBOLS}

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class Engine:
    """What a crash destroys: one process's view of the journal file."""

    def __init__(self, world: World, directory: Path) -> None:
        self.world, self.dir = world, directory
        self.journal = CrashJournal(directory / "journal.sqlite", world.clock, world.fuse)
        world.venue.journal = self.journal
        self.store = JournalStore(self.journal, live=True)
        self.replicator = Replicator(self.journal, world.replica, name=REPLICA)
        runs = self.store.tables.get("run") or []
        self.run_id: int | None = runs[0]["id"] if runs else None
        self.om = OrderManager(world.venue, self.journal, world.clock, run_id=self.run_id) if runs else None

    def recover(self) -> None:
        """Start-up: the unconfirmed orders are looked up, the venue's fills are booked."""
        if self.om is not None:
            self.om.recover()

    def book(self):
        return Ledger.fold(self.journal.entries(kinds=LEDGER_KINDS)).book(self.run_id)

    def image(self, damage: str | None = None) -> Path:
        """The journal's files as they are on disk now, copied (the connection is still open, as at a real kill:
        nothing is flushed or checkpointed for the copy), optionally with a damaged last record."""
        out = self.world.image_dir()
        for suffix in ("", "-wal"):
            src = Path(f"{self.journal.path}{suffix}")
            if src.exists():
                shutil.copyfile(src, out / f"journal.sqlite{suffix}")
        wal = out / "journal.sqlite-wal"
        if damage is not None and wal.exists() and wal.stat().st_size > 64:
            raw = bytearray(wal.read_bytes())
            if damage == "torn":
                del raw[-37:]  # the last frame's tail never reached the disk
            elif damage == "flipped":
                raw[-200] ^= 0x40  # one bit of the last frame's page
            else:
                raise ValueError(damage)
            wal.write_bytes(bytes(raw))
        return out

    def abandon(self) -> None:
        self.journal.close()


# ---------------------------------------------------------------- the scenario
def coid(e: Engine, strategy: str, symbol: str, day: str, purpose: str = "target", attempt: int = 1) -> str:
    return client_order_id(e.run_id, strategy, symbol, day, purpose, attempt)


def _loop(w: World, e: Engine, style: str) -> None:
    held = {k: s.qty for k, s in e.book().sleeves.items()}
    quotes = {s: (px - 0.01, px + 0.01) for s, px in w.prices().items()}
    targets = [Target(s, sym, n) for (s, sym), n in sorted(w.targets.items())]
    reconcile_targets(e.om, ExecutionPolicy(style), targets, held, now=w.clock(), session=w.session(), run=e.run_id,
                      quotes=quotes)


def _mark(w: World, e: Engine, session: str) -> None:
    book = e.book()
    if any(c.session == session for c in book.closes):
        return
    px = w.prices()
    e.journal.append("mark", {"run_id": e.run_id, "session": session, "pnl_venue": book.day_realized,
                              "pnl_shadow": book.day_realized, "ts": w.clock().isoformat(),
                              "prices": {price_key(s, sym): px[sym] for (s, sym) in book.sleeves}})


def _day_rows(w: World, e: Engine, session: str) -> None:
    """The rows the engine writes for the dashboard: the day's result (an upsert) and the run's status (an update)."""
    book = e.book()
    close = next(c for c in book.closes if c.session == session)
    e.store.upsert("daily_result", {"run_id": e.run_id, "session": session, "pnl_venue": close.pnl_venue,
                                    "pnl_shadow": close.pnl_shadow}, "run_id,session")
    e.store.update("run", {"id": e.run_id}, {"status": "running", "last_session": session})
    for f in (b for _, _, _, b in e.journal.entries(kinds=("fill",))):
        if not any(r.get("activity") == f["activity"] for r in e.store.tables.get("fill", [])):
            e.store.insert("fill", {"run_id": e.run_id, "activity": f["activity"], "symbol": f["symbol"],
                                    "side": f["side"], "qty": f["qty"], "price": f["px"]}, returning=False)


def s_open_run(w: World, e: Engine) -> None:
    if not e.store.tables.get("run"):
        e.store.insert("run", {"amount_usd": START_CASH, "status": "scheduled", "level": "balanced"})
    e.run_id = e.store.tables["run"][0]["id"]
    if e.book() is None:
        e.journal.append("run", {"run_id": e.run_id, "amount": START_CASH})
    if e.om is None:
        e.om = OrderManager(w.venue, e.journal, w.clock, run_id=e.run_id)


def _passes(w: World, e: Engine, style: str, day: str, *times: str) -> None:
    """The engine's later passes inside the window: sync, then the loop again. With nothing lost they send
    nothing; after a kill between an intent and its venue call they are what sends the decision's next attempt."""
    for hhmm in times:
        w.to(day, hhmm)
        e.om.sync()
        _loop(w, e, style)


def s_first_buys(w: World, e: Engine) -> None:
    """15:51, session 1: three near-close buys; the venue fills B only in part."""
    w.to(DAYS[0], "15:51")
    w.targets.update({("s1", "A"): 5, ("s1", "B"): 3, ("s2", "A"): 2})
    if not w.buys_planned:
        w.buys_planned = True
        w.venue.faults[coid(e, "s1", "B", DAYS[0]).rsplit("-", 1)[0]] = ("fills", None, [2.0])
    _loop(w, e, "near_close")
    _passes(w, e, "near_close", DAYS[0], "15:53", "15:55")


def s_close_one(w: World, e: Engine) -> None:
    w.to(DAYS[0], "16:01")
    e.om.sync()
    _mark(w, e, DAYS[0])
    _day_rows(w, e, DAYS[0])
    e.replicator.pump()


def s_limit_buy(w: World, e: Engine) -> None:
    """09:50, session 2: s1 wants 8 A; a marketable limit goes out."""
    w.to(DAYS[1], "09:50")
    w.targets[("s1", "A")] = 8
    w.targets[("s1", "B")] = 2  # what the partial fill left: nothing more to buy
    _loop(w, e, "marketable_limit")


def s_reprice(w: World, e: Engine) -> None:
    """The limit is moved to a fixed price well through the market (a replace: a new order id at the venue)."""
    for r in e.om.working(symbol="A", strategy="s1"):
        if r.type == "limit" and r.limit != 104.0 and r.state not in UNCONFIRMED:
            e.om.replace(r.coid, limit=104.0)
    _passes(w, e, "marketable_limit", DAYS[1], "09:53", "09:55", "10:02")


def s_rest(w: World, e: Engine) -> None:
    """A limit buy far below the market: it rests."""
    c = coid(e, "s2", "B", DAYS[1], "rest")
    if e.om.get(c) is None:
        e.om.submit(OrderRequest("B", "buy", 4, "limit", "day", c, limit_price=80.0), strategy="s2", purpose="rest",
                    session=DAYS[1])
    e.replicator.pump()


def s_cancel(w: World, e: Engine) -> None:
    r = e.om.get(coid(e, "s2", "B", DAYS[1], "rest"))
    if r is not None and r.open and r.order_id is not None:
        e.om.cancel(r.coid)
    w.to(DAYS[1], "10:05")
    e.om.sync()


def s_sales(w: World, e: Engine) -> None:
    """15:52, session 2: two sleeves are sold; the answer to one sale never arrives (the order is in)."""
    w.to(DAYS[1], "15:52")
    w.targets[("s1", "B")] = 0
    w.targets[("s2", "A")] = 0
    if not w.sales_planned:
        w.sales_planned = True
        w.venue.faults[coid(e, "s2", "A", DAYS[1]).rsplit("-", 1)[0]] = ("timeout", None, True)
    _loop(w, e, "near_close")
    e.om.sync()  # the lookup by client order id finds the order that timed out
    _loop(w, e, "near_close")  # idempotent: everything is working, nothing new goes out
    _passes(w, e, "near_close", DAYS[1], "15:54", "15:56")


def s_close_two(w: World, e: Engine) -> None:
    w.to(DAYS[1], "16:02")
    e.om.sync()
    _mark(w, e, DAYS[1])
    _day_rows(w, e, DAYS[1])
    e.replicator.pump()


STEPS = (s_open_run, s_first_buys, s_close_one, s_limit_buy, s_reprice, s_rest, s_cancel, s_sales, s_close_two)


def settle(w: World, e: Engine) -> None:
    """A minute later the engine syncs once more and the replicator drains."""
    w.tick(2)
    e.om.sync()
    e.replicator.pump()


# ---------------------------------------------------------------- running it
@dataclass
class Outcome:
    world: World
    engine: Engine
    restarts: int
    killed_at: str | None
    effects: list[str]


def run(at: int | None = None, phase: str = "before", damage: str | None = None) -> Outcome:
    """The scenario, killed at crash point (`at`, `phase`) if given, restarted from the disk image (damaged as
    `damage` says), and run to its end."""
    w = World(Fuse(at, phase))
    e = Engine(w, w.image_dir())
    restarts = 0
    i = 0
    while i < len(STEPS):
        try:
            STEPS[i](w, e)
            i += 1
        except Kill:
            restarts += 1
            image = e.image(damage)
            e.abandon()
            e = Engine(w, image)
            s_open_run(w, e)  # start-up: the run and its book are there (a damaged image may have lost the last one)
            e.recover()  # then the interrupted step runs again, as the engine's next pass would
    settle(w, e)
    return Outcome(w, e, restarts, w.fuse.fired, w.fuse.log)


def fingerprint(o: Outcome) -> dict:
    """Where the scenario ended, as the venue sees it: the positions. (Not the cash: a decision whose first attempt
    died with the process is sent again a minute or two later, at that minute's price.)"""
    return {s: float(p["qty"]) for s, p in sorted(o.world.sim.pos.items())}


def _strip(row: dict, table: str) -> dict:
    """A row without the columns only one side has (the replica's jseq; ids of tables nothing points to)."""
    return {k: v for k, v in canon(row).items() if k != "jseq" and (k != "id" or table in ID_TABLES)}


def _text(row: dict) -> str:
    return json.dumps(row, sort_keys=True, default=str)


def violations(o: Outcome) -> list[str]:
    """Every invariant the end state breaks (an empty list: none)."""
    w, e, out = o.world, o.engine, []
    sim, om, book = w.sim, e.om, e.book()
    rows = e.journal.entries()
    if [r[0] for r in rows] != list(range(1, len(rows) + 1)):
        out.append("journal sequence numbers have a gap")
    if w.venue.sent_without_intent:
        out.append(f"orders sent before their intent was journalled: {w.venue.sent_without_intent}")
    roots = [x for x in sim.orders.values() if not x.get("replaces")]
    coids = [x["client_order_id"] for x in roots]
    if len(coids) != len(set(coids)):
        out.append(f"two venue orders for one client order id: {coids}")
    intents = {b["coid"] for _, _, k, b in rows if k == "om_intent"}
    if not set(coids) <= intents:
        out.append(f"venue orders without a journalled intent: {sorted(set(coids) - intents)}")
    alive: dict[str, int] = {}
    for x in roots:
        tip = x
        while tip.get("replaced_by"):
            tip = sim.orders[tip["replaced_by"]]
        if tip["status"] in WORKING:
            base = x["client_order_id"].rsplit("-", 1)[0]
            alive[base] = alive.get(base, 0) + 1
    if any(n > 1 for n in alive.values()):
        out.append(f"a decision with two working orders: {alive}")
    booked = [b["activity"] for _, _, k, b in rows if k == "fill"]
    executed = {str(f["id"]) for f in sim.fills}
    if len(booked) != len(set(booked)):
        out.append("an execution was booked twice")
    if set(booked) != executed:
        out.append(f"booked {len(set(booked))} of {len(executed)} executions; missing {sorted(executed - set(booked))}"
                   f", unknown {sorted(set(booked) - executed)}")
    mine = {s: q for s, q in book.qty_by_symbol().items() if abs(q) > 1e-9}
    theirs = {s: float(p["qty"]) for s, p in sim.pos.items()}
    if mine != theirs:
        out.append(f"ledger shares {mine} != venue {theirs}")
    if abs(book.cash - sim.cash) > 0.005:
        out.append(f"ledger cash {book.cash} != venue {sim.cash}")
    rec = compare(book.qty_by_symbol(), sim.positions(), om=om, ledger_cash=book.cash, ledger_cash0=START_CASH,
                  broker_cash=sim.cash, broker_cash0=START_CASH, equity=START_CASH)
    if not rec.ok:
        out.append(f"the engine's reconciliation finds differences: {rec.summary()['diffs']}")
    for r in om.live.values():
        if r.state in UNCONFIRMED:
            out.append(f"{r.coid} is still {r.state}")
            continue
        at_venue = sim.order(r.order_id)
        want = VENUE[at_venue["status"]]
        if r.state is not want and not (want is St.ACCEPTED and r.state is St.PARTIAL):
            out.append(f"{r.coid} is {r.state} here and {at_venue['status']} at the venue")
    rest = om.get(coid(e, "s2", "B", DAYS[1], "rest"))
    # the scenario cancels the resting order at 10:00. Cancelled, or never at the venue at all (its first attempt
    # died with the process: `rejected`, lost), is right; a DAY order whose cancel was never sent expires instead
    if rest is not None and rest.state not in (St.CANCELED, St.REJECTED):
        out.append(f"{rest.coid} was to be cancelled and ended {rest.state}")
    if e.replicator.pending():
        out.append(f"{e.replicator.pending()} rows never reached the replica ({e.replicator.last_error})")
    if e.replicator.conflicts:
        out.append(f"{e.replicator.conflicts} replication conflicts")
    for table, local in e.store.tables.items():
        remote = w.replica.tables.get(table, [])
        a = sorted((_strip(r, table) for r in local), key=_text)
        b = sorted((_strip(r, table) for r in remote), key=_text)
        if a != b:
            out.append(f"replica table {table}: {len(remote)} rows differ from the journal's {len(local)}")
    return out
