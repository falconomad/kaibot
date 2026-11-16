"""Issue #238: the ledger books the fee the venue charged, never an estimate, so its cash follows the broker's to the
cent however long the run.

Before the fix, the exit paths (`Engine._on_exit_fill`, `Engine._sync_md_exits`) booked `shadow_costs.sell_fees`
(cost model v1's placeholder rates) whatever the venue charged: +$0.43 a $15k sale at 0 bp, +$0.10 on cost model
v2. The difference never resets, so it crossed the reconcile tolerance ($50 at $100k) after about 140 (0 bp) or 550
(v2) sessions and blocked entries for the rest of the run.

1. A SimBroker run of the engine at 0 bp and on cost model v2: the drift (broker cash change minus ledger cash
   change) is 0 at every close and nothing is blocked; every sale's booked fee is the venue's.
2. `Engine._venue_fees`: the fees of the order's FILL activities; Alpaca's carry none (its regulatory fees are
   separate FEE activities) and give 0; a failed read gives 0 and says so.
3. A multi-day sleeve sold by a flatten books the venue's fee."""

from __future__ import annotations

import math
import sys
import tempfile
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kaibot.engine.core import Engine
from kaibot.engine.journal import Journal, JournalStore
from kaibot.engine.runner import run_sessions, simulation
from kaibot.replay.data import ET, N_MIN, from_frame
from kaibot.replay.sim_costs import adv_table, costs_for
from kaibot.risk.profiles import load_profiles
from kaibot.strategies import registry

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import spy_engine as se  # noqa: E402

FIRST, N_SESSIONS, TEST = "2016-01-04", 50, 35


def make_frame(seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows, px = [], 200.0
    for d in pd.bdate_range(FIRST, periods=N_SESSIONS):
        px *= math.exp(rng.normal(0, 0.004))
        drift = rng.choice([-1.0, 0.0, 1.0]) * rng.uniform(0.00002, 0.00006)
        c = px * np.exp(np.cumsum(drift + rng.normal(0, 0.0004, N_MIN)))
        o = np.r_[px, c[:-1]]
        ts = pd.Timestamp(f"{d.date()} 09:30", tz=ET).tz_convert(UTC) + pd.to_timedelta(np.arange(N_MIN), "min")
        rows.append(pd.DataFrame({"ts": ts, "o": o, "h": np.maximum(o, c) * 1.0002, "l": np.minimum(o, c) * 0.9998,
                                  "c": c, "v": rng.uniform(5e4, 2e5, N_MIN), "vw": (o + c) / 2}))
        px = float(c[-1])
    return pd.concat(rows, ignore_index=True)


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return make_frame()


def _trace(frame: pd.DataFrame, scenario: str) -> tuple[list[dict], list[dict], list[dict]]:
    """The engine through TEST sessions; per close: drift, blocked. Plus the ledger's sale entries and the venue's
    fills (with their fees)."""
    sd = from_frame("SPY", frame)
    days = [str(d) for d in sd.dates][-TEST:]
    costs = costs_for(scenario, {"SPY": adv_table(frame)})
    profiles = load_profiles()
    start = pd.Timestamp(f"{days[0]} 09:00", tz=ET).tz_convert(UTC).to_pydatetime() - timedelta(minutes=30)
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        store = JournalStore(Journal(Path(tmp) / "journal.sqlite"))
        sim = simulation({"SPY": frame}, {}, "SPY", store=store, policies=profiles, start=start,
                         cache_dir=Path(tmp) / "h", vix=pd.Series(dtype=float), fomc=set(), costs=costs,
                         cash=100_000.0, calendar=se.calendar_of(sd), strategies={lv: [] for lv in profiles})
        costs.clock = sim.clock
        eng = sim.engine
        eng.shadow_only = ()
        strat = registry.research_intraday()["spy_noise_band"]
        eng.live_strategies = lambda: (strat,)
        store.insert("command", {"kind": "new_run", "payload": {"amount_usd": 100_000.0, "level": "balanced"},
                                 "idempotency_key": f"drift-{scenario}", "applied_at": None})
        eng.startup()
        eng.apply_commands()
        for day in days:
            log = run_sessions(eng, sim.broker, sim.clock, [day], strict=True)
            assert not log.errors
            b = eng.ledger.book(eng._rid())
            drift = (float(sim.broker.account()["cash"]) - b.broker_cash0) - (b.cash - b.amount)
            out.append({"day": day, "drift": drift, "blocked": not eng._reconciled()})
        sales = [e[3] for e in eng.journal.entries(kinds=("fill",)) if e[3]["side"] == "sell"]
        venue = [f for f in sim.broker.fills if f["side"] == "sell"]
        store.journal.close()
    return out, sales, venue


@pytest.mark.parametrize("scenario", ["0", "v2"])
def test_the_ledger_cash_follows_the_broker_to_the_cent_at_every_close(frame, scenario):
    trace, sales, venue = _trace(frame, scenario)
    assert len(sales) >= 15  # the rule traded: about one round trip a session
    assert max(abs(r["drift"]) for r in trace) < 1e-6, [r for r in trace if abs(r["drift"]) >= 1e-6][:3]
    assert not any(r["blocked"] for r in trace)
    assert [round(s["fees"], 10) for s in sales] == [round(float(f["fees"]), 10) for f in venue]
    if scenario == "v2":  # the venue charges the 2026 fees, rounded up to the cent: those are booked
        assert all(s["fees"] > 0 and round(s["fees"], 2) == s["fees"] for s in sales)
    else:
        assert all(s["fees"] == 0 for s in sales)


# ---------------------------------------------------------------- the fee lookup


class _Broker:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail, self.calls = rows or [], fail, []

    def activities(self, after=None, types=("FILL",)):
        self.calls.append((after, types))
        if self.fail:
            raise RuntimeError("activities down")
        return list(self.rows)


def _engine(broker) -> Engine:
    eng = Engine.__new__(Engine)  # only the attributes _venue_fees reads
    eng.broker = broker
    eng.store = types.SimpleNamespace(events=[], event=lambda kind, body, rid=None: eng.store.events.append(
        (kind, body)))
    eng.run = {"id": 7}
    eng.journal = Journal()  # in memory: the flatten's sale bookkeeping (#252) reads and writes it
    return eng


T = "2026-11-18T20:59:00+00:00"


def test_the_fee_is_the_sum_of_the_orders_fill_activities_after_a_minute_before_the_fill():
    b = _Broker([{"order_id": "o1", "fees": 0.21}, {"order_id": "o2", "fees": 5.0}, {"order_id": "o1", "fees": "0.1"}])
    assert _engine(b)._venue_fees("o1", T) == pytest.approx(0.31)
    assert b.calls == [(datetime.fromisoformat(T) - timedelta(minutes=1), ("FILL",))]


def test_alpaca_fill_activities_carry_no_fee_so_none_is_booked_on_the_fill():
    b = _Broker([{"order_id": "o1", "price": "100", "qty": "10", "side": "sell"}])  # Alpaca's shape: no `fees`
    assert _engine(b)._venue_fees("o1", T) == 0.0


def test_a_failed_read_books_no_fee_and_says_so():
    eng = _engine(_Broker(fail=True))
    assert eng._venue_fees("o1", T) == 0.0
    assert eng.store.events and eng.store.events[0][0] == "venue_fee_unread"
    assert _engine(_Broker([{"order_id": "o1", "fees": 1.0}]))._venue_fees("o1", None) == 1.0  # no time: all


# ---------------------------------------------------------------- the multi-day sale


def test_a_multi_day_sleeve_sold_by_a_flatten_books_the_venues_fee():
    booked = []
    eng = _engine(_Broker([{"order_id": "x1", "fees": 0.33}]))
    eng._md_exits = {"x1": {"run_id": 7, "keys": [(("trend_momentum", "B"), 40)], "reason": "flatten"}}
    eng.broker.order = lambda oid: {"status": "filled", "filled_avg_price": "101.5", "filled_at": T,
                                    "client_order_id": "c1"}
    eng._book = lambda kind, body: booked.append((kind, body))
    eng._rid = lambda: 7
    eng._sync_md_exits()
    got = [(k, {f: v for f, v in b.items() if f != "order_id"}) for k, b in booked]  # #252 adds the order id
    assert got == [("fill", {"run_id": 7, "strategy": "trend_momentum", "symbol": "B", "side": "sell",
                             "qty": 40, "px": 101.5, "fees": 0.33, "coid": "c1", "ts": T,
                             "reason": "flatten"})]


# ---------------------------------------------------------------- QA-Ops second key (#241): every part, once


def _sim_sale(**script):
    """A SimBroker on cost model v1 (it charges a sale fee) that bought 100 A and sold them at market."""
    from test_sim_broker import bar, buy, venue

    from kaibot.broker import OrderRequest
    from kaibot.broker.sim import COSTS_V1

    b, _ = venue(costs=COSTS_V1)
    buy(b, 100)
    b.advance(bar(30))
    for kind, arg in script.items():
        getattr(b.script, kind)("A", *arg)
    s = b.submit(OrderRequest("A", "sell", 100, "market", "day"))
    for m in (31, 32, 33):
        b.advance(bar(m))
    assert s["status"] == "filled"
    return b, s


def test_a_partly_filled_sale_books_the_fee_of_every_part_not_only_the_last_minute():
    b, s = _sim_sale(fills=([40, 30, 30],))
    parts = [f for f in b.fills if f["order_id"] == s["id"]]
    assert len(parts) == 3 and all(f["fees"] > 0 for f in parts)
    got = _engine(b)._venue_fees(s["id"], s["filled_at"], s["submitted_at"])
    assert got == pytest.approx(sum(f["fees"] for f in parts))
    assert _engine(b)._venue_fees(s["id"], s["filled_at"]) < got  # from the last fill only: the first parts missed


def test_an_activity_the_venue_reports_twice_is_booked_once():
    b, s = _sim_sale(duplicate=())
    rows = [f for f in b.fills if f["order_id"] == s["id"]]
    assert len(rows) == 2 and rows[0]["id"] == rows[1]["id"]  # the same activity twice
    assert _engine(b)._venue_fees(s["id"], s["filled_at"], s["submitted_at"]) == pytest.approx(rows[0]["fees"])


def test_the_order_sync_passes_the_orders_sending_time_on_every_exit_path():
    """The sale sent by the engine (time / daily stop / flatten / manual) and a bracket's leg: the fee lookup gets
    the order's `submitted_at`, so it reaches back to the first part."""
    calls = []
    eng = _engine(_Broker())
    eng._md_exits = {}
    eng._on_exit_fill = lambda p, px, reason, ts, oid=None, since=None: calls.append((reason, oid, since))
    sent = types.SimpleNamespace(status="open", exit_order_id="x1", order_id="e1", exit_reason="time")
    legged = types.SimpleNamespace(status="open", exit_order_id=None, order_id="e2", armed=False)
    orders = {"x1": {"id": "x1", "status": "filled", "filled_avg_price": "100", "filled_at": T,
                     "submitted_at": "2026-11-18T20:55:00+00:00"},
              "e2": {"id": "e2", "status": "filled", "submitted_at": "2026-11-18T14:31:00+00:00",
                     "legs": [{"id": "e2s", "type": "stop", "status": "filled", "filled_avg_price": "99",
                               "filled_at": T}]}}
    eng.broker.order = orders.__getitem__
    eng.pos = types.SimpleNamespace(values=lambda: [sent, legged])
    eng._sync_orders()
    assert calls == [("time", "x1", "2026-11-18T20:55:00+00:00"), ("stop", "e2s", "2026-11-18T14:31:00+00:00")]


def test_the_reconciliation_still_catches_a_real_cash_difference(frame):
    """The fix books what the venue reports; cash the venue takes without reporting it on a fill (an Alpaca FEE
    activity, a fee the engine never read) still shows: $40 stays inside the $50 tolerance at $100k, $60 more
    blocks entries at the next check."""
    sd = from_frame("SPY", frame)
    days = [str(d) for d in sd.dates][-4:]
    costs = costs_for("0", {"SPY": adv_table(frame)})
    profiles = load_profiles()
    start = pd.Timestamp(f"{days[0]} 09:00", tz=ET).tz_convert(UTC).to_pydatetime() - timedelta(minutes=30)
    blocked = []
    with tempfile.TemporaryDirectory() as tmp:
        store = JournalStore(Journal(Path(tmp) / "journal.sqlite"))
        sim = simulation({"SPY": frame}, {}, "SPY", store=store, policies=profiles, start=start,
                         cache_dir=Path(tmp) / "h", vix=pd.Series(dtype=float), fomc=set(), costs=costs,
                         cash=100_000.0, calendar=se.calendar_of(sd), strategies={lv: [] for lv in profiles})
        costs.clock = sim.clock
        eng = sim.engine
        eng.shadow_only = ()
        strat = registry.research_intraday()["spy_noise_band"]
        eng.live_strategies = lambda: (strat,)
        store.insert("command", {"kind": "new_run", "payload": {"amount_usd": 100_000.0, "level": "balanced"},
                                 "idempotency_key": "drift-recon", "applied_at": None})
        eng.startup()
        eng.apply_commands()
        for day, taken in zip(days, (0.0, 40.0, 60.0, 0.0), strict=True):
            sim.broker.cash -= taken  # after the previous close: the venue takes cash the engine is not told of
            assert not run_sessions(eng, sim.broker, sim.clock, [day], strict=True).errors
            blocked.append(not eng._reconciled())
        store.journal.close()
    assert blocked[:2] == [False, False]  # nothing, then $40: inside the tolerance
    assert blocked[2:] == [True, True], blocked  # $100 in all: blocked from the next check on
