"""Issue #115: the runner's tick schedule, the simulated feed (`HistoricalData` over `BarHistory`) and the proof
that the faster market read (history arrays cached per session, daily statistics computed once) gives the same
arrays, features and candidates as the old one-pass rebuild."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
import spine_fixture as sf

from kaibot.broker.shadow_costs import half_spread
from kaibot.engine.clock import SimClock
from kaibot.engine.market import LiveMarket
from kaibot.engine.runner import run_sessions
from kaibot.replay.data import ET, N_MIN
from kaibot.replay.features import daily_stats
from kaibot.replay.history import BarHistory, HistoricalData

DAY = "2026-09-15"


def et(day: str, hhmm: str, sec: int = 0) -> datetime:
    return (pd.Timestamp(f"{day} {hhmm}", tz=ET) + pd.Timedelta(seconds=sec)).tz_convert(UTC).to_pydatetime()


def frame(day: str, minutes, px: float = 100.0, v: float = 1_000.0) -> pd.DataFrame:
    mins = np.asarray(list(minutes))
    ts = pd.Timestamp(f"{day} 09:30", tz=ET).tz_convert(UTC) + pd.to_timedelta(mins, unit="m")
    c = px + 0.01 * mins  # the close of minute m is px + m cents
    return pd.DataFrame({"ts": ts, "o": c - 0.01, "h": c + 0.02, "l": c - 0.03, "c": c, "v": v, "vw": c})


# ================================================================ the runner
class StubEngine:
    def __init__(self, clock, fail_at=()):
        self.clock, self.fail_at, self.calls, self.errors, self.oks = clock, set(fail_at), [], [], 0

    def apply_commands(self):
        self.calls.append(("commands", self.clock()))

    def tick(self):
        self.calls.append(("tick", self.clock()))
        if self.clock() in self.fail_at:
            raise RuntimeError("feed down")
        return {"state": "managing"}

    def on_tick_ok(self):
        self.oks += 1

    def on_tick_error(self, e):
        self.errors.append(str(e))


class StubVenue:
    def __init__(self, engine, hours):
        self.engine, self.hours = engine, hours

    def calendar(self, day):
        h = self.hours.get(day)
        return None if h is None else {"date": day, "open": h[0], "close": h[1]}

    def advance(self, t):
        self.engine.calls.append(("bar", t))


def test_runner_schedule_is_the_live_loops():
    clock = SimClock(et(DAY, "08:00"))
    eng = StubEngine(clock)
    venue = StubVenue(eng, {DAY: ("09:30", "16:00"), "2026-09-16": ("09:30", "13:00")})
    seen = []
    log = run_sessions(eng, venue, clock, [DAY, "2026-09-16", "2026-09-19"], on_tick=lambda t, st: seen.append(t))
    assert (log.sessions, log.ticks, log.errors) == (2, 392 + 212, []) and eng.oks == log.ticks == len(seen)
    calls = eng.calls
    assert calls[:2] == [("commands", et(DAY, "09:00")), ("tick", et(DAY, "09:00"))]  # before the open
    assert calls[2:5] == [("bar", et(DAY, "09:30")), ("commands", et(DAY, "09:31", 7)), ("tick", et(DAY, "09:31", 7))]
    first = [c for c in calls if c[1] < et(DAY, "23:00")]
    assert first[-5:] == [("bar", et(DAY, "15:59")), ("commands", et(DAY, "16:00", 7)), ("tick", et(DAY, "16:00", 7)),
                          ("commands", et(DAY, "16:30")), ("tick", et(DAY, "16:30"))]  # the close, then after it
    bars = [c[1] for c in calls if c[0] == "bar"]
    assert len(bars) == 390 + 210 and bars[390] == et("2026-09-16", "09:30") and bars[-1] == et("2026-09-16", "12:59")
    ticks = [c[1] for c in calls if c[0] == "tick"]
    assert ticks == sorted(ticks) and ticks[-1] == et("2026-09-16", "13:30")
    # every bar is traded before the tick that sees it complete
    order = [(kind, t) for kind, t in calls if kind in ("bar", "tick")]
    for n, (kind, t) in enumerate(order):
        if kind == "bar":
            assert order[n + 1] == ("tick", t + timedelta(minutes=1, seconds=7))


def test_runner_can_stop_a_session_early_and_use_another_tick_second():
    clock = SimClock(et(DAY, "09:20"))
    eng = StubEngine(clock)
    log = run_sessions(eng, StubVenue(eng, {DAY: ("09:30", "16:00")}), clock, [DAY], minutes=3, tick_s=0)
    assert log.ticks == 4
    assert [t for kind, t in eng.calls if kind == "tick"] == [et(DAY, "09:20"), et(DAY, "09:31"), et(DAY, "09:32"),
                                                              et(DAY, "09:33")]


def test_runner_errors_strict_or_as_the_live_loop():
    bad = et(DAY, "09:32", 7)
    clock = SimClock(et(DAY, "09:00"))
    eng = StubEngine(clock, fail_at=[bad])
    with pytest.raises(RuntimeError, match="feed down"):
        run_sessions(eng, StubVenue(eng, {DAY: ("09:30", "16:00")}), clock, [DAY], minutes=5)
    clock = SimClock(et(DAY, "09:00"))
    eng = StubEngine(clock, fail_at=[bad])
    log = run_sessions(eng, StubVenue(eng, {DAY: ("09:30", "16:00")}), clock, [DAY], minutes=5, strict=False)
    assert log.ticks == 6 and log.errors == [(bad, "RuntimeError: feed down")]
    assert eng.errors == ["feed down"] and eng.oks == 5, "reported to Engine.on_tick_error, then the loop goes on"


# ================================================================ the stored bars and the feed
def test_bar_history_lookups():
    frames = {"A": pd.concat([frame("2026-09-14", range(390)), frame(DAY, [0, 1, 2, 5, 6])], ignore_index=True)}
    h = BarHistory(frames)
    o = et(DAY, "09:30")
    assert h.bar("A", o + timedelta(minutes=2)).c == 100.02 and h.bar("A", o + timedelta(minutes=3)) is None
    assert h.bar("ZZZ", o) is None and h.last_close("ZZZ", o) is None
    assert h.last_close("A", o + timedelta(minutes=5)) == 100.02, "strictly before the time asked"
    assert h.last_close("A", o) == pytest.approx(103.89), "yesterday's last close"
    assert h.last_close("A", o, since=o) is None, "nothing yet in today's session"
    carried = h.bar_or_carried("A", o + timedelta(minutes=4), o)
    assert carried == (100.02, 100.02, 100.02, 100.02, 0.0), "no trades: the last close, zero volume"
    assert h.bar_or_carried("A", o + timedelta(minutes=5), o).v == 1_000.0
    assert list(h.between("A", o + timedelta(minutes=1), o + timedelta(minutes=6))["c"]) == [100.01, 100.02, 100.05]
    assert h.sessions() == ["2026-09-14", DAY]
    frames["B"] = frame(DAY, range(3))  # a frame added later is picked up
    assert h.bar("B", o).o == 99.99
    frames["B"] = frame(DAY, [2, 1, 0])
    with pytest.raises(ValueError, match="sorted"):
        h.bar("B", o)


def test_sigma_is_the_replays_daily_volatility():
    fx = sf.load()
    h = BarHistory(fx.frames)
    cands = sf.prepared(fx)[0].cands
    assert len(cands) == 28
    for r in cands.itertuples():
        assert h.sigma_1m(r.symbol, pd.Timestamp(r.date).date()) == pytest.approx(r.sigma_1m, rel=1e-12)


def test_historical_data_never_serves_the_future_and_has_one_feed():
    f = frame(DAY, range(390))
    clock = SimClock(et(DAY, "09:35", 7))
    data = HistoricalData({"A": f}, clock)
    got = data.bars(["A", "ZZZ"], et(DAY, "09:30"), et(DAY, "16:00"), feed="iex")
    assert list(got) == ["A"] and len(got["A"]) == 6, "the 09:35 bar has begun before :07, later ones have not"
    assert got["A"]["ts"].max() < pd.Timestamp(clock())
    pd.testing.assert_frame_equal(got["A"], data.bars(["A"], et(DAY, "09:30"), et(DAY, "16:00"), feed="sip")["A"])
    clock.set(et(DAY, "17:00"))
    day = data.bars(["A"], et(DAY, "00:00"), et(DAY, "23:00"), feed="sip", timeframe="1Day")["A"]
    assert len(day) == 1 and day["v"][0] == 390_000.0 and day["o"][0] == f["o"][0] and day["c"][0] == f["c"].iloc[-1]
    assert day["h"][0] == f["h"].max() and day["l"][0] == f["l"].min()


def test_quotes_are_the_last_close_with_the_cost_models_spread():
    clock = SimClock(et(DAY, "09:32", 7))
    data = HistoricalData({"A": frame(DAY, range(390)), "P": frame(DAY, range(390), px=4.0)}, clock)
    q = data.latest_quotes(["A", "P", "ZZZ"])
    assert set(q) == {"A", "P"}
    assert q["A"] == {"bp": 100.02 - half_spread(100.02), "ap": 100.02 + half_spread(100.02), "t": clock().isoformat()}
    assert q["P"]["ap"] - q["P"]["bp"] == pytest.approx(0.01), "never tighter than a cent"
    data.quote_age = timedelta(seconds=6)
    assert data.latest_quotes(["A"])["A"]["t"] == (clock() - timedelta(seconds=6)).isoformat()


def test_news_count_uses_the_replays_window():
    t = np.array(["2026-09-14T14:00:00", "2026-09-15T13:59:59", "2026-09-15T14:00:00"], dtype="datetime64[s]")
    data = HistoricalData({}, SimClock(et(DAY, "10:00")), news={"A": t})
    end = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)
    assert data.news_count("A", end - timedelta(hours=24), end) == 3, "both ends count, as in replay.features"
    assert data.news_count("A", end - timedelta(hours=24) + timedelta(seconds=1), end - timedelta(seconds=1)) == 1
    assert data.news_count("B", end - timedelta(hours=24), end) == 0


# ================================================================ the faster market read is the same read
def same_days(a, b) -> None:
    assert (a is None) == (b is None)
    if a is None:
        return
    assert np.array_equal(a.dates, b.dates) and a.dates.dtype == b.dates.dtype
    for k in ("o", "h", "l", "c", "v", "vw"):
        x, y = getattr(a, k), getattr(b, k)
        assert x.shape == y.shape and x.dtype == y.dtype and np.array_equal(x, y, equal_nan=True), k


def random_frame(rng, days, miss=0.2, extended=True) -> pd.DataFrame:
    parts = []
    for d in days:
        mins = np.arange(-30 if extended else 0, N_MIN + (20 if extended else 0))
        mins = mins[rng.uniform(size=len(mins)) >= miss]
        ts = pd.Timestamp(f"{d} 09:30", tz=ET).tz_convert(UTC) + pd.to_timedelta(mins, unit="m")
        c = 50 + rng.normal(0, 1, len(mins)).cumsum() * 0.05
        parts.append(pd.DataFrame({"ts": ts, "o": c - 0.01, "h": c + 0.03, "l": c - 0.04, "c": c,
                                   "v": rng.randint(1, 5_000, len(mins)).astype(float), "vw": c + 0.001}))
    return pd.concat(parts, ignore_index=True)


def test_symbol_days_from_cached_history_equals_one_pass_over_both_frames(tmp_path):
    rng = np.random.RandomState(5)
    hist_days = [str(d.date()) for d in pd.bdate_range(end="2026-09-14", periods=6)]
    mk = LiveMarket(None, ["A", "B", "C", "D", "E"], {}, "A", tmp_path, lambda: pd.Series(dtype=float))
    mk.day, mk.open_utc = pd.Timestamp(DAY).date(), et(DAY, "09:30")
    today = random_frame(rng, [DAY])
    early = today[today["ts"] < pd.Timestamp(et(DAY, "09:30"))]
    mk.history = {"A": random_frame(rng, hist_days), "B": random_frame(rng, hist_days, miss=0.6),
                  "D": random_frame(rng, [*hist_days, DAY]),  # history that already holds today: one pass
                  "E": random_frame(rng, hist_days).iloc[0:0]}
    mk.vol_ratio = {"A": 3.7, "B": 1.0}
    cases = {"A": today, "B": early, "C": today, "D": today, "E": today}  # B: no regular-hours bar yet today
    for m in (0, 4, 57, 200, 388, 389):
        cut = pd.Timestamp(et(DAY, "09:30")) + pd.Timedelta(minutes=m + 1)
        mk.today_bars = {s: f[f["ts"] < cut].reset_index(drop=True) for s, f in cases.items()}
        for s in "ABCDE":
            same_days(mk.symbol_days(s, m), mk._symbol_days_one_pass(s, m))
    mk.today_bars = {}
    for s in "ABCDE":  # before the first bar of the day
        same_days(mk.symbol_days(s, 10), mk._symbol_days_one_pass(s, 10))
    first = mk._history_days("A")
    assert mk._history_days("A") is first, "built once per session"
    mk.history["A"] = mk.history["A"].iloc[:-5].reset_index(drop=True)  # prepare_day replaces the frame
    assert mk._history_days("A") is not first
    got = mk.symbol_days("A", 10)
    got.c[:] = -1.0  # a caller may scribble on the result: the cache must not see it
    same_days(mk.symbol_days("A", 10), mk._symbol_days_one_pass("A", 10))
    assert (mk._history_days("A").c != -1.0).any()


def test_candidates_and_regime_are_unchanged_by_the_session_caches(tmp_path):
    """Two markets on the fixture's first test session: one as shipped, one forced onto the old path (one pass
    over both frames, daily statistics recomputed on every scan). Same candidates, near-misses and regime."""
    fx = sf.load()
    day = pd.Timestamp(fx.test_days[0]).date()
    open_utc = et(fx.test_days[0], "09:30")
    clock = SimClock(open_utc - timedelta(minutes=30))
    data = HistoricalData(fx.frames, clock, fx.news)

    def market(name):
        mk = LiveMarket(data, fx.stocks, fx.sector_of, fx.market, tmp_path / name, lambda: fx.vix)
        mk.prepare_day(day, open_utc)
        return mk

    fast, old = market("fast"), market("old")
    old.symbol_days = old._symbol_days_one_pass
    old._session_stats = lambda s, sd, ed: None
    found = 0
    for m in [*range(4, 375, 10), 69, 174, 184, 344]:
        clock.t = open_utc + timedelta(minutes=m + 1, seconds=7)
        for mk in (fast, old):
            mk.update(clock())
        a, b = fast.candidates_at(m), old.candidates_at(m)
        assert len(a) == len(b)
        if len(a):
            pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True), check_exact=True)
            found += len(a)
        assert (fast.last_checked, fast.last_near) == (old.last_checked, old.last_near)
        assert fast.regime_at(m) == old.regime_at(m)
        for s in fast.all:
            same_days(fast.symbol_days(s, m), old.symbol_days(s, m))
    assert found >= 4 and fast._stats and not old._stats
    for s in fx.stocks:  # the cached statistics are today's, whatever minute they were first computed at
        sd, ed = fast.symbol_days(s, 374), fast.symbol_days(fx.sector_of[s], 374)
        for x, y in zip(fast._session_stats(s, sd, ed), daily_stats(sd, ed), strict=True):
            assert np.array_equal(np.asarray(x), np.asarray(y), equal_nan=True)


# ================================================================ the runner is the old test driver
def test_runner_with_a_strict_simbroker_reproduces_the_engine_golden(tmp_path):
    """The engine golden was recorded with the hand-written minute loop and the old FakeBroker. The runner with a
    strict SimBroker (no cost model, no cash book: the fake's settings) and the fake's quotes must give the same
    first session: every signal, gate run, order, fill and trade."""
    import json

    from fakes import FakeData, FakeStore
    from test_spine_golden import canon, engine_summary

    from kaibot.broker.sim import SimBroker
    from kaibot.engine.core import Engine
    from kaibot.risk.profiles import load_profiles, load_strategies

    fx = sf.load()
    day = fx.test_days[0]
    clock = SimClock(et(day, "09:00"))
    data = FakeData(fx.frames, clock)
    data.news_count = HistoricalData(data.history, clock, fx.news).news_count
    broker = SimBroker(data.history, clock, costs=None, cash=None)
    market = LiveMarket(data, fx.stocks, fx.sector_of, fx.market, tmp_path / "hist", lambda: fx.vix)
    store = FakeStore()
    eng = Engine(broker, market, store, load_profiles(), set(fx.fomc), now=clock, strategies=load_strategies())
    store.insert("command", {"kind": "new_run", "payload": {"amount_usd": sf.EQUITY, "level": "balanced"},
                             "idempotency_key": "k1", "applied_at": None})
    eng.startup()
    eng.apply_commands()
    log = run_sessions(eng, broker, clock, [day])
    assert log.errors == [] and log.ticks == 392

    got = json.loads(json.dumps(canon(engine_summary(broker, store))))
    want = json.loads((sf.DIR / "engine_on_fakes.json").read_text())
    for name, when in (("trades", "entry_ts"), ("fills", "ts"), ("signals", "ts")):
        first = [r for r in want[name] if str(r[when]).startswith(day)]
        assert got[name] == first and first, name
    assert got["orders"] == want["orders"][:len(got["orders"])] and len(got["orders"]) == 6
    assert got["gate_runs"] == want["gate_runs"][:len(got["gate_runs"])]
    assert got["broker_orders"] == want["broker_orders"][:len(got["broker_orders"])]
    assert got["daily_results"] == want["daily_results"][:1] and got["gate_random"] == want["gate_random"][:1]
    assert got["events"] == want["events"][:len(got["events"])] and got["halts"] == []
    assert {t["exit_reason"] for t in got["trades"]} == {"stop", "target", "trail", "time", "moc"}
