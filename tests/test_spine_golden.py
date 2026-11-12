"""Safety net for the engine-v3 spine (issue #115; decision 15 section 3; audit migration step 1).

Three goldens pin today's behaviour on the committed bar fixture (tests/fixtures/engine_spine). They were
recorded BEFORE the Broker protocol, SimBroker and the runner existed, and those changes must not move them:

  replay_events.json    replay.sim.run_portfolio (variant A, Balanced): every event and both digests
  engine_on_fakes.json  the live Engine on tests/fakes (FakeBroker, FakeData, FakeStore) over the three sessions,
                        dip reversal and SPY late momentum: signals, gate runs, orders, fills, trades, daily
                        results, halts, events, the minute ledger, the broker's final orders
  alpaca_http.json      the exact HTTP request AlpacaPaper sends for every broker call

Re-record only for an intended change of behaviour, and say so in the PR:

    KAIBOT_UPDATE_GOLDEN=1 .venv/bin/python -m pytest tests/test_spine_golden.py -q
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import spine_fixture as sf
from fakes import Clock, FakeBroker, FakeData, FakeStore

from kaibot.engine import alpaca as alp
from kaibot.engine.core import Engine
from kaibot.engine.market import LiveMarket
from kaibot.replay.data import ET
from kaibot.replay.events import events, events_digest, input_digest
from kaibot.risk.profiles import load_profiles, load_strategies

GOLDEN = sf.DIR
ROOT = Path(__file__).resolve().parents[1]
UPDATE = bool(os.environ.get("KAIBOT_UPDATE_GOLDEN"))
# Not behaviour: the git commit, the hash of the strategy files and wall-clock timings.
VOLATILE = {"code_sha", "fingerprint", "cost_ms"}


def canon(x):
    """JSON-ready and machine-independent: floats rounded to 1e-6, numpy and datetime types made plain."""
    if isinstance(x, bool | np.bool_):
        return bool(x)
    if isinstance(x, float | np.floating):
        if not np.isfinite(x):
            return None
        v = round(float(x), 6)
        return 0.0 if v == 0 else v
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, datetime | pd.Timestamp):
        return pd.Timestamp(x).tz_convert(UTC).isoformat() if pd.Timestamp(x).tzinfo else pd.Timestamp(x).isoformat()
    if isinstance(x, date):
        return x.isoformat()
    if isinstance(x, np.datetime64):
        return str(x)
    if isinstance(x, dict):
        return {str(k): canon(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0])) if k not in VOLATILE}
    if isinstance(x, list | tuple | set | frozenset):
        return [canon(v) for v in x]
    return x


def sha(x) -> str:
    return hashlib.sha256(json.dumps(canon(x), sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def check(name: str, got: dict) -> None:
    path = GOLDEN / name
    text = json.dumps(canon(got), indent=1, sort_keys=True) + "\n"
    if UPDATE:
        path.write_text(text)
    assert path.exists(), f"{name} is missing: record it with KAIBOT_UPDATE_GOLDEN=1"
    want = json.loads(path.read_text())
    have = json.loads(text)
    for key in sorted(set(want) | set(have)):
        assert have.get(key) == want.get(key), f"{name}: `{key}` changed (behaviour differs from the golden)"


# ---------------------------------------------------------------- the fixture itself
def test_fixture_is_what_the_seeded_script_writes():
    spec = importlib.util.spec_from_file_location("make_engine_fixture", ROOT / "scripts" / "make_engine_fixture.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert gen.check(GOLDEN), "run scripts/make_engine_fixture.py (and re-record the goldens if that is intended)"
    assert sum(f.stat().st_size for f in GOLDEN.glob("bars.parquet")) < 300_000


def test_fixture_shape():
    fx = sf.load()
    assert len(fx.stocks) == 5 and len(fx.frames) == 8 and len(fx.test_days) == 3 and len(fx.sessions) == 64
    days = fx.days()
    for s, sd in days.items():
        assert [str(d) for d in sd.dates] == list(fx.sessions), s
    full = fx.frames["AAA"]
    per_day = full.groupby(full["ts"].dt.tz_convert(ET).dt.date).size()
    assert list(per_day.iloc[-3:]) == [390, 390, 390] and set(per_day.iloc[:-3]) == {13}
    span = pd.Timestamp(fx.sessions[-1]) - pd.Timestamp(fx.sessions[0])
    assert span.days < 100, "the live market keeps 105 calendar days: engine and replay must see the same history"


# ---------------------------------------------------------------- (a) today's replay
def test_replay_events_golden():
    fx = sf.load()
    pol = load_profiles()["balanced"]
    book = sf.replay(fx, pol)
    days = fx.days()
    got = {
        "policy": dataclasses.asdict(pol),
        "equity0": sf.EQUITY,
        "variant": "A",
        "input_digest": input_digest({s: days[s] for s in fx.stocks}, fx.news, {"vix": fx.vix.to_dict()},
                                     {"fomc": sorted(str(d) for d in fx.fomc)}),
        "events_digest": events_digest(book),
        "events": list(events(book)),
        "exit_reasons": sorted({t.reason for t in book.trades}),
    }
    assert {"stop", "target", "time", "trail"} <= set(got["exit_reasons"]), "the fixture must exercise every exit"
    assert len(book.trades) >= 10 and all(r["trades"] for r in book.daily), "the fixture must trade every session"
    check("replay_events.json", got)


# ---------------------------------------------------------------- (b) the live engine on today's fakes
def open_utc(day: str) -> datetime:
    return pd.Timestamp(f"{day} 09:30", tz=ET).tz_convert(UTC).to_pydatetime()


def engine_on_fakes(tmp_path, fx: sf.SpineFixture, level: str = "balanced"):
    clock = Clock(open_utc(fx.test_days[0]) - timedelta(minutes=30))
    data = FakeData(fx.frames, clock)

    def news_count(sym, start, end):
        t = fx.news.get(sym, np.array([], dtype="datetime64[s]"))
        a = np.datetime64(start.astimezone(UTC).replace(tzinfo=None), "s")
        b = np.datetime64(end.astimezone(UTC).replace(tzinfo=None), "s")
        return int(((t >= a) & (t <= b)).sum())

    data.news_count = news_count
    broker = FakeBroker(data, clock, fx.test_days[0])
    market = LiveMarket(data, fx.stocks, fx.sector_of, fx.market, tmp_path / "hist", lambda: fx.vix)
    store = FakeStore()
    eng = Engine(broker, market, store, load_profiles(), set(fx.fomc), now=clock, strategies=load_strategies())
    store.insert("command", {"kind": "new_run", "payload": {"amount_usd": sf.EQUITY, "level": level},
                             "idempotency_key": "k1", "applied_at": None})
    eng.startup()
    eng.apply_commands()
    for day in fx.test_days:
        o = open_utc(day)
        broker.day = day
        clock.t = o - timedelta(minutes=30)
        eng.tick()  # before the open: history is loaded
        for k in range(390):
            bar = o + timedelta(minutes=k)
            broker.advance(pd.Timestamp(bar))
            clock.t = bar + timedelta(minutes=1, seconds=7)
            eng.tick()
        clock.t = o + timedelta(hours=7)
        broker.advance(pd.Timestamp(o + timedelta(minutes=389)))
        eng.tick()
    return eng, broker, store


def _leg(leg: dict) -> dict:
    px = leg.get("stop_price") if leg.get("type") == "stop" else leg.get("limit_price")
    return {"id": leg["id"], "type": leg["type"], "price": float(px), "status": leg["status"],
            "filled_avg_price": None if leg.get("filled_avg_price") is None else float(leg["filled_avg_price"]),
            "filled_at": leg.get("filled_at")}


def broker_orders(broker) -> list[dict]:
    """What the venue holds at the end, reduced to the facts every broker implementation must agree on."""
    out = []
    for o in broker.orders.values():
        out.append({"id": o["id"], "client_order_id": o.get("client_order_id"), "symbol": o["symbol"],
                    "side": o["side"], "type": o["type"], "time_in_force_cls": o.get("time_in_force") == "cls",
                    "qty": float(o["qty"]), "status": o["status"],
                    "filled_avg_price": None if o.get("filled_avg_price") is None else float(o["filled_avg_price"]),
                    "filled_at": o.get("filled_at"), "legs": [_leg(x) for x in o.get("legs") or []]})
    return out


def engine_summary(broker, store) -> dict:
    t = store.tables

    def rows(name):
        return t.get(name, [])

    trades = [{"id": r["id"], "strategy": r["strategy"], "symbol": r["symbol"], "entry_ts": r["entry_ts"],
               "exit_ts": r.get("exit_ts"), "qty": r["qty"], "entry_px": r["entry_px"], "exit_px": r.get("exit_px"),
               "stop_px": r["stop_px"], "target_px": r["target_px"], "exit_reason": r.get("exit_reason"),
               "pnl_usd": r.get("pnl_usd"), "risk_usd": r["risk_usd"],
               "pnl_venue": (r.get("features") or {}).get("pnl_venue"),
               "pnl_shadow": (r.get("features") or {}).get("pnl_shadow"),
               "armed": (r.get("features") or {}).get("armed"), "coid": (r.get("features") or {}).get("coid"),
               "features": sha(r.get("features"))} for r in rows("trade")]
    signals = [{"id": r["id"], "strategy": r["strategy"], "mode": r["mode"], "symbol": r["symbol"], "ts": r["ts"],
                "entry": r["entry"], "stop": r["stop"], "target": r["target"], "rules_veto": r["rules_veto"],
                "features": sha(r["features"])} for r in rows("signal")]
    gates = [{"signal_id": r["signal_id"], "approved": r["approved"], "qty": r["qty"], "binding": r["binding"],
              "checks": sha(r["checks"])} for r in rows("gate_run")]
    randoms = [{"session": r["session"], "n_trades": r["n_trades"], "seed": r["seed"], "totals": sha(r["totals"])}
               for r in rows("gate_random")]
    everything = {k: rows(k) for k in ("signal", "gate_run", "order", "fill", "trade", "daily_result", "halt",
                                        "event", "ledger", "gate_random", "run", "segment")}
    return {
        "trades": trades,
        "fills": rows("fill"),
        "orders": rows("order"),
        "signals": signals,
        "gate_runs": gates,
        "daily_results": rows("daily_result"),
        "halts": rows("halt"),
        "events": [[e["type"], sha(e["payload"])] for e in rows("event")],
        "ledger": {"rows": len(rows("ledger")), "sha": sha(rows("ledger")),
                   "last": rows("ledger")[-1] if rows("ledger") else None},
        "gate_random": randoms,
        "run": [{k: r.get(k) for k in ("status", "level", "amount_usd", "peak_equity", "started_on")}
                for r in rows("run")],
        "broker_orders": broker_orders(broker),
        "broker_positions": sorted(broker.positions()),
        "counts": {k: len(v) for k, v in sorted(t.items())},
        "sha_of_every_row": sha(everything),
    }


def test_engine_on_fakes_golden(tmp_path):
    fx = sf.load()
    eng, broker, store = engine_on_fakes(tmp_path, fx)
    got = engine_summary(broker, store)
    reasons = {t["exit_reason"] for t in got["trades"]}
    strategies = {t["strategy"] for t in got["trades"]}
    assert {"stop", "target", "time", "trail"} <= reasons, f"the fixture must exercise every exit, got {reasons}"
    assert strategies == {"dip_reversal", "spy_late_momentum"}, strategies
    assert "moc" in reasons, "SPY late momentum must leave market-on-close"
    assert any(s["rules_veto"] == "company news in 24 h" for s in got["signals"])
    assert got["broker_positions"] == [] and eng.pos == {}
    check("engine_on_fakes.json", got)


# ---------------------------------------------------------------- (c) what AlpacaPaper sends
def test_alpaca_paper_http_golden(monkeypatch):
    sent = []
    answers = {
        ("GET", "/v2/orders"): [{"id": "x1", "symbol": "AAPL"}, {"id": "x2", "symbol": "MSFT"}],
        ("GET", "/v2/positions"): [{"symbol": "AAPL", "qty": "3"}],
        ("GET", "/v2/calendar"): [{"date": "2026-09-15", "open": "09:30", "close": "16:00"}],
    }

    def call(self, method, url, params=None, body=None):
        path = url.split("alpaca.markets", 1)[1]
        sent.append({"method": method, "url": url, "params": params,
                     "body": None if body is None else json.dumps(body)})  # as text: key order is part of it
        return answers.get((method, path), {"id": "o1", "status": "accepted"})

    monkeypatch.setattr(alp._Http, "call", call)
    b = alp.AlpacaPaper("k", "s")
    calls = [
        ("account", lambda: b.account()),
        ("clock", lambda: b.clock()),
        ("calendar", lambda: b.calendar("2026-09-15")),
        ("positions", lambda: b.positions()),
        ("open_orders", lambda: b.open_orders()),
        ("order", lambda: b.order("o1")),
        ("order_by_client_id", lambda: b.order_by_client_id("kb-1-2-1")),
        ("submit_bracket", lambda: b.submit_bracket("kb-1-2-1", "AAPL", 3, 190.0, 200.129)),
        ("submit_oto_stop", lambda: b.submit_oto_stop("kb-1-3-1", "SPY", 22, 641.5)),
        ("submit_moc_sell", lambda: b.submit_moc_sell("kb-1-3-1-moc", "SPY", 22)),
        ("replace_stop", lambda: b.replace_stop("o1s", 191.005)),
        ("replace_limit", lambda: b.replace_limit("o1t", 201.5)),
        ("cancel_order", lambda: b.cancel_order("o1")),
        ("close_position", lambda: b.close_position("AAPL")),
    ]
    got = {}
    for name, fn in calls:
        sent.clear()
        out = fn()
        got[name] = {"requests": list(sent), "returns": out}
    assert b.name == "alpaca_paper" and b.base == "https://paper-api.alpaca.markets"
    check("alpaca_http.json", got)
