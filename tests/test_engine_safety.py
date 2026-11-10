"""Fail-closed behaviour (stories S11, S16, S18): injected faults must stop new entries, never add risk.

Each test injects one fault into the synthetic day from test_engine.py and checks that the engine halts entries,
records why, and leaves broker-held protection alone."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from test_engine import fake_candidate, make, run_day, synth_frames

import kaibot.engine.alpaca as alp


def step(eng, broker, clock, open_utc, k0, k1):
    for k in range(k0, k1):
        bar = open_utc + timedelta(minutes=k)
        broker.advance(pd.Timestamp(bar))
        clock.t = bar + timedelta(minutes=1, seconds=7)
        eng.tick()


# ---------- S11: broker-held protection survives a dead engine ----------
def test_bracket_legs_are_good_till_cancelled(monkeypatch):
    """With day legs, a dead engine at 16:00 leaves the position open overnight with NO stop. GTC keeps it."""
    sent = {}

    def call(self, method, url, params=None, body=None):
        sent.update(body or {})
        return {"id": "o1", "status": "accepted"}

    monkeypatch.setattr(alp._Http, "call", call)
    alp.AlpacaPaper("k", "s").submit_bracket("kb-1-2-1", "AAPL", 3, 190.0, 200.0)
    assert sent["order_class"] == "bracket" and sent["time_in_force"] == "gtc"
    assert sent["stop_loss"] == {"stop_price": "190.00"} and sent["take_profit"] == {"limit_price": "200.00"}


# ---------- S16: unknown orders and repeated errors ----------
def test_unknown_open_order_blocks_entries(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    broker.orders["x1"] = {"id": "x1", "client_order_id": "kb-9-99-1", "symbol": "MSFT", "qty": 5, "side": "buy",
                           "type": "market", "status": "new", "legs": []}
    broker.data.frames.setdefault("MSFT", broker.data.frames["A"].iloc[0:0])
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc, until=80)
    assert not eng.account_clean and "trade" not in store.tables
    ev = [e for e in store.tables["event"] if e["type"] == "account_not_clean"]
    assert ev and "MSFT" in str(ev[-1]["payload"])


def test_repeated_tick_errors_trip_the_kill_switch(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    fake_candidate(eng)
    step(eng, broker, clock, open_utc, 0, 30)
    for _ in range(3):
        eng.on_tick_error(RuntimeError("data feed down"))
    assert eng.kill == "engine_errors"
    step(eng, broker, clock, open_utc, 30, 80)
    assert "trade" not in store.tables
    assert any(h["kind"] == "engine_errors" for h in store.tables["halt"])
    gate = store.tables.get("gate_run", [])
    assert all(not g["approved"] for g in gate)


def test_one_error_then_recovery_does_not_halt(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    fake_candidate(eng)
    eng.on_tick_error(RuntimeError("blip"))
    eng.on_tick_ok()
    eng.on_tick_error(RuntimeError("blip"))
    assert eng.kill is None
    run_day(eng, broker, clock, open_utc, until=80)
    assert len(store.tables["trade"]) == 1


def test_three_daily_stops_in_ten_sessions_pause_the_run(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    rid = store.active_run()["id"]
    for d in ("2026-07-27", "2026-07-29", "2026-08-03"):
        store.insert("halt", {"run_id": rid, "kind": "daily_stop", "session": d, "reason": "test"})
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc, until=80)
    assert store.tables["run"][0]["status"] == "paused" and "trade" not in store.tables
    assert any(h["kind"] == "three_stops" for h in store.tables["halt"])


def test_old_daily_stops_do_not_pause(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    rid = store.active_run()["id"]
    for d in ("2026-06-01", "2026-06-02", "2026-08-03"):
        store.insert("halt", {"run_id": rid, "kind": "daily_stop", "session": d, "reason": "test"})
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc, until=80)
    assert store.tables["run"][0]["status"] == "running" and len(store.tables["trade"]) == 1


def test_stale_event_calendar_blocks_entries(tmp_path):
    """If nobody updated config/macro_calendar.yaml, FOMC days are unknown: fail closed."""
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    eng.calendar_until = date(2026, 7, 31)
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc, until=80)
    assert "trade" not in store.tables
    assert any(e["type"] == "calendar_stale" for e in store.tables["event"])


# ---------- S18: ledger drift ----------
def test_position_drift_halts_entries(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    fake_candidate(eng, at=(54, 104))
    step(eng, broker, clock, open_utc, 0, 60)
    assert eng.pos["A"].status == "open"
    broker.pos["A"]["qty"] = str(int(float(broker.pos["A"]["qty"])) + 7)  # someone traded the account by hand
    step(eng, broker, clock, open_utc, 60, 70)
    assert eng.kill == "drift"
    assert any(h["kind"] == "drift" for h in store.tables["halt"])
    step(eng, broker, clock, open_utc, 70, 120)
    assert len(store.tables["trade"]) == 1  # the 11:15 candidate was not taken


def test_buying_power_is_the_smaller_of_pot_and_broker(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames(), amount=100_000.0)
    broker.buying_power = 600.0  # the paper account has less than the pot
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc, until=60)
    t = store.tables["trade"][0]
    assert t["qty"] * t["entry_px"] <= 600.0 + 1e-6


def test_a_one_minute_difference_is_not_drift(tmp_path):
    """A stop that fills between the order sync and the positions read must not halt the session."""
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    fake_candidate(eng, at=(54,))
    step(eng, broker, clock, open_utc, 0, 60)
    real = broker.positions
    broker.positions = lambda: {k: v for k, v in real().items() if k != "A"}  # one stale read
    eng._check_drift()
    broker.positions = real
    step(eng, broker, clock, open_utc, 60, 62)
    assert eng.kill is None


# ---------- S15: event gates ----------
def test_event_calendar_loads_and_blacks_out_30_minutes_each_side(tmp_path):
    from kaibot.engine.events import in_blackout, load_calendar

    events, covers = load_calendar(__import__("kaibot.engine.config", fromlist=["ROOT"]).ROOT
                                   / "config" / "macro_calendar.yaml")
    assert covers >= date(2027, 12, 31)
    fomc = date(2026, 12, 9)
    assert in_blackout(events, fomc, 239) is None and in_blackout(events, fomc, 240) == "fomc_statement_1400"
    assert in_blackout(events, fomc, 300) and in_blackout(events, fomc, 301) is None
    assert all(in_blackout(events, date(2026, 11, 10), m) is None for m in range(390))  # CPI is pre-open
    bad = tmp_path / "cal.yaml"
    bad.write_text("fomc_statment_1400: [2026-01-01]\n")
    import pytest

    with pytest.raises(ValueError):
        load_calendar(bad)


def test_intraday_event_blocks_entries_in_its_window(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    eng.events = {date(2026, 8, 4): [("test_event", 60)]}  # 10:30 ET -> no entries 10:00-11:00
    fake_candidate(eng, at=(54, 94))
    run_day(eng, broker, clock, open_utc, until=100)
    trades = store.tables.get("trade", [])
    assert len(trades) == 1 and store.tables["signal"][0]["ts"] >= open_utc + timedelta(minutes=95)
