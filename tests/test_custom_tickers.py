"""M5 S68 (#96): custom ticker lists.

- the config stores a custom list by value and can only add ACCEPTED tickers (shape here, acceptance in SQL and in the
  engine's own point-in-time check);
- a custom list is valid ONLY in 'Only these' mode (approved 11 Nov 2026): in All and Prefer mode it is rejected, and
  the engine never loads or trades it;
- one verdict (engine/tickercheck.py) for the dashboard's check worker and for the engine at every session open;
- PROPERTY: a refused or unchecked ticker can never reach an entry;
- no custom list = exactly the seed universe; a replay on a custom list logs a trial.
All network calls are faked."""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
import yaml
from fakes import FakeStore
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from test_engine import DAY, make, run_day, synth_frames
from test_userconfig import PROFILES, STRATS, THEMES, UNIVERSE, T

from kaibot.engine import tickercheck as tc
from kaibot.engine.config import ROOT
from kaibot.engine.userconfig import (
    CUSTOM_NEEDS_ONLY,
    MAX_CUSTOM,
    REFERENCE_ETFS,
    ConfigError,
    Rules,
    is_tightening,
)
from kaibot.replay.config_replay import NoData, replay_config

UCFG = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())
SQL = (ROOT / "migrations" / "0014_m5_custom_tickers.sql").read_text()
GOOD = {"symbol": "PLTR", "class": "us_equity", "exchange": "NASDAQ", "status": "active", "tradable": True}
OK_STATS = {"sessions": 70, "adv20_usd": 2e9, "last_close": 40.0}


def tick(sym, etf="XLK"):
    return {"symbol": sym, "etf": etf}


def custom(*syms, name="AI picks"):
    return {"name": name, "tickers": [tick(s) for s in syms]}


# ---------------- config: stored by value, tighten-only shape ----------------
def test_reference_funds_match_the_seed_and_the_migration():
    assert {UCFG["market"], *UCFG["sectors"]} == REFERENCE_ETFS
    for arr in re.findall(r"array\[('SPY'[^\]]*)\]", SQL) + re.findall(r"etf in \(('SPY'[^)]*)\)", SQL):
        assert {x.strip(" '") for x in arr.split(",")} == REFERENCE_ETFS
    assert not REFERENCE_ETFS & UNIVERSE


def test_custom_list_is_stored_by_value_and_joins_the_focus():
    r = T("balanced", {"focus": {"mode": "only", "custom": custom("PLTR", "UBER", "SHOP", "COIN", "BRK.B")}})
    assert r.custom == (("BRK.B", "XLK"), ("COIN", "XLK"), ("PLTR", "XLK"), ("SHOP", "XLK"), ("UBER", "XLK"))
    assert r.custom_name == "AI picks" and r.focus == {"PLTR", "UBER", "SHOP", "COIN", "BRK.B"}
    assert r.tradable("PLTR") and not r.tradable("AAPL")
    assert is_tightening(r, PROFILES["balanced"], STRATS["balanced"])
    mixed = T("balanced", {"focus": {"mode": "only", "symbols": ["AAPL", "MSFT", "NVDA"],
                                     "custom": custom("PLTR", "UBER")}})
    assert mixed.focus == {"AAPL", "MSFT", "NVDA", "PLTR", "UBER"}  # catalogue stocks + custom tickers count together
    assert mixed.custom_universe() == {"PLTR": "XLK", "UBER": "XLK"}
    # the same list, however it is ordered, is the same version content
    five = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"]
    a = T("balanced", {"focus": {"mode": "only", "symbols": five, "custom": custom("PLTR", "UBER")}})
    assert a.custom == T("balanced", {"focus": {"mode": "only", "symbols": five,
                                                "custom": custom("UBER", "PLTR")}}).custom


@pytest.mark.parametrize("mode", ["all", "prefer", None])
def test_custom_list_is_rejected_outside_only_mode(mode):
    """Approved 11 Nov 2026: custom tickers trade only in 'Only these' mode. A config that carries a custom list
    with All, Prefer or no mode (= All) is rejected with a plain message, however good the list is."""
    fo = {"symbols": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"], "custom": custom("PLTR", "UBER")}
    if mode:
        fo["mode"] = mode
    with pytest.raises(ConfigError) as e:
        T("balanced", {"focus": fo})
    assert str(e.value) == CUSTOM_NEEDS_ONLY and "Only these" in CUSTOM_NEEDS_ONLY
    assert CUSTOM_NEEDS_ONLY.replace("'", "''") in SQL  # the server says the very same sentence
    T("balanced", {"focus": {**fo, "mode": "only"}})  # the same list is fine in Only mode
    fo.pop("custom")
    assert T("balanced", {"focus": fo}).custom == ()  # and All / Prefer without a list are unchanged


def test_custom_universe_is_empty_outside_only_mode_even_for_hand_built_rules():
    """The loader's own guard: whatever reaches the engine, custom tickers load only in Only mode and only when
    they are in the chosen focus."""
    pairs = (("PLTR", "XLK"), ("UBER", "XLI"))
    for mode in ("all", "prefer"):
        r = Rules(1, PROFILES["balanced"], ("dip_reversal",), mode=mode, focus=frozenset({"PLTR", "UBER"}),
                  custom=pairs)
        assert r.custom_universe() == {} and not is_tightening(r, PROFILES["balanced"], STRATS["balanced"])
    only = Rules(1, PROFILES["balanced"], ("dip_reversal",), mode="only",
                 focus=frozenset({"PLTR", "AAPL", "MSFT", "NVDA", "AMZN"}), custom=pairs)
    assert only.custom_universe() == {"PLTR": "XLK"}  # UBER is not in the chosen focus


def test_no_custom_list_is_exactly_the_old_rules():
    r = T("balanced", {"focus": {"mode": "only", "themes": ["hyperscalers"]}})
    assert r.custom == () and r.custom_name == ""


def test_custom_list_rejections():
    many = [tick(a + b) for a in "ABCDEFG" for b in "QRSTUVW"][: MAX_CUSTOM + 1]
    assert len(many) == MAX_CUSTOM + 1
    T("balanced", {"focus": {"mode": "only", "custom": {"tickers": many[:MAX_CUSTOM]}}})  # 40 is allowed
    bad = [
        {"tickers": many},                                         # 41 > the cap
        {"tickers": []}, {}, {"name": "x"}, [], None, "PLTR",      # no tickers / wrong type
        {"tickers": [tick("AAPL")]},                               # catalogue stock: goes in focus.symbols
        {"tickers": [tick("SPY")]}, {"tickers": [tick("XLK")]},    # reference funds are never traded
        {"tickers": [tick("pltr")]}, {"tickers": [tick("TOOLONG")]}, {"tickers": [tick("PL TR")]},
        {"tickers": [tick("PLTR\n")]}, {"tickers": [tick("")]}, {"tickers": [tick("BRK.BB")]},
        {"tickers": [tick("PLTR", "QQQ")]}, {"tickers": [tick("PLTR", None)]},   # unknown sector fund
        {"tickers": [tick("PLTR"), tick("PLTR", "XLF")]},          # listed twice
        {"tickers": ["PLTR"]}, {"tickers": [{"symbol": "PLTR"}]},
        {"tickers": [{"symbol": "PLTR", "etf": "XLK", "leverage": 2}]},
        {"tickers": [tick("PLTR")], "name": "x" * 41}, {"tickers": [tick("PLTR")], "name": 5},
        {"tickers": [tick("PLTR")], "shorts": True},
    ]
    five = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"]  # so that the list itself is what gets rejected
    for b in bad:
        with pytest.raises(ConfigError) as e:
            T("balanced", {"focus": {"mode": "only", "symbols": five, "custom": b}})
        assert str(e.value) != CUSTOM_NEEDS_ONLY and "at least 5" not in str(e.value)
    with pytest.raises(ConfigError, match="at least 5"):  # 'Only' still needs five
        T("balanced", {"focus": {"mode": "only", "custom": custom("PLTR", "UBER", "SHOP", "COIN")}})


# ---------------- the verdict ----------------
def test_verdict_accepts_and_gives_plain_reasons():
    assert tc.verdict("PLTR", GOOD, OK_STATS) == (True, None)
    cases = [
        ("PLTR", None, OK_STATS, "could not find PLTR"),
        ("PLTR", {**GOOD, "symbol": "PLTX"}, OK_STATS, "could not find"),
        ("PLTR", {**GOOD, "tradable": False}, OK_STATS, "cannot be traded"),
        ("PLTR", {**GOOD, "status": "inactive"}, OK_STATS, "cannot be traded"),
        ("PLTR", {**GOOD, "exchange": "OTC"}, OK_STATS, "major US exchange"),
        ("PLTR", {**GOOD, "class": "crypto"}, OK_STATS, "major US exchange"),
        ("PLTR", GOOD, {**OK_STATS, "sessions": 59}, "only 59 trading days"),
        ("PLTR", GOOD, {**OK_STATS, "last_close": 4.99}, "above $5"),
        ("PLTR", GOOD, {**OK_STATS, "last_close": None}, "unknown price"),
        ("PLTR", GOOD, {**OK_STATS, "last_close": float("nan")}, "unknown price"),
        ("PLTR", GOOD, {**OK_STATS, "adv20_usd": 50e6}, "more than $50M"),
        ("PLTR", GOOD, {**OK_STATS, "adv20_usd": float("nan")}, "more than $50M"),
        ("PLTR", GOOD, {}, "only 0 trading days"),
        ("SPY", {**GOOD, "symbol": "SPY"}, OK_STATS, "reference fund"),
        ("pltr", GOOD, OK_STATS, "does not look like"),
    ]
    for sym, asset, stats, needle in cases:
        ok, why = tc.verdict(sym, asset, stats)
        assert not ok and needle in why, (sym, why)


def test_verdict_floors_can_only_be_raised():
    assert tc.verdict("PLTR", GOOD, {**OK_STATS, "adv20_usd": 40e6}, min_adv=1.0)[0] is False
    assert tc.verdict("PLTR", GOOD, {**OK_STATS, "last_close": 3.0}, min_price=1.0)[0] is False
    assert tc.verdict("PLTR", GOOD, {**OK_STATS, "adv20_usd": 80e6}, min_adv=100e6)[0] is False
    assert tc.verdict("PLTR", GOOD, {**OK_STATS, "last_close": 8.0}, min_price=10.0)[0] is False


def daily(n, close=40.0, vol=5e6, end="2026-08-03"):
    days = pd.bdate_range(end=end, periods=n)
    ts = [pd.Timestamp(f"{d.date()} 09:30", tz="America/New_York").tz_convert(UTC) for d in days]
    return pd.DataFrame({"ts": ts, "o": close, "h": close, "l": close, "c": close, "v": vol, "vw": close})


def test_stats_are_point_in_time():
    bars = daily(70)
    s = tc.stats(bars, date(2026, 8, 4))
    assert s == {"sessions": 70, "adv20_usd": 40.0 * 5e6, "last_close": 40.0}
    # a huge bar ON the decision day (or later) changes nothing
    later = pd.concat([bars, daily(1, close=900.0, vol=9e9, end="2026-08-04")], ignore_index=True)
    assert tc.stats(later, date(2026, 8, 4)) == s
    assert tc.stats(bars, date(2026, 7, 1))["sessions"] < 70
    assert tc.stats(None, date(2026, 8, 4))["sessions"] == 0 and tc.stats(daily(10), date(2026, 8, 4))["adv20_usd"] == 0


def test_sector_etf_mapping_and_fallback():
    assert tc.sector_etf("Semiconductors") == "XLK" and tc.sector_etf(" banking ") == "XLF"
    assert tc.sector_etf(None) == "SPY" and tc.sector_etf("") == "SPY" and tc.sector_etf("Space tourism") == "SPY"
    assert set(tc.INDUSTRY_ETF.values()) <= REFERENCE_ETFS
    # the map reproduces the hand-made seed sectors for the catalogue (Finnhub files the big-box stores under Retail)
    meta = json.loads((ROOT / "apps" / "web" / "src" / "data" / "symbols.json").read_text())
    off = {s for etf, syms in UCFG["sectors"].items() for s in syms if tc.sector_etf(meta[s]["industry"]) != etf}
    assert off == {"COST", "WMT"}


# ---------------- the check worker (all vendors faked) ----------------
NOW = datetime(2026, 8, 4, 15, 0, tzinfo=UTC)


def worker(assets, bars, industry=lambda s: ("Palantir", "Technology")):
    calls = []

    def bars_fn(s, start, end):
        calls.append((s, start, end))
        return bars.get(s)

    return {"asset_fn": lambda s: assets.get(s), "bars_fn": bars_fn, "industry_fn": industry, "now": lambda: NOW}, calls


def pending(*syms):
    st_ = FakeStore()
    for i, s in enumerate(syms):
        st_.insert("ticker_check", {"symbol": s, "checked_on": "2026-08-04", "status": "pending", "attempts": 0,
                                    "updated_at": f"2026-08-04T14:00:0{i}Z"})
    return st_


def row(store, sym):
    return next(r for r in store.tables["ticker_check"] if r["symbol"] == sym)


def test_worker_accepts_refuses_and_maps_the_sector():
    store = pending("PLTR", "TINY", "NOPE")
    kw, calls = worker({"PLTR": GOOD, "TINY": {**GOOD, "symbol": "TINY"}},
                       {"PLTR": daily(80), "TINY": daily(80, close=20.0, vol=1e5)})
    out = tc.process(store, **kw)
    assert [o["status"] for o in out] == ["accepted", "refused", "refused"]
    p = row(store, "PLTR")
    assert p["status"] == "accepted" and p["etf"] == "XLK" and p["reason"] is None and p["name"] == "Palantir"
    assert p["detail"]["sessions"] == 80 and p["detail"]["as_of"] == "2026-08-04"
    assert row(store, "TINY")["status"] == "refused" and "$2M a day" in row(store, "TINY")["reason"]
    assert row(store, "TINY")["etf"] is None
    assert "could not find NOPE" in row(store, "NOPE")["reason"]
    assert all(end <= NOW - timedelta(minutes=15) for _, _, end in calls)  # SIP only when >= 15 minutes old
    assert [c[0] for c in calls] == ["PLTR", "TINY"]  # no bars are fetched for an unknown ticker
    assert tc.process(store, **kw) == []  # nothing pending: cached for the day


def test_worker_unknown_or_failing_industry_falls_back_to_spy():
    for ind in (lambda s: None, lambda s: ("X", None), lambda s: ("X", "Space tourism")):
        store = pending("PLTR")
        tc.process(store, **worker({"PLTR": GOOD}, {"PLTR": daily(80)}, ind)[0])
        assert row(store, "PLTR")["status"] == "accepted" and row(store, "PLTR")["etf"] == "SPY"

    def boom(s):
        raise TimeoutError

    store = pending("PLTR")
    tc.process(store, **worker({"PLTR": GOOD}, {"PLTR": daily(80)}, boom)[0])
    assert row(store, "PLTR")["etf"] == "SPY" and "industry lookup failed" in row(store, "PLTR")["detail"]["note"]


def test_worker_vendor_failure_retries_then_reports_error_never_accepts():
    store = pending("PLTR", "UBER")
    kw, _ = worker({"UBER": {**GOOD, "symbol": "UBER"}}, {"UBER": daily(80)})

    def asset(s):
        if s == "PLTR":
            raise ConnectionError
        return {**GOOD, "symbol": "UBER"}

    kw["asset_fn"] = asset
    for n in (1, 2):
        tc.process(store, **kw)
        assert row(store, "PLTR")["status"] == "pending" and row(store, "PLTR")["attempts"] == n
    tc.process(store, **kw)
    assert row(store, "PLTR")["status"] == "error" and "Try again" in row(store, "PLTR")["reason"]
    assert row(store, "UBER")["status"] == "accepted"  # one bad ticker does not stop the queue


# ---------------- engine: loaded at the next open, vetted point-in-time ----------------
def frames_with(*extra, seed=1):
    f = synth_frames(seed=seed)
    for i, s in enumerate(extra):
        f[s] = synth_frames(seed=seed + 10 + i)["A"]
    return f


FILLER = ["FA", "FB", "FC", "FD"]  # catalogue names with no data here: they only make the Only focus >= 5 stocks


def engine(tmp_path, custom_map, frames, mode="only", before_open=True, extra_body=None):
    eng, broker, clock, store, open_utc = make(tmp_path, frames)
    eng.market._base_symbols = eng.market.symbols = ["A"]  # the catalogue of this fake world is just A
    eng.market._base_etf_of = eng.market.etf_of = {"A": "XLK"}
    eng.market.base = frozenset({"A"})
    eng.market._refresh()
    eng._uni_cache = (frozenset({"A", *FILLER}), {})
    if custom_map is not None:
        body = {"focus": {"mode": mode, "symbols": ["A", *FILLER],
                          "custom": {"name": "mine", "tickers": [tick(s, e) for s, e in custom_map.items()]}},
                **(extra_body or {})}
        ts = open_utc - timedelta(hours=10) if before_open else open_utc + timedelta(minutes=5)
        store.insert("config", {"run_id": store.tables["run"][0]["id"], "version": 1, "body": body,
                                "status": "applied", "created_at": ts.isoformat(), "applies_from": ts.isoformat()})
    return eng, broker, clock, store, open_utc


def offer(eng, syms, at=(54,)):
    """Bypass the feature filters: offer every symbol in `syms` as a dip candidate (rules downstream still apply)."""
    def cands(m):
        if m not in at:
            return pd.DataFrame()
        rows = []
        for s in syms:
            px = eng.market.last_close(s)
            if px is None:
                continue
            rows.append({"symbol": s, "date": np.datetime64(DAY, "D"), "m": m, "price": px, "atr5": px * 0.002,
                         "vwap": px * 1.002, "z": -2.0, "resid_z": 0.1, "turnover": 1.0, "sigma_1m": 0.0006,
                         "news_24h": False})
        return pd.DataFrame(rows)

    eng.market.candidates_at = cands
    eng.market.regime_at = lambda m: (False, 18.0)


def traded(store):
    return {t["symbol"] for t in store.tables.get("trade", [])}


def test_defaults_scan_exactly_the_seed_universe(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, frames_with("ZED"))
    syms, etf_of, all_ = eng.market.symbols, eng.market.etf_of, list(eng.market.all)
    run_day(eng, broker, clock, open_utc, until=20)
    assert eng.market.symbols is syms and eng.market.etf_of is etf_of and eng.market.all == all_
    assert eng.market.custom == {} and eng.day.custom_ok == frozenset()
    assert not [e for e in store.tables["event"] if e["type"] == "custom_tickers"]


def test_accepted_custom_ticker_is_loaded_with_its_sector_etf_and_trades(tmp_path):
    eng, broker, clock, store, open_utc = engine(tmp_path, {"ZED": "SPY", "YOLO": "XLK"}, frames_with("ZED", "YOLO"))
    loaded = []
    real = eng.market.prepare_day
    eng.market.prepare_day = lambda *a, **k: (loaded.append(list(eng.market.all)), real(*a, **k))[1]
    seen = []
    real_scan = eng.market.candidates_at
    eng.market.candidates_at = lambda m: (seen.append(list(eng.market.symbols)), real_scan(m))[1]
    run_day(eng, broker, clock, open_utc, until=30)  # the real scanner runs over the custom tickers too
    assert all({"ZED", "YOLO"} <= set(a) for a in loaded) and loaded  # history backfilled before the session
    assert len(eng.market.history["ZED"]) > 0
    assert eng.market.etf_of == {"A": "XLK", "ZED": "SPY", "YOLO": "XLK"}
    assert seen and all(s == ["A", "YOLO", "ZED"] for s in seen) and eng.market.last_checked == 3
    ev = [e for e in store.tables["event"] if e["type"] == "custom_tickers"][-1]["payload"]
    assert ev["accepted"] == ["YOLO", "ZED"] and ev["refused"] == {} and ev["version"] == 1

    eng2, broker2, clock2, store2, open2 = engine(tmp_path / "b", {"ZED": "SPY"}, frames_with("ZED"))
    offer(eng2, ["ZED"])
    run_day(eng2, broker2, clock2, open2, until=60)
    assert traded(store2) == {"ZED"} and all(t["config_version"] == 1 for t in store2.tables["trade"])


@pytest.mark.parametrize("why", ["not_tradable", "unknown", "thin", "cheap", "short_history", "broker_down"])
def test_refused_custom_ticker_is_never_scanned_or_entered(tmp_path, why):
    frames = frames_with("ZED", "BAD")
    if why == "thin":
        frames["BAD"] = frames["BAD"].assign(v=100.0)  # ~ $4M a day
    if why == "cheap":
        frames["BAD"] = frames["BAD"].assign(**{k: frames["BAD"][k] / 50 for k in ("o", "h", "l", "c", "vw")},
                                             v=5e7)
    if why == "short_history":
        cut = frames["BAD"]["ts"].max() - pd.Timedelta(days=40)
        frames["BAD"] = frames["BAD"][frames["BAD"]["ts"] > cut].reset_index(drop=True)
    eng, broker, clock, store, open_utc = engine(tmp_path, {"ZED": "XLK", "BAD": "XLK"}, frames)
    if why == "not_tradable":
        broker.assets["BAD"] = {**GOOD, "symbol": "BAD", "tradable": False}
    if why == "unknown":
        broker.assets["BAD"] = None
    if why == "broker_down":
        real = broker.asset
        broker.asset = lambda s: (_ for _ in ()).throw(ConnectionError()) if s == "BAD" else real(s)
    offer(eng, ["BAD", "ZED"])  # even if a candidate for it appeared, it must not be entered
    run_day(eng, broker, clock, open_utc, until=60)
    assert "BAD" not in eng.market.symbols and "BAD" not in eng.day.custom_ok
    assert traded(store) == {"ZED"}
    assert not [o for o in broker.orders.values() if o.get("symbol") == "BAD"]
    ev = [e for e in store.tables["event"] if e["type"] == "custom_tickers"][-1]["payload"]
    assert list(ev["refused"]) == ["BAD"] and ev["accepted"] == ["ZED"]
    sig = [s for s in store.tables["signal"] if s["symbol"] == "BAD"]
    assert sig and all(s["rules_veto"] == "not an accepted ticker today" for s in sig)


def test_config_applied_after_the_open_waits_for_the_next_session(tmp_path):
    eng, broker, clock, store, open_utc = engine(tmp_path, {"ZED": "XLK"}, frames_with("ZED"), before_open=False)
    offer(eng, ["ZED"])
    run_day(eng, broker, clock, open_utc, until=60)
    assert eng.market.custom == {} and "ZED" not in traded(store) and eng.day.rules.version == 0


def test_invalid_config_refuses_the_session_and_loads_no_custom_ticker(tmp_path):
    eng, broker, clock, store, open_utc = engine(tmp_path, {"ZED": "QQQ"}, frames_with("ZED"))  # unknown sector fund
    offer(eng, ["ZED", "A"])
    run_day(eng, broker, clock, open_utc, until=60)
    assert eng.day.refused and eng.market.custom == {} and not traded(store)


@pytest.mark.parametrize("mode", ["all", "prefer"])
def test_custom_list_in_all_or_prefer_mode_is_never_loaded_or_traded(tmp_path, mode):
    """A stored config with a custom list outside Only mode (the server rejects it, so this is the engine's own
    line of defence): the session is refused, the ticker's history is never loaded and it can never be entered."""
    eng, broker, clock, store, open_utc = engine(tmp_path, {"ZED": "XLK"}, frames_with("ZED"), mode=mode)
    loaded = []
    real = eng.market.prepare_day
    eng.market.prepare_day = lambda *a, **k: (loaded.append(list(eng.market.all)), real(*a, **k))[1]
    assert eng._custom_before_open(open_utc) == {}  # nothing to backfill ahead of the open
    offer(eng, ["ZED", "A"])
    run_day(eng, broker, clock, open_utc, until=60)
    assert eng.day.refused and "Only these" in eng.day.refused
    assert eng.market.custom == {} and eng.day.custom_ok == frozenset() and eng.market.symbols == ["A"]
    assert loaded and all("ZED" not in a for a in loaded)
    assert not traded(store) and not [o for o in broker.orders.values() if o.get("symbol") == "ZED"]
    assert not [e for e in store.tables["event"] if e["type"] == "custom_tickers"]


def test_loader_ignores_custom_tickers_when_the_rules_are_not_only_mode(tmp_path, monkeypatch):
    """Even if a validator bug let such rules through, the universe loader reads Rules.custom_universe(), so a
    Prefer-mode custom ticker is not loaded, not scanned and is vetoed if offered."""
    import kaibot.engine.core as core

    eng, broker, clock, store, open_utc = engine(tmp_path, {"ZED": "XLK"}, frames_with("ZED"))
    real = core.tighten

    def leaky(*a, **k):
        r = real(*a, **k)
        return Rules(**{**r.__dict__, "mode": "prefer"})

    monkeypatch.setattr(core, "tighten", leaky)
    assert eng._custom_before_open(open_utc) == {}
    offer(eng, ["ZED"])
    run_day(eng, broker, clock, open_utc, until=60)
    assert eng.day.rules.mode == "prefer" and eng.day.rules.custom and not eng.day.refused
    assert eng.market.custom == {} and "ZED" not in eng.market.all and eng.market.symbols == ["A"]
    assert not traded(store) and "ZED" not in eng.market.history
    eng._decide("ZED", "dip_reversal", 100.0, 99.0, 102.0, {"m": eng.day.last_m}, ["test"], None,
                sigma_1m=0.0006, atr=0.2, vix=18.0)  # offered directly: still vetoed
    last = store.tables["signal"][-1]
    assert last["symbol"] == "ZED" and last["rules_veto"] == "not an accepted ticker today"
    assert "ZED" not in eng.pos and not [o for o in broker.orders.values() if o.get("symbol") == "ZED"]


def test_stricter_owner_floor_applies_to_custom_tickers(tmp_path):
    frames = frames_with("ZED")  # ~ $8bn a day
    eng, broker, clock, store, open_utc = engine(tmp_path, {"ZED": "XLK"}, frames,
                                                 extra_body={"advanced": {"min_adv_usd": 20e9}})
    run_day(eng, broker, clock, open_utc, until=10)
    assert eng.day.custom_ok == frozenset() and "ZED" not in eng.market.symbols


# PROPERTY: whatever the broker says and whatever the history looks like, a custom ticker is scanned and can be
# entered only if the verdict accepts it; every other custom ticker is vetoed even when offered directly to _decide.
asset_st = st.one_of(st.none(), st.fixed_dictionaries({
    "class": st.sampled_from(["us_equity", "us_equity", "crypto"]),
    "exchange": st.sampled_from(["NASDAQ", "NYSE", "ARCA", "OTC"]),
    "status": st.sampled_from(["active", "active", "inactive"]), "tradable": st.sampled_from([True, True, False, None]),
    "wrong_symbol": st.booleans()}), st.just("raise"))
hist_st = st.fixed_dictionaries({"sessions": st.integers(0, 90),
                                 "close": st.sampled_from([0.5, 4.99, 5.0, 30.0, 400.0]),
                                 "vol": st.sampled_from([0.0, 1e3, 1e5, 2e6, 5e7])})
SYMS = ["QA", "QB", "QC", "QD", "QE", "QF"]


@pytest.fixture(scope="module")
def prop_engine(tmp_path_factory):
    eng, broker, clock, store, open_utc = engine(tmp_path_factory.mktemp("prop"), {s: "XLK" for s in SYMS},
                                                 frames_with(*SYMS))
    run_day(eng, broker, clock, open_utc, until=20)
    return eng, broker, store


@settings(max_examples=120, deadline=None, suppress_health_check=list(HealthCheck), database=None)
@given(world=st.fixed_dictionaries({s: st.tuples(asset_st, hist_st) for s in SYMS}))
def test_property_refused_ticker_never_reaches_an_entry(prop_engine, world):
    eng, broker, store = prop_engine
    d = eng.day
    expect = {}

    def asset(s):
        a = world[s][0]
        if a == "raise":
            raise ConnectionError
        if a is None:
            return None
        return {**{k: v for k, v in a.items() if k != "wrong_symbol"}, "symbol": "ZZZZ" if a["wrong_symbol"] else s}

    broker.asset = asset
    for s, (a, h) in world.items():
        eng.market.history[s] = daily(h["sessions"], close=h["close"], vol=h["vol"]) if h["sessions"] else None
        fine_asset = (isinstance(a, dict) and not a["wrong_symbol"] and a["class"] == "us_equity"
                      and a["exchange"] != "OTC" and a["status"] == "active" and a["tradable"] is True)
        expect[s] = bool(fine_asset and h["sessions"] >= 60 and h["close"] >= 5 and h["close"] * h["vol"] > 50e6)
    for p in list(eng.pos):
        eng.pos.pop(p)
    d.entries, eng.kill = 0, None
    eng._vet_custom(d)
    ok = {s for s, v in expect.items() if v}
    assert d.custom_ok == ok and set(eng.market.symbols) == {"A"} | ok
    n_orders, n_trades = len(broker.orders), len(store.tables.get("trade", []))
    for s in SYMS:
        if expect[s]:
            continue
        px = eng.market.last_close(s) or 100.0
        eng._decide(s, "dip_reversal", px, px * 0.99, px * 1.02, {"m": d.last_m}, ["test"], None,
                    sigma_1m=0.0006, atr=px * 0.002, vix=18.0)
        assert store.tables["signal"][-1]["symbol"] == s
        assert store.tables["signal"][-1]["rules_veto"] == "not an accepted ticker today"
        assert s not in eng.pos
    assert len(broker.orders) == n_orders and len(store.tables.get("trade", [])) == n_trades


def test_set_custom_never_overrides_the_catalogue_or_a_reference(tmp_path):
    eng, *_ = make(tmp_path, synth_frames())
    mk = eng.market
    assert mk.set_custom({"A": "SPY", "XLK": "SPY", "SPY": "XLK", "NEW": "QQQ"}) is False and mk.custom == {}
    assert mk.set_custom({"NEW": "SPY"}) is True and mk.symbols == ["A"] and "NEW" in mk.all  # loaded, not scanned
    mk.accept_custom(["NEW", "GHOST"])
    assert mk.symbols == ["A", "NEW"] and mk.etf_of == {"A": "XLK", "NEW": "SPY"}
    assert mk.set_custom({"NEW": "SPY"}) is False and mk.set_custom({}) is True and mk.symbols == ["A"]


# ---------------- replay: a custom list logs a trial ----------------
spec = importlib.util.spec_from_file_location("run_config_replays", ROOT / "scripts" / "run_config_replays.py")
replays = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replays)
LIST_BODY = {"focus": {"mode": "only", "symbols": ["AAPL", "MSFT", "NVDA"], "custom": custom("PLTR", "UBER")}}


def test_replay_on_a_custom_list_logs_a_trial(tmp_path):
    store = FakeStore()
    store.insert("run", {"id": 2, "level": "balanced", "status": "running"})
    store.insert("config", {"run_id": 2, "version": 1, "body": LIST_BODY, "status": "applied"})
    store.insert("replay_job", {"run_id": 2, "config_version": 1, "status": "queued"})
    seen = []

    def fake_replay(rules, cfg, data_dir, fomc):
        seen.append(rules)
        return {"trades": 7, "net_return_pct": 0.1, "t_stat_per_trade": 0.2, "daily_sharpe_ann": 0.3}

    replays.process(store, tmp_path, replay=fake_replay)
    assert seen[0].custom == (("PLTR", "XLK"), ("UBER", "XLK")) and seen[0].tradable("PLTR")
    (trial,) = store.tables["trial"]
    assert trial["params"]["body"]["focus"]["custom"]["tickers"] == [tick("PLTR"), tick("UBER")]
    assert trial["params"]["config_version"] == 1 and trial["metrics"]["trades"] == 7
    assert store.tables["replay_job"][0]["status"] == "done"
    assert "config_r2_v1" in (tmp_path / "trials.jsonl").read_text()


def test_replay_waits_for_custom_history_and_never_fakes_it(tmp_path, monkeypatch):
    rules = T("balanced", LIST_BODY)
    import kaibot.replay.config_replay as cr

    monkeypatch.setattr(cr, "have_data", lambda d, syms: True)  # the catalogue history is there; the custom is not
    with pytest.raises(NoData, match="custom ticker.*PLTR, UBER"):
        replay_config(rules, UCFG, tmp_path, set())
    store = FakeStore()
    store.insert("run", {"id": 2, "level": "balanced", "status": "running"})
    store.insert("config", {"run_id": 2, "version": 1, "body": LIST_BODY, "status": "applied"})
    store.insert("replay_job", {"run_id": 2, "config_version": 1, "status": "queued"})
    replays.process(store, tmp_path)
    assert store.tables["replay_job"][0]["status"] == "waiting_for_data" and not store.tables.get("trial")


def test_themes_are_unchanged():
    assert THEMES and all(set(v) <= UNIVERSE for v in THEMES.values())
