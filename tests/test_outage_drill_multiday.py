"""QA-Ops outage drill: the engine is down for THREE trading sessions while trend_momentum holds multi-day positions.

The real engine path with KAIBOT_MULTIDAY=on on the simulated venue (SimBroker, cost model v1, a cash book), the
on-laptop watchdog (engine/watchdog.py) and the off-box dead-man (migrations 0017/0019/0022 run as SQL in a throwaway
local Postgres, tests/pg_harness.py; never Supabase). Built from the S80 fail-safe drills and the go-live drill (#281).

    D0 (Mon 3 Aug)   the engine runs. trend_momentum holds B (40) and C (30), bought earlier, no stop by design
                     (0028). After the close the engine writes the day's ledger rows and decides for D1: keep B and C,
                     buy D. At 20:30 ET the engine dies (its last heartbeat).
    D1 (Tue)         engine down. The fail-safes put an emergency stop 10% below the last price on B and C.
    D2 (Wed)         engine down. C opens 15% lower (an overnight gap): its stop fills at the open, below the stop.
    D3 (Thu)         engine down. B is up 9% from D1: its stop stays where it was (nobody trails it).
    D4 (Fri)         09:00 ET the engine starts again; it reconciles, books C's stop fill, takes over B's stop and at
                     15:50 sends the rule's orders once. A second restart at 15:53 sends nothing again.

Two cases: the laptop stays up (the watchdog and the dead-man both run) and the laptop is off (the dead-man alone).
Per outage session: nothing is bought, no multi-day share is sold except by its own stop, each multi-day position
has exactly one resting stop, and the alarms are raised. Plain-words write-up: docs/ops/outage_drill.md, "Three
sessions down".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, timedelta

import numpy as np
import pandas as pd
import pytest
from fakes import Clock, FakeData, FakeStore
from pg_harness import DeadmanDb
from test_failsafe_drills import beat
from test_go_live_drill import Venue

from kaibot.broker.sim import COSTS_V1
from kaibot.engine import live_daily as ld
from kaibot.engine.core import Engine
from kaibot.engine.daily import DailyData
from kaibot.engine.journal import Journal
from kaibot.engine.market import LiveMarket
from kaibot.engine.watchdog import Watchdog, book_reader
from kaibot.replay.data import ET
from kaibot.risk.profiles import load_profiles, rule_book_limits
from kaibot.strategies import momentum as mom
from kaibot.strategies.base import TargetList

MD = "trend_momentum"
DAYS = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]
D0, D1, D2, D3, D4 = DAYS
DOWN = (D1, D2, D3)
HELD = {"B": 40, "C": 30}
NEW = "D"  # the D0 evening decision buys it; while the engine is down nothing is bought
SEND = 379  # D4's tick whose clock reads 15:50:07: the 15:50-15:57 send window opens (#337)
POT = 20_000.0
# the price level of each stock per drill day (multiplied into its random walk): C gaps down 15% at D2's open,
# B climbs 3% a day
LEVEL = {"B": {D1: 1.03, D2: 1.06, D3: 1.09, D4: 1.09}, "C": {D2: 0.85, D3: 0.85, D4: 0.85}}


def _open(day: str):
    return pd.Timestamp(f"{day} 09:30", tz=ET).tz_convert(UTC).to_pydatetime()


def frames() -> dict:
    """Minute bars for 75 sessions to D4: random walks with small moves, scaled per drill day by LEVEL."""
    days = pd.bdate_range(end=D4, periods=75)
    out = {}
    for i, (s, p0) in enumerate((("A", 100.0), ("XLK", 200.0), ("SPY", 500.0), ("B", 80.0), ("C", 120.0),
                                 ("D", 60.0))):
        rng = np.random.default_rng(300 + i)
        rows, p = [], p0
        for d in days:
            ts = pd.Timestamp(f"{d.date()} 09:30", tz=ET).tz_convert(UTC) + pd.to_timedelta(np.arange(390), unit="m")
            c = p * np.exp(np.cumsum(rng.normal(0, 0.0003, 390)))
            o = np.r_[p, c[:-1]]
            p = c[-1]
            k = LEVEL.get(s, {}).get(str(d.date()), 1.0)
            o, c = o * k, c * k
            h, lo = np.maximum(o, c) * 1.0003, np.minimum(o, c) * 0.9997
            rows.append(pd.DataFrame({"ts": ts, "o": o, "h": h, "l": lo, "c": c, "v": 2e5, "vw": (h + lo + c) / 3}))
        out[s] = pd.concat(rows, ignore_index=True)
    return out


@dataclass(frozen=True)
class Keeps(mom.TrendMomentum):
    """trend_momentum's name, holding policy and sizing settings; its decision, every evening: hold B, C and D."""

    def decision_day(self, prev, fill) -> bool:
        return True

    def on_event(self, ctx):
        want = ["B", "C", NEW]
        return TargetList(self.name, ctx.fill_session, {s: 1.0 for s in want},
                          features={"keep": [s for s in want if s in ctx.held],
                                    "buy": [s for s in want if s not in ctx.held], "sell": {}}, resize=False)


def daily_view(f: dict, k: date) -> DailyData:
    """The evening's daily view from the minute bars, sessions <= k only (point in time)."""
    syms = ["B", "C", NEW, "SPY"]
    g = {s: f[s].assign(day=f[s]["ts"].dt.tz_convert(ET).dt.date).groupby("day").agg(
        o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), v=("v", "sum")) for s in syms}
    days = [d for d in g["SPY"].index if d <= k]
    col = {x: np.column_stack([g[s].loc[days, x].to_numpy() for s in syms]) for x in ("o", "h", "l", "c", "v")}
    return DailyData(dates=np.array(days, dtype="datetime64[D]"), symbols=syms, sig_c=col["c"].copy(),
                     sig_h=col["h"], sig_l=col["l"], raw_o=col["o"], raw_c=col["c"].copy(), raw_h=col["h"],
                     raw_l=col["l"], v_first=col["v"] * 0.02, v_last=col["v"] * 0.02, raw_v=col["v"],
                     stocks=np.array([0, 1, 2]), spy=3, sig_unfilled_c=col["c"].copy())


class FiveDays(Venue):
    """The go-live drill's venue (cash book, cost model v1, Alpaca's account shape) over the drill's five sessions."""

    def _days(self):
        return list(DAYS)


class Drill:
    def __init__(self, tmp_path, monkeypatch, laptop: bool):
        monkeypatch.setenv("KAIBOT_MULTIDAY", "on")
        self.tmp, self.laptop = tmp_path, laptop
        self.frames = frames()
        self.clock = Clock(_open(D0) - timedelta(minutes=30))
        self.data = FakeData(self.frames, self.clock)
        self.broker = FiveDays(self.data, self.clock, D0)
        self.broker.costs, self.broker.cash = COSTS_V1, POT
        self.broker.wash_check, self.broker.shorting_enabled = True, False
        self.store, self.jpath = FakeStore(), tmp_path / "journal.sqlite"
        s = Keeps()
        self.rule = ld.Rule(s, ld.live_builder(s, ld.live_limits(s, load_profiles()["balanced"],
                                                                 rule_book_limits(MD, "balanced", research=True))))
        self.eng = self.engine()
        self.store.insert("command", {"kind": "new_run", "payload": {"amount_usd": POT, "level": "balanced"},
                                      "idempotency_key": "drill", "applied_at": None})
        self.eng.startup()
        self.eng.apply_commands()
        self.rid = self.eng.run["id"]
        self.log: dict = {}  # what happened per outage session

    def load(self, k, now):
        return daily_view(self.frames, k)

    def engine(self) -> Engine:
        market = LiveMarket(self.data, ["A", "B", "C", NEW], {s: "XLK" for s in ("A", "B", "C", NEW)}, "SPY",
                            self.tmp / "hist", lambda: pd.Series(18.0, index=pd.date_range("2025-01-01", "2026-12-31")))
        eng = Engine(self.broker, market, self.store, load_profiles(), set(), now=self.clock,
                     journal=Journal(self.jpath))
        eng.market.candidates_at = lambda m: pd.DataFrame()  # no intraday trades: the multi-day book only
        eng.daily = ld.for_engine(eng, self.load, rules=[self.rule])
        return eng

    def restart(self) -> Engine:
        self.eng.journal.close()
        self.eng = self.engine()
        self.eng.startup()
        return self.eng

    def tick(self, day, k0, k1):
        for k in range(k0, k1):
            bar = _open(day) + timedelta(minutes=k)
            self.broker.advance(pd.Timestamp(bar))
            self.clock.t = bar + timedelta(minutes=1, seconds=7)
            self.eng.tick()
            beat(self.store, self.clock())

    def after_close(self, day, hours=7.0):
        self.broker.advance(pd.Timestamp(_open(day) + timedelta(minutes=389)))
        self.clock.t = _open(day) + timedelta(hours=hours)
        st = self.eng.tick()
        beat(self.store, self.clock())
        return st

    # ---------------------------------------------------------------- D0: the book, the decision, the death
    def d0(self):
        self.tick(D0, 0, 30)
        for sym, q in HELD.items():  # bought before D0 (booked by the ledger, paid at the venue)
            px = float(self.data.history.last_close(sym, self.clock()))
            self.broker.pos[sym] = {"qty": q, "avg_entry_price": px}
            self.broker.cash -= q * px
            self.eng._book("fill", {"run_id": self.rid, "strategy": MD, "symbol": sym, "side": "buy", "qty": q,
                                    "px": px, "fees": 0.0, "coid": f"kb{self.rid}-{MD}-{sym}-20260729-target-1",
                                    "session": "2026-07-29"})
        self.tick(D0, 30, 390)
        self.after_close(D0)
        self.after_close(D0, hours=11)  # 20:30 ET: the rule decides for D1
        self.died_at = self.clock()  # the last heartbeat; the engine process is gone from here on
        self.stops_d0 = [o for o in self.broker.orders.values() if o["type"] == "stop"]
        self.rows = [dict(r) for r in self.store.tables.get("ledger_position", [])]  # Supabase's copy of the rows

    # ---------------------------------------------------------------- D1-D3: the fail-safes alone
    def outage(self, db: DeadmanDb):
        wd = Watchdog(self.broker, self.store, now=self.clock, book=book_reader(self.jpath, self.store,
                                                                               now=self.clock)) if self.laptop else None
        db.beat(self.died_at)
        db.run(self.rid)
        for s in sorted({str(r["session"]) for r in self.rows}):
            db.ledger(self.rid, s, [(r["strategy"], r["symbol"], r["qty"], r["stop_px"])
                                    for r in self.rows if str(r["session"]) == s])
        for day in DOWN:
            n_orders, halts = len(self.broker.orders), len(self.store.tables.get("halt", []))
            got = {"wd": [], "dm": [], "stops_seen": {}}
            if wd is not None:
                self.clock.t = _open(day) - timedelta(minutes=20)
                wd.step()  # before the open: idle ("waiting")
            for k in range(390):
                bar = _open(day) + timedelta(minutes=k)
                self.broker.advance(pd.Timestamp(bar))  # the minute's bar trades (a stop may fill)
                if wd is not None:
                    for sec in (15, 30, 45):  # the watchdog polls every 15 s
                        self.clock.t = bar + timedelta(seconds=sec)
                        got["wd"].append(wd.step())
                self.clock.t = bar + timedelta(seconds=50)
                got["dm"].append(db.check(self.clock()))  # pg_cron, once a minute
                db.pump(self.broker)
                for sym in HELD:
                    got["stops_seen"][sym] = max(got["stops_seen"].get(sym, 0), len(self.resting_stops(sym)))
            self.broker.advance(pd.Timestamp(_open(day) + timedelta(minutes=390)))  # the close: DAY orders expire
            got["new_orders"] = list(self.broker.orders.values())[n_orders:]
            got["halts"] = self.store.tables.get("halt", [])[halts:]
            got["positions"] = {s: float(p["qty"]) for s, p in self.broker.pos.items()}
            got["resting"] = {s: [dict(o) for o in self.resting_stops(s)] for s in HELD}
            got["passes"] = [p for p in db.sql(f"select result from kaibot.deadman_pass where session = '{day}' "
                                               "and state = 'done' order by id")]
            got["dm_halts"] = db.sql(f"select reason from kaibot.halt where kind = 'deadman' and session = '{day}'")
            self.log[day] = got

    def resting_stops(self, sym):
        return [o for o in self.broker.orders.values() if o["symbol"] == sym and o["type"] == "stop"
                and o["side"] == "sell" and o["status"] in ("new", "accepted", "pending_new", "partially_filled")]

    # ---------------------------------------------------------------- D4: the restart
    def d4(self):
        self.clock.t = _open(D4) - timedelta(minutes=30)  # 09:00 ET
        self.n_before = len(self.broker.orders)
        self.restart()
        st = self.eng.tick()  # market closed: the decision for D4 after D3's close (missed while down)
        beat(self.store, self.clock())
        self.at_restart = {"clean": self.eng.account_clean, "kill": self.eng.kill, "state": st}
        self.tick(D4, 0, SEND)
        self.before_send = {"recon": self.eng.recon.check("drill").diffs, "clean": self.eng.account_clean,
                            "kill": self.eng.kill, "orders": list(self.broker.orders.values())[self.n_before:]}
        self.tick(D4, SEND, SEND + 3)  # 15:50-15:53: the rule's orders go out and fill
        self.restart()  # killed again at 15:53, right after the send
        self.tick(D4, SEND + 3, 390)
        self.after_close(D4)
        for _ in range(3):
            self.eng.tick()

    def md(self):
        """The rule's own orders (near-close DAY market orders)."""
        mine = f"kb{self.rid}-{MD}-"
        return [o for o in self.broker.orders.values() if str(o.get("client_order_id")).startswith(mine)
                and (o["type"], o["time_in_force"]) == ("market", "day")]


def run_drill(tmp_path, monkeypatch, laptop: bool) -> Drill:
    d = Drill(tmp_path, monkeypatch, laptop)
    d.d0()
    db = DeadmanDb(tmp_path)
    try:
        d.outage(db)
        d.d4()
        d.dm_d4 = db.sql("select count(*) from kaibot.deadman_pass where session = '2026-08-07'")
    finally:
        db.stop()
    return d


@pytest.fixture(scope="module", params=[True, False], ids=["laptop_up", "laptop_off"])
def drill(request, tmp_path_factory):
    mp = pytest.MonkeyPatch()
    try:
        yield run_drill(tmp_path_factory.mktemp("laptop_up" if request.param else "laptop_off"), mp, request.param)
    finally:
        mp.undo()


def _price(d, sym, day, minute=0):
    return float(d.frames[sym].set_index("ts").loc[pd.Timestamp(_open(day) + timedelta(minutes=minute)), "o"])


# ======================================================================= before the outage


def test_d0_leaves_a_book_without_stops_and_a_decision_that_buys(drill):
    d = drill
    rows = [r for r in d.rows if str(r["session"]) == D0]
    assert sorted((r["symbol"], float(r["qty"]), r["stop_state"], r["stop_px"]) for r in rows) == [
        ("B", 40.0, "none_by_design", None), ("C", 30.0, "none_by_design", None)]
    (dec,) = [b for *_, b in d.eng.journal.entries(kinds=("md_decision",)) if b["session"] == D0]
    assert dec["fill_session"] == D1 and NEW in dict(dec["target"]["weights"])
    assert d.stops_d0 == []  # no stop while the engine ran (the rule has none)


# ======================================================================= each outage session


@pytest.mark.parametrize("day", DOWN)
def test_nothing_is_bought_while_the_engine_is_down(drill, day):
    got = drill.log[day]
    assert [o for o in got["new_orders"] if o["side"] == "buy"] == []
    assert NEW not in got["positions"]
    assert all(got["positions"].get(s, 0) <= q for s, q in HELD.items())


@pytest.mark.parametrize("day", DOWN)
def test_no_multi_day_share_is_sold_except_by_its_own_stop(drill, day):
    got = drill.log[day]
    sells = [o for o in got["new_orders"] if o["side"] == "sell"]
    assert all(o["type"] == "stop" and o["time_in_force"] == "gtc" for o in sells), sells
    for o in got["wd"]:
        assert not set(o.get("closed", [])) & set(HELD)
    for p in got["passes"]:
        r = json.loads(p)
        assert r["closed"] == [] and r["sold"] == [] and not r.get("flattened_all")


def test_d1_puts_one_emergency_stop_10_pct_below_the_last_price_on_each_position(drill):
    d = drill
    got = d.log[D1]
    stops = [o for o in got["new_orders"] if o["type"] == "stop"]
    assert sorted((o["symbol"], float(o["qty"])) for o in stops) == [("B", 40.0), ("C", 30.0)]
    for o in stops:  # #585: the watchdog (at 90 s) and the dead-man (after 3 minutes) send the same id
        assert o["client_order_id"].startswith(f"kbdm-{MD}-{o['symbol']}-") and o["client_order_id"].endswith(".g0")
        # 10% below the last price the fail-safe saw, in the session's first minutes
        first = [_price(d, o["symbol"], D1, m) for m in range(0, 8)]
        assert min(first) * 0.9 - 0.02 <= float(o["stop_price"]) <= max(first) * 0.9 + 0.01, o
    assert got["stops_seen"] == {"B": 1, "C": 1}  # never two stops on one position


def test_d2_c_gaps_through_its_stop_and_fills_at_the_open_below_it(drill):
    d = drill
    got = d.log[D2]
    assert "C" not in got["positions"] and got["positions"]["B"] == 40
    (stop,) = [o for o in d.broker.orders.values() if o["symbol"] == "C" and o["type"] == "stop"]
    assert stop["status"] == "filled" and float(stop["filled_avg_price"]) < float(stop["stop_price"]) * 0.97
    assert [o for o in got["new_orders"] if o["symbol"] == "C"] == []  # no new stop on the flat position (#179)


def test_d3_b_s_stop_stays_where_d1_put_it(drill):
    d = drill
    (stop,) = d.log[D3]["resting"]["B"]
    assert d.log[D3]["new_orders"] == [] and d.log[D3]["stops_seen"]["B"] == 1
    assert stop["id"] == [o for o in d.log[D1]["new_orders"] if o["symbol"] == "B"][0]["id"]  # D1's stop
    last = _price(d, "B", D3, 389)
    first = _price(d, "B", D1, 0)
    assert last > first * 1.03 and float(stop["stop_price"]) < last * 0.87  # B rose; the stop was not trailed


@pytest.mark.parametrize("day", DOWN)
def test_the_alarm_is_raised_every_session(drill, day):
    got = drill.log[day]
    assert len(got["dm_halts"]) == 1 and got["dm_halts"][0].startswith("engine silent")
    if drill.laptop:
        assert [h["kind"] for h in got["halts"]] == ["watchdog"]
        assert "multi-day positions are kept" in got["halts"][0]["reason"]
    else:
        assert got["halts"] == []  # no watchdog: the database halt row is the alarm


# ======================================================================= the restart


def test_the_restart_reconciles_cleanly_and_books_c_s_stop_once(drill):
    d = drill
    assert d.at_restart["clean"] and d.at_restart["kill"] is None
    assert d.before_send["recon"] == [] and d.before_send["clean"] and d.before_send["kill"] is None
    assert d.before_send["orders"] == []  # nothing sent before the window: no stale D1 order
    fills = [b for *_, b in d.eng.journal.entries(kinds=("fill",)) if b["symbol"] == "C" and b["side"] == "sell"]
    assert len(fills) == 1 and fills[0]["qty"] == HELD["C"]
    adopted = [b for *_, b in d.eng.journal.entries(kinds=("stop_adopted",))]
    assert {b["symbol"] for b in adopted} >= {"B"}


def test_the_restart_sends_each_order_once_and_takes_the_fail_safe_stop_off(drill):
    d = drill
    o = d.md()
    late = [x for x in o if x["status"] != "canceled"]
    assert len({x["client_order_id"] for x in o}) == len(o)
    assert sorted((x["symbol"], x["side"]) for x in late) == [("C", "buy"), (NEW, "buy")]
    assert all(x["status"] == "filled" for x in late)
    assert not d.resting_stops("B")  # the rule has no stop: the fail-safe stop is cancelled before its orders
    assert [x for x in d.broker.orders.values() if x["type"] == "stop" and x["symbol"] == "B"
            and x["status"] == "canceled"]
    decisions = {b["session"] for *_, b in d.eng.journal.entries(kinds=("md_decision",))}
    assert D3 in decisions and not decisions & {D1, D2}  # the missed evenings are not decided after the fact


def test_after_the_restart_the_ledger_equals_the_broker(drill):
    d = drill
    b = d.eng.ledger.book(d.rid)
    venue = {s: float(p["qty"]) for s, p in d.broker.pos.items()}
    assert {s: q for s, q in b.qty_by_symbol().items() if q} == venue
    assert abs((b.cash - b.amount) - (d.broker.cash - b.broker_cash0)) < 0.005
    assert d.eng.recon.check("drill").diffs == [] and d.eng.account_clean and d.eng.kill is None
