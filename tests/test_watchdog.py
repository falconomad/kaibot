"""Story S16: the watchdog cancels entries when the engine goes quiet and flattens at 15:55; never opens."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
from test_engine import fake_candidate, make, synth_frames

from kaibot.engine.watchdog import Watchdog


def setup(tmp_path, minutes=58):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    fake_candidate(eng, at=(54,))
    for k in range(minutes):
        bar = open_utc + timedelta(minutes=k)
        broker.advance(pd.Timestamp(bar))
        clock.t = bar + timedelta(minutes=1, seconds=7)
        eng.tick()
    return eng, broker, clock, store, open_utc


def beat(store, ts):
    store.upsert("heartbeat", {"process": "engine", "ts": ts.isoformat(), "detail": {}}, "process")


def test_fresh_heartbeat_does_nothing(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path)
    beat(store, clock())
    before = {k: dict(v) for k, v in broker.orders.items()}
    out = Watchdog(broker, store, now=clock).step()
    assert out["state"] == "ok" and broker.orders == before and "A" in broker.pos


def test_stale_heartbeat_cancels_unfilled_entries_but_keeps_stops(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path, minutes=55)  # entry sent at 10:25, not yet filled
    pending = [o for o in broker.orders.values() if o["side"] == "buy" and o["status"] == "new"]
    assert pending
    beat(store, clock() - timedelta(minutes=5))
    out = Watchdog(broker, store, now=clock).step()
    assert out["state"] == "engine_stale" and out["canceled"] == ["A"]
    assert all(o["status"] == "canceled" for o in pending)
    assert any(h["kind"] == "watchdog" for h in store.tables["halt"])


def test_filled_position_keeps_its_stop_until_1555_then_flattened(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path)
    assert "A" in broker.pos
    beat(store, clock() - timedelta(minutes=5))
    wd = Watchdog(broker, store, now=clock)
    wd.step()
    o = next(o for o in broker.orders.values() if o["side"] == "buy")
    assert {leg["status"] for leg in o["legs"]} <= {"new", "held"}  # protection untouched
    assert "A" in broker.pos
    clock.t = open_utc + timedelta(minutes=385, seconds=10)  # 15:55:10
    out = wd.step()
    assert out["closed"] == ["A"]
    sells = [o for o in broker.orders.values() if o["side"] == "sell"]
    assert len(sells) == 1
    wd.step()  # once a day only
    assert len([o for o in broker.orders.values() if o["side"] == "sell"]) == 1


def test_unreadable_heartbeat_counts_as_stale(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path, minutes=55)

    def boom(*a, **k):
        raise RuntimeError("supabase down")

    store.select = boom
    out = Watchdog(broker, store, now=clock).step()
    assert out["state"] == "engine_stale"


def test_watchdog_never_opens(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path)
    called = []
    broker.submit_bracket = lambda *a, **k: called.append(a)
    broker.submit_oto_stop = lambda *a, **k: called.append(a)
    beat(store, clock() - timedelta(hours=1))
    wd = Watchdog(broker, store, now=clock)
    for k in [*range(60, 390, 15), 386]:
        clock.t = open_utc + timedelta(minutes=k)
        wd.step()
    assert called == []
    assert [o["symbol"] for o in broker.orders.values() if o["side"] == "sell"] == ["A"]


def test_closed_market_is_ignored(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path)
    clock.t = open_utc + timedelta(hours=8)
    assert Watchdog(broker, store, now=clock).step()["state"] == "market_closed"


def test_beats_while_the_market_is_closed(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path)
    clock.t = open_utc + timedelta(hours=8)
    beats = []
    store.heartbeat = lambda proc, d: beats.append((proc, d))
    Watchdog(broker, store, now=clock).step()
    assert beats and beats[0][0] == "watchdog" and beats[0][1]["state"] == "waiting"
