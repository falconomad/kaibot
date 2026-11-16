"""Issue #220: `engine.runner.SimMarket`, the faster read of stored bars in a SimBroker run, changes nothing.

1. The engine run of scripts/spy_engine.py with the old read (`simulation(fast=False)`, plain `LiveMarket`) and the
   new one gives the same journal, entry by entry and byte for byte, and the same fills and daily rows.
2. Point-in-time: at every minute the new read returns exactly what `LiveMarket` returns, and a session whose later
   bars are different (every bar from minute m + 1 on) gives the same arrays at minute m: the session's whole row,
   read once, never shows a later bar.
3. The live engine never builds a `SimMarket`."""

from __future__ import annotations

import json
import math
import sys
import types
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kaibot.engine import journal as journal_mod
from kaibot.engine import runner
from kaibot.engine import shadow as shadow_mod
from kaibot.engine import store as store_mod
from kaibot.engine.clock import SimClock
from kaibot.engine.market import LiveMarket
from kaibot.evaluation import gate_report, gate_v2
from kaibot.replay.data import ET, N_MIN, from_frame
from kaibot.replay.history import HistoricalData
from kaibot.replay.sim_costs import adv_table

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import spy_engine as se  # noqa: E402

FIRST, N_SESSIONS, TEST = "2017-03-01", 36, 20
EARLY = "2017-04-13"  # a 13:00 close inside the test window
GAP = range(100, 104)  # minutes without a trade on every session (the close is carried, zero volume)


def make_frame(seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows, px = [], 230.0
    for d in pd.bdate_range(FIRST, periods=N_SESSIONS):
        n_min = 210 if str(d.date()) == EARLY else N_MIN
        px *= math.exp(rng.normal(0, 0.004))
        drift = rng.choice([-1.0, 0.0, 1.0]) * rng.uniform(0.00002, 0.00006)
        c = px * np.exp(np.cumsum(drift + rng.normal(0, 0.0004, n_min)))
        o = np.r_[px, c[:-1]]
        ts = (pd.Timestamp(f"{d.date()} 09:30", tz=ET).tz_convert(UTC) + pd.to_timedelta(np.arange(n_min), "min"))
        f = pd.DataFrame({"ts": ts, "o": o, "h": np.maximum(o, c) * 1.0002, "l": np.minimum(o, c) * 0.9998, "c": c,
                          "v": rng.uniform(5e4, 2e5, n_min), "vw": (o + c) / 2})
        rows.append(f[~np.isin(np.arange(n_min), GAP)])
        px = float(c[-1])
    return pd.concat(rows, ignore_index=True)


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return make_frame()


# ---------------------------------------------------------------- 1. the same journal


_CLOSE = journal_mod.Journal.close


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2030, 1, 1, tzinfo=UTC)


def _journal_run(frame: pd.DataFrame, monkeypatch, fast: bool) -> list[str]:
    """One spy_engine run; the journal's entries as text. Wall-clock stamps (event times, report times, the shadow
    desk's own compute time) are frozen: they are the only parts of a journal that are not the run's."""
    for mod in (journal_mod, store_mod, gate_report, gate_v2):
        monkeypatch.setattr(mod, "datetime", _Frozen)
    monkeypatch.setattr(shadow_mod, "_time", types.SimpleNamespace(perf_counter=lambda: 0.0))
    monkeypatch.setattr(se, "simulation", partial(runner.simulation, fast=fast))
    lines: list[str] = []
    def keep(self):
        lines.extend(json.dumps([k, b], sort_keys=True) for _, _, k, b in self.entries())
        _CLOSE(self)

    monkeypatch.setattr(journal_mod.Journal, "close", keep)
    sd = from_frame("SPY", frame)
    days = [str(d) for d in sd.dates][-TEST:]
    run = se.engine_run(frame, se.calendar_of(sd), days, "spy_noise_band", "v2", level="balanced",
                        vix=pd.Series(dtype=float), events={}, covers=None, adv={"SPY": adv_table(frame)})
    run.pop("runtime_s")
    lines.append(json.dumps(run, sort_keys=True, default=str))
    return lines


def test_the_engine_run_is_byte_identical_with_and_without_the_fast_read(frame, monkeypatch):
    old = _journal_run(frame, monkeypatch, fast=False)
    new = _journal_run(frame, monkeypatch, fast=True)
    again = _journal_run(frame, monkeypatch, fast=True)
    run = json.loads(old[-1])
    assert run["errors"] == [] and len(run["fills"]) >= 6  # the rule traded: the comparison covers fills and exits
    assert len(old) > 1000
    assert old == new == again


# ---------------------------------------------------------------- 2. point-in-time, minute by minute


def _markets(frame: pd.DataFrame, tmp: Path, start: datetime):
    clock = SimClock(start)
    data = HistoricalData({"SPY": frame}, clock)
    args = ([], {}, "SPY")
    live = LiveMarket(data, *args, tmp / "live", lambda: pd.Series(dtype=float))
    fast = runner.SimMarket(data, *args, tmp / "fast", lambda: pd.Series(dtype=float))
    return clock, live, fast


def _same_days(a, b) -> None:
    assert a is not None and b is not None
    assert np.array_equal(a.dates, b.dates)
    for k in ("o", "h", "l", "c", "v", "vw"):
        assert np.array_equal(getattr(a, k), getattr(b, k), equal_nan=True), k


def _session(frame: pd.DataFrame, day: str) -> tuple[datetime, int]:
    open_utc = pd.Timestamp(f"{day} 09:30", tz=ET).tz_convert(UTC).to_pydatetime()
    return open_utc, 210 if day == EARLY else N_MIN


@pytest.mark.parametrize("day", ["2017-04-12", EARLY])
def test_every_minute_reads_what_live_market_reads(frame, tmp_path, day):
    open_utc, n_min = _session(frame, day)
    clock, live, fast = _markets(frame, tmp_path, open_utc - timedelta(minutes=30))
    for mk in (live, fast):
        mk.prepare_day(datetime.fromisoformat(day).date(), open_utc)
    assert set(live.history) == set(fast.history)
    for s in live.history:  # the same bars (LiveMarket's day-one frame holds them as objects: values compared)
        a, b = live.history[s], fast.history[s]
        assert np.array_equal(pd.DatetimeIndex(a["ts"]).as_unit("ns").asi8,
                              pd.DatetimeIndex(b["ts"]).as_unit("ns").asi8)
        cols = [*"ohlcv", "vw"]
        assert np.array_equal(a[cols].to_numpy(dtype=float), b[cols].to_numpy(dtype=float))
    assert live.vol_ratio == fast.vol_ratio
    for m in range(n_min):
        clock.set(open_utc + timedelta(minutes=m + 1, seconds=7))
        for mk in (live, fast):
            mk.update(clock())
        assert live.last_complete_minute(clock()) == m
        assert np.array_equal(live.today_bars["SPY"][["o", "h", "l", "c", "v", "vw"]].to_numpy(dtype=float),
                              fast.today_bars["SPY"][["o", "h", "l", "c", "v", "vw"]].to_numpy(dtype=float))
        assert (live.last_close("SPY"), live.bar_volume("SPY"), live.last_bar_minute("SPY"), live.spy_return()) == (
            fast.last_close("SPY"), fast.bar_volume("SPY"), fast.last_bar_minute("SPY"), fast.spy_return())
        _same_days(live.symbol_days("SPY", m), fast.symbol_days("SPY", m))


@pytest.mark.parametrize("m", [0, 57, 99, 101, 250, 388])
def test_a_later_bar_cannot_reach_the_arrays_of_minute_m(frame, tmp_path, m):
    """The fast read takes the whole session's bars once. A copy of the data whose bars from minute m + 1 on are
    different (prices x 1.5, ten times the volume) must give exactly the same arrays at minute m."""
    day = "2017-04-12"
    open_utc, _ = _session(frame, day)
    cut = open_utc + timedelta(minutes=m + 1)
    later = frame.copy()
    after = later["ts"] >= pd.Timestamp(cut)
    assert after.any()
    for k in ("o", "h", "l", "c", "vw"):
        later.loc[after, k] *= 1.5
    later.loc[after, "v"] *= 10
    got = []
    for f, sub in ((frame, "a"), (later, "b")):
        clock, _, fast = _markets(f, tmp_path / sub, open_utc - timedelta(minutes=30))
        fast.prepare_day(datetime.fromisoformat(day).date(), open_utc)
        clock.set(open_utc + timedelta(minutes=m + 1, seconds=7))
        fast.update(clock())
        got.append((fast.symbol_days("SPY", m), fast.today_bars["SPY"].copy(), fast.last_close("SPY")))
    (a, ta, ca), (b, tb, cb) = got
    _same_days(a, b)
    assert ta.equals(tb) and ca == cb
    assert len(ta) == m + 1 - sum(1 for g in GAP if g <= m)  # only bars that started before minute m + 1
    assert pd.Timestamp(ta["ts"].iloc[-1]) < pd.Timestamp(cut)
    today = a.dates[-1] == np.datetime64(day, "D")
    assert today and np.isnan(a.c[-1, m + 1:]).all() and (a.v[-1, m + 1:] == 0).all()


# ---------------------------------------------------------------- 3. never in the live engine


def test_the_live_engine_builds_live_market_not_the_simulation_read():
    src = (ROOT / "src" / "kaibot" / "engine" / "__main__.py").read_text()
    assert "LiveMarket(" in src and "SimMarket" not in src and "engine.runner" not in src
    assert issubclass(runner.SimMarket, LiveMarket)
    with pytest.raises(TypeError):
        runner.SimMarket(object(), [], {}, "SPY", Path("/nonexistent"), lambda: pd.Series(dtype=float))
