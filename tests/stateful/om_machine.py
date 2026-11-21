"""The state machine of M12 ticket 3.1 (#463): Hypothesis drives the real order manager, target loop, journal and
ledger against `SimBroker`, beside a hundred-line reference ledger (reference_ledger.py), and checks the engine
audit's nine invariants after every step (docs/research/engine_audit/report.md, "What to build so the engine keeps
finding its own edge cases").

What is real: `OrderManager`, `reconcile_targets` + `ExecutionPolicy` (the daily path's pre-send code), `Journal` on
a real SQLite file, `Ledger`, `reconcile.compare`, and `SimBroker` with Alpaca's replace behaviour (a new order id).
What is the test's: the clock, the decisions (targets of 0..CAP shares per sleeve), the venue's scripted faults
(reject, timeout with and without the order arriving, partial fill, an order the venue sits on, a fill reported
twice, a network cut) and the crashes.

Crashes. `crash(0, ...)` is a clean kill between two calls. `crash(n, phase)` plants a fuse: the n-th effect from now
(a journal append, or a submit / cancel / replace at the venue) kills the process either just before it happens or
just after it happened and before anything else ran. The kill is a `BaseException`, so no `except Exception` of
the engine can swallow it; everything in memory is thrown away, the journal file is reopened, a new manager folds
it and runs `recover()`. The venue and the clock survive, as the real ones do.

The nine invariants (the report's list; where each is checked):
 1. accounting identity: cash + positions = equity to the cent, engine against reference  (`inv1_accounting`)
 2. every venue execution is booked exactly once                                          (`inv2_exactly_once`)
 3. at most one venue order per journalled intent, across restarts                        (`inv3_one_order_per_intent`)
 4. order states move along the legal graph; filled quantity never falls                  (`inv4_legal_transitions`)
 5. caps hold counting working orders: a sleeve's shares plus its open buys <= CAP        (`inv5_caps_with_working`)
 6. no unintended short, no negative cash                                                 (`inv6_no_short_no_debt`)
 7. after faults stop the ledger equals the venue and no order stays unknown              (rule `settle`)
 8. journal sequence numbers are gapless; every send has a preceding intent               (`inv8_journal`)
 9. no order leaves outside its window or uses data newer than "now"                      (`inv9_time`)

Invariants 5 and 9 are checked at the venue (the truth), against limits the engine's own pre-send code is given:
targets never above CAP, and the execution style's window. The account-level caps of `risk/gate.py` and the send
window of `engine/live_daily.py` sit above the order manager and belong to the whole-engine fault simulation
(ticket 3.2).

The planted bugs of test_stateful_orders_ledger.py (`PLANTED`) are found only through these checks, and each of
the nine catches at least one of them on its own (QA review of PR #494: the others switched off in turn).

Known engine defects the machine steps around are listed in `KNOWN`, each with its issue; each has a strict xfail
test with the minimal case in test_stateful_orders_ledger.py.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule
from reference_ledger import ZERO, ReferenceLedger, dec, same_money

from kaibot.broker.protocol import OrderRequest
from kaibot.broker.sim import NY, WORKING, SimBroker
from kaibot.engine.clock import SimClock
from kaibot.engine.execution import ExecutionPolicy
from kaibot.engine.journal import Journal
from kaibot.engine.ledger import LEDGER_KINDS, Ledger, price_key
from kaibot.engine.orders import TERMINAL, UNCONFIRMED, VENUE, OrderBlocked, OrderManager, St
from kaibot.engine.reconcile import compare
from kaibot.engine.targets import Target, reconcile_targets
from kaibot.replay.history import BarHistory

RUN = 1
SYMBOLS = ("A", "B")
STRATEGIES = ("s1", "s2")
CAP = 6  # shares per sleeve: every target is 0..CAP
DAYS = ("2026-09-15", "2026-09-16")
START_CASH = 1_000_000.0
STYLES = ("near_close", "marketable_limit", "close")
FAULTS = ("none", "none", "reject", "timeout_lost", "timeout_at_venue", "partial", "hold", "duplicate")

# Engine defects this machine found, as {name: issue}. The machine avoids the one step that trips each, so that
# everything else stays checked; the strict xfail tests fail (and tell us to drop the entry) once the fix lands.
KNOWN: dict[str, int] = {
    # a cancel that never reached the venue (network error, or a kill between the journal write and the call) leaves
    # the order `pending_cancel` for good: nothing asks again. The `cancel` rule therefore runs with the network up
    # and the fuse held.
    "cancel_unanswered": 491,
}

# Invariant 4: the legal graph. A state may always be written again (the venue's fields changed); nothing leaves a
# terminal state.
_ANY_VENUE = {St.ACCEPTED, St.PARTIAL, St.FILLED, St.CANCELED, St.EXPIRED, St.REJECTED}
LEGAL: dict[St, set[St]] = {
    St.INTENT: _ANY_VENUE | {St.UNKNOWN},
    St.UNKNOWN: _ANY_VENUE,  # found by its client id, or `rejected` (lost: it never reached the venue)
    St.ACCEPTED: _ANY_VENUE | {St.PENDING_CANCEL, St.PENDING_REPLACE},
    St.PARTIAL: {St.FILLED, St.CANCELED, St.EXPIRED, St.PENDING_CANCEL, St.PENDING_REPLACE},
    St.PENDING_CANCEL: {St.FILLED, St.CANCELED, St.EXPIRED, St.REJECTED},
    St.PENDING_REPLACE: _ANY_VENUE | {St.PENDING_CANCEL},  # cancelled while its replace was unanswered
    **{s: set() for s in TERMINAL},
}


class Kill(BaseException):
    """The process dies here. Not an `Exception`: the engine must not be able to catch it."""


class Fuse:
    """Counts effects (journal appends and venue writes) and kills at the armed one, before or after it."""

    def __init__(self) -> None:
        self.left: int | None = None
        self.phase = "before"
        self.effects = 0  # every effect ever seen (the crash tests number their crash points with it)

    def arm(self, n: int, phase: str) -> None:
        self.left, self.phase = n, phase

    def disarm(self) -> None:
        self.left = None

    def before(self) -> None:
        self.effects += 1
        if self.left is not None:
            self.left -= 1
            if self.left <= 0 and self.phase == "before":
                self.left = None
                raise Kill

    def after(self) -> None:
        if self.left is not None and self.left <= 0:
            self.left = None
            raise Kill


class FusedJournal(Journal):
    def __init__(self, path, now, fuse: Fuse) -> None:
        super().__init__(path, now)
        self.fuse = fuse
        self.db.execute("pragma synchronous=off")  # the test kills the Python objects, not the machine's power

    def append(self, kind: str, body: dict) -> int:
        self.fuse.before()
        seq = super().append(kind, body)
        self.fuse.after()
        return seq


class FusedVenue:
    """`SimBroker` with the fuse on its three writes, and a witness for invariant 8: at each submit, was the
    intent already in the journal?"""

    def __init__(self, sim: SimBroker, fuse: Fuse) -> None:
        self.sim, self.fuse = sim, fuse
        self.journal: Journal | None = None
        self.sent_without_intent: list[str] = []

    def __getattr__(self, name: str):
        return getattr(self.sim, name)

    def submit(self, order: OrderRequest) -> dict:
        intents = {b["coid"] for _, _, _, b in self.journal.entries(kinds=("om_intent",))}
        if order.client_order_id not in intents:
            self.sent_without_intent.append(str(order.client_order_id))
        self.fuse.before()
        out = self.sim.submit(order)
        self.fuse.after()
        return out

    def cancel(self, order_id: str) -> None:
        self.fuse.before()
        self.sim.cancel(order_id)
        self.fuse.after()

    def replace(self, order_id: str, **kw) -> dict:
        self.fuse.before()
        out = self.sim.replace(order_id, **kw)
        self.fuse.after()
        return out


def et(day: str, hhmm: str, sec: int = 0) -> datetime:
    return (pd.Timestamp(f"{day} {hhmm}", tz=NY) + pd.Timedelta(seconds=sec)).tz_convert(UTC).to_pydatetime()


def bars(day: str, shift: int) -> pd.DataFrame:
    """One session of 1-minute bars on five price levels (98..102), so limits sometimes fill and sometimes wait."""
    m = np.arange(390)
    px = 100.0 + ((m * 7 + shift) % 5) - 2.0
    ts = pd.Timestamp(f"{day} 09:30", tz=NY).tz_convert(UTC) + pd.to_timedelta(m, unit="m")
    return pd.DataFrame({"ts": ts, "o": px, "h": px + 0.05, "l": px - 0.05, "c": px, "v": 10_000.0, "vw": px})


def strategy_of(coid: str) -> str:
    """kb<run>-<strategy>-<symbol>-<day>-<purpose>-<n>: read by the test, independently of the manager."""
    return coid.split("-")[1]


def base_of(coid: str) -> str:
    return coid.rsplit("-", 1)[0]


class OrdersAndLedger(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.dir = Path(tempfile.mkdtemp(prefix="kb-stateful-"))
        self.clock = SimClock(et(DAYS[0], "09:40", 7))
        frames = {s: pd.concat([bars(d, i + j) for j, d in enumerate(DAYS)], ignore_index=True)
                  for i, s in enumerate(SYMBOLS)}
        self.history = BarHistory(frames)
        self.sim = SimBroker(self.history, self.clock, cash=START_CASH, replace_new_id=True, near_close=time(15, 50))
        self.fuse = Fuse()
        self.venue = FusedVenue(self.sim, self.fuse)
        self.targets: dict[tuple[str, str], int] = {}
        self.restarts = 0
        self.journal: FusedJournal
        self.om: OrderManager
        self._open()
        self.journal.append("run", {"run_id": RUN, "amount": START_CASH})

    # ---------------------------------------------------------------- the process: start, die, start again
    def _open(self) -> None:
        self.journal = FusedJournal(self.dir / "journal.sqlite", self.clock, self.fuse)
        self.venue.journal = self.journal
        self.om = OrderManager(self.venue, self.journal, self.clock, run_id=RUN)

    def _restart(self) -> None:
        """Everything in memory is gone; the journal file, the venue and the clock are not."""
        self.fuse.disarm()
        self.journal.close()
        self.restarts += 1
        self._open()
        self.om.recover()

    def _run(self, step) -> bool:
        """Run one engine step; False when the process was killed in it (and restarted)."""
        try:
            step()
        except Kill:
            self._restart()
            return False
        return True

    def teardown(self) -> None:
        self.journal.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    # ---------------------------------------------------------------- views
    def _session(self) -> str:
        return str(self.clock().astimezone(NY).date())

    def _et(self) -> time:
        return self.clock().astimezone(NY).time()

    def _prices(self) -> dict[str, float]:
        return {s: self.history.last_close(s, self.clock()) or 100.0 for s in SYMBOLS}

    def _book(self):
        return Ledger.fold(self.journal.entries(kinds=LEDGER_KINDS)).book(RUN)

    def _booked(self) -> list[dict]:
        return [b for _, _, _, b in self.journal.entries(kinds=("fill",))]

    def _executions(self) -> dict[str, dict]:
        """The venue's executions by id, each once, in the venue's order."""
        out: dict[str, dict] = {}
        for f in self.sim.fills:
            out.setdefault(str(f["id"]), f)
        return out

    def _root(self, o: dict) -> dict:
        while o.get("replaces"):
            o = self.sim.orders[o["replaces"]]
        return o

    def _tip(self, o: dict) -> dict:
        while o.get("replaced_by"):
            o = self.sim.orders[o["replaced_by"]]
        return o

    def _roots(self) -> list[dict]:
        return [o for o in self.sim.orders.values() if not o.get("replaces")]

    def _strategy_of_execution(self, f: dict) -> str:
        return strategy_of(self._root(self.sim.orders[f["order_id"]])["client_order_id"])

    def _reference(self, executions) -> ReferenceLedger:
        ref = ReferenceLedger(START_CASH)
        for f in executions:
            ref.execute(str(f["id"]), self._strategy_of_execution(f), f["symbol"], f["side"], f["qty"], f["price"],
                        f.get("fees") or 0)
        return ref

    def _truth(self) -> ReferenceLedger:
        return self._reference(self._executions().values())

    def _agrees(self, book, ref: ReferenceLedger, what: str) -> None:
        assert same_money(book.cash, ref.cash), f"{what}: cash {book.cash} != reference {ref.cash}"
        assert same_money(book.realized_venue, ref.realized), \
            f"{what}: realised {book.realized_venue} != reference {ref.realized}"
        mine = {k: s for k, s in book.sleeves.items() if abs(s.qty) > 1e-9}
        assert set(mine) == set(ref.holdings), f"{what}: sleeves {sorted(mine)} != reference {sorted(ref.holdings)}"
        for k, h in ref.holdings.items():
            assert dec(mine[k].qty) == h.qty, f"{what}: {k} holds {mine[k].qty}, reference {h.qty}"
            assert same_money(mine[k].cost, h.cost), f"{what}: {k} cost {mine[k].cost}, reference {h.cost}"

    @initialize(start=st.sampled_from(["09:40", "09:50", "12:00", "15:44", "15:51"]),
                shares=st.lists(st.integers(0, CAP), min_size=4, max_size=4), warm=st.booleans())
    def begin(self, start: str, shares: list[int], warm: bool) -> None:
        """A time of day and a first decision, so that most sequences trade from their first steps. `warm`: the
        first decision was already traded at the first session's close (no faults) and the sequence starts in the
        second session with those shares held, so sales and resizes are reachable in a few steps."""
        keys = [(s, sym) for s in STRATEGIES for sym in SYMBOLS]
        self.targets = dict(zip(keys, shares, strict=True))
        if warm:
            self.clock.set(et(DAYS[0], "15:51", 7))
            self._send("near_close", True, "none", 1)
            self._tick(10)
            self.om.sync()
            self.clock.set(et(DAYS[1], start, 7))
        else:
            self.clock.set(et(DAYS[0], start, 7))

    # ---------------------------------------------------------------- rules: time
    def _tick(self, minutes: int) -> None:
        for _ in range(minutes):
            self.sim.advance(self.clock().replace(second=0, microsecond=0))
            self.clock.advance(minutes=1)

    @rule(minutes=st.sampled_from([1, 1, 2, 7, 30, 120]), then_sync=st.booleans())
    def tick(self, minutes: int, then_sync: bool) -> None:
        self._tick(minutes)
        if then_sync:
            self._run(self.om.sync)

    @precondition(lambda self: self._et() < time(15, 50))
    @rule(extra=st.integers(0, 9))
    def to_near_close(self, extra: int) -> None:
        """Into the last ten minutes of the session, where the daily rules send."""
        target = et(self._session(), "15:50", 7) + timedelta(minutes=extra)
        self._tick(int((target - self.clock()).total_seconds() // 60))

    @precondition(lambda self: self._session() == DAYS[0] and self._et() >= time(16, 0))
    @rule()
    def to_next_session(self) -> None:
        self.clock.set(et(DAYS[1], "09:31", 7))

    # ---------------------------------------------------------------- rules: decisions and orders
    def _open_styles(self) -> list[str]:
        t = self._et()
        return [s for s, ok in (("near_close", time(15, 50) <= t < time(16, 0)),
                                ("marketable_limit", time(9, 45) <= t < time(16, 0)),
                                ("close", not time(15, 50) <= t < time(19, 0))) if ok]

    @rule(strategy=st.sampled_from(STRATEGIES), symbol=st.sampled_from(SYMBOLS), shares=st.integers(0, CAP),
          style=st.sampled_from(STYLES), fit=st.sampled_from([True, True, True, False]),
          fault=st.sampled_from(FAULTS), part=st.integers(1, CAP - 1))
    def trade(self, strategy: str, symbol: str, shares: int, style: str, fit: bool, fault: str, part: int) -> None:
        """A new decision for one sleeve, then the send."""
        self.targets[(strategy, symbol)] = shares
        self._send(style, fit, fault, part)

    @rule(style=st.sampled_from(STYLES), fit=st.sampled_from([True, True, True, False]),
          fault=st.sampled_from(FAULTS), part=st.integers(1, CAP - 1))
    def send(self, style: str, fit: bool, fault: str, part: int) -> None:
        """The same decisions sent again: idempotent while the first orders work, the retry when they ended."""
        self._send(style, fit, fault, part)

    def _send(self, style: str, fit: bool, fault: str, part: int) -> None:
        """The daily path's pre-send step: targets less (ledger + working orders), through the order manager.
        `fit`: usually a style whose window is open now; otherwise whichever was drawn (the policy must refuse)."""
        fitting = self._open_styles()
        if fit and fitting and style not in fitting:
            style = fitting[STYLES.index(style) % len(fitting)]
        script = self.sim.script
        script._plans.clear()
        if fault == "reject":
            script.reject(status=403)
        elif fault == "timeout_lost":
            script.timeout(accepted=False)
        elif fault == "timeout_at_venue":
            script.timeout(accepted=True)
        elif fault == "partial":
            script.fills(None, [float(part)])
        elif fault == "hold":
            script.hold(None, bars=3)
        elif fault == "duplicate":
            script.duplicate()
        held = {k: s.qty for k, s in self._book().sleeves.items()}
        quotes = {s: (px - 0.01, px + 0.01) for s, px in self._prices().items()}
        targets = [Target(s, sym, n) for (s, sym), n in sorted(self.targets.items())]
        self._run(lambda: reconcile_targets(self.om, ExecutionPolicy(style), targets, held, now=self.clock(),
                                            session=self._session(), run=RUN, quotes=quotes))
        script._plans.clear()

    def _pick(self, i: int, **kw):
        working = sorted(self.om.working(**kw), key=lambda r: r.coid)
        return working[i % len(working)] if working else None

    @precondition(lambda self: bool(self.om.working()))
    @rule(i=st.integers(0, 7))
    def cancel(self, i: int) -> None:
        r = self._pick(i)
        held = None
        if "cancel_unanswered" in KNOWN:
            if self.sim.down:
                return
            held, self.fuse.left = self.fuse.left, None

        def step() -> None:
            with contextlib.suppress(OrderBlocked):  # an order nobody has confirmed cannot be cancelled yet
                self.om.cancel(r.coid)

        self._run(step)
        if held is not None:
            self.fuse.left = held

    @precondition(lambda self: any(r.type == "limit" for r in self.om.working()))
    @rule(i=st.integers(0, 7), cents=st.sampled_from([-150, -5, 5, 150]))
    def replace(self, i: int, cents: int) -> None:
        limits = sorted((r for r in self.om.working() if r.type == "limit"), key=lambda r: r.coid)
        r = limits[i % len(limits)]

        def step() -> None:
            with contextlib.suppress(OrderBlocked):
                self.om.replace(r.coid, limit=round(float(r.limit) + cents / 100, 2))

        self._run(step)

    @rule()
    def sync(self) -> None:
        """Book the venue's fills and poll the open orders; reading the same activities again books nothing."""
        if self._run(self.om.sync) and not self.sim.down:
            self._run(self.om.read_fills)  # invariant 2 then finds anything this booked a second time

    @rule()
    def mark(self) -> None:
        """The session's close as the engine journals it; the two equity figures of the close must agree."""
        book = self._book()
        px = self._prices()
        body = {"run_id": RUN, "session": self._session(), "pnl_venue": book.day_realized,
                "pnl_shadow": book.day_realized, "ts": self.clock().isoformat(),
                "prices": {price_key(s, sym): px[sym] for (s, sym) in book.sleeves}}
        self._run(lambda: self.journal.append("mark", body))
        closes = self._book().closes
        if len(closes) == len(book.closes):
            return  # killed before the write
        close = closes[-1]
        assert same_money(close.close_equity_venue, close.cash + close.invested)
        assert same_money(close.close_equity, close.close_equity_venue), \
            f"reported equity {close.close_equity} != cash + positions {close.close_equity_venue}"

    # ---------------------------------------------------------------- rules: faults
    @rule(down=st.booleans())
    def network(self, down: bool) -> None:
        self.sim.down = down

    @rule(n=st.integers(0, 6), phase=st.sampled_from(["before", "after"]))
    def crash(self, n: int, phase: str) -> None:
        """n = 0: killed now, between two calls. Otherwise the fuse: killed at the n-th effect from now."""
        if n:
            self.fuse.arm(n, phase)
            return
        self.sim.down = False  # `recover()` needs the venue; a start-up without it is the lease's subject, not ours
        self._restart()

    @rule()
    def settle(self) -> None:
        """Invariant 7: the faults stop, a few minutes pass, and the engine's book is the venue's."""
        self.sim.down = False
        self.fuse.disarm()
        self.sim.script._plans.clear()
        for _ in range(4):
            self._tick(1)
            self.om.sync()
        om, book = self.om, self._book()
        stuck = [(r.coid, str(r.state)) for r in om.live.values() if r.state in UNCONFIRMED]
        assert not stuck, f"orders still unknown after the faults stopped: {stuck}"
        assert not om.foreign, f"fills never attributed: {om.foreign}"
        assert not om.settling, f"fills the venue reported but never booked: {sorted(om.settling)}"
        for r in om.live.values():
            at_venue = self.sim.order(r.order_id)
            want = VENUE[at_venue["status"]]
            if want is St.ACCEPTED and float(at_venue.get("filled_qty") or 0) > 0:
                want = St.PARTIAL
            if r.state is St.PENDING_CANCEL and "cancel_unanswered" in KNOWN:
                continue  # the target loop's own cancels can still trip it
            assert r.state is want, f"{r.coid} is {r.state} here and {at_venue['status']} at the venue"
        positions = self.sim.positions()
        rec = compare(book.qty_by_symbol(), positions, om=om, ledger_cash=book.cash, ledger_cash0=START_CASH,
                      broker_cash=self.sim.cash, broker_cash0=START_CASH, equity=START_CASH)
        assert rec.ok, f"the engine's own reconciliation finds differences: {rec.summary()}"
        assert same_money(book.cash, self.sim.cash), f"cash {book.cash} != venue {self.sim.cash}"
        mine = {s: q for s, q in book.qty_by_symbol().items() if abs(q) > 1e-9}
        assert mine == {s: float(p["qty"]) for s, p in positions.items()}, f"shares {mine} != venue {positions}"
        self._agrees(book, self._truth(), "after settling")

    # ---------------------------------------------------------------- the invariants, after every step
    @invariant()
    def inv1_accounting(self) -> None:
        """The engine's ledger is the reference fed the executions the engine has booked, to the cent; and
        cash + positions = start + realised + unrealised."""
        book, px = self._book(), self._prices()
        by_id = self._executions()
        ref = self._reference(by_id[b["activity"]] for b in self._booked() if b["activity"] in by_id)
        self._agrees(book, ref, "booked so far")
        equity = book.cash + sum(s.qty * px[sym] for (_, sym), s in book.sleeves.items())
        assert same_money(equity, ref.equity(px)), f"equity {equity} != reference {ref.equity(px)}"
        unrealized = sum(s.qty * px[sym] - s.cost for (_, sym), s in book.sleeves.items())
        assert same_money(equity, START_CASH + book.realized_venue + unrealized), "the accounting identity is broken"
        assert abs(ref.identity_gap(px)) < dec("1e-9")

    @invariant()
    def inv2_exactly_once(self) -> None:
        booked, by_id = self._booked(), self._executions()
        ids = [b["activity"] for b in booked]
        assert len(ids) == len(set(ids)), f"an execution was booked twice: {sorted(i for i in ids if ids.count(i) > 1)}"
        for b in booked:
            f = by_id.get(b["activity"])
            assert f is not None, f"a fill was booked that the venue never executed: {b}"
            assert (b["symbol"], b["side"], float(b["qty"]), float(b["px"])) == \
                (f["symbol"], f["side"], float(f["qty"]), float(f["price"])), f"booked {b}, executed {f}"
        for r in self.om.orders.values():
            assert r.booked <= r.qty + 1e-9, f"{r.coid}: booked {r.booked} of an order for {r.qty}"

    @invariant()
    def inv3_one_order_per_intent(self) -> None:
        intents = [b["coid"] for _, _, _, b in self.journal.entries(kinds=("om_intent",))]
        assert len(intents) == len(set(intents)), "one client order id was journalled as two intents"
        roots = self._roots()
        coids = [o["client_order_id"] for o in roots]
        assert len(coids) == len(set(coids)), f"two venue orders for one intent: {coids}"
        assert set(coids) <= set(intents), f"venue orders without an intent: {set(coids) - set(intents)}"
        alive: dict[str, int] = {}
        for o in roots:  # one decision (the id without its attempt number) never has two orders working at once
            if self._tip(o)["status"] in WORKING:
                alive[base_of(o["client_order_id"])] = alive.get(base_of(o["client_order_id"]), 0) + 1
        assert all(n == 1 for n in alive.values()), f"a decision with two working orders: {alive}"

    @invariant()
    def inv4_legal_transitions(self) -> None:
        state: dict[str, St] = {}
        cum: dict[str, float] = {}
        for _, _, kind, b in self.journal.entries(kinds=("om_intent", "om_state")):
            c = b["coid"]
            if kind == "om_intent":
                state[c], cum[c] = St.INTENT, 0.0
                continue
            new = St(b["state"])
            assert new is state[c] or new in LEGAL[state[c]], f"{c}: illegal transition {state[c]} -> {new}"
            if b.get("cum") is not None:
                assert float(b["cum"]) >= cum[c] - 1e-9, f"{c}: filled quantity fell from {cum[c]} to {b['cum']}"
                cum[c] = float(b["cum"])
            state[c] = new
        for c, r in self.om.orders.items():
            assert r.state is state[c], f"{c}: the manager says {r.state}, its journal {state[c]}"
            assert r.cum <= r.qty + 1e-9

    @invariant()
    def inv5_caps_with_working(self) -> None:
        """At the venue, a sleeve's shares plus what its open buy orders can still add never exceed the cap its
        targets were given under."""
        truth = self._truth()
        exposure = {k: h.qty for k, h in truth.holdings.items()}
        for o in self._roots():
            tip = self._tip(o)
            if o["side"] == "buy" and tip["status"] in WORKING:
                k = (strategy_of(o["client_order_id"]), o["symbol"])
                exposure[k] = exposure.get(k, ZERO) + dec(tip["qty"]) - dec(tip.get("filled_qty") or 0)
        over = {k: v for k, v in exposure.items() if v > CAP}
        assert not over, f"sleeves above their cap of {CAP} shares counting working orders: {over}"

    @invariant()
    def inv6_no_short_no_debt(self) -> None:
        truth, book = self._truth(), self._book()
        assert not truth.oversold, f"a sleeve sold shares it did not hold: {truth.oversold}"
        assert all(h.qty >= 0 for h in truth.holdings.values())
        assert all(float(p["qty"]) > 0 for p in self.sim.pos.values()), f"short at the venue: {self.sim.pos}"
        assert all(s.qty >= -1e-9 for s in book.sleeves.values()), "a short sleeve in the ledger"
        assert self.sim.cash >= 0 and book.cash >= 0, f"negative cash: venue {self.sim.cash}, ledger {book.cash}"

    @invariant()
    def inv8_journal(self) -> None:
        rows = self.journal.entries()
        assert [r[0] for r in rows] == list(range(1, len(rows) + 1)), "journal sequence numbers have a gap"
        assert not self.venue.sent_without_intent, \
            f"orders sent before their intent was journalled: {self.venue.sent_without_intent}"
        known: set[str] = set()
        for _, _, kind, b in rows:
            if kind == "om_intent":
                known.add(b["coid"])
            elif kind in ("om_state", "om_terms", "fill"):
                assert b["coid"] in known, f"a {kind} entry for {b['coid']} before its intent"

    @invariant()
    def inv9_time(self) -> None:
        now = self.clock()
        last = None
        for _, ts, kind, b in self.journal.entries():
            t = datetime.fromisoformat(ts)
            assert t <= now and (last is None or t >= last), "a journal entry from the future, or out of order"
            last = t
            if kind == "fill" and b.get("ts"):
                assert datetime.fromisoformat(b["ts"]) <= t, f"a fill booked before it happened: {b}"
        for o in self._roots():
            at = datetime.fromisoformat(o["submitted_at"])
            assert at <= now
            t = at.astimezone(NY).time()
            if o["time_in_force"] == "cls":
                assert not time(15, 50) <= t < time(19, 0), f"a closing-auction order sent at {t}"
            elif o["type"] == "market":
                assert time(15, 50) <= t < time(16, 0), f"a near-close market order sent at {t}"
            else:
                assert time(9, 45) <= t < time(16, 0), f"a marketable limit sent at {t}"

