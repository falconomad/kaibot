"""Audit 2, 5.19 (#699): crash points inside the REAL engine's start-up and loop, not crash_world.py's stand-in.

crash_world.py (M12 3.3, #465) kills a hand-written script that drives the order manager, the journal store and the
replicator the way the engine would. This harness kills the engine itself: `core.Engine(intraday=False)` as the live
process builds it, started by `Engine.startup` (whose recovery routine is `Engine.recover`) and run pass by pass by
`Engine.tick`, which is `engine/book_loop.tick`. The world is the deterministic simulation's (tests/dst/world.py):
the simulated venue behind `FaultyBroker`, the simulated Supabase behind `FaultyStore`, the journal on disk, the
`Checker` with its invariants after every pass. No fault is injected and nothing in the engine is patched.

The scenario (`SCENARIO`, `CrashWorld.run`): a paper run is started on the evening of D0; the evening's decision
buys B and C in D1's send window; a planned restart of the process right after the window's first fills
(`RESTART`), so that `Engine.recover` books them and finds the orders it left; D1's evening decides to sell B and
buy D, which D2's window does; then the evening of D2 and the quiescence checks.

A crash point is a numbered effect and a phase. The effects are every journal append, every write to the venue
(submit, cancel, replace, close) and every write to the replica (insert, upsert, update; the heartbeat row of every
pass is left out: it is rewritten whole by the next pass and would only multiply the count), in the order the
uninterrupted run makes them. `before` kills the process just before the effect (it did not happen); `after` just
after it (it happened, and nothing else did). The kill is a `BaseException`. The process is started again at the
next pass of the loop, on the journal file as it is, by the engine's own start-up, and the run goes on to its end.

Each effect is labelled with where in the engine it was made (`REGIONS`): inside `Engine.recover`, inside
`book_loop.tick` (the minute's pass, the evening's `_tick_closed` included), elsewhere in `core.Engine` (the rest of
`startup`, the commands, the tick's error handling), or in the main loop around it (the replicator's pump).

What must hold after every kill: the `Checker`'s invariants after every pass and at quiescence (ledger = fold of
the journalled fills = the venue, every execution booked once, one working order per decision, no buy beyond the
target, the replica has every row, the book holds what the latest decision wanted), and the venue ends with the
same positions as the uninterrupted run (`fingerprint`).
"""

from __future__ import annotations

import contextlib
import os
import random
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE.parent / "dst", HERE.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from world import D0, D1, D2, D3, Kill, Result, Scenario, World, et  # noqa: E402

from kaibot.broker.faulty import NO_FAULTS  # noqa: E402

# where an effect was made: the innermost of these on the stack names it (recover before book_loop before core)
REGIONS = ("recover", "book_loop", "core.Engine", "main loop")
SEED = 5190
PLAN = {D1: ("B", "C"), D2: ("C", "D"), D3: None}
READY = {D0: et(D0, "16:10"), D1: et(D1, "16:10"), D2: et(D2, "16:10"), D3: et(D3, "16:10")}
RESTART = et(D1, "15:51", 3)  # stopped and started after the window's first fills, before the pass that books them
SCENARIO = Scenario(SEED, PLAN, READY, profile=NO_FAULTS, book_loop=True, sparse=True)
SKIPPED = ("store.heartbeat",)  # not a crash point (see the module's docstring)

def _files() -> tuple[str, str]:
    from kaibot.engine import book_loop, core

    return str(Path(core.__file__).resolve()), str(Path(book_loop.__file__).resolve())


def region() -> str:
    """Where in the engine the effect being made now comes from (the call stack)."""
    core, loop = _files()
    seen = set()
    f = sys._getframe(2)
    while f is not None:
        path = f.f_code.co_filename
        if path == core:
            seen.add("recover" if f.f_code.co_name == "recover" else "core.Engine")
        elif path == loop and f.f_code.co_name == "tick":
            seen.add("book_loop")
        f = f.f_back
    return next((r for r in REGIONS if r in seen), "main loop")


class Points:
    """The world's fuse (it stands where tests/dst/world.Fuse stands): numbers every effect, labels it with its
    region, and kills the process at the armed crash point (`at`, `phase`), once."""

    def __init__(self, at: int | None = None, phase: str = "before") -> None:
        self.at, self.phase = at, phase
        self.n = 0
        self.log: list[tuple[str, str]] = []  # (effect, region), in order
        self.fired: list[str] = []
        self._pending = False
        self.left = 0  # (tests/dst/world.World reads it: no outage is armed here)
        self.orders_only = self.after = False

    def arm(self, o) -> None:  # the scenario has no outage
        raise AssertionError("no outage in the crash scenario")

    def disarm(self) -> None:  # the world disarms at every death; the crash point fires once anyway
        self._pending = False

    def __call__(self, phase: str, what: str) -> None:
        if what.startswith(SKIPPED):
            return
        if phase == "before":
            self.n += 1
            self.log.append((what, region()))
            if self.at == self.n:
                if self.phase == "before":
                    self._fire(f"before {what}")
                self._pending = True
            return
        if self._pending:
            self._pending = False
            self._fire(f"after {what}")

    def _fire(self, what: str) -> None:
        self.at = None
        self.fired.append(what)
        raise Kill(what)


class CrashWorld(World):
    """The DST world on the crash scenario: two sessions, a planned restart, the crash point's kill."""

    def __init__(self, at: int | None = None, phase: str = "before", root: Path | None = None) -> None:
        super().__init__(SCENARIO, root)
        self.fuse = Points(at, phase)
        self.remote.on_write = self.fuse  # (built with the world's own fuse)
        self.restarted = False

    def at(self, t, run: bool = True) -> None:
        if not self.restarted and t >= RESTART and self.eng is not None:
            self.restarted = True
            self.die("planned restart (the process is stopped between two passes)")
            self.down_until = None
        super().at(t, run)

    def run(self) -> Result:
        os.environ["KAIBOT_MULTIDAY"] = "on"
        rng = random.Random(f"kaibot-crash-ticks-{SEED}")
        unseam = self._state_seam()
        try:
            self.evening(D0)
            for day in (D1, D2):
                self.session(day, rng)
                self.evening(day)
            for m in range(1, 4):  # quiescence: the loop runs on, nothing new happens
                self.at(et(D2, "21:10", 7) + timedelta(minutes=m))
            self.check.at_quiescence()
        finally:
            unseam()
            if self.eng is not None:
                with contextlib.suppress(Exception):
                    self.eng.journal.close()
            if self.own_root:
                shutil.rmtree(self.root, ignore_errors=True)
        self.stats["kills_fired"] = list(self.fuse.fired)
        return Result(self.s, self.violations, self.trace, self.stats)


@dataclass
class Outcome:
    at: int | None
    phase: str
    violations: list[str]
    fired: list[str]
    effects: list[tuple[str, str]]
    end: dict
    boots: int
    trace: list[str] = field(default_factory=list)


def run(at: int | None = None, phase: str = "before") -> Outcome:
    was = os.environ.get("KAIBOT_MULTIDAY")
    try:
        w = CrashWorld(at, phase)
        res = w.run()
    finally:
        if was is None:
            os.environ.pop("KAIBOT_MULTIDAY", None)
        else:
            os.environ["KAIBOT_MULTIDAY"] = was
    end = {s: float(p["qty"]) for s, p in sorted(w.sim.pos.items()) if abs(float(p["qty"])) > 1e-9}
    return Outcome(at, phase, list(res.violations), list(w.fuse.fired), list(w.fuse.log), end, w.stats["boots"],
                   res.trace)


def kinds(effects: list[tuple[str, str]]) -> Counter:
    """effect kind ("journal.fill", "venue.submit", "store.insert") x region -> count."""
    return Counter((" ".join(e.split()[:1]), r) for e, r in effects)


def engine_points(effects: list[tuple[str, str]]) -> list[int]:
    """The crash points inside the engine (effect numbers, 1-based): every effect not made by the main loop around
    it (the replicator's writes to the replica are crash_world.py's to sweep)."""
    return [k for k, (_, r) in enumerate(effects, start=1) if r != "main loop"]


# Engine defects this harness found that are still open: issue -> the crash points they break and why. Each has a
# strict xfail in test_engine_crash_points.py; remove both when the issue is fixed.
KNOWN: dict[int, str] = {
    # #775 (a kill between an order's journalled intent and its venue call in the last window) is fixed: the restarted
    # process sends the order again in the same window under its own client order id (OrderManager.resend; the venue
    # refuses a duplicate), test_a_kill_between_a_sales_intent_and_its_venue_call_still_sells_in_the_window
}

def known(effects: list[tuple[str, str]], k: int, phase: str) -> int | None:
    """The open issue a failing crash point is put down to, or None: #775 for a kill between an order's intent and
    its venue call (after the `om_intent`, before the `venue.submit` that follows it) in the last window (D2's)."""
    what = effects[k - 1][0]
    call = effects[k][0] if phase == "after" and what == "journal.om_intent" and k < len(effects) else \
        what if phase == "before" else ""
    if call.startswith("venue.submit ") and f"-{D2.replace('-', '')}-" in call:
        return next(iter(KNOWN), None)
    return None


def main(argv: list[str] | None = None) -> int:
    """Run one crash point again and print what broke and the end of the trace:
    `PYTHONHASHSEED=0 PYTHONPATH=src python tests/crash/engine_crash.py <effect number> before|after`."""
    import logging

    a = sys.argv[1:] if argv is None else argv
    logging.disable(logging.CRITICAL)
    base = run()
    k, phase = int(a[0]), a[1]
    o = run(k, phase)
    print(f"crash point {k} {phase} {base.effects[k - 1]} (seed {SEED}): killed at {o.fired}")
    print("violations:", *(o.violations or ["none"]), sep="\n  ")
    print(f"end {o.end}; the uninterrupted run ends with {base.end}")
    print("trace:", *o.trace[-int(a[2]) if len(a) > 2 else -80:], sep="\n  ")
    return 1 if o.violations or sorted(o.end) != sorted(base.end) else 0


if __name__ == "__main__":
    raise SystemExit(main())
