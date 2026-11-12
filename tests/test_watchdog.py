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


# --- S74c (#147): a flatten is done only when every close succeeded; until the dead-man time it is retried ---

from kaibot.engine.watchdog import DEADMAN_BEFORE_CLOSE, FLATTEN_BEFORE_CLOSE, POLL_S  # noqa: E402


def _stale_at_1555(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path)
    assert "A" in broker.pos
    beat(store, clock() - timedelta(minutes=5))
    wd = Watchdog(broker, store, now=clock)
    wd.step()
    clock.t = open_utc + timedelta(minutes=385, seconds=10)  # 15:55:10
    return wd, broker, clock, store, open_utc


def _failing_closes(broker, n):
    """The venue refuses the first `n` closes (a cancel still pending, a timeout), then accepts."""
    real, tries = broker.close_position, []

    def close(sym, *a, **k):
        tries.append(sym)
        if len(tries) <= n:
            raise RuntimeError("Alpaca 409: cancel not confirmed")
        return real(sym, *a, **k)
    broker.close_position = close
    return tries


def _flatten_events(store):
    return [e for e in store.tables.get("event", []) if e["type"] == "watchdog_flatten"]


def _payload(e):
    return e.get("payload") or e.get("detail") or {}


def test_the_dead_man_time_is_after_the_watchdog_flatten():
    assert timedelta(0) < DEADMAN_BEFORE_CLOSE < FLATTEN_BEFORE_CLOSE  # 15:56 ET, the pg_cron dead-man (0006)


def test_a_failed_close_is_not_recorded_as_flattened_and_is_retried(tmp_path):
    wd, broker, clock, store, open_utc = _stale_at_1555(tmp_path)
    tries = _failing_closes(broker, 1)
    out = wd.step()
    assert out["closed"] == [] and "A" in out["flatten_failed"]
    assert wd.flattened_on is None and "A" in broker.pos
    assert _payload(_flatten_events(store)[-1])["complete"] is False
    clock.t += timedelta(seconds=POLL_S)  # the next poll
    out = wd.step()
    assert out["closed"] == ["A"] and not out["flatten_failed"] and tries == ["A", "A"]
    assert wd.flattened_on == clock().date()
    assert _payload(_flatten_events(store)[-1])["complete"] is True
    clock.t += timedelta(seconds=POLL_S)
    wd.step()  # done: no third try
    assert tries == ["A", "A"]
    assert len([o for o in broker.orders.values() if o["side"] == "sell"]) == 1


def test_a_close_that_keeps_failing_is_retried_until_the_dead_man_time_only(tmp_path):
    wd, broker, clock, store, open_utc = _stale_at_1555(tmp_path)
    tries = _failing_closes(broker, 10**6)
    deadman = open_utc + timedelta(minutes=390) - DEADMAN_BEFORE_CLOSE  # 15:56:00
    while clock() < deadman + timedelta(minutes=3):
        wd.step()
        clock.t += timedelta(seconds=POLL_S)
    # 15:55:10, :25, :40, :55 are before 15:56; the dead-man takes over after that
    assert len(tries) == 4 and wd.flattened_on is None
    assert all(_payload(e)["complete"] is False for e in _flatten_events(store))


def test_a_positions_read_that_fails_is_retried(tmp_path):
    wd, broker, clock, store, open_utc = _stale_at_1555(tmp_path)
    real, n = broker.positions, []

    def positions():
        n.append(1)
        if len(n) == 1:
            raise RuntimeError("Alpaca 0: giving up on GET /v2/positions (deadline)")
        return real()
    broker.positions = positions
    out = wd.step()
    assert wd.flattened_on is None and out["flatten_failed"]
    clock.t += timedelta(seconds=POLL_S)
    out = wd.step()
    assert out["closed"] == ["A"] and wd.flattened_on == clock().date()


def test_a_watchdog_that_first_sees_the_stale_engine_after_the_dead_man_time_still_tries_once(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path)
    beat(store, clock() - timedelta(minutes=5))
    wd = Watchdog(broker, store, now=clock)
    tries = _failing_closes(broker, 10**6)
    clock.t = open_utc + timedelta(minutes=387)  # 15:57, after the dead-man
    wd.step()
    clock.t += timedelta(seconds=POLL_S)
    wd.step()
    assert tries == ["A"]
