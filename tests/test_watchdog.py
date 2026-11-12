"""Story S16: the watchdog cancels entries when the engine goes quiet and flattens at 15:55; never opens."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest
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
    # 15:55:10 and :25 leave CLOSE_BUDGET_S before 15:56; :40 and :55 do not (S74d, #154): the dead-man takes over
    assert len(tries) == 2 and wd.flattened_on is None
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


# --- S74d (#154): early closes, a flatten pass that stops at the dead-man time, a failed open-orders read ---

from kaibot.broker.alpaca import CANCEL_WAIT_S, ORDER_DEADLINE_S  # noqa: E402
from kaibot.engine.watchdog import CLOSE_BUDGET_S  # noqa: E402
from kaibot.replay.data import ET  # noqa: E402


def _early_close(broker, day="2026-08-04", clock_says_close="13:00"):
    """The broker calendar says the session ends at 13:00 ET. The clock reports `clock_says_close`."""
    o = pd.Timestamp(f"{day} 09:30", tz=ET)
    cal_close = pd.Timestamp(f"{day} 13:00", tz=ET)
    clk_close = pd.Timestamp(f"{day} {clock_says_close}", tz=ET)
    calls = []

    def clock():
        now = pd.Timestamp(broker.clk()).tz_convert(ET)
        return {"is_open": bool(o <= now < cal_close), "next_open": (o + pd.Timedelta(days=1)).isoformat(),
                "next_close": clk_close.isoformat(), "timestamp": now.isoformat()}

    def calendar(d):
        calls.append(d)
        return {"date": d, "open": "09:30", "close": "13:00"}
    broker.clock, broker.calendar = clock, calendar
    return calls


def _stale(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path)
    assert "A" in broker.pos
    beat(store, clock() - timedelta(minutes=5))
    return Watchdog(broker, store, now=clock), broker, clock, store, open_utc


def _until(wd, clock, end):
    while clock() < end:
        wd.step()
        clock.t += timedelta(seconds=POLL_S)


def test_the_close_budget_covers_one_close_on_the_order_path():
    assert CLOSE_BUDGET_S == ORDER_DEADLINE_S + CANCEL_WAIT_S
    assert (FLATTEN_BEFORE_CLOSE - DEADMAN_BEFORE_CLOSE).total_seconds() > CLOSE_BUDGET_S


@pytest.mark.parametrize("clock_says_close", ["13:00", "16:00"])
def test_early_close_flattens_at_1255_and_retries_until_1256(tmp_path, clock_says_close):
    # the broker calendar's 13:00 wins even if the clock still says 16:00 (the earlier close decides)
    wd, broker, clock, store, open_utc = _stale(tmp_path)
    cal_calls = _early_close(broker, clock_says_close=clock_says_close)
    tries = _failing_closes(broker, 10**6)
    clock.t = open_utc + timedelta(minutes=205, seconds=10)  # 12:55:10
    _until(wd, clock, open_utc + timedelta(minutes=215))  # to 13:05
    # 12:55:10 and :25 leave CLOSE_BUDGET_S before 12:56; then the early-close dead-man (0017) takes over
    assert len(tries) == 2 and wd.flattened_on is None
    assert all("not started" in v for v in _payload(_flatten_events(store)[-1])["failed"].values())
    assert cal_calls == ["2026-08-04"]  # read once a day


def test_early_close_flatten_completes_at_1255(tmp_path):
    wd, broker, clock, store, open_utc = _stale(tmp_path)
    _early_close(broker)
    clock.t = open_utc + timedelta(minutes=204)  # 12:54: not yet
    assert wd.step()["closed"] == []
    clock.t = open_utc + timedelta(minutes=205, seconds=10)
    assert wd.step()["closed"] == ["A"] and wd.flattened_on == clock().date()


def test_a_calendar_read_that_fails_falls_back_to_the_clock_and_is_read_again(tmp_path):
    wd, broker, clock, store, open_utc = _stale(tmp_path)
    _early_close(broker)  # the clock says 13:00 too
    n = []

    def calendar(d):
        n.append(d)
        raise RuntimeError("Alpaca 0: giving up on GET /v2/calendar (deadline)")
    broker.calendar = calendar
    clock.t = open_utc + timedelta(minutes=205, seconds=10)
    assert wd.step()["closed"] == ["A"]
    assert len(n) == 1
    clock.t += timedelta(seconds=POLL_S)
    wd.step()
    assert len(n) == 2  # not cached while it fails


def test_a_market_holiday_row_does_not_move_the_close(tmp_path):
    wd, broker, clock, store, open_utc = _stale(tmp_path)
    broker.calendar = lambda d: None  # no session row: the clock decides
    clock.t = open_utc + timedelta(minutes=385, seconds=10)
    assert wd.step()["closed"] == ["A"]


def test_the_happy_path_does_not_read_the_calendar(tmp_path):
    eng, broker, clock, store, open_utc = setup(tmp_path)
    beat(store, clock())
    calls = []
    broker.calendar = lambda d: calls.append(d)
    clock.t = open_utc + timedelta(minutes=385, seconds=10)
    beat(store, clock())
    assert Watchdog(broker, store, now=clock).step()["state"] == "ok"
    assert calls == []


def _slow_closes(broker, clock, seconds, fail=True):
    """Every close takes `seconds` on the fake clock (a blackholed venue) and fails."""
    started = []

    def close(sym, *a, **k):
        started.append((sym, clock()))
        clock.t += timedelta(seconds=seconds)
        if fail:
            raise RuntimeError("Alpaca 0: giving up (deadline)")
    broker.close_position = close
    return started


def test_a_flatten_pass_does_not_start_a_close_that_could_run_past_the_dead_man(tmp_path):
    wd, broker, clock, store, open_utc = _stale(tmp_path)
    syms = {s: {"symbol": s} for s in ("A", "B", "C", "D", "E")}
    broker.positions = lambda: dict(syms)
    started = _slow_closes(broker, clock, ORDER_DEADLINE_S)
    deadman = open_utc + timedelta(minutes=390) - DEADMAN_BEFORE_CLOSE  # 15:56:00
    clock.t = open_utc + timedelta(minutes=385, seconds=10)  # 15:55:10
    out = wd.step()
    # 15:55:10 and 15:55:30 fit (each ends by 15:55:55 at the latest); 15:55:50 + 25 s would not
    assert [s for s, _ in started] == ["A", "B"]
    assert all(t + timedelta(seconds=CLOSE_BUDGET_S) <= deadman for _, t in started)
    assert set(out["flatten_failed"]) == set(syms) and wd.flattened_on is None
    assert "dead-man" in out["flatten_failed"]["C"]
    _until(wd, clock, deadman + timedelta(minutes=3))
    assert len(started) == 2  # no pass after it and no close started past 15:56 - 25 s
    assert clock() > deadman


def test_a_flatten_pass_never_runs_past_the_dead_man_with_a_blackholed_venue(tmp_path):
    wd, broker, clock, store, open_utc = _stale(tmp_path)
    broker.positions = lambda: {s: {"symbol": s} for s in "ABCDEFGHIJ"}
    started = _slow_closes(broker, clock, CLOSE_BUDGET_S)  # every close takes its whole budget
    deadman = open_utc + timedelta(minutes=390) - DEADMAN_BEFORE_CLOSE
    clock.t = open_utc + timedelta(minutes=385, seconds=10)
    ends = []
    while clock() < deadman + timedelta(minutes=2):
        wd.step()
        ends.append(clock())
        clock.t += timedelta(seconds=POLL_S)
    assert started and all(t + timedelta(seconds=CLOSE_BUDGET_S) <= deadman for _, t in started)
    assert max(t for _, t in started) + timedelta(seconds=CLOSE_BUDGET_S) <= deadman


def test_a_late_watchdog_keeps_its_one_pass_without_the_budget(tmp_path):
    wd, broker, clock, store, open_utc = _stale(tmp_path)
    broker.positions = lambda: {"A": {"symbol": "A"}, "B": {"symbol": "B"}}
    started = _slow_closes(broker, clock, 1)
    clock.t = open_utc + timedelta(minutes=387)  # 15:57: the dead-man has run, this is a backstop
    wd.step()
    assert [s for s, _ in started] == ["A", "B"]


def test_a_failed_open_orders_read_does_not_skip_the_flatten(tmp_path):
    wd, broker, clock, store, open_utc = _stale(tmp_path)
    real = broker.open_orders

    def open_orders():
        if clock() < open_utc + timedelta(minutes=386):
            raise RuntimeError("Alpaca 0: giving up on GET /v2/orders (deadline)")
        return real()
    broker.open_orders = open_orders
    clock.t = open_utc + timedelta(minutes=385, seconds=10)
    out = wd.step()
    assert "entry_read_failed" in out and out["canceled"] == []
    assert out["closed"] == ["A"] and wd.flattened_on == clock().date()
    assert any(h["kind"] == "watchdog" for h in store.tables["halt"])  # the trip is still recorded
    assert _payload(_flatten_events(store)[-1])["complete"] is True
