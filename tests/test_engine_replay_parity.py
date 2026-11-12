"""The proof of issue #115: the UNCHANGED `Engine`, driven by the one runner on `SimClock`, `SimBroker` and
`HistoricalData`, takes the trades of `replay.sim.run_portfolio` on the committed fixture, trade for trade.

Identical on the fixture (Balanced, $100,000, dip reversal, variant A):
- the 28 candidates and their features, the 2 news vetoes, the 3 FOMC-blackout skips, the 4 skips during the
  loss-streak pause and the skip of a symbol already held;
- all 18 trades: session, symbol, entry minute, exit reason, in the same order;
- every entry fill: the open of the next minute through the cost model (to 1e-9 where the quantity is equal).

Where the engine and the replay legitimately differ, the difference is listed here and pinned by a test below
(the audit's A3 / A4 numbering in brackets; D = found while building this test):

  D1  broker-held prices are whole cents         stop, target and trail differ by < 1.5 cents; 2 exits a bar early
  D2  time exit [A3]                             engine sells at the next bar's open, replay at the 15:55 close
  D3  VIX above 25 halves the size [A3]          engine floors to whole shares, replay keeps half shares
  D4  reported P&L                               min(venue, shadow) prices costs twice when the venue is SimBroker;
                                                 the equity the gate sizes on runs lower (one trade is a share
                                                 smaller) and a small winner counts as a loss in the streak
  D5  bracket levels on the fill bar [A4.10]     sent around the reference price, re-anchored on the fill a tick later
  A4.1 / A4.2  IEX feed and volume scaling       cannot differ in simulation: one bar store serves both feeds
  A4.4  stale quote                              the gate refuses it; the replay assumes a fresh quote
  A4.5  stale bar                                the gate refuses a symbol with no bar for three minutes
  A4.6  level filters                            minimum price and maximum spread exist only in the engine
  A4.7  buying power                             the engine also sizes to the broker's cash
  A4.11 half-day                                 the engine follows the calendar, the replay does not
  A4.13 run drawdown                             the engine ends the run and flattens; the replay only skips entries
  A3    daily stop                               same trigger minute; the engine sells one bar later, at the open

Not reproducible in a simulation and therefore not pinned: latency after the bar (A4.3), pump-risk and room
vetoes (A4.6, both injected into the engine by __main__), pot and universe size (A4.8, A4.9: configuration, the
same in both here), SPY momentum against its own replay (A4.12, another simulator) and the VIX publication lag
(A4.14).
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from datetime import UTC, timedelta

import numpy as np
import pandas as pd
import pytest
import spine_fixture as sf
from fakes import FakeStore

from kaibot.broker.shadow_costs import buy_px, sell_fees, sell_px
from kaibot.engine.runner import RunLog, Simulation, simulation
from kaibot.replay.data import ET
from kaibot.replay.sim import Book
from kaibot.risk.gate import Proposal, Side, Snapshot, size_position
from kaibot.risk.profiles import load_profiles

D1, D2, D3 = sf.load().test_days
EPS = 1e-9


@dataclass
class T:
    """One trade, engine or replay, in the same units."""

    date: str
    symbol: str
    entry_m: int  # minute of the fill bar (0 = 09:30)
    exit_m: int | None
    qty: float
    entry: float
    exit: float | None
    reason: str | None
    pnl: float | None  # replay: after costs and fees; engine: the venue's (SimBroker fills already carry costs)
    reported: float | None = None  # engine only: min(venue, shadow), what the dashboard shows

    @property
    def key(self) -> tuple[str, str, int]:
        return self.date, self.symbol, self.entry_m


def open_utc(day: str):
    return pd.Timestamp(f"{day} 09:30", tz=ET).tz_convert(UTC).to_pydatetime()


def minute(ts, day: str) -> int:
    return int((pd.Timestamp(ts) - pd.Timestamp(open_utc(day))).total_seconds() // 60)


def engine_trades(store) -> list[T]:
    out = []
    for r in store.tables.get("trade", []):
        day = str(pd.Timestamp(r["entry_ts"]).tz_convert(ET).date())
        f = r.get("features") or {}
        out.append(T(day, r["symbol"], minute(r["entry_ts"], day),
                     None if r.get("exit_ts") is None else minute(r["exit_ts"], day), r["qty"], r["entry_px"],
                     r.get("exit_px"), r.get("exit_reason"), f.get("pnl_venue"), r.get("pnl_usd")))
    return sorted(out, key=in_time)


def replay_trades(book: Book) -> list[T]:
    return sorted((T(str(t.date), t.symbol, t.entry_m, t.exit_m, t.qty, t.entry, t.exit, t.reason, t.pnl)
                   for t in book.trades), key=in_time)


def in_time(t: T) -> tuple[str, int, str]:
    return t.date, t.entry_m, t.symbol


@dataclass
class Run:
    sim: Simulation
    store: FakeStore
    log: RunLog

    @property
    def trades(self) -> list[T]:
        return engine_trades(self.store)


def engine_run(tmp, fx: sf.SpineFixture, days, *, policies=None, minutes=None, strict=True, **sim_kw) -> Run:
    """The engine on SimBroker over `days` of the fixture: a new Balanced paper run of $100,000, dip only."""
    store = FakeStore()
    sim = simulation(fx.frames, fx.sector_of, fx.market, store=store, policies=policies or load_profiles(),
                     start=open_utc(days[0]) - timedelta(minutes=45), cache_dir=tmp / "hist", vix=fx.vix,
                     fomc=fx.fomc, news=fx.news, **sim_kw)
    store.insert("command", {"kind": "new_run", "payload": {"amount_usd": sf.EQUITY, "level": "balanced"},
                             "idempotency_key": "k1", "applied_at": None})
    sim.engine.startup()
    sim.engine.apply_commands()
    return Run(sim, store, sim.run(days, minutes=minutes, strict=strict))


class Bars:
    """The fixture's forward-filled arrays, for recomputing a fill by hand."""

    def __init__(self, fx: sf.SpineFixture):
        self.days = fx.days()
        self.cands = sf.prepared(fx)[0].cands

    def at(self, symbol: str, day: str, m: int, col: str) -> float:
        sd = self.days[symbol]
        return float(getattr(sd, col)[int(np.nonzero(sd.dates == np.datetime64(day))[0][0]), m])

    def cand(self, t: T) -> pd.Series:
        c = self.cands
        return c[(c["symbol"] == t.symbol) & (c["date"] == np.datetime64(t.date)) & (c["m"] == t.entry_m - 1)].iloc[0]


@pytest.fixture(scope="module")
def main(tmp_path_factory):
    """One engine run and one replay over the whole fixture, shared by the tests of the main scenario."""
    fx = sf.load()
    pol = load_profiles()["balanced"]
    run = engine_run(tmp_path_factory.mktemp("main"), fx, fx.test_days)
    book = sf.replay(fx, pol)
    return fx, pol, run, replay_trades(book), book, Bars(fx)


# ================================================================ what is identical
def test_engine_with_simbroker_takes_the_replays_trades(main):
    fx, pol, run, R, book, bars = main
    E = run.trades
    assert run.log.errors == [] and run.log.sessions == 3 and run.log.ticks == 3 * 392
    assert len(E) == len(R) == 18
    assert [t.key for t in E] == [t.key for t in R], "same session, symbol and entry minute, in the same order"
    assert [t.reason for t in E] == [t.reason for t in R], "same exit reason"
    assert {t.reason for t in E} == {"stop", "target", "trail", "time"}
    assert run.sim.broker.positions() == {} and run.sim.engine.pos == {}


def test_entries_fill_at_the_next_open_through_the_cost_model(main):
    fx, pol, run, R, book, bars = main
    same = 0
    for e, r in zip(run.trades, R, strict=True):
        c = bars.cand(e)
        o, v = bars.at(e.symbol, e.date, e.entry_m, "o"), bars.at(e.symbol, e.date, e.entry_m, "v")
        assert e.entry == pytest.approx(buy_px(o, e.qty, float(c["sigma_1m"]), v), abs=EPS), e.key
        if e.qty == r.qty:
            assert e.entry == pytest.approx(r.entry, abs=EPS), e.key
            same += 1
    assert same == 14


def test_same_candidates_features_and_vetoes(main):
    """A4.1 and A4.2 cannot bite in a simulation: both feeds read one bar store, so the volume ratio is exactly
    1 and every feature the engine computed is the replay's."""
    fx, pol, run, R, book, bars = main
    assert set(run.sim.engine.market.vol_ratio.values()) == {1.0}
    cands = {(str(r.date)[:10], r.symbol, int(r.m)): r for r in bars.cands.itertuples()}
    gates = {g["signal_id"]: g for g in run.store.tables["gate_run"]}
    seen = set()
    for s in run.store.tables["signal"]:
        assert s["mode"] == "live" and s["strategy"] == "dip_reversal"
        key = (str(pd.Timestamp(s["ts"]).tz_convert(ET).date()), s["symbol"], s["features"]["m"])
        c = cands[key]
        seen.add(key)
        assert s["entry"] == c.price
        for k in ("z", "resid_z", "turnover", "atr5", "vwap", "sigma_1m"):
            assert s["features"][k] == round(float(getattr(c, k)), 6), (key, k)
        assert (s["rules_veto"] == "company news in 24 h") == bool(c.news_24h)
        assert gates[s["id"]]["approved"] == (not c.news_24h)
    assert len(cands) == 28 and len(seen) == 20
    # the eight candidates neither side acts on: the engine does not even reach a decision
    assert sorted(set(cands) - seen) == [
        (D2, "AAA", 269), (D2, "CCC", 269),  # FOMC blackout, 13:30-14:30
        (D2, "DDD", 184),  # DDD is already held
        (D2, "DDD", 274),  # FOMC blackout
        (D3, "BBB", 124), (D3, "CCC", 124), (D3, "DDD", 124), (D3, "EEE", 124),  # paused after three losses
    ]


def test_venue_pnl_is_the_replays_formula(main):
    fx, pol, run, R, book, bars = main
    for e in run.trades:
        assert e.pnl == pytest.approx((e.exit - e.entry) * e.qty - sell_fees(e.qty, e.exit), abs=1e-6), e.key
    by_day = {r["session"]: r["pnl_venue"] for r in run.store.tables["daily_result"]}
    for row in book.daily:
        assert abs(by_day[str(row["date"])] - row["pnl"]) < 5.0, "per session within $5 of the replay"


# ================================================================ D1: whole cents at the broker
def test_d1_broker_prices_are_whole_cents(main):
    """The engine sends stop and target rounded to cents (Alpaca refuses sub-penny prices) and moves a trailing
    stop only by a cent or more; the replay uses the unrounded level. Exit prices differ by less than 1.5 cents
    and twice the rounded level is touched one bar before the replay's."""
    fx, pol, run, R, book, bars = main
    early = {}
    for e, r in zip(run.trades, R, strict=True):
        if e.reason == "time":
            continue
        if e.exit_m != r.exit_m:
            early[e.key] = (r.exit_m, e.exit_m)
        if e.reason == "target":  # a limit fill: exactly the cent-rounded target, no cost
            assert e.exit == round(e.exit, 2) and abs(e.exit - r.exit) <= 0.005 + EPS, e.key
        else:  # stop or trail: a market sell from a level within a cent and a half
            assert abs(e.exit - r.exit) < 0.015, e.key
    assert early == {(D1, "BBB", 175): (267, 266), (D2, "BBB", 150): (162, 161)}
    for o in run.sim.broker.orders.values():  # every price the venue ever held has two decimals
        for leg in o["legs"]:
            px = leg.get("stop_price") or leg.get("limit_price")
            assert px == f"{float(px):.2f}"


def test_d1_plain_stops_fill_at_the_rounded_level_through_the_cost_model(main):
    fx, pol, run, R, book, bars = main
    coid = {(str(pd.Timestamp(t["entry_ts"]).tz_convert(ET).date()), t["symbol"],
             minute(t["entry_ts"], str(pd.Timestamp(t["entry_ts"]).tz_convert(ET).date()))): t["features"]["coid"]
            for t in run.store.tables["trade"]}
    held = {o["client_order_id"]: float(o["legs"][0]["stop_price"]) for o in run.sim.broker.orders.values()
            if o["legs"]}
    n = 0
    for e, r in zip(run.trades, R, strict=True):
        if e.reason != "stop":
            continue
        level = held[coid[e.key]]  # the stop the venue held when it was hit
        replay_level = next(x.stop for x in book.trades if (str(x.date), x.symbol, x.entry_m) == r.key)
        assert level == round(level, 2) and abs(level - replay_level) < 0.015, e.key
        o, v = bars.at(e.symbol, e.date, e.exit_m, "o"), bars.at(e.symbol, e.date, e.exit_m, "v")
        want = sell_px(min(level, o), e.qty, float(bars.cand(e)["sigma_1m"]), v)
        assert e.exit == pytest.approx(want, abs=EPS), e.key
        n += 1
    assert n == 7


# ================================================================ D2: the time exit
def test_d2_time_exit_is_one_bar_later_at_the_open(main):
    fx, pol, run, R, book, bars = main
    timed = [(e, r) for e, r in zip(run.trades, R, strict=True) if e.reason == "time"]
    assert [e.key for e, _ in timed] == [(D1, "AAA", 345), (D2, "DDD", 355), (D3, "BBB", 365)]
    for e, r in timed:
        sigma = float(bars.cand(e)["sigma_1m"])
        assert (r.exit_m, e.exit_m) == (385, 386)
        c385, v385 = bars.at(e.symbol, e.date, 385, "c"), bars.at(e.symbol, e.date, 385, "v")
        o386, v386 = bars.at(e.symbol, e.date, 386, "o"), bars.at(e.symbol, e.date, 386, "v")
        assert r.exit == pytest.approx(sell_px(c385, r.qty, sigma, v385), abs=EPS)  # replay: the 15:55 close
        assert e.exit == pytest.approx(sell_px(o386, e.qty, sigma, v386), abs=EPS)  # engine: the 15:56 open


# ================================================================ D3 and D4: quantities
def test_quantities_are_equal_until_the_vix_rule_and_the_equity_basis_bite(main):
    fx, pol, run, R, book, bars = main
    E = run.trades
    assert fx.vix[pd.Timestamp(D3)] > 25 >= max(fx.vix[pd.Timestamp(D1)], fx.vix[pd.Timestamp(D2)])
    for e, r in zip(E, R, strict=True):
        if e.date != D3:
            assert e.qty == r.qty, e.key
    third = [(e.key[1:], r.qty, e.qty) for e, r in zip(E, R, strict=True) if e.date == D3]
    assert third == [
        (("AAA", 75), 48.5, 48),  # D3: 97 halved, floored
        (("BBB", 75), 126.5, 126),  # D3
        (("CCC", 75), 27.0, 26),  # D4: the engine's gate allowed 53 shares, the replay's 54
        (("CCC", 235), 28.0, 28),
        (("BBB", 240), 129.0, 129),
        (("BBB", 365), 130.5, 130),  # D3
    ]
    assert all(isinstance(e.qty, int) for e in E), "whole shares only (decision 15)"


def test_d4_reported_pnl_prices_costs_twice_and_lowers_the_equity_the_gate_sizes_on(main):
    """Engine P&L is min(venue, shadow), built for an optimistic paper venue. SimBroker's fills already carry
    the cost model, so the shadow leg prices the exit costs a second time. S77 (ledger: P&L from fills) removes
    this; until then numbers from a SimBroker run must read the venue P&L."""
    fx, pol, run, R, book, bars = main
    E = run.trades
    assert all(e.reported <= e.pnl + 0.005 for e in E)
    e = next(e for e in E if e.key == (D2, "DDD", 165))
    r = next(r for r in R if r.key == e.key)
    assert e.pnl > 10 and r.pnl > 10 and e.reported < 0, "a $13 winner is reported as a loss (it counts in the streak)"
    venue, reported, replay = sum(e.pnl for e in E), sum(e.reported for e in E), sum(r.pnl for r in R)
    assert abs(venue - replay) < 10.0, "the venue P&L is the replay's P&L up to D1-D4"
    assert venue - reported > 100.0, "the reported P&L is more than $100 lower on 18 trades"
    starts = {e["payload"]["day"]: e["payload"]["start_equity"] for e in run.store.tables["event"]
              if e["type"] == "session_start"}
    assert starts[D3] == pytest.approx(99_948.61, abs=0.005)
    assert book.daily[2]["start"] == pytest.approx(100_021.73, abs=0.005)
    # the first three entries of 17 Sep are sized on the session's start equity: CCC at 277.70 is the casualty
    c = bars.cand(next(e for e in E if e.key == (D3, "CCC", 75)))
    prop = Proposal("CCC", Side.BUY, float(c["price"]), float(c["price"]) - 1.5 * float(c["atr5"]))

    def shares(equity: float) -> int:
        snap = Snapshot(equity=equity, day_start_equity=equity, open_positions=0, symbol_exposure=0.0,
                        open_risk=0.0, quote_age_s=0.0, kill_switch=False, reconciled=True, market_open=True)
        return size_position(prop, snap, pol)[0]

    assert (shares(book.daily[2]["start"]), shares(starts[D3])) == (54, 53)
    assert (54 * 0.5, math.floor(53 * 0.5)) == (27.0, 26)


# ================================================================ D5 / A4.10: the bracket on the fill bar
def test_d5_bracket_is_sent_around_the_reference_and_re_anchored_on_the_fill(main):
    """The engine submits stop and target around the decision price and learns the fill one tick later; only
    then does it move both legs to fill - risk and fill + 2R. During the fill bar the venue therefore holds
    the reference-based prices (the replay uses the fill-based ones from the fill bar on). On the fixture no
    fill bar comes near either level, so no trade differs."""
    fx, pol, run, R, book, bars = main
    orders = {o["client_order_id"]: o["legs"] for o in run.store.tables["order"]}
    moved = 0
    for row in run.store.tables["trade"]:
        f = row["features"]
        sent = orders[f["coid"]]
        ref, risk = f["ref"], f["risk_ps"]
        assert abs(sent["stop"] - (ref - risk)) <= 0.005 + EPS and abs(sent["target"] - (ref + 2 * risk)) <= 0.005 + EPS
        day = str(pd.Timestamp(row["entry_ts"]).tz_convert(ET).date())
        m = minute(row["entry_ts"], day)
        low, high = bars.at(row["symbol"], day, m, "l"), bars.at(row["symbol"], day, m, "h")
        fill = row["entry_px"]
        for stop, target in ((sent["stop"], sent["target"]), (fill - risk, fill + 2 * risk)):
            assert low > stop and high < target + 0.01, "the fill bar triggers neither set of levels"
        moved += abs(round(fill - risk, 2) - sent["stop"]) >= 0.01
    assert moved >= 12, "most fills differ from the reference by a cent or more, so the legs were replaced"


# ================================================================ A4.4: quote freshness
def test_a4_4_stale_quote_blocks_the_entry_the_replay_takes(tmp_path):
    fx = sf.load()
    run = engine_run(tmp_path, fx, (D1,), minutes=75)
    assert [t.key for t in run.trades] == [(D1, "BBB", 70), (D1, "CCC", 70)]

    stale = sf.load()
    old = engine_run(tmp_path / "stale", stale, (D1,), minutes=75, quote_age=timedelta(seconds=6))
    assert old.trades == []
    checks = [g["checks"]["quote_age_s"] for g in old.store.tables["gate_run"]]
    assert len(checks) == 2 and all(c["state"] == "fail" and c["actual"] == 6.0 and c["limit"] == 5.0 for c in checks)


# ================================================================ A4.5: bar freshness
def test_a4_5_a_symbol_without_a_bar_for_three_minutes_is_refused_live_and_traded_in_replay(tmp_path):
    fx = sf.load()
    o = pd.Timestamp(open_utc(D1))
    f = fx.frames["CCC"]
    fx.frames["CCC"] = f[~f["ts"].isin([o + timedelta(minutes=k) for k in (67, 68, 69)])].reset_index(drop=True)
    R = replay_trades(sf.replay(fx, load_profiles()["balanced"], days=(D1,)))
    assert (D1, "CCC", 70) in [t.key for t in R], "the replay fills the gap with the last close and trades"
    run = engine_run(tmp_path, fx, (D1,), minutes=75)
    assert [t.key for t in run.trades] == [(D1, "BBB", 70)]
    sig = next(s for s in run.store.tables["signal"] if s["symbol"] == "CCC")
    gate = next(g for g in run.store.tables["gate_run"] if g["signal_id"] == sig["id"])
    assert gate["approved"] is False and gate["checks"]["data_ok"]["state"] == "fail"


# ================================================================ A4.6: filters only the engine has
def test_a4_6_level_price_and_spread_filters_exist_only_in_the_engine(tmp_path):
    """Scaling a symbol's prices leaves every return-based feature unchanged, so the replay still trades it.
    The engine's level defaults refuse a price under $5 and a quoted spread above 5 bp (one cent on $15)."""
    fx = sf.load()
    for sym, k in (("BBB", 15.0), ("CCC", 20.0)):  # BBB about $4, CCC about $15
        f = fx.frames[sym].copy()
        for col in ("o", "h", "l", "c", "vw"):
            f[col] = f[col] / k
        f["v"] = f["v"] * k * 2  # keep the dollar volume above the liquidity floor
        fx.frames[sym] = f
    R = replay_trades(sf.replay(fx, load_profiles()["balanced"], days=(D1,)))
    assert {(D1, "BBB", 70), (D1, "CCC", 70)} <= {t.key for t in R}
    run = engine_run(tmp_path, fx, (D1,), minutes=75)
    assert run.trades == []
    veto = {s["symbol"]: s["rules_veto"] for s in run.store.tables["signal"]}
    assert veto == {"BBB": "price below your minimum ($5)", "CCC": "spread above your limit (5 bp)"}


# ================================================================ A4.7: buying power
def test_a4_7_the_engine_also_sizes_to_the_brokers_cash(tmp_path):
    fx = sf.load()
    run = engine_run(tmp_path, fx, (D1,), minutes=75, cash=20_000.0)
    R = {t.key: t for t in replay_trades(sf.replay(fx, load_profiles()["balanced"], days=(D1,)))}
    E = {t.key: t for t in run.trades}
    assert E[(D1, "CCC", 70)].qty == R[(D1, "CCC", 70)].qty == 48, "the first order fits the account"
    assert (R[(D1, "BBB", 70)].qty, E[(D1, "BBB", 70)].qty) == (251.0, 88)
    gates = {g["qty"]: g["binding"] for g in run.store.tables["gate_run"]}
    assert gates == {48: "position_cap", 88: "buying_power"}
    assert float(run.sim.broker.account()["cash"]) > 0


# ================================================================ A4.11: half-days
def test_a4_11_engine_follows_an_early_close_the_replay_does_not(tmp_path):
    fx = sf.load()
    cal = {d: ("09:30", "13:00" if d == D1 else "16:00") for d in fx.sessions}
    run = engine_run(tmp_path, fx, (D1,), calendar=cal)
    assert run.log.ticks == 212 and run.sim.engine.day.n_min == 210
    E = {t.key: t for t in run.trades}
    R = {t.key: t for t in replay_trades(sf.replay(fx, load_profiles()["balanced"], days=(D1,)))}
    assert sorted(R) == [(D1, "AAA", 345), (D1, "BBB", 70), (D1, "BBB", 175), (D1, "CCC", 70), (D1, "EEE", 185)]
    assert sorted(E) == sorted(k for k in R if k[2] <= 195), "no entry after 12:45 on a 13:00 close"
    for key in ((D1, "BBB", 175), (D1, "EEE", 185)):  # open at 12:55: flattened, sold at the 12:56 open
        assert (E[key].reason, E[key].exit_m) == ("time", 206)
    assert (R[(D1, "BBB", 175)].reason, R[(D1, "BBB", 175)].exit_m) == ("trail", 267)
    assert (R[(D1, "EEE", 185)].reason, R[(D1, "EEE", 185)].exit_m) == ("stop", 212)


# ================================================================ A4.13: run drawdown
def test_a4_13_run_drawdown_ends_the_run_in_the_engine_only(tmp_path):
    """With a 0.05 % drawdown limit the engine flattens and ends the run at 12:53; the replay's gate only
    refuses entries while the drawdown lasts and trades again later. (The tick after the run ends raises in
    `Engine._risk_halts` because `self.run` is already None: an engine bug outside this story, reported in the
    PR. `strict=False` gives the live loop's handling, so this test holds before and after that fix.)"""
    fx = sf.load()
    pols = load_profiles()
    pol = dataclasses.replace(pols["balanced"], run_drawdown_end_pct=0.05)
    run = engine_run(tmp_path, fx, (D1,), policies={**pols, "balanced": pol}, strict=False)
    E = {t.key: t for t in run.trades}
    R = {t.key: t for t in replay_trades(sf.replay(fx, pol, days=(D1,)))}
    assert len(run.log.errors) <= 1
    assert sorted(R) == [(D1, "AAA", 345), (D1, "BBB", 70), (D1, "BBB", 175), (D1, "CCC", 70), (D1, "EEE", 185)]
    assert sorted(E) == [(D1, "BBB", 70), (D1, "BBB", 175), (D1, "CCC", 70), (D1, "EEE", 185)]
    assert {E[k].reason for k in ((D1, "BBB", 175), (D1, "EEE", 185))} == {"run_drawdown"}
    assert {E[k].exit_m for k in ((D1, "BBB", 175), (D1, "EEE", 185))} == {204}
    assert run.store.tables["run"][0]["status"] == "ended" and run.sim.broker.positions() == {}
    assert any(h["kind"] == "run_drawdown" for h in run.store.tables["halt"])


# ================================================================ A3: the daily stop
def test_a3_daily_stop_same_minute_engine_sells_one_bar_later(tmp_path):
    """CCC gaps down 5 % at 10:48 while BBB is also held: CCC's stop is gapped through and fills at the open
    in both; the loss breaks a 0.5 % daily stop in the same minute in both. The replay then sells BBB at that
    bar's close, the engine one bar later at the open; neither enters again that day."""
    fx = sf.load()
    f = fx.frames["CCC"].copy()
    hit = f["ts"] >= pd.Timestamp(open_utc(D1)) + timedelta(minutes=78)
    for col in ("o", "h", "l", "c", "vw"):
        f.loc[hit, col] = (f.loc[hit, col] * 0.95).round(2)
    fx.frames["CCC"] = f
    pols = load_profiles()
    pol = dataclasses.replace(pols["balanced"], max_daily_loss_pct=0.5)
    run = engine_run(tmp_path, fx, (D1,), policies={**pols, "balanced": pol}, minutes=200)
    E = {t.key: t for t in run.trades}
    R = {t.key: t for t in replay_trades(sf.replay(fx, pol, days=(D1,)))}
    assert sorted(E) == sorted(R) == [(D1, "BBB", 70), (D1, "CCC", 70)], "halted: nothing after the stop"
    bars = Bars(fx)
    e, r = E[(D1, "CCC", 70)], R[(D1, "CCC", 70)]
    gap_open = bars.at("CCC", D1, 78, "o")
    assert (e.reason, e.exit_m) == (r.reason, r.exit_m) == ("stop", 78)
    assert e.exit == pytest.approx(r.exit, abs=EPS) and e.exit < gap_open < r.entry * 0.96, "filled at the open"
    e, r = E[(D1, "BBB", 70)], R[(D1, "BBB", 70)]
    assert (r.reason, r.exit_m) == ("daily_stop", 78) and (e.reason, e.exit_m) == ("daily_stop", 79)
    sigma = float(bars.cand(e)["sigma_1m"])
    at_close = sell_px(bars.at("BBB", D1, 78, "c"), r.qty, sigma, bars.at("BBB", D1, 78, "v"))
    at_open = sell_px(bars.at("BBB", D1, 79, "o"), e.qty, sigma, bars.at("BBB", D1, 79, "v"))
    assert r.exit == pytest.approx(at_close, abs=EPS) and e.exit == pytest.approx(at_open, abs=EPS)
    assert [h["kind"] for h in run.store.tables["halt"]] == ["daily_stop"] and run.sim.engine.day.halted
