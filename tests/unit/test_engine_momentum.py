"""Issue #161: the momentum rules as engine strategies, the daily runner path through `SimBroker`, and parity with
the research harness (`replay/multiday_core.py`).

Parity, as stated in the PR: on the committed fixture (tests/fixtures/daily_momentum: a daily store with splits,
dividends, data holes, a late index joiner, a leaver, a delisting and a forced exit before a disputed split) the
engine reproduces the harness ORDER BY ORDER (same sessions, kinds, symbols, share targets and sell reasons) and
FILL BY FILL (same sessions, symbols, sides and share counts; prices within 1e-9 relative), with the daily equity
within one cent, for all three versions, base and stress fills, 100% and 60% caps. Amendment 3 of both
pre-registrations (a 3% cash reserve; a buy above the cash at the print cut to whole shares; split fractions paid
in cash) is implemented the same way in both, so the two differences first found here (the harness's fill-time cash
cut, the kept half share) are gone; what remains is listed in `DIFFERENCES`. Small synthetic data only."""

from __future__ import annotations

import contextlib
import io
import json
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kaibot.broker.protocol import BrokerError, OrderRequest
from kaibot.broker.sim import NY
from kaibot.engine import daily_report as dr
from kaibot.engine import research
from kaibot.engine.daily import DailyData, run_daily
from kaibot.ingest.daily_store import DailyPanel
from kaibot.replay import multiday as md
from kaibot.replay import multiday_core as mc
from kaibot.replay.multiday_rules import book_digest
from kaibot.risk.profiles import load_profiles
from kaibot.strategies import momentum as mom
from kaibot.strategies import registry
from kaibot.strategies.base import Holding, Mode

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "daily_momentum"
POL = load_profiles()["balanced"]
N, FIRST, I0 = 560, "2022-01-03", 280

# Every difference between the engine path and the harness, and where each is pinned.
DIFFERENCES = {
    "sale_waiting_cap": "a sale that finds no bar on a decision day: the harness still counts it as held at the fill "
                        "(names and exposure caps); the sizing rule counts it as sold, as the harness's own plan "
                        "does (does not occur on the fixture; documented in engine/daily.atr_risk_sizing)",
    "dispute_netting": "a resize on the session a forced split-dispute exit sells the name: the engine sends the one "
                       "sale, the harness trades the resize and then sells (does not occur on the fixture)",
}


# ---------------------------------------------------------------- the committed fixture


def fixture_panel() -> tuple[DailyPanel, list[str], dict]:
    """Thirty random walks priced $100-$600 and SPY (the generator of test_core_pit.raw_market, seed 5), with
    events on names the rules hold: a 4:1 split (P26, 400), a 3:2 split (P22, 420), dividends (P26, P22, SPY),
    six one-day holes in held names, a delisting (P28's bars stop after 530, terminal -40%), a forced exit before a
    disputed split (P20 at 534, no entries 535-550), a leaver (P27 not a member from 545) and a late joiner
    (P09 a member from 300)."""
    rng = np.random.default_rng(5)
    n_stocks = 30
    stocks = [f"P{k:02d}" for k in range(n_stocks)]
    mkt = rng.normal(0.0006, 0.007, N)
    mkt[400:440] -= 0.004
    drift = np.linspace(-0.0004, 0.0015, n_stocks)
    r = drift + np.outer(mkt, rng.uniform(0.7, 1.4, n_stocks)) + rng.normal(0, 0.008, (N, n_stocks))
    c = np.exp(np.cumsum(np.column_stack([r, mkt]), axis=0)) * np.r_[np.linspace(100, 600, n_stocks), 400.0]
    o = np.vstack([c[:1], c[:-1]]) * np.exp(rng.normal(0, 0.003, c.shape))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.003, c.shape)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.003, c.shape)))
    v = np.full(c.shape, 2e6)
    dates = np.busday_offset(np.datetime64(FIRST), np.arange(N), roll="forward").astype("datetime64[D]")
    syms = [*stocks, "SPY"]
    actions = [("P26", 400, 4.0, None), ("P22", 420, 1.5, None), ("P26", 450, None, 2.0), ("P22", 500, None, 1.0),
               ("SPY", 300, None, 1.6), ("SPY", 426, None, 1.8)]
    acts = []
    for sym, i, ratio, cash in actions:
        j = syms.index(sym)
        if ratio:
            for a in (o, h, lo, c):
                a[i:, j] /= ratio
            v[i:, j] *= ratio
        acts.append((sym, dates[i], ratio, cash))
    for sym, i in (("P22", 435), ("P26", 470), ("P18", 505), ("P08", 527), ("P28", 517), ("P16", 527)):
        for a in (o, h, lo, c, v):
            a[i, syms.index(sym)] = np.nan
    for a in (o, h, lo, c, v):
        a[531:, syms.index("P28")] = np.nan
    meta = {"symbols": syms, "stocks": stocks, "market": "SPY", "minute_share": 0.02,
            "window": [str(dates[I0]), str(dates[-1])],
            "not_member": {"P20": [[535, 551]], "P27": [[545, N]], "P09": [[0, 300]]},
            "terminal_returns": {"P28": -0.4}, "exits": {"P20": [534]}}
    return DailyPanel.make(dates, syms, o, h, lo, c, v, actions=acts), acts, meta


def write_fixture(root: Path) -> None:
    """The fixture as a daily store in #124's layout plus fixture.json."""
    panel, acts, meta = fixture_panel()
    for j, sym in enumerate(panel.symbols):
        has = np.isfinite(panel.c[:, j])
        f = root / "bars" / f"symbol={sym}.parquet"
        f.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table({"date": pa.array(panel.dates[has]),
                                 **{k: getattr(panel, k)[has, j] for k in ("o", "h", "l", "c", "v")}}), f)
    pq.write_table(pa.table({
        "key": [a[0] for a in acts], "type": ["forward_split" if a[2] else "cash_dividend" for a in acts],
        "ex_date": pa.array(np.array([a[1] for a in acts], dtype="datetime64[D]")),
        "ratio": [float(a[2] or np.nan) for a in acts], "cash": [float(a[3] or np.nan) for a in acts],
        "dup": [False] * len(acts)}), root / "corporate_actions.parquet")
    (root / "fixture.json").write_text(json.dumps(meta, indent=1) + "\n")


def test_the_committed_fixture_is_the_generator_output(tmp_path):
    write_fixture(tmp_path)
    for f in sorted(tmp_path.rglob("*")):
        if f.is_file():
            g = FIXTURE / f.relative_to(tmp_path)
            if f.suffix == ".parquet":
                assert pq.read_table(g).to_pandas().equals(pq.read_table(f).to_pandas()), g
            else:
                assert g.read_text() == f.read_text(), g


@pytest.fixture(scope="module")
def fx():
    m, info = dr.load_fixture(FIXTURE)
    data = DailyData.from_market(m)
    i0 = int(np.searchsorted(data.dates, np.datetime64(info["window"][0])))
    return m, data, i0, len(data.dates) - 1


# ---------------------------------------------------------------- the harness, with every fill recorded


class RecordingEngine(md.Engine):
    """The research harness's simulator, unchanged, writing down each fill: (session, symbol, side, qty, price)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fills: list[tuple] = []

    def sell(self, i, j, price, reason, vol, gap=False, qty=None, venue="close", costless=False):
        super().sell(i, j, price, reason, vol, gap, qty, venue, costless)
        t = self.book.trades[-1]
        self.fills.append((i, self.b.symbols[j], "sell", t.qty, t.exit, reason))

    def _buy_fill(self, i, j, cash0, q, reason):
        if q:
            self.fills.append((i, self.b.symbols[j], "buy", float(abs(q)), (cash0 - self.cash) / abs(q), reason))

    def enter(self, i, j, eq, config, caps, **kw):
        cash0 = self.cash
        q = super().enter(i, j, eq, config, caps, **kw)
        self._buy_fill(i, j, cash0, q, "buy")
        return q

    def adjust(self, i, j, target, eq, caps, **kw):
        cash0 = self.cash
        q = super().adjust(i, j, target, eq, caps, **kw)
        if q > 0:
            self._buy_fill(i, j, cash0, q, "resize")
        return q


def harness(m: mc.Market, version: str, i0: int, i1: int, fill: str = "base", cap: float = 100.0):
    where, factor = mc.FILLS[fill]
    spec = mc.VERSIONS[version]
    rule = mc.CoreRule(m.signals, POL, spec, where, cap)
    eng = RecordingEngine(m.raw, m.ind, POL, rule, lambda i: spec, 100_000.0, cost_mult=factor,
                          actions=m.actions, terminal=m.terminal, exits=m.exits)
    return eng.run(i0, i1), rule, eng.fills


def compare(m, data, version, i0, i1, fill="base", cap=100.0) -> dict:
    book, rule, h_fills = harness(m, version, i0, i1, fill, cap)
    run = run_daily(mom.VERSIONS[version], data, POL, i0, i1, fill=fill, exposure_pct=cap)
    h_orders = [(i, k, m.raw.symbols[j], x) for i, k, j, x in rule.orders]
    e_fills = [(f["i"], f["symbol"], f["side"], f["qty"], f["px"], f["reason"]) for f in run.fills]

    def key(f):
        return f[0], f[1], f[2]

    hs, es = sorted(h_fills, key=key), sorted(e_fills, key=key)
    fills_diff = [(a, b) for a, b in zip(hs, es, strict=False)
                  if key(a) != key(b) or not math.isclose(a[3], b[3], abs_tol=1e-9)
                  or not math.isclose(a[4], b[4], rel_tol=1e-9)]
    eq_h = np.array([r["end"] for r in book.daily])
    eq_e = np.array([r["end"] for r in run.book.daily])
    return {"book": book, "run": run, "orders_equal": h_orders == run.orders, "n_orders": len(h_orders),
            "h_orders": h_orders, "fills_equal": len(hs) == len(es) and not fills_diff, "fills_diff": fills_diff,
            "n_fills": len(hs), "h_fills": hs, "e_fills": es, "max_equity_gap": float(np.max(np.abs(eq_h - eq_e))),
            "digest_equal": book_digest(book) == book_digest(run.book)}


# ---------------------------------------------------------------- 1. the strategies


def test_the_rules_are_multi_day_target_list_plugins_with_the_frozen_settings():
    tm, mb, nf = (mom.VERSIONS[k] for k in ("trend_momentum", "mom_12_1_band", "mom_12_1_band_nofilter"))
    for s in (tm, mb, nf):
        assert s.holding is Holding.MULTI_DAY and s.mode is Mode.SHADOW and s.atr_risk == 0.001
        assert registry.get(s.name) is s and s.name not in registry.names()
    assert (tm.max_names, tm.enter_top, tm.exit_top, tm.reg_window, tm.resize, tm.market_filter) == (
        30, 0.20, 0.20, 90, True, True)
    assert (mb.max_names, mb.enter_top, mb.exit_top, mb.lookbacks, mb.market_filter, nf.market_filter) == (
        20, 0.10, 0.20, (126, 189, 252), True, False)
    for v in ("trend_momentum", "mom_12_1_band"):  # the neighbours are the pre-registered ones
        got = {s.name.split("~")[1]: s for s in mom.neighbours(v)}
        want = {sp.name.split("~")[1]: sp for sp in mc.neighbours(v)}
        assert set(got) == set(want)
        for n, sp in want.items():
            for f in ("max_names", "enter_top", "exit_top", "market_filter", "atr_risk"):
                assert getattr(got[n], f) == getattr(sp, f), (v, n, f)
    src = (ROOT / "src/kaibot/strategies/momentum.py").read_text()
    assert "OrderRequest" not in src and "submit" not in src and "floor" not in src  # no order or sizing code


def test_decision_days_are_wednesday_weekly_and_first_session_monthly():
    assert mom.is_weekly_decision(date(2024, 1, 9), date(2024, 1, 10))  # Tue -> Wed
    assert not mom.is_weekly_decision(date(2024, 1, 10), date(2024, 1, 11))  # Wed -> Thu: already decided
    assert mom.is_weekly_decision(date(2024, 7, 2), date(2024, 7, 5))  # Wednesday 4 July closed: Friday
    assert mom.is_monthly_decision(date(2024, 1, 31), date(2024, 2, 1))
    assert not mom.is_monthly_decision(date(2024, 2, 1), date(2024, 2, 2))
    assert mom.TrendMomentum().resize_week(date(2024, 1, 10)) == (mom.week_number(date(2024, 1, 10)) % 2 == 0)


def test_the_strategy_reads_nothing_after_the_decisions_close(fx):
    """Change every bar after session k: the target list decided after the close of k is the same."""
    m, data, i0, i1 = fx
    k = next(k for k in range(i0, i1) if mom.is_weekly_decision(data.day(k), data.day(k + 1)))
    from kaibot.strategies.base import Event
    from kaibot.strategies.momentum import DailyContext

    def decide(d):
        return mom.VERSIONS["trend_momentum"].on_event(DailyContext(Event.AFTER_CLOSE, d.day(k), d.day(k + 1),
                                                                    d.view(k), {"P26": 10.0}))
    a = decide(data)
    m2, _ = dr.load_fixture(FIXTURE)
    for arr in (m2.sig.c, m2.sig.h, m2.sig.l, m2.raw.c, m2.raw.v):
        arr[k + 1:] *= 1.7
    b = decide(DailyData.from_market(m2))
    assert a is not None and a.weights == b.weights and a.features == b.features


def test_the_signals_equal_the_harness_on_every_decision_day(fx):
    m, data, i0, i1 = fx
    sig = m.signals
    for v in ("trend_momentum", "mom_12_1_band"):
        strat, spec = mom.VERSIONS[v], mc.VERSIONS[v]
        score_h = sig.score(spec)
        for i in np.flatnonzero(sig.decision_days(spec))[1:]:
            k = int(i) - 1
            score, ok, _ = strat.signals(data.view(k))
            assert (ok == sig.ok[k]).all()
            assert np.allclose(score, score_h[k], rtol=1e-12, atol=0, equal_nan=True), (v, k)
            assert (mom.ranking(score, ok) == mc.CoreRule(sig, POL, spec).ranking(k)).all()
        assert np.allclose(data.atr20, sig.atr20, equal_nan=True, rtol=1e-9, atol=0)


# ---------------------------------------------------------------- 2. parity with the research harness


@pytest.mark.parametrize("version", ["trend_momentum", "mom_12_1_band", "mom_12_1_band_nofilter"])
@pytest.mark.parametrize("fill,cap", [("base", 100.0), ("base", 60.0), ("stress", 60.0)])
def test_the_engine_reproduces_the_harness_order_by_order_and_fill_by_fill(fx, version, fill, cap):
    m, data, i0, i1 = fx
    c = compare(m, data, version, i0, i1, fill, cap)
    assert c["orders_equal"] and c["n_orders"] > 10
    assert c["fills_equal"] and c["n_fills"] > 10, c["fills_diff"][:3]
    assert c["max_equity_gap"] <= 0.01 and c["digest_equal"]
    assert c["run"].ledger.overdrafts == [] and c["run"].cash_gap < 1e-6  # never below zero; venue = ledger
    reasons = {f[5] for f in c["e_fills"]}
    assert {"delisted", "end_of_data"} <= reasons
    if version == "trend_momentum":
        assert {"split_dispute", "gap", "trend", "rank", "resize"} & reasons >= {"split_dispute", "trend", "rank",
                                                                                 "resize"}


def test_the_fixture_exercises_every_event_on_held_positions(fx):
    m, data, i0, i1 = fx
    c = compare(m, data, "trend_momentum", i0, i1)
    run = c["run"]
    kinds = {k for k, _ in run.ledger.entries}
    assert {"run", "fill", "dividend", "split", "mark"} <= kinds
    assert run.book.dividends > 0 and c["book"].dividends == pytest.approx(run.book.dividends)
    sold = {(f["i"], f["symbol"]) for f in run.fills if f["side"] == "sell"}
    ordered = {(i, s) for i, k, s, _ in run.orders if k == "sell"}
    assert ordered - sold  # a sale that found no bar on its day ...
    assert any(f["reason"] in ("rank", "trend", "gap") and (f["i"], f["symbol"]) not in ordered for f in run.fills)
    # ... filled on a later session (the harness's "waiting" sale): the venue was sent it again


def test_a_fraction_of_a_share_after_a_split_is_paid_in_cash_in_both(fx):
    """Amendment 3: P22's 3:2 split leaves half a share; harness and engine both pay it out at the adjusted close."""
    m, data, i0, i1 = fx
    c = compare(m, data, "mom_12_1_band", i0, i1)
    lieu = [f for f in c["h_fills"] if f[5] == "cash_in_lieu"]
    assert lieu == [f for f in c["e_fills"] if f[5] == "cash_in_lieu"]
    assert len(lieu) == 1 and lieu[0][1] == "P22" and lieu[0][3] == pytest.approx(0.5)
    assert all(float(t.qty).is_integer() for t in c["book"].trades if t.reason != "cash_in_lieu")
    assert c["fills_equal"] and c["digest_equal"] and c["book"].equity == pytest.approx(102629.6022, abs=1e-3)


def core_today():
    import sys

    sys.path.insert(0, str(ROOT / "tests" / "unit"))
    import test_multiday_core as tc

    b, stocks = tc.core_market()
    return mc.Market.from_bars(b, b.cols(stocks), b.col("SPY")), tc.I0, len(b.dates) - 1


def test_the_cash_reserve_keeps_both_inside_the_cash_and_identical():
    """Amendment 3 on #127's market at a 100% cap, where the harness used to cut buys at the fill and the engine
    overdrew: both now keep 3% of equity back, the engine never goes below zero, and they agree exactly."""
    m, i0, i1 = core_today()
    data = DailyData.from_market(m)
    for v in ("trend_momentum", "mom_12_1_band_nofilter"):
        c = compare(m, data, v, i0, i1)
        assert c["orders_equal"] and c["fills_equal"] and c["digest_equal"] and c["max_equity_gap"] <= 0.01
        assert c["run"].ledger.overdrafts == [] and c["run"].cash_gap < 1e-6
        assert min(r["end"] - r["exposure"] * r["end"] for r in c["run"].book.daily) >= 0  # cash never negative
    assert compare(m, data, "trend_momentum", i0, i1)["book"].equity == pytest.approx(125272.0285, abs=1e-3)


def test_a_buy_above_the_cash_at_the_print_is_cut_the_same_way_in_both(monkeypatch):
    """With no reserve the last buys of a decision outgrow the cash at the auction: the harness cuts them at the
    fill, `SimBroker(cash_at="fill")` cuts the auction order to the whole shares that fit, at the same price."""
    from kaibot.engine import daily

    monkeypatch.setattr(mc, "CASH_RESERVE", 0.0)
    monkeypatch.setattr(daily, "CASH_RESERVE", 0.0)
    m, i0, i1 = core_today()
    data = DailyData.from_market(m)
    c = compare(m, data, "trend_momentum", i0, i1)
    cut = [o for o in c["run"].broker.orders.values() if o["side"] == "buy" and o["status"] == "canceled"]
    assert cut and any(float(o["filled_qty"]) < float(o["qty"]) for o in cut)
    assert c["orders_equal"] and c["fills_equal"] and c["digest_equal"] and c["max_equity_gap"] <= 0.01
    assert c["run"].ledger.overdrafts == [] and c["run"].cash_gap < 1e-6


def test_logged_runs_from_before_an_amendment_stay_attributable_to_the_rule():
    """Prereg hash handling: every committed version of both files is today's file minus appended amendments, so
    Gate A accepts trial rows logged with an earlier hash and reports them; an edit of the text does not pass."""
    from kaibot.evaluation import prereg

    for rule in ("trend_momentum", "mom_12_1_band"):
        p = prereg.load(rule, ROOT)
        assert len(p.amendments) >= 3 and "Amendment 3" in p.amendments[2]
        assert p.first_committed_at < p.committed_at and len(p.versions) >= 4
        assert all(amended for _, _, amended in p.versions) and all(p.accepts(v) for v, _, _ in p.versions)
        assert p.versions[-1][0] == p.sha256 and not p.accepts("0" * 64)
    base = "# Pre-registration\n\n## 2. Frozen construction\n\nATR 20\n"
    assert prereg.amended_only(base, base + "\n## Amendment 1 (12 Nov 2026)\n\nmore text\n")
    assert not prereg.amended_only(base, base.replace("20", "14") + "\n## Amendment 1 (12 Nov 2026)\n\nx\n")
    assert not prereg.amended_only(base, base + "\n## 8. A new section\n\nx\n")


# ---------------------------------------------------------------- 3. the venue's closing-auction rules


def test_orders_are_whole_share_closing_auction_orders_sent_before_1550(fx):
    m, data, i0, i1 = fx
    run = run_daily(mom.VERSIONS["trend_momentum"], data, POL, i0, i1)
    orders = list(run.broker.orders.values())
    assert orders and all(o["time_in_force"] == "cls" and float(o["qty"]).is_integer() for o in orders)
    sent = {datetime.fromisoformat(o["submitted_at"]).astimezone(NY).time() for o in orders}
    assert sent == {datetime(2024, 1, 1, 9, 0).time()}
    for f in run.broker.fills:  # filled at the closing print (the 15:59 bar)
        assert datetime.fromisoformat(f["transaction_time"]).astimezone(NY).strftime("%H:%M") == "15:59"
    bk = run.broker
    late = datetime.combine(data.day(i1), datetime(2024, 1, 1, 15, 55).time(), NY)
    with pytest.raises(BrokerError, match="3:50pm"):
        bk.check(OrderRequest("P00", "buy", 1, "market", "cls"), late)
    with pytest.raises(BrokerError, match="DAY"):
        bk.check(OrderRequest("P00", "buy", 1.5, "market", "cls"), late.replace(hour=9))


# ---------------------------------------------------------------- 4. live: refused


def test_the_live_engine_refuses_to_load_multi_day_strategies():
    from kaibot.engine.core import Engine

    for name in ("trend_momentum", "mom_12_1_band", "mom_12_1_band_nofilter"):
        with pytest.raises(registry.RefusedStrategy):
            registry.live([name, "dip_reversal"])
        with pytest.raises(registry.RefusedStrategy):
            Engine(None, None, None, {}, set(), strategies={"balanced": ["dip_reversal", name]})
    assert [s.name for s in registry.live(["dip_reversal"])] == ["dip_reversal"]
    assert not set(registry.names()) & {s.name for s in registry.MULTI_DAY}  # no level, SQL row or owner toggle
    assert not registry.owner_toggles() & {s.name for s in registry.MULTI_DAY}
    with pytest.raises(SystemExit, match="--universe"):
        research.run("trend_momentum")  # the intraday research path does not take them either


# ---------------------------------------------------------------- 5. the research command


def test_the_research_command_runs_the_engine_and_reports_gate_b_on_the_fixture(tmp_path):
    buf = io.StringIO()
    out = tmp_path / "rep.json"
    with contextlib.redirect_stdout(buf):
        assert research.main(["--strategy", "trend_momentum", "--control-runs", "3", "--out", str(out)]) == 0
    rep = json.loads(out.read_text())
    assert rep["status"].startswith("engine + SimBroker") and rep["universe"] == "fixture"
    assert [g["mark"] for g in rep["gate_b"]] == [1, 2, 3, 4, 5, 6]
    assert {r["label"] for r in rep["rows"]} == {f"trend_momentum|base|{c}x|cap100|hold" for c in ("1", "2", "3", "0")}
    assert set(rep["neighbours"]) == {"reg60", "reg125", "exit30", "nofilter", "risk15"}
    assert rep["control"]["kind"] == "exposure_matched" and rep["control"]["runs"] == 3
    assert "spy_total_return" in rep["benchmarks"]  # the fixture's SPY pays dividends
    base = rep["rows"][0]
    m = dr.load_fixture(FIXTURE)[0]
    data = DailyData.from_market(m)
    i0 = int(np.searchsorted(data.dates, np.datetime64(rep["oos_window"][0])))
    again = run_daily(mom.VERSIONS["trend_momentum"], data, POL, i0, len(data.dates) - 1)
    assert base["final_equity"] == pytest.approx(again.book.equity, abs=0.01)  # the report is the engine's fills
    assert "| Gate B mark |" in buf.getvalue() and "prereg" in rep and len(rep["prereg"]["sha256"]) == 64


def test_the_research_command_on_a_store_writes_the_report_and_trial_rows(tmp_path, monkeypatch):
    """`--universe pit` on a store: the same path the stakeholder's ten-year command takes."""
    import shutil

    from kaibot.ingest import sp500_pit

    data_dir = tmp_path / "data"
    shutil.copytree(FIXTURE, data_dir / "daily")
    panel, _, meta = fixture_panel()
    d = panel.dates
    stocks = meta["stocks"]
    log = [sp500_pit.Change(date(2021, 12, 1), tuple(s for s in stocks if s != "P09")),
           sp500_pit.Change(d[300].astype(object), ("P09",), ()),
           sp500_pit.Change(d[545].astype(object), (), ("P27",))]
    ms = sp500_pit.Membership(log, {}, stocks, date(2024, 2, 1))
    monkeypatch.setattr(sp500_pit.Membership, "load", classmethod(lambda cls, *a, **k: ms))
    uni = tmp_path / "universe.yaml"
    uni.write_text(json.dumps({"sectors": {"XLK": stocks[:15], "XLF": stocks[15:]}, "market": "SPY"}))
    with contextlib.redirect_stdout(io.StringIO()):
        research.main(["--strategy", "mom_12_1_band", "--universe", "pit", "--data", str(data_dir),
                       "--universe-file", str(uni), "--start", FIRST, "--test-start", str(d[I0]),
                       "--end", str(d[-1]), "--control-runs", "0"])
    reps = list((data_dir / "reports").glob("engine_balanced_mom_12_1_band_pit_*.json"))
    assert len(reps) == 1
    rep = json.loads(reps[0].read_text())
    assert rep["gate_b"][4]["verdict"] != mc.CANNOT or "point-in-time" in rep["gate_b"][4]["detail"]
    rows = [json.loads(x) for x in (data_dir / "trials.jsonl").read_text().splitlines()]
    assert len(rows) == 9 and {r["variant"] for r in rows} == {"engine_simbroker"}
    assert sum(r["n_trials"] for r in rows) == 6  # the base configuration and its five neighbours


def test_the_live_engine_never_loads_the_daily_research_path():
    """The live entry points import neither the daily research report, the harness nor the daily store."""
    import subprocess
    import sys

    code = ("import sys, kaibot.engine.__main__, kaibot.engine.core, kaibot.engine.watchdog; "
            "bad = [m for m in sys.modules if m.startswith(('kaibot.engine.daily', 'kaibot.replay.multiday', "
            "'kaibot.ingest.daily_'))]; print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
