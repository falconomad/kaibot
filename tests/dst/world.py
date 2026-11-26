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

in a week the seed draws (`Week`, audit 2 5.16): the four summer sessions of 31 Jul-6 Aug 2026 (half the seeds), or
Thanksgiving week 2026 (a holiday on Thu 26 Nov, a 13:00 close on Fri 27 Nov; also with the broker's calendar down
that Friday), or the March and October weeks in which New York changes its clocks while Berlin, where the Mac is, has
not yet or already has. The new weeks run the live process's engine (`Engine(intraday=False)`, engine/book_loop.py).

And on the way, drawn from the seed: which evenings decide and what they want; when each evening's daily view
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
    9 no order outside its window (the session's real one: 12:50-12:59 on a 13:00 close), and no daily view from
      the future
   11 a pass without an error inside a fill session's window, with its decision journalled, has journalled the send
      (audit 2 5.16: an engine that takes an early close for a regular one waits for 15:50 and misses it; #733)

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
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from fakes import FakeStore

from kaibot.broker.faulty import (
    AUDIT2_FAULTS,
    CA_FAULTS,
    DEFAULT_FAULTS,
    FaultProfile,
    FaultyBroker,
    FaultyData,
    FaultyStore,
)
from kaibot.broker.protocol import BrokerError
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
from kaibot.engine.lease import LeaseLost
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
REGULAR_CLOSE = "16:00"
FOCUS = {"lease_renew_fail": 6.0, "sale_refused": 2.5, "endpoint_down": 10.0}  # see Scenario.from_seed
BAND_SHARE = 0.35  # audit 2, 5.20: the share of the new weeks' seeds whose book stop is in band mode
# the band table of a band-mode run (trend_momentum at balanced): a 95% line of 0.01%, so the alert (a record, nothing
# sold) is met about once a session, and a 99% line far beyond the simulation's gentle walks, so the band is read and
# judged at every check and never ends the book (the book's end is its own tests' business: test_m11_book_band)
BAND_TABLE = {"rule": "trend_momentum", "level": "balanced", "horizons": [5, 20, 252], "q95": [0.0001, 0.0001, 0.0001],
              "q99": [0.08, 0.12, 0.2], "source": {"window": ["2017-05-01", "2024-12-31"], "dst": True}}
MAC = ZoneInfo("Europe/Berlin")  # the owner's Mac: launchd runs the nightly bar refresh on its wall clock


# ---------------------------------------------------------------- the week a seed runs in (audit 2, 5.16)
@dataclass(frozen=True)
class Week:
    """The calendar of a run: the evening D0, the sessions D1-D3 and the next session D4 (the venue's calendar goes
    on, so D3's evening has a session to decide for). `closes`: early closes, ((day, "HH:MM" ET), ...); `holidays`:
    weekdays inside the run on which the market is closed (the world runs the engine through them, as the Mac
    would); `calendar_down`: spells in which the broker's calendar cannot be read, ((day, "HH:MM", "HH:MM" ET), ...):
    from just after the first read at or after the first time (that one still answers) to the second, every read
    raises, whatever day it asks for. With "09:31" the session's start at the open has read the day's row and the
    calendar goes down in the same second: the daily path's read of the day's close fails, and the session's close
    comes from the calendar-unreadable fallback (#733: the local early-close table)."""

    name: str
    days: tuple[str, str, str, str, str]
    closes: tuple = ()
    holidays: tuple = ()
    calendar_down: tuple = ()

    @property
    def evenings(self) -> tuple[str, ...]:
        return self.days[:4]

    @property
    def sessions(self) -> tuple[str, ...]:
        return self.days[1:4]

    def close(self, day: str) -> str:
        """The session's close, "HH:MM" ET."""
        return dict(self.closes).get(str(day), REGULAR_CLOSE)

    def minutes(self, day: str) -> int:
        """The session's length in minute bars (390, or 210 on a 13:00 close)."""
        hh, mm = (int(x) for x in self.close(day).split(":"))
        return hh * 60 + mm - 570

    def kinds(self) -> set[str]:
        """The session types this week has (what the generator must reach)."""
        k = set()
        if self.closes:
            k.add("early_close")
        if self.holidays:
            k.add("holiday")
        if self.calendar_down:
            k.add("calendar_unreadable")
        utc = {pd.Timestamp(f"{d} 12:00", tz=NY).utcoffset() for d in self.days}
        if len(utc) > 1:
            k.add("dst_change")  # New York's clocks change inside the run
        gap = {pd.Timestamp(f"{d} 12:00", tz=NY).utcoffset() - pd.Timestamp(f"{d} 12:00", tz=MAC).utcoffset()
               for d in self.days}
        if gap != {timedelta(hours=-6)}:
            k.add("dst_gap")  # New York is five hours behind Berlin, not six: the Mac's schedule moves
        return k or {"regular"}


SUMMER = Week("summer", (D0, D1, D2, D3, D4))  # four summer sessions, the world as it was until 5.16
# Thanksgiving week 2026: Wed 25 Nov a regular session, Thu 26 Nov closed, Fri 27 Nov closes at 13:00 ET (send window
# 12:50-12:57), Mon 30 Nov regular; the run starts on the evening of Tue 24 Nov
THANKSGIVING = Week("thanksgiving", ("2026-11-24", "2026-11-25", "2026-11-27", "2026-11-30", "2026-12-01"),
                    closes=(("2026-11-27", "13:00"),), holidays=("2026-11-26",))
# the same week with the broker's calendar unreadable for the early close: the window must come from the engine's
# local table (live_daily.LOCAL_EARLY_CLOSES, #733), not from the regular 16:00 close
THANKSGIVING_NO_CALENDAR = Week("thanksgiving_no_calendar", THANKSGIVING.days, closes=THANKSGIVING.closes,
                                holidays=THANKSGIVING.holidays, calendar_down=(("2026-11-27", "09:31", "16:30"),))
# the March gap: New York moves to summer time on Sun 8 Mar 2026, Berlin only on Sun 29 Mar. The run crosses New
# York's change between D0 (Fri 6 Mar, EST, UTC-5) and D1 (Mon 9 Mar, EDT, UTC-4), inside the gap weeks
MARCH_GAP = Week("march_gap", ("2026-03-06", "2026-03-09", "2026-03-10", "2026-03-11", "2026-03-12"))
# the October gap: Berlin moves back on Sun 25 Oct 2026, New York only on Sun 1 Nov. The run starts in the gap
# (Thu 29 Oct, Fri 30 Oct: New York five hours behind Berlin) and crosses New York's change before D2 (Mon 2 Nov, EST)
OCTOBER_GAP = Week("october_gap", ("2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03", "2026-11-04"))
WEEKS = (SUMMER, THANKSGIVING, THANKSGIVING_NO_CALENDAR, MARCH_GAP, OCTOBER_GAP)
WEEK_WEIGHTS = (4, 1, 1, 1, 1)  # half the seeds stay in the summer week, the other half are spread over the new ones


def week_of(seed: int) -> Week:
    """The week a seed runs in. Its own draw (not the scenario's stream), so a seed that stays in the summer week
    draws exactly the scenario it drew before 5.16."""
    return random.Random(f"kaibot-dst-week-{seed}").choices(WEEKS, weights=WEEK_WEIGHTS)[0]
STOCKS = ("B", "C", "D")
MD = "trend_momentum"
AMOUNT = 50_000.0
VENUE_CASH = 100_000.0
HISTORY_DAYS = 6  # sessions of minute bars before D0 (the market's history load; few, see daily_from)
SEND, CLOSE = 380, 390  # the minute bars at 15:50 and (one past the last) at 16:00
MARKET_PURPOSES = ("buy", "sell", "resize", "carry", "target")  # the daily path's near-close orders
# invariant 12: venue faults of a minute that leave it a chance to sell (what the invariant is about, and the marks)
SALE_NEUTRAL = ("sale_refused", "submit_refused", "calendar_down", "mark_bad")
log = logging.getLogger("kaibot.dst")
SAID = {  # the journal entries the trace shows, with the fields that tell the story
    "run": ("run_id", "amount"), "om_intent": ("coid", "side", "qty", "type"), "om_state": ("coid", "state", "reason"),
    "fill": ("coid", "activity", "side", "qty", "px"), "md_decision": ("session", "fill_session", "late", "note"),
    "md_send": ("session", "late_of", "targets"), "md_sent": ("session", "sent", "skipped"),
    "md_decision_lost": ("session", "fill_session", "why"),
    "md_cancel": ("day", "sides", "reason"), "md_withheld": ("session", "symbol", "why"),
    "md_book_end": ("drawdown_pct",), "reconcile": ("when", "ok"),
}


SPINOFF_ISSUE = 780  # audit 2 5.20's finding (see KNOWN)
# Engine defects the simulation found that are still open: issue -> what the generator leaves out so that every
# seed does not trip over the same one. Each has a strict xfail (test_dst.py, or its kind's file); remove both then.
KNOWN: dict[int, str] = {
    # Audit 2, 5.13 fixed #529's first half: the decision in force now sells a buy booked after it
    # (LiveDaily._booked_late; test_a_buy_booked_a_day_late_is_sold_by_the_decision_taken_meanwhile passes). The
    # second half is still open, so the generator still leaves the fault out: with it drawn (1500 in DROP_MINUTES),
    # 205 of 206 seeds pass and seed 172 fails on exactly this
    529: "no fill activity is delayed past the next session's window (faulty.DROP_MINUTES ends at 300 minutes): "
         "a watchdog flatten of the shares while their buy is still unbooked leaves a difference only the owner "
         "can accept (the venue's sale is a foreign fill, then the late booking puts the shares in the ledger)",
    SPINOFF_ISSUE: "invariants 11 and 12 are not judged in the session of a spin-off, and the daily store gets the "
                   "new symbol's bars from that evening on: the adopted shares are outside the daily view the send "
                   "sizes with (`daily.builder_sizing` raises KeyError), so the whole send of that session fails "
                   "(the new shares' sale, every other sale, every buy) and goes out late at the next session; with a "
                   "store that never has the new symbol it fails at every session",
}


class Kill(BaseException):
    """The process dies here. Not an `Exception`: the engine must not be able to catch it."""


def et(day: str, hhmm: str, sec: int = 0) -> datetime:
    return (pd.Timestamp(f"{day} {hhmm}", tz=NY) + pd.Timedelta(seconds=sec)).tz_convert(UTC).to_pydatetime()


def session_open(day: str) -> datetime:
    return et(day, "09:30")


def mac_time(day: str, hhmm: str) -> datetime:
    """A wall time on the owner's Mac (Berlin), in UTC."""
    return pd.Timestamp(f"{day} {hhmm}", tz=MAC).tz_convert(UTC).to_pydatetime()


# ---------------------------------------------------------------- the scenario of a seed
@dataclass(frozen=True)
class Outage:
    """The engine goes away. `kill`: at the `effects`-th effect (a journal append, a venue write, a replica write;
    with `orders_only`, only those of the order path) counted from `arm`, before or after that effect; it is back
    `down` minutes later. Not `kill` (a pause): the process sleeps from `arm` for `down` minutes and wakes; its
    monotonic clock runs on (a process that is only stopped).

    `mac` (audit 2, 5.17): the whole Mac sleeps from `arm` to `until` (or `down` minutes): the engine, the watchdog
    (it runs on the same Mac) and the monotonic clock stop, while the wall clock, the venue, Supabase and the daily
    store go on. The pass at `arm` is the last one (its heartbeat is the last the watchdog and the dead-man see). At
    the wake the watchdog runs first, then the engine wakes where it was (one loop pass hours after the last) or,
    with `restart`, is started again (a new process)."""

    arm: datetime
    down: int
    kill: bool = True
    effects: int = 1
    orders_only: bool = False
    after: bool = False
    mac: bool = False
    until: datetime | None = None
    restart: bool = False

    @property
    def wake(self) -> datetime:
        return self.until if self.until is not None else self.arm + timedelta(minutes=self.down)


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
    heal: str | None = None  # the session before whose open the faults stop (None: the week's D3)
    week: Week = SUMMER  # audit 2, 5.16: the calendar the run is in
    book_loop: bool = False  # the live process's engine, `Engine(intraday=False)` (engine/book_loop.py)
    # audit 2, 5.17: the live process's loop around the engine (engine/__main__.py): the cross-machine lease and its
    # order fence (FencedBroker), the heartbeat that renews it, `before_tick`, and the gap watch (engine/gap.py)
    lease: bool = False
    # audit 2, 5.20: the multi-day book's stop, config/book_stop.yaml's `mode` ("fixed", the repository's; "band": the
    # world's own band table for the book, `World.band_config`)
    book_stop: str = "fixed"

    @property
    def heal_day(self) -> str:
        return self.heal or self.week.days[3]

    @classmethod
    def from_seed(cls, seed: int, profile: FaultProfile = DEFAULT_FAULTS, week: Week | None = None,
                  book_loop: bool | None = None) -> Scenario:
        """The scenario of a seed. `week`: the calendar (None: the seed's own draw, `week_of`); `book_loop`: the
        engine (None: the live process's book loop in every week but the summer one, which keeps the engine it was
        drawn with before 5.16)."""
        week = week_of(seed) if week is None else week
        book_loop = week is not SUMMER if book_loop is None else book_loop
        lease = book_loop and week is not SUMMER  # the new weeks run the live process's loop, lease and all
        D0, D1, D2, D3 = week.evenings  # noqa: N806 (the week's days, named as the module's summer ones)
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
        rw = random.Random(f"kaibot-dst-mac-{seed}")  # draws only the new weeks make (the summer stream is unchanged)
        for ev, nxt in ((D0, D1), (D1, D2), (D2, D3)):
            if r.random() < 0.12:  # the daily store is refreshed only the next morning: a late decision
                ready[ev] = et(nxt, "09:30") + timedelta(minutes=r.choice((5, 40, 200)))
                if week is not SUMMER and rw.random() < 0.5:
                    # by the Mac's own night job (com.kaibot.daily, 03:15 and 07:45 on the Mac's clock,
                    # scripts/install_service.sh): Berlin time, so in a gap week an hour later in New York
                    ready[ev] = mac_time(nxt, rw.choice(("03:15", "07:45"))) + timedelta(minutes=rw.choice((2, 20)))
            else:
                ready[ev] = et(ev, week.close(ev)) + timedelta(minutes=r.choice((3, 12, 25, 70, 200)))
        ready[D3] = et(D3, week.close(D3)) + timedelta(minutes=10)
        outages = []
        for _ in range(r.choice((0, 1, 1, 2, 2, 3, 4))):
            day = r.choice((D1, D1, D2, D2, D0))
            where = r.random()
            send, close = week.minutes(day) - 10, week.minutes(day)
            if day == D0 or where < 0.2:  # an evening: the decision's journal steps
                arm = et(day, week.close(day)) + timedelta(minutes=r.randrange(0, 300))
            elif where < 0.75:  # around the send window and the fills
                arm = session_open(day) + timedelta(minutes=r.randrange(send - 8, close + 3))
            else:
                arm = session_open(day) + timedelta(minutes=r.randrange(-20, send - 8))
            down = r.choice((0, 0, 1, 1, 2, 3, 5, 9, 15, 45, 400, 1100))
            if r.random() < 0.25:
                outages.append(Outage(arm, max(down, 2), kill=False))
            else:
                outages.append(Outage(arm, down, effects=r.randrange(1, 25), orders_only=r.random() < 0.6,
                                      after=r.random() < 0.5))
        if week is not SUMMER:  # audit 2, 5.17: draws only the new weeks make (the summer stream is unchanged)
            rs = random.Random(f"kaibot-dst-sleep-{seed}")
            for ev, nxt in ((D0, D1), (D1, D2)):
                if rs.random() < 0.06:  # the daily store comes only after the fill session (#691: taken late, #751)
                    ready[ev] = et(nxt, week.close(nxt)) + timedelta(minutes=rs.choice((20, 90, 400)))
            # audit 2, 5.18: a third of the new weeks' seeds lean on one of the second audit's fault kinds
            focus = rs.choice((None, None, "lease_renew_fail", "sale_refused", "endpoint_down"))
            if focus is not None:
                profile = replace(profile, **{focus: min(getattr(profile, focus) * FOCUS[focus], 1.0)})
            if rs.random() < 0.35:  # the Mac sleeps through a night, maybe into the next session (24-25 Nov)
                day = rs.choice((D0, D1, D2))
                arm = et(day, week.close(day)) + timedelta(minutes=rs.randrange(30, 420), seconds=rs.randrange(60))
                hours = rs.choice((2, 5, 9, 14, 17, 19))
                outages.append(Outage(arm, hours * 60 + rs.randrange(60), kill=False, mac=True,
                                      restart=rs.random() < 0.5))
        flatten = None
        if r.random() < 0.15:
            day = r.choice((D2, D2, D3))
            send, close = week.minutes(day) - 10, week.minutes(day)
            minute = r.choice((r.randrange(5, send), r.randrange(send, close)))
            flatten = session_open(day) + timedelta(minutes=minute)
        scale = r.choice((0.0, 0.5, 1.0, 1.0, 1.0, 2.0))
        book_stop = "fixed"
        if week is SUMMER:  # audit 2 5.18's and 5.20's fault kinds only in the new weeks: a summer seed runs as it did
            profile = profile.without(*AUDIT2_FAULTS, *CA_FAULTS)
        elif random.Random(f"kaibot-dst-band-{seed}").random() < BAND_SHARE:
            book_stop = "band"  # audit 2, 5.20: the book's stop in band mode (its own stream: nothing else moves)
        profile = profile.scaled(scale)
        return cls(seed, plan, ready, tuple(sorted(outages, key=lambda o: o.arm)), flatten,
                   watchdog=r.random() < 0.5, profile=profile, week=week, book_loop=book_loop, lease=lease,
                   book_stop=book_stop)

    def wanted(self, decidable: set[str] | None = None) -> tuple:
        """What the book must hold at the end: the latest decision's names. `decidable`: the fill sessions whose
        decision the engine had a chance to take (None: all of them)."""
        last = None
        for day in self.week.sessions:
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


def trading_days(week: Week = SUMMER) -> list[str]:
    """The sessions of minute bars: HISTORY_DAYS before D0 to D4, the week's holidays left out."""
    if not week.holidays:
        days = pd.bdate_range(end=week.days[4], periods=HISTORY_DAYS + 5)
    else:
        days = pd.bdate_range(end=week.days[4], periods=HISTORY_DAYS + 5, freq="C", holidays=list(week.holidays))
    return [str(d.date()) for d in days]


def minute_frames(seed: int, week: Week = SUMMER) -> dict[str, pd.DataFrame]:
    """Minute bars of the three stocks, XLK and SPY from HISTORY_DAYS sessions before D0 to D4: gentle random walks,
    different for every seed. A session has bars only while it is open (none after a 13:00 close)."""
    rng = np.random.default_rng(seed)
    ns = []
    for d in trading_days(week):
        o = pd.Timestamp(f"{d} 09:30", tz=NY).tz_convert(UTC).value
        ns.append(o + np.arange(week.minutes(d)) * 60_000_000_000)
    ts = pd.to_datetime(np.concatenate(ns), utc=True)
    out = {}
    for s, p0, vol in (("B", 100.0, 2.5e-4), ("C", 60.0, 3e-4), ("D", 150.0, 2e-4), ("XLK", 200.0, 2e-4),
                       ("SPY", 500.0, 1.5e-4)):
        c = p0 * np.exp(np.cumsum(rng.normal(2e-6, vol, len(ts))))
        o = np.r_[p0, c[:-1]]
        h, lo = np.maximum(o, c) * 1.0003, np.minimum(o, c) * 0.9997
        out[s] = pd.DataFrame({"ts": ts, "o": o, "h": h, "l": lo, "c": c, "v": 2e5, "vw": (h + lo + c) / 3})
    return out


def daily_from(frames: dict[str, pd.DataFrame], week: Week = SUMMER, extra: tuple = ()) -> DailyData:
    """300 sessions of daily bars up to D4: the last ones are the minute bars' own days, the earlier ones a steady
    rise to them: enough history for the rule's averages, and steep enough against the few gentle days of minute bars
    that every stock stays above its 100-day average and SPY above its 200-day average. The carry-over and the late
    send skip a buy the rule could not make at the last close ("not buyable: trend", the market filter): by design,
    and not what this simulation is about, so the bars never give them a reason."""
    n = 300
    dates = np.busday_offset(np.datetime64(week.days[4]), -np.arange(n)[::-1], roll="backward",
                             holidays=list(week.holidays)).astype("datetime64[D]")
    symbols = [*STOCKS, "SPY", *extra]  # `extra`: names outside the universe the store also has (a spin-off's)
    c = np.zeros((n, len(symbols)))
    o = np.zeros_like(c)
    tail = HISTORY_DAYS + 5
    ends = np.cumsum([week.minutes(d) for d in trading_days(week)])  # one past each session's last bar
    for j, s in enumerate(symbols):
        f = frames[s]
        day_c = f["c"].to_numpy()[ends - 1]
        day_o = f["o"].to_numpy()[np.r_[0, ends[:-1]]]
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

    def __init__(self, history, clock, calendar=None) -> None:
        super().__init__(history, clock, cash=VENUE_CASH, calendar=calendar)
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
                 "outages " + str([(str(o.arm), o.down, "kill" if o.kill else "mac sleep" if o.mac else "pause")
                                   for o in s.outages]),
                 f"week {s.week.name}  engine {'book loop' if s.book_loop else 'intraday'}  book stop {s.book_stop}",
                 f"run it again: PYTHONHASHSEED=0 PYTHONPATH=src python tests/dst/run_seed.py {s.seed} --week "
                 f"{s.week.name} {'--book-loop' if s.book_loop else '--intraday'}",
                 f"the last {tail} lines of the trace:", *[f"  {t}" for t in self.trace[-tail:]]]
        return "\n".join(lines)


class World:
    """Everything a crash does not touch, and the loop that drives the engine through the scenario."""

    def __init__(self, scenario: Scenario, root: Path | None = None) -> None:
        self.s = scenario
        self.own_root = root is None
        self.root = Path(tempfile.mkdtemp(prefix="kb-dst-")) if root is None else root
        self.root.mkdir(parents=True, exist_ok=True)
        self.week = week = scenario.week
        self.clock = SimClock(et(week.days[0], week.close(week.days[0])) + timedelta(minutes=1, seconds=7))
        frames = minute_frames(scenario.seed, week)
        self.history = HistoricalData(frames, self.clock)
        self.daily_all = daily_from(frames, week)
        self.fuse = Fuse()
        # the summer week's venue takes its calendar from the bars, as it always did; every other week names its
        # sessions and their closes (a holiday has no row, an early close its 13:00)
        cal = None if week is SUMMER else {d: ("09:30", week.close(d)) for d in trading_days(week)}
        self.sim = Venue(self.history.history, self.clock, cal)
        self.broker = FaultyBroker(self.sim, scenario.seed, scenario.profile, on_write=self._venue_write,
                                   forced=scenario.forced)
        self.broker.on_fault = lambda fault, what: self.say(f"fault {fault} {what}")
        self.broker.on_action = self._on_action  # audit 2, 5.20: a corporate action the venue applied
        self.spun: list[str] = []  # spun-off symbols the daily store has (KNOWN)
        self.spun_days: set[str] = set()  # sessions of a spin-off (KNOWN)
        self._calendar_went: set[str] = set()
        if week.calendar_down:
            self.broker.calendar = self._calendar
        if scenario.lease:  # the engine reaches the venue through the lease's fence, as engine/__main__.py builds it
            from kaibot.engine.fence import FencedBroker

            self.broker = FencedBroker(self.broker)  # an instance attribute: the wrapper's other calls are unchanged
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
        self.outages = [o for o in scenario.outages if not o.mac]
        self.mac_sleeps = [o for o in scenario.outages if o.mac]
        self.mac_asleep: Outage | None = None  # the Mac sleeps (audit 2, 5.17)
        self._last_pass: datetime | None = None  # the world's last moment with the Mac awake
        self.slept = 0.0  # seconds the Mac slept: the monotonic clock did not run then
        self.lease = None  # the engine process's lease (Scenario.lease)
        self.gaps = None  # its gap watch
        self.beat = None  # its heartbeat, which renews the lease (engine/__main__.make_beat)
        self.healed = False
        self.trace: list[str] = []
        self.violations: list[str] = []
        self.stats = {"passes": 0, "kills": 0, "pauses": 0, "boots": 0, "boot_failures": 0, "tick_errors": 0,
                      "watchdog_acts": 0, "mac_sleeps": 0, "lease_lost": 0}
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
        # A decision the engine did journal counts whenever it was taken (#591; `Checker.journal_tail`).
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

    def _calendar(self, day: str) -> dict | None:
        """The broker's calendar as the engine reads it (Alpaca: GET /v2/calendar): during a spell of the week's
        `calendar_down` every read is a 503, whatever day it asks for."""
        now = self.clock()
        for d, a, b in self.week.calendar_down:
            if et(d, a) <= now < et(d, b):
                if d not in self._calendar_went:
                    self._calendar_went.add(d)  # the last read that works: the spell starts right after it
                    self.say(f"the broker's calendar goes down until {b} ET")
                    break
                self.broker._note("calendar_down", str(day))
                raise BrokerError(503, '{"message":"faulty: calendar unavailable"}')
        return self.sim.calendar(day)

    def calendar_out(self, now: datetime) -> bool:
        """Is the broker's calendar down now (a spell of the week's `calendar_down` that has started)?"""
        return any(d in self._calendar_went and et(d, a) <= now < et(d, b) for d, a, b in self.week.calendar_down)

    def close_of(self, day: str) -> datetime:
        return et(str(day), self.week.close(str(day)))

    def load(self, k: date, now: datetime):
        """The daily store: the view of the evening of session k, once it has been refreshed."""
        if self.close_of(str(k)) > now:
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
            store = JournalStore(journal, self.remote, live=True, mono=self.mono)
            market = LiveMarket(self.data, list(STOCKS), {s: "XLK" for s in STOCKS}, "SPY", self.root / "hist",
                                lambda: pd.Series(18.0, index=pd.date_range("2025-01-01", "2026-12-31")))
            # the live process builds `Engine(intraday=False)` (engine/book_loop.py, no intraday strategies); the
            # world's default stays the engine it was written for, which drives the same daily path
            extra = {"intraday": False, "strategies": {lv: [] for lv in load_profiles()}} if self.s.book_loop else {}
            eng = Engine(self.broker, market, store, load_profiles(), set(), now=self.clock,
                         daily=lambda e: ld.for_engine(e, self.load, rules=[self.rule]),
                         recent_actions=self.recent_actions, **extra)
            eng.market.candidates_at = lambda m: pd.DataFrame()  # no intraday candidates: the daily path only
            eng.sleep = lambda s: None
            eng.monotonic = self.mono
            rep = Replicator(journal, self.remote)
            rep.clock = self.mono
            if self.s.lease:
                self._start_live(eng)
            else:
                eng.startup()
        except BaseException:
            journal.close()
            raise
        self.eng, self.rep = eng, rep
        self.check.attach(eng)
        self.say("engine up")

    # ------------------------------------------------------------ corporate actions (audit 2, 5.20)
    def _on_action(self, a: dict) -> None:
        """The venue applied a corporate action before the open: the daily store follows (split-adjusted from the
        venue's own bars), and the oracle's targets are in the new shares."""
        if a["kind"] == "split":
            self.check.split(a["symbol"], float(a["ratio"]))
        if a["kind"] == "spin_off" and SPINOFF_ISSUE in KNOWN:  # (KNOWN: the store gets the new symbol's bars, and
            self.spun.append(a["new_symbol"])  # the send of the spin-off's own session is not judged)
            self.spun_days.add(a["on"])
        if a["kind"] in ("split", "spin_off"):
            self.daily_all = daily_from(self.history.frames, self.week, tuple(self.spun))

    def recent_actions(self, today: date, symbols) -> list:
        """Alpaca's corporate-actions record as the engine reads it (engine/corp_guard.RecentActions): what the
        venue applied in the ten days to `today`, for these symbols."""
        from kaibot.engine.corp_guard import Link

        start = str(today - timedelta(days=10))
        want = {str(x).upper() for x in symbols}
        return [Link(a["kind"], a["symbol"], a["new_symbol"], a["on"], a["ratio"], a["ref"])
                for a in self.broker.actions if a["kind"] != "split" and a["symbol"] in want
                and start <= a["on"] <= str(today)]

    def band_config(self) -> Path:
        """config/book_stop.yaml with `mode: band` and BAND_TABLE as the book's table, written under the root."""
        import yaml

        from kaibot.risk import book_stop as bstop

        table = self.root / "band_trend_momentum_balanced.json"
        table.write_text(json.dumps(BAND_TABLE))
        cfg = yaml.safe_load(bstop.DEFAULT_PATH.read_text())
        cfg["mode"] = "band"
        cfg["tables"] = {"trend_momentum": {"balanced": str(table)}}
        path = self.root / "book_stop.yaml"
        path.write_text(yaml.safe_dump(cfg))
        return path

    def mono(self) -> float:
        """The engine's monotonic clock: the wall clock less the time the Mac slept (macOS's monotonic clock does not
        run while the machine sleeps; engine/lease.py takes the larger of the two ages for that reason)."""
        t = self.clock().timestamp() - self.slept
        if self.mac_asleep is not None:
            t -= (self.clock() - self.mac_asleep.arm).total_seconds()
        return t

    def _start_live(self, eng) -> None:
        """engine/__main__.py's start: the last heartbeat read for the gap watch, the lease taken (a lease that cannot
        be checked: the start fails and the launcher tries again), the fence given its tenure, the engine started,
        one `engine_gap` event if it was down more than an hour without a clean stop."""
        from kaibot.engine import __main__ as main
        from kaibot.engine.gap import GapWatch
        from kaibot.engine.lease import EngineLease, machine_key

        gaps = GapWatch(eng.store, eng._rid, "dst", process=eng.process, now=self.clock)
        with contextlib.suppress(Exception):
            gaps.before_start(self.remote)
        lease = EngineLease(eng.store, "H-dst", "dst",
                            mono=self.mono, wall=lambda: self.clock().timestamp(), journal=eng.journal,
                            machine=machine_key("dst", "UUID-DST"))
        lease.acquire()  # LeaseUnavailable / LeaseHeld: the process exits, launchd starts it again
        eng.lease = lease
        main.fence_orders(eng, lease)
        eng.startup()
        with contextlib.suppress(Exception):
            gaps.after_start()
        self.lease, self.gaps = lease, gaps
        self._stop: dict = {"now": False}
        self.beat = main.make_beat(eng, lease, "dst", self._stop)

    def die(self, why: str) -> None:
        if self.eng is not None:
            with contextlib.suppress(Exception):
                self.eng.journal.close()
        self.eng = self.rep = None
        if self.s.lease:  # (tests/drills' DrillWorld keeps its own lease across a kill)
            self.lease = self.gaps = self.beat = None
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
        faults = self._faults()
        try:
            status: dict = {"state": "starting"}
            try:
                if self.lease is not None:  # engine/__main__.main's loop: the gap watch, the lease, the commands
                    from kaibot.engine import __main__ as main

                    with contextlib.suppress(Exception):
                        self.gaps.tick()
                    if self.lease.lost:
                        raise LeaseLost("The engine lease is no longer ours (found by the order fence). Stopping.")
                    main.before_tick(eng, self.lease)
                else:
                    eng.apply_commands()
                status = eng.tick()
                eng.on_tick_ok()
            except JournalCorrupt as e:
                self.bad(f"the journal is damaged: {e}")
                self.die("journal corrupt")
                return
            except LeaseLost:
                raise
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
            if self.beat is not None:
                self.beat(status)  # renews the lease, then the heartbeat row (engine/__main__.make_beat)
                if self._stop.get("lease_lost"):
                    raise LeaseLost("the lease belongs to another engine")
            else:
                eng.store.heartbeat(eng.process, status)
            self.rep.pump()
        except Kill as k:
            self._killed(str(k))
            return
        except LeaseLost as e:  # engine/__main__ stops (exit 3); launchd starts it again a minute later
            self.stats["lease_lost"] += 1
            self.down_until = self.clock() + timedelta(minutes=1)
            self.die(f"{type(e).__name__}: {str(e)[:100]}")
            return
        # a pass that ran without an error and without an injected fault (but the week's calendar outage)
        self.check.after_pass(clean=status.get("state") != "error" and self._faults() == faults)

    def _faults(self) -> int:
        """How many faults have been injected so far (the week's calendar outage is not counted)."""
        return len(self.data.log) + len(self.remote.log) + sum(f[1] != "calendar_down" for f in self.broker.log)

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
            for day in self.week.sessions:  # a session in progress that opened after the run was first seen
                if self._run_seen <= session_open(day) <= now < self.close_of(day):
                    first = self._first_due.setdefault(day, now)
                    if now - first >= ld.LATE_EVERY + timedelta(minutes=1):
                        self.bad(f"the run is still scheduled in the session of {day}, an open after it was started")
        if not run or run.get("status") not in ("running", "paused"):
            return
        if self.calendar_out(now):
            # a decision needs the broker's calendar (which session the view is for, which it fills): a pass while
            # the calendar cannot be read is no chance to take it (the engine has no local calendar of sessions,
            # only of closes: live_daily.LOCAL_EARLY_CLOSES)
            return
        for prev, fill in zip(self.week.evenings, self.week.sessions, strict=False):
            if fill in self.decidable or not (self.s.ready[prev] <= now < self.close_of(fill)):
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
        if self.eng is not None and self.asleep_until is None and self.mac_asleep is None:
            with contextlib.suppress(Exception):
                self.replica.heartbeat("engine", {"state": "alive"})

    def watchdog(self) -> None:
        if self.dog is None or self.healed or self.mac_asleep is not None:  # it runs on the Mac too
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
        if self.mac_asleep is not None:
            self._wake(self.clock())
        self.mac_sleeps.clear()
        if self.down_until is not None and self.down_until > self.clock():
            # an engine that was to stay down returns before the last window at the latest
            late = self.close_of(self.s.heal_day) - timedelta(minutes=40)
            self.down_until = min(self.down_until, late)
        self.say("the world is healed: no more faults")

    def at(self, t: datetime, run: bool = True) -> None:
        """Run the world at `t`. With the live process's loop, an awake Mac runs it at least every 50 minutes, also
        through nights and holidays: its gap watch (engine/gap.py) takes two passes more than an hour apart for a
        frozen process."""
        while self.s.lease and self.mac_asleep is None and self._last_pass is not None \
                and t - self._last_pass > timedelta(minutes=55):
            fill = max(self._last_pass + timedelta(minutes=50), self.clock() + timedelta(seconds=1))
            if fill >= t:
                break
            if self.mac_sleeps and self.mac_sleeps[0].arm <= fill and not self.healed:
                break  # the Mac goes to sleep at its own pass first
            self._at(fill)
        self._at(t, run)

    def _at(self, t: datetime, run: bool = True) -> None:
        if self.mac_asleep is not None and t >= self.mac_asleep.wake:
            self._wake(self.mac_asleep.wake)
        self.clock.set(t)
        self.owner()
        if self.mac_asleep is not None:
            return  # nothing runs on a sleeping Mac
        if run:
            self._last_pass = t
            self.one_pass()
        else:
            self.beat_alive()
        if self.mac_sleeps and self.mac_sleeps[0].arm <= t and not self.healed:
            o = self.mac_sleeps.pop(0)
            self.mac_asleep = o
            self.stats["mac_sleeps"] += 1
            self.say(f"the Mac sleeps until {o.wake.astimezone(NY):%m-%d %H:%M:%S} New York"
                     + (" (the engine is started again at the wake)" if o.restart else ""))

    def _wake(self, t: datetime) -> None:
        """The Mac wakes at `t`: the wall clock jumped, the monotonic clock did not. The watchdog's job runs first
        (launchd runs a missed interval at the wake), then the engine wakes or is started again."""
        o, self.mac_asleep = self.mac_asleep, None
        if t > self.clock():
            self.clock.set(t)
        self.slept += (t - o.arm).total_seconds()
        self.say("the Mac wakes")
        self.watchdog()
        if o.restart and self.eng is not None:
            self.down_until = t
            self.die("started again after the Mac woke")

    def session(self, day: str, rng: random.Random) -> None:
        o = session_open(day)
        for hhmm in ("09:00", "09:25"):
            self.at(et(day, hhmm, 7))
        due = self._due(rng, self.week.minutes(day))
        for k in range(self.week.minutes(day)):
            self.broker.advance(pd.Timestamp(o + timedelta(minutes=k)))
            t = o + timedelta(minutes=k + 1, seconds=7)
            busy = self.eng is None or self.asleep_until is not None or bool(self.fuse.left) \
                or bool(self.eng.daily and self.eng.daily.om and self.eng.daily.om.live)
            self.at(t, run=k in due or busy or not self.s.sparse)
            self.watchdog()

    def _due(self, rng: random.Random, close: int = CLOSE) -> set[int]:
        send = close - (CLOSE - SEND)
        due, k = {0, 1, 2}, 2
        while k < send - 10:
            k += rng.choice((1, 2, 3, 5, 8, 13, 21, 34))
            due.add(k)
        return due | set(range(send - 10, close))

    def evening(self, day: str) -> None:
        marks = {5, 20, 45, 90, 180, 300}
        ready = self.s.ready.get(day)
        close = self.close_of(day)
        if ready is not None and ready < et(day, "23:00"):
            marks |= {int((ready - close).total_seconds() // 60) + m for m in (1, 2)}
        regular = et(day, REGULAR_CLOSE)
        if close < regular:  # an early close: the loop runs every minute of the regular window too, where a send
            # that took the session for a regular one would go out (#733)
            back = int((regular - close).total_seconds() // 60)
            marks |= set(range(back - 11, back + 1))
        times = {close + timedelta(minutes=m, seconds=7) for m in marks}
        times |= {o.arm for o in self.mac_sleeps if close <= o.arm < close + timedelta(hours=15)}
        for t in sorted(times):  # the Mac goes to sleep right after a pass at its moment
            self.at(t)

    def closed_day(self, day: str) -> None:
        """A weekday the market is closed (a holiday): the Mac runs the engine all the same. Before the open, at
        what would be the open, around noon, in what would be the send window and after the close: the engine must
        neither trade nor take that day for a session (no window, no decision for it, no fill)."""
        for hhmm in ("08:45", "09:31", "12:00", "15:51", "15:55", "16:05", "18:00"):
            self.at(et(day, hhmm, 7))
            self.watchdog()

    def _state_seam(self):
        """Invariant 10 (M10 1.21, #446): no buy reaches the venue while the engine's one trading state is not
        `active` (engine/state.py). Checked at the order seam (engine/order_path.py), so every path is covered; the
        check only reads (`Engine.trading_state`), and adds nothing to the trace unless it fails."""
        from kaibot.engine import order_path
        from kaibot.engine.state import allows

        saved = {n: getattr(order_path, n) for n in ("submit", "submit_bracket", "submit_oto_stop")}

        def wrap(name, f):
            def sent(broker, *a, **kw):
                side = getattr(a[0], "side", None) if name == "submit" else "buy"
                sym = getattr(a[0], "symbol", None) if name == "submit" else a[1]
                if self.eng is not None and side == "buy":
                    t = self.eng.trading_state()
                    if not allows(t.state, "submit", side):
                        self.bad(f"invariant 10: a buy of {sym} left while trading was {t.state.value} ({t.why})")
                return f(broker, *a, **kw)
            return sent

        for n, f in saved.items():
            setattr(order_path, n, wrap(n, f))
        return lambda: [setattr(order_path, n, f) for n, f in saved.items()]

    def run(self) -> Result:
        os.environ["KAIBOT_MULTIDAY"] = "on"
        rng = random.Random(f"kaibot-dst-ticks-{self.s.seed}")
        unseam = self._state_seam()
        band = contextlib.nullcontext()
        if self.s.book_stop == "band":
            from kaibot.engine import core

            band = mock.patch.object(core, "BOOK_STOP_PATH", self.band_config())  # where the engine reads its config
        try:
            band.__enter__()
            self.evening(self.week.days[0])
            for day, nxt in zip(self.week.sessions, self.week.days[2:], strict=True):
                if day == self.s.heal_day:
                    if self.s.lease:
                        self.at(et(day, "08:00"), run=False)  # (the night's passes up to here)
                    else:
                        self.clock.set(et(day, "08:00"))
                    self.heal()
                self.session(day, rng)
                self.evening(day)
                for h in self.week.holidays:
                    if day < h < nxt:
                        self.closed_day(h)
            for m in range(1, 4):  # quiescence: the loop runs on, nothing new happens
                self.at(et(self.week.days[3], "21:10", 7) + timedelta(minutes=m))
            self.check.at_quiescence()
        finally:
            band.__exit__(None, None, None)
            unseam()
            if self.eng is not None:
                with contextlib.suppress(Exception):
                    self.eng.journal.close()
            if self.own_root:
                shutil.rmtree(self.root, ignore_errors=True)
        faults = [*self.broker.log, *self.data.log, *self.remote.log]
        self.stats["faults"] = len(faults)
        self.stats["fault_kinds"] = sorted({f[1] for f in faults})
        # audit 2, 5.20: what the nightly coverage counts besides the faults
        self.stats["week"] = self.week.name
        self.stats["book_stop"] = self.s.book_stop
        self.stats["band_checks"] = self.check.band_checks
        self.stats["band_alerts"] = self.check.kinds.get("md_book_band_alert", 0)
        self.stats["corporate_actions"] = sorted({a["kind"] for a in self.broker.actions})
        return Result(self.s, self.violations, self.trace, self.stats)


# ---------------------------------------------------------------- the invariants
class Checker:
    """The nine invariants and the eleventh (the tenth, the trading state, is checked at the order seam:
    World._state_seam),
    checked incrementally: the journal's new entries after every pass, the venue's books
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
        self.decided: set[str] = set()  # fill sessions with a journalled decision (a target list) of this run
        self.sends: set[str] = set()  # sessions with a journalled md_send of this run
        # invariant 12 (audit 2, 5.18): per session, the names the ledger held at the window's first pass, the
        # sales that reached the venue in the window (symbol, time), and the passes that were a chance to sell
        self.held_at: dict[str, set[str]] = {}
        self.sold: dict[str, list[tuple[str, datetime]]] = {}
        self.chances: dict[str, list[tuple[datetime, frozenset]]] = {}
        self.judged: set[str] = set()
        self.fenced: list[tuple[str, datetime]] = []  # buys the lease's fence refused for a failed renewal
        self.intent_at: list[tuple[str, str, datetime]] = []  # (symbol, side, when) of every journalled order intent
        self.kinds: dict[str, int] = {}  # journal entries by kind (the run's coverage, audit 2 5.20)
        self.band_checks = 0  # passes whose book check judged the book by its band (audit 2 5.20)

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
            self.kinds[kind] = self.kinds.get(kind, 0) + 1
            if kind in SAID and not (kind == "om_state" and self.state.get(b["coid"]) == str(b.get("state"))):
                w.say(f"journal {seq} {kind} " + " ".join(f"{k}={b[k]}" for k in SAID[kind] if b.get(k) is not None))
            if kind == "run":
                self.rid = b.get("run_id")
            elif kind == "md_decision" and b.get("target") is not None and b.get("run_id") == self.rid:
                self.decided.add(str(b["fill_session"]))
                # #591: a decision the engine journalled was a chance it took, however quickly (`_could_decide`
                # waits LATE_EVERY into the session; a late decision at 09:31 and a kill right after it is sent
                # late on the next session, M9 1.4, and that send is what the book must show at the end)
                w.decidable.add(str(b["fill_session"]))
            elif kind == "om_intent":
                self.intents.add(b["coid"])
                self.state[b["coid"]] = "intent"
                self.intent_at.append((str(b.get("symbol")), str(b.get("side")),
                                       w.clock()))
            elif kind == "om_state":
                if str(b.get("state")) == "rejected" and "lease not proved" in str(b.get("reason") or ""):
                    self.fenced.append((str(b["coid"]), w.clock()))
                was, now = self.state.get(b["coid"]), str(b.get("state"))
                if was in TERMINAL and now != was and not (was == "rejected" and now in ("accepted", "filled",
                                                                                         "partially_filled")):
                    w.bad(f"{b['coid']} left its final state {was} for {now}")
                self.state[b["coid"]] = now
            elif kind == "split":  # audit 2, 5.20: the venue's split, booked (the shares multiply, no cash)
                sym = b["symbol"]
                self.fills_qty[sym] = self.fills_qty.get(sym, 0.0) * float(b["ratio"])
            elif kind == "spinoff":  # the spun-off shares, adopted (no cash: the cost basis moves)
                self.fills_qty[b["new_symbol"]] = self.fills_qty.get(b["new_symbol"], 0.0) + float(b["qty"])
            elif kind == "fill" and b.get("reason") == "cash_merger":  # the venue's cash for the merged shares
                self.fills_cash += float(b["qty"]) * float(b["px"]) - float(b.get("fees") or 0.0)
                self.fills_qty[b["symbol"]] = self.fills_qty.get(b["symbol"], 0.0) - float(b["qty"])
            elif kind == "fill":
                aid = str(b.get("activity"))
                if aid.startswith("owner:"):  # #529: the owner booked the order's fill from its view: it stands for
                    # the venue's executions of that order (their activities never came); same shares, or a violation
                    execs = [f for f in w.sim.fills if str((w.sim.orders.get(f["order_id"]) or {}).get(
                        "client_order_id") or "") == b.get("coid") and str(f["id"]) not in self.booked]
                    if execs and abs(sum(float(f["qty"]) for f in execs) - float(b["qty"])) < 1e-9:
                        self.booked.update(str(f["id"]) for f in execs)
                        aid = None
                    else:
                        w.bad(f"the owner booked {b['qty']} on {b.get('coid')}, the venue's unbooked executions of "
                              f"it are {[(f['id'], f['qty']) for f in execs]}")
                if aid in self.booked:
                    w.bad(f"execution {aid} was booked twice")
                if aid is not None:
                    self.booked.add(aid)
                sign = 1.0 if b["side"] == "buy" else -1.0
                self.fills_cash += -sign * float(b["qty"]) * float(b["px"]) - float(b.get("fees") or 0.0)
                self.fills_qty[b["symbol"]] = self.fills_qty.get(b["symbol"], 0.0) + sign * float(b["qty"])
            elif kind == "md_send":
                if b.get("run_id") == self.rid:
                    self.sends.add(str(b.get("session")))
                for sym, shares, *_ in b.get("targets") or []:
                    self.targets[sym] = max(self.targets.get(sym, 0), int(shares))

    def split(self, symbol: str, ratio: float) -> None:
        """Audit 2, 5.20: the venue split `symbol`: the targets journalled so far are in the old shares."""
        if symbol in self.targets:
            self.targets[symbol] = int(round(self.targets[symbol] * ratio))

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
        # the session's real close (the venue's, not what the engine thinks it is): 16:00, or 13:00 on an early
        # close; on a holiday there is no session at all
        today = f"{now:%Y-%m-%d}"
        close = w.close_of(today) if w.sim.calendar(today) is not None else None
        if purpose in MARKET_PURPOSES and side == "sell":
            self.sold.setdefault(today, []).append((sym, w.clock()))
        if purpose in MARKET_PURPOSES:
            if day != f"{now:%Y%m%d}" or close is None or not (close - timedelta(minutes=10) <= w.clock() < close):
                w.bad(f"{coid} left outside its window, at {now:%Y-%m-%d %H:%M:%S} New York"
                      + ("" if close is None or close.astimezone(NY).hour == 16
                         else f" (the session closes at {close.astimezone(NY):%H:%M})"))
            if w.broker._skew is not None and w.broker.armed:
                w.bad(f"{coid} was sent while the host clock and the venue's disagreed")
        elif close is None or not (now.hour * 60 + now.minute >= 570 and w.clock() < close):
            w.bad(f"{coid} ({purpose}) was sent while the market was closed, at {now:%H:%M:%S}")

    # --- after every pass
    def after_pass(self, clean: bool = False) -> None:
        self.journal_tail()
        line = (self.eng.book_line or {}) if self.eng is not None else {}
        if (line.get("stop") or {}).get("mode") == "band":  # audit 2, 5.20: the book was judged by its band
            self.band_checks += 1
        self.venue_side()
        self.ledger_side()
        if clean:
            self.window_side()
        self.sales_side()

    # --- 12 (audit 2, 5.18): due sales go out in their window, and a refused one is sent again there
    def sales_side(self) -> None:
        """A sale is due in a fill session's window when the session's decision (the scenario's plan: the rule decides
        exactly that) no longer wants a name the ledger holds at the window's first pass. A pass in the window is a
        chance to sell a name when the engine ran it, the run is running and not flattening, the host's and the
        venue's clocks agree, the venue answered every call of that minute (but the positions read and refused
        sales, which 12 is about), and the name has no order the engine cannot confirm yet (it waits for it, M9
        1.11). At the window's end:

            12a  a due sale with two chances or more reached the venue in the window (one failing read endpoint
                 must not hold a due sale behind it: 5.10, #737)
            12b  a sale the venue refused, with a chance after the refusal, reached it again in the window
                 (5.9, #736)
            12c  a buy the lease's fence refused because a renewal failed, with a chance after it in a minute the
                 renewals work again, was sent again in the window (5.8, #735)"""
        w, eng = self.w, self.eng
        now = w.clock()
        day = f"{now.astimezone(NY):%Y-%m-%d}"
        if day not in w.week.sessions or eng is None or self.rid is None:
            return
        close = w.close_of(day)
        start, end = close - timedelta(minutes=10), close - timedelta(minutes=3)
        if start <= now < end:
            if day not in self.held_at:
                book = eng.ledger.book(self.rid)
                self.held_at[day] = {s for s, q in (book.qty_by_symbol().items() if book else ()) if q > 1e-9}
            if self._chance(now):
                om = eng.daily.om if eng.daily is not None else None
                waiting = frozenset(r.symbol for r in (om.orders.values() if om is not None else ())
                                    if r.state in UNCONFIRMED and r.session == day)
                self.chances.setdefault(day, []).append((now, waiting))
            return
        if now >= end and day in self.held_at and day not in self.judged:
            self.judged.add(day)
            if day not in w.spun_days:  # KNOWN (SPINOFF_ISSUE): the spin-off's own session sends nothing
                self._judge_sales(day, start, end)

    def _chance(self, now: datetime) -> bool:
        w, eng = self.w, self.eng
        if w.flatten_sent or eng.run is None or eng.run.get("status") != "running":
            return False
        if w.broker._skew is not None and w.broker.armed:
            return False
        minute = f"{now.astimezone(UTC):%Y%m%dT%H%M}"
        # a minute in which the venue answered nothing but what 12 is about (the positions read down, a refused sale)
        return all(m != minute or f in SALE_NEUTRAL or (f == "endpoint_down" and what == "positions")
                   for m, f, what in w.broker.log)

    def _judge_sales(self, day: str, start: datetime, end: datetime) -> None:
        w = self.w
        want = w.s.plan.get(day)
        sold = [(s, t) for s, t in self.sold.get(day, []) if start <= t < end + timedelta(minutes=3)]
        chances = self.chances.get(day, [])
        if want is not None and day in self.decided:
            for sym in sorted(self.held_at[day] - set(want)):
                n = sum(sym not in waiting for _, waiting in chances)
                if n >= 2 and not any(s == sym for s, _ in sold):
                    w.bad(f"invariant 12a: the due sale of {sym} on {day} did not reach the venue in its window, with "
                          f"{n} passes that could have sent it")
        for m, fault, what in w.broker.log:
            if fault not in ("sale_refused", "submit_refused"):
                continue
            coid = what.split(" ")[0]
            parts = coid.split("-")
            if len(parts) < 4 or parts[-3] != day.replace("-", "") or parts[-2] not in MARKET_PURPOSES:
                continue
            at = datetime.strptime(m, "%Y%m%dT%H%M").replace(tzinfo=UTC)
            sym = parts[-4]
            if parts[-2] == "buy" or not start <= at < end:
                continue
            later = [t for t, waiting in chances if t >= at + timedelta(minutes=1) and sym not in waiting]
            again = [t for s, t in sold if s == sym and t >= at + timedelta(minutes=1)]
            if later and not again:
                w.bad(f"invariant 12b: the sale {coid} the venue refused at {at.astimezone(NY):%H:%M} was not sent "
                      f"again on {day}, with {len(later)} passes of the window left")
        renew_down = {m for m, f, _ in w.remote.log if f == "lease_renew_fail"}
        for coid, at in self.fenced:
            parts = coid.split("-")
            if len(parts) < 4 or parts[-3] != day.replace("-", "") or not start <= at < end:
                continue
            sym = parts[-4]
            later = [t for t, waiting in chances if t >= at + timedelta(minutes=1) and sym not in waiting
                     and f"{t.astimezone(UTC):%Y%m%dT%H%M}" not in renew_down]
            again = [t for s, side, t in self.intent_at if s == sym and side == "buy" and at < t < end]
            if later and not again:
                w.bad(f"invariant 12c: the buy {coid} the lease's fence refused at {at.astimezone(NY):%H:%M} (a failed "
                      f"renewal) was not sent again on {day}, with {len(later)} passes of the window left")

    # --- 11 (audit 2, 5.16): a decided send goes out in its fill session's own window
    def window_side(self) -> None:
        """A pass that ran without an error and without an injected fault inside the fill session's real send window
        (the venue's close minus 10 to minus 3 minutes: 12:50-12:57 ET on a 13:00 close), with the session's
        decision journalled: the send of that decision is journalled by the end of the pass. A send the engine puts
        in another window (the regular 15:50 on an early close, #733) is missed at the venue's close and only goes
        out late, a session later.
        Not checked once the owner pressed "Flatten and stop" or the book ended, nor while the venue's clock is
        skewed (no order goes out then, M9 1.10)."""
        w = self.w
        now = w.clock()
        day = f"{now.astimezone(NY):%Y-%m-%d}"
        if day not in w.week.sessions or day not in self.decided or day in self.sends or w.flatten_sent:
            return
        if day in w.spun_days:  # KNOWN (SPINOFF_ISSUE): the spin-off's own session sends nothing
            return
        close = w.close_of(day)
        if not (close - timedelta(minutes=10) <= now < close - timedelta(minutes=3)):
            return
        if self.eng is None or self.eng.run is None or self.eng.run.get("status") != "running":
            return
        if w.broker._skew is not None and w.broker.armed:
            return  # the host's clock and the venue's disagree: no order is sent (M9 1.10), the send waits
        with contextlib.suppress(Exception):
            if self.eng.daily is not None and self.eng.daily.account.book_ended():
                return
        self.sends.add(day)  # said once per session (the trace has the pass)
        c = close.astimezone(NY)
        w.bad(f"invariant 11: the decision for {day} was not sent in its window ({c - timedelta(minutes=10):%H:%M}-"
              f"{c - timedelta(minutes=3):%H:%M} ET; the session closes at {c:%H:%M}) although a pass ran there "
              "without an error")

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
        # audit 2, 5.20: a cash merger took a name off the market: no decision can hold it any more
        wanted = tuple(s for s in w.s.wanted(w.decidable) if s not in w.broker.merged)
        if held != tuple(sorted(wanted)):
            w.bad(f"at the end the book holds {held}; the latest decision wanted {tuple(sorted(wanted))}"
                  + (" (the owner flattened)" if w.s.flatten is not None else ""))
        if w.s.flatten is not None and eng.run is not None:
            w.bad("the owner pressed Flatten and stop and the run has not ended")


def run_seed(seed: int, profile: FaultProfile = DEFAULT_FAULTS, root: Path | None = None, week: Week | None = None,
             book_loop: bool | None = None) -> Result:
    """The run of a seed: in its own week on its own engine (`Scenario.from_seed`), unless `week` / `book_loop`."""
    return run_scenario(Scenario.from_seed(seed, profile, week=week, book_loop=book_loop), root)


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
