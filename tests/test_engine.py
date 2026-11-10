"""Engine mechanics on synthetic bars (no network): commands, bracket entries, trailing, 15:55 flatten,
daily stop, carried positions and the account-clean guard. Plus live/replay parity on real data when present."""

from __future__ import annotations

import os
from datetime import UTC, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fakes import Clock, FakeBroker, FakeData, FakeStore, frame_from_days

from kaibot.engine.core import Engine
from kaibot.engine.market import LiveMarket
from kaibot.replay.data import ET
from kaibot.risk.profiles import load_profiles

DAY = "2026-08-04"


def synth_frames(seed=1, n_days=75, shock=None):
    """A, its ETF XLK and SPY: random walks; `shock(c)` may reshape symbol A's last day."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end=DAY, periods=n_days)
    out = {}
    for s, p0, vol in (("A", 100.0, 0.0006), ("XLK", 200.0, 0.0005), ("SPY", 500.0, 0.0004)):
        rows = []
        p = p0
        for d in days:
            open_utc = pd.Timestamp(f"{d.date()} 09:30", tz=ET).tz_convert(UTC)
            r = rng.normal(0, vol, 390)
            c = p * np.exp(np.cumsum(r))
            if s == "A" and shock is not None and d == days[-1]:
                c = shock(c)
            o = np.r_[p, c[:-1]]
            h = np.maximum(o, c) * 1.0003
            lo = np.minimum(o, c) * 0.9997
            ts = open_utc + pd.to_timedelta(np.arange(390), unit="m")
            rows.append(pd.DataFrame({"ts": ts, "o": o, "h": h, "l": lo, "c": c, "v": 2e5, "vw": (h + lo + c) / 3}))
            p = c[-1]
        out[s] = pd.concat(rows, ignore_index=True)
    return out


def make(tmp_path, frames, day=DAY, start_level="balanced", amount=5000.0):
    open_utc = pd.Timestamp(f"{day} 09:30", tz=ET).tz_convert(UTC).to_pydatetime()
    clock = Clock(open_utc - timedelta(minutes=30))
    data = FakeData(frames, clock)
    broker = FakeBroker(data, clock, day)
    syms = [s for s in frames if s not in ("XLK", "SPY")]
    market = LiveMarket(
        data,
        syms,
        {s: "XLK" for s in syms},
        "SPY",
        tmp_path / "hist",
        lambda: pd.Series(18.0, index=pd.date_range("2025-01-01", "2026-12-31")),
    )
    store = FakeStore()
    eng = Engine(broker, market, store, load_profiles(), set(), now=clock)
    store.insert(
        "command",
        {
            "kind": "new_run",
            "payload": {"amount_usd": amount, "level": start_level},
            "idempotency_key": "k1",
            "applied_at": None,
        },
    )
    eng.startup()
    eng.apply_commands()
    return eng, broker, clock, store, open_utc


def run_day(eng, broker, clock, open_utc, until=390, hook=None):
    for k in range(until):
        bar = open_utc + timedelta(minutes=k)
        broker.advance(pd.Timestamp(bar))
        clock.t = bar + timedelta(minutes=1, seconds=7)
        eng.tick()
        if hook:
            hook(k, eng)
    clock.t = open_utc + timedelta(hours=7)  # after the close
    broker.advance(pd.Timestamp(open_utc + timedelta(minutes=389)))
    eng.tick()


def fake_candidate(eng, sym="A", at=(54,)):
    """Bypass the feature filters: offer `sym` as a candidate at minutes `at` (rules downstream still apply)."""
    real = eng.market.candidates_at

    def cands(m):
        if m not in at:
            return real(m).iloc[0:0] if len(real(m)) else pd.DataFrame()
        px = eng.market.last_close(sym)
        return pd.DataFrame(
            [
                {
                    "symbol": sym,
                    "date": np.datetime64(DAY, "D"),
                    "m": m,
                    "price": px,
                    "atr5": px * 0.002,
                    "vwap": px * 1.002,
                    "z": -2.0,
                    "resid_z": 0.1,
                    "turnover": 1.0,
                    "sigma_1m": 0.0006,
                    "news_24h": False,
                }
            ]
        )

    eng.market.candidates_at = cands
    eng.market.regime_at = lambda m: (False, 18.0)


def test_new_run_entry_bracket_and_time_flatten(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    assert store.active_run()["status"] == "scheduled"
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc)
    run = store.tables["run"][0]
    assert run["status"] == "running" and run["level"] == "balanced"
    trades = store.tables["trade"]
    assert len(trades) == 1
    t = trades[0]
    assert t["exit_ts"] is not None and t["exit_reason"] in ("stop", "trail", "target", "time")
    # sizing: 0.5% of $5,000 = $25 risk; position <= 15% of the pot
    assert t["risk_usd"] <= 25.0 + 1e-6 and t["qty"] * t["entry_px"] <= 0.15 * 5000 + 1
    assert broker.pos == {}  # flat at the end of the day
    dr = store.tables["daily_result"][0]
    assert dr["trades"] == 1 and abs(min(dr["pnl_venue"], dr["pnl_shadow"]) - t["pnl_usd"]) < 0.02
    assert store.tables["gate_run"][0]["approved"] is True


def test_news_vetoes_and_pause_blocks_entries(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    fake_candidate(eng, at=(54, 104))
    eng.market.data.news["A"] = 2
    run_day(eng, broker, clock, open_utc, until=60)
    assert "trade" not in store.tables
    assert store.tables["signal"][0]["rules_veto"] == "company news in 24 h"
    eng.market.data.news["A"] = 0
    store.insert("command", {"kind": "pause", "payload": {}, "idempotency_key": "k2", "applied_at": None})
    eng.apply_commands()
    for k in range(60, 120):
        bar = open_utc + timedelta(minutes=k)
        broker.advance(pd.Timestamp(bar))
        clock.t = bar + timedelta(minutes=1, seconds=7)
        eng.tick()
    assert "trade" not in store.tables and store.active_run()["status"] == "paused"


def test_trailing_stop_moves_up_and_never_down(tmp_path):
    def rally(c):  # steady climb after 10:25 so the trade arms (+1R) and trails
        c = c.copy()
        c[55:] = c[55] * np.exp(np.linspace(0, 0.02, 335))
        return c

    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames(shock=rally))
    fake_candidate(eng)
    stops = []
    run_day(
        eng,
        broker,
        clock,
        open_utc,
        hook=lambda k, e: stops.append(e.pos["A"].stop) if "A" in e.pos and e.pos["A"].status == "open" else None,
    )
    assert stops and all(b >= a - 1e-9 for a, b in zip(stops, stops[1:], strict=False))
    t = store.tables["trade"][0]
    assert t["features"]["armed"] is True and t["exit_reason"] in ("trail", "target", "time")


def test_daily_stop_flattens_and_halts(tmp_path):
    def crash(c):
        c = c.copy()
        c[56:] = c[56] * np.exp(np.linspace(0, -0.05, 334))
        return c

    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames(shock=crash), start_level="aggressive")
    fake_candidate(eng, at=(54,))
    eng.policies = {
        **eng.policies,
        "aggressive": type(eng.policies["aggressive"])(
            **{**eng.policies["aggressive"].__dict__, "max_daily_loss_pct": 0.05}
        ),
    }  # tiny stop to force it
    run_day(eng, broker, clock, open_utc)
    assert eng.day is not None and eng.day.halted
    assert any(h["kind"] == "daily_stop" for h in store.tables["halt"])
    assert broker.pos == {}


def test_foreign_position_blocks_entries(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    broker.foreign = {"ZZZ": {"qty": "10"}}
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc, until=80)
    assert not eng.account_clean and "trade" not in store.tables
    assert any(e["type"] == "account_not_clean" for e in store.tables["event"])


def test_flatten_and_stop_command(tmp_path):
    def drift_up(c):
        c = c.copy()
        c[55:] = c[55] * np.exp(np.linspace(0, 0.002, 335))
        return c

    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames(shock=drift_up))
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc, until=58)
    assert "A" in eng.pos
    store.insert("command", {"kind": "flatten_and_stop", "payload": {}, "idempotency_key": "k3", "applied_at": None})
    eng.apply_commands()
    for k in range(58, 62):
        bar = open_utc + timedelta(minutes=k)
        broker.advance(pd.Timestamp(bar))
        clock.t = bar + timedelta(minutes=1, seconds=7)
        eng.tick()
    assert broker.pos == {} and store.tables["run"][0]["status"] == "stopped"
    assert store.tables["trade"][0]["exit_reason"] == "manual"


DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.mark.skipif(
    not (DATA / "bars_1m" / "symbol=SPY").exists() or not os.environ.get("KAIBOT_SLOW"),
    reason="needs the M0 replay data and KAIBOT_SLOW=1 (about 7 minutes)",
)
def test_live_matches_replay_on_a_real_day(tmp_path):
    """Same rules on the same bars: the live engine should pick the replay's trades on 4 Aug 2026."""
    import yaml

    from kaibot.replay.data import load_news_times, load_symbol

    cfg = yaml.safe_load((DATA.parent / "config" / "universe_seed.yaml").read_text())
    etf_of = {s: e for e, ss in cfg["sectors"].items() for s in ss}
    frames, news = {}, {}
    for s in sorted(set(etf_of) | set(etf_of.values()) | {"SPY"}):
        sd = load_symbol(DATA, s)
        keep = (sd.dates >= np.datetime64("2026-03-01")) & (sd.dates <= np.datetime64(DAY))
        sd.dates, sd.o, sd.h, sd.l, sd.c, sd.v, sd.vw = (
            x[keep] for x in (sd.dates, sd.o, sd.h, sd.l, sd.c, sd.v, sd.vw)
        )
        frames[s] = frame_from_days(sd)
        news[s] = load_news_times(DATA, s)
    eng, broker, clock, store, open_utc = make(tmp_path, frames, amount=100000.0)
    eng.market.symbols = sorted(etf_of)
    eng.market.etf_of = etf_of

    def news_count(sym, start, end):
        t = news.get(sym, np.array([], dtype="datetime64[s]"))
        a = np.datetime64(start.astimezone(UTC).replace(tzinfo=None), "s")
        b = np.datetime64(end.astimezone(UTC).replace(tzinfo=None), "s")
        return int(((t >= a) & (t <= b)).sum())

    eng.market.data.news_count = news_count
    vix = pd.Series(16.0, index=pd.date_range("2025-01-01", "2026-12-31"))
    eng.market.vix_loader = lambda: vix
    run_day(eng, broker, clock, open_utc)
    got = sorted((t["symbol"]) for t in store.tables.get("trade", []))
    assert got == ["COP", "DE", "GE"]  # replay 002 hold-out trades that day
