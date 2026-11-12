"""Story S74b (#137): the ticks after a run-drawdown halt has ended the run.

`_risk_halts` ends the run (`self.run = None`) after flattening. Until the closes are booked the engine still holds
the positions, so the next tick ran `_risk_halts` again and raised on `self.run["id"]`: a loop error every minute,
three of them trip the kill switch. Now the engine idles cleanly: no exception, no error streak, the heartbeat keeps
going, the broker-held stops and the exit orders are left alone, the halt is written once and the status names the
run that ended. Fakes only (no network, no broker); the loop is `Sim` from test_outage_drill.
"""

from __future__ import annotations

from test_outage_drill import buys, start, stop_leg


def errors(sim) -> list[str]:
    return [d["error"] for _, d in sim.beats if d.get("state") == "error"]


def end_by_drawdown(sim, eng, at: int = 61):
    """A is open with its broker-held stop; at minute `at` the run's peak is far above today's equity, so the
    run-drawdown rule flattens the book and ends the run."""
    sim.run_until(sim.at(60, 2))
    assert eng.pos["A"].status == "open" and eng.run is not None
    rid = eng.run["id"]
    eng.peak = 10 * eng.day.start_equity
    sim.run_until(sim.at(at, 9))  # the tick of minute at-1 (10:30:07+) ends the run
    assert eng.run is None, "the drawdown rule must have ended the run"
    return rid


def test_the_tick_after_a_run_drawdown_end_does_not_raise(tmp_path):
    sim, eng, broker, store = start(tmp_path)
    real = broker.close_position
    held: list[str] = []

    def close_later(symbol):  # the venue takes the close but does not fill it yet: the engine still holds A
        held.append(symbol)
        o = real(symbol)
        o["status"] = "accepted"
        return o

    broker.close_position = close_later
    rid = end_by_drawdown(sim, eng)
    assert held == ["A"] and "A" in eng.pos
    n_beats = len(sim.beats)
    sim.run_until(sim.at(66))
    assert errors(sim) == [], errors(sim)
    assert eng.error_streak == 0 and eng.kill is None
    assert sim.status["state"] == "managing" and sim.status["run_id"] is None
    assert sim.status["run_ended"] == rid
    assert len(sim.beats) > n_beats and sim.beats[-1][1]["state"] != "error"  # the heartbeat keeps going
    assert [h["kind"] for h in store.tables["halt"]].count("run_drawdown") == 1  # written once, not every minute
    assert held == ["A"], "the exit order already out is not sent again"
    run = next(r for r in store.tables["run"] if r["id"] == rid)
    assert run["status"] == "ended" and run["ended_on"]


def test_after_the_run_ended_the_engine_idles_once_flat(tmp_path):
    sim, eng, broker, store = start(tmp_path)
    rid = end_by_drawdown(sim, eng)
    sim.run_until(sim.at(70))
    assert errors(sim) == [] and eng.error_streak == 0 and eng.kill is None
    assert not [p for p in eng.pos.values() if p.status == "open"]
    assert sim.status["state"] in ("idle", "managing") and sim.status.get("run_id") is None
    assert len(buys(broker)) == 1  # no entry after the run ended
    assert [h["kind"] for h in store.tables["halt"]].count("run_drawdown") == 1
    assert next(r for r in store.tables["run"] if r["id"] == rid)["status"] == "ended"


def test_after_the_run_ended_a_protective_stop_is_left_at_the_broker(tmp_path):
    """The first close attempt fails, so flatten_all raises and the run is not ended (#114): the stop stays at the
    broker. The loop's retry closes A and ends the run; the ticks after that send and cancel nothing."""
    sim, eng, broker, store = start(tmp_path)
    sim.run_until(sim.at(60, 2))
    leg = dict(stop_leg(broker))
    eng.peak = 10 * eng.day.start_equity
    real, calls = broker.close_position, []

    def fails_once(symbol):
        calls.append(symbol)
        if len(calls) == 1:
            raise ConnectionError("close failed once")
        return real(symbol)

    broker.close_position = fails_once
    sim.run_until(sim.at(60, 12))  # 10:30:10: the close fails
    assert eng.run is not None and stop_leg(broker) == leg  # not flat: the run goes on, protection untouched
    sim.run_until(sim.at(60, 22))  # 10:30:20: the retry closes A and ends the run
    assert eng.run is None and len(calls) == 2
    n_orders = len(broker.orders)
    sim.run_until(sim.at(66))
    assert len(errors(sim)) == 1 and eng.error_streak == 0 and eng.kill is None
    assert len(broker.orders) == n_orders and len(calls) == 2  # nothing new sent after the run ended
