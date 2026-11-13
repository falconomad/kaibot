"""Issue #168: the daily runner sizes with the portfolio builder (#119) and books with the ledger (#117), and the
pre-registered results do not move. Product's rule: what trades live is what was tested.

- `engine/daily.momentum_builder` configures the builder's ranked fill per rule (k, per-name cap, exposure cap,
  names, 10% resize buffer, 3% cash reserve). Parity with the research harness (`replay/multiday_core.py`) is exact,
  order by order and fill by fill with the equity curve, on the committed fixture (every version and neighbour) and
  on a 730-stock synthetic decade with splits, dividends, holes, delistings and index leavers.
- Amendment 4 of both pre-registrations: the 40% sector cap (best-ranked first; no sector = its own) is part of
  the rule in both, and at the fill only the cash is checked in both. The rules run at their own limits within the
  hard ceilings. The remaining book caps are outer guards and never cut in these runs (asserted).
- Cash, shares, marks and venue equity come from `engine.ledger.Ledger`, folded from the run's journal."""

from __future__ import annotations

import dataclasses
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from kaibot.engine.daily import DailyData, momentum_builder, momentum_limits, run_daily
from kaibot.engine.ledger import LEDGER_KINDS, Ledger
from kaibot.ingest.daily_store import DailyPanel
from kaibot.replay import multiday_core as mc
from kaibot.risk.gate import BOOK_HARD_CEILINGS
from kaibot.risk.portfolio import PortfolioBuilder
from kaibot.risk.profiles import load_book_limits
from kaibot.strategies import momentum as mom

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine_momentum as tem  # noqa: E402

POL = tem.POL
VERSIONS = ("trend_momentum", "mom_12_1_band", "mom_12_1_band_nofilter")


# ---------------------------------------------------------------- a 730-stock synthetic decade


def decade_market(seed: int = 168, n_stocks: int = 730, n: int = 2800, sector_beta: float = 0.0):
    """730 random walks (different drifts, betas and volatilities, $8-$400, 11 sectors) and SPY over 2800 sessions
    (280 of warm-up, then ten years), with two bear stretches (the 200-day filter switches off and on), 40 days
    with a 20% move, 250 splits
    (2:1, 3:2, 3:1, 4:1; a 3:2 leaves fractions), quarterly dividends on 150 names and SPY, 600 one-day holes,
    20 eight-session holes (longer than the 5-session carry), 12 delistings with terminal returns and 40 index
    leave-and-rejoin spans. Volumes differ, so some names fail the $50M liquidity filter. `sector_beta`: a common
    sector factor (0 = none)."""
    rng = np.random.default_rng(seed)
    stocks = [f"D{k:03d}" for k in range(n_stocks)]
    sec = rng.integers(0, 11, n_stocks)
    mkt = rng.normal(0.0004, 0.009, n)
    mkt[900:1000] -= 0.003
    mkt[2000:2060] -= 0.004
    secf = rng.normal(0, 0.006, (n, 11))
    drift, beta = rng.normal(0.0003, 0.0006, n_stocks), rng.uniform(0.6, 1.5, n_stocks)
    vol = rng.uniform(0.006, 0.02, n_stocks)
    r = drift + np.outer(mkt, beta) + sector_beta * secf[:, sec] + rng.normal(0, 1, (n, n_stocks)) * vol
    c = np.exp(np.cumsum(np.column_stack([r, mkt]), axis=0)) * np.r_[rng.uniform(8, 400, n_stocks), 300.0]
    o = np.vstack([c[:1], c[:-1]]) * np.exp(rng.normal(0, 0.003, c.shape))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.004, c.shape)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.004, c.shape)))
    v = np.exp(rng.normal(np.log(4e8), 0.8, n_stocks + 1))[None, :] / c[:1] * np.ones((n, 1))
    v[:, -1] = 5e7
    dates = np.busday_offset(np.datetime64("2015-01-05"), np.arange(n), roll="forward").astype("datetime64[D]")
    syms = [*stocks, "SPY"]
    acts = []
    jump = rng.choice(n_stocks, 40, replace=False)  # 15%-move days (trend_momentum sells and blocks them)
    r[rng.integers(400, n - 5, 40), jump] += rng.choice([-0.2, 0.2], 40)
    for j in rng.choice(n_stocks, 250, replace=False):
        i, ratio = int(rng.integers(400, n - 20)), float(rng.choice([2.0, 1.5, 1.5, 3.0, 4.0]))
        for a in (o, h, lo, c):
            a[i:, j] /= ratio
        v[i:, j] *= ratio
        acts.append((syms[j], dates[i], ratio, None))
    for j in rng.choice(n_stocks, 150, replace=False):
        for i in range(int(rng.integers(300, 360)), n, 63):
            acts.append((syms[j], dates[i], None, float(round(c[i - 1, j] * 0.004, 4))))
    acts += [("SPY", dates[i], None, 1.5) for i in range(300, n, 63)]
    for length, count in ((1, 600), (8, 20)):
        for _ in range(count):
            j, i = int(rng.integers(0, n_stocks)), int(rng.integers(300, n - 20))
            for a in (o, h, lo, c, v):
                a[i:i + length, j] = np.nan
    terminal = {}
    for j in rng.choice(n_stocks, 12, replace=False):
        i = int(rng.integers(600, n - 30))
        for a in (o, h, lo, c, v):
            a[i:, j] = np.nan
        terminal[syms[j]] = float(rng.choice([-0.5, 0.2, 0.0]))
    member = np.ones(c.shape, dtype=bool)
    for j in rng.choice(n_stocks, 40, replace=False):
        a = int(rng.integers(0, n - 100))
        member[a:a + int(rng.integers(50, 600)), j] = False
    sectors = {s: f"SEC{sec[k]:02d}" for k, s in enumerate(stocks) if k % 50}  # every 50th: no sector (its own)
    m = mc.Market.from_panel(DailyPanel.make(dates, syms, o, h, lo, c, v, actions=acts), stocks, "SPY",
                             member=member, terminal_returns=terminal, sectors=sectors)
    return m, DailyData.from_market(m)


@pytest.fixture(scope="module")
def decade():
    m, data = decade_market()
    return m, data, tem.I0, len(data.dates) - 1


_RUNS: dict = {}


def run_pair(decade, version, fill="base", cap=100.0, neighbour=None, guards=True):
    """Harness and engine on the decade (cached: each pair is about ten seconds). `guards=False`: the builder's
    outer guards switched off, to show a pinned difference is the guard's and nothing else."""
    key = (version, fill, cap, neighbour, guards)
    if key not in _RUNS:
        m, data, i0, i1 = decade
        spec = strat = None
        if neighbour:
            spec = next(s for s in mc.neighbours(version) if s.name.endswith(f"~{neighbour}"))
            strat = next(s for s in mom.neighbours(version) if s.name.endswith(f"~{neighbour}"))
        with pytest.MonkeyPatch.context() as mp:
            if not guards:
                mp.setattr(PortfolioBuilder, "_guards", lambda self, *a: {})
            _RUNS[key] = tem.compare(m, data, version, i0, i1, fill, cap, spec=spec, strategy=strat)
    return _RUNS[key]


def exact(c: dict) -> None:
    """Orders, fills and equity identical; no outer guard cut; the venue's cash is the ledger's."""
    run = c["run"]
    assert c["orders_equal"] and c["n_orders"] > 10
    assert c["fills_equal"] and c["n_fills"] > 10, c["fills_diff"][:3]
    assert c["max_equity_gap"] <= 0.01 and c["digest_equal"]
    assert run.guards == [] and run.basis_gaps == []
    assert run.ledger.overdrafts == [] and run.cash_gap < 1e-6


# ---------------------------------------------------------------- parity on the decade

DECADE_RUNS = [("trend_momentum", "base", 100.0, None), ("trend_momentum", "base", 60.0, None),
               ("trend_momentum", "stress", 60.0, None), ("trend_momentum", "base", 100.0, "risk15"),
               ("mom_12_1_band", "base", 100.0, None), ("mom_12_1_band", "base", 60.0, None),
               ("mom_12_1_band", "stress", 60.0, None), ("mom_12_1_band_nofilter", "base", 100.0, None)]


@pytest.mark.parametrize("version,fill,cap,neighbour", DECADE_RUNS)
def test_the_builder_reproduces_the_harness_on_a_730_stock_decade(decade, version, fill, cap, neighbour):
    """Exact in all eight runs since Amendment 4 (before it, four of these differed: the sector guard twice, an
    order without a bar at its fill twice)."""
    m, data, i0, i1 = decade
    assert len(data.symbols) == 731 and i1 - i0 + 1 >= 2520  # ten years of sessions after the warm-up
    c = run_pair(decade, version, fill, cap, neighbour)
    exact(c)
    reasons = Counter(str(f[5]) for f in c["e_fills"])
    assert reasons["end_of_data"] and reasons["rank"]
    assert c["run"].book.dividends > 0 and c["book"].dividends == pytest.approx(c["run"].book.dividends)
    if version == "trend_momentum":
        assert reasons["resize"] > 20 and reasons["trend"]


def test_the_decade_exercises_splits_cuts_skips_and_waiting_sales(decade):
    c = run_pair(decade, "trend_momentum")
    run = c["run"]
    reasons = Counter(str(f[5]) for f in c["e_fills"])
    assert reasons["delisted"] and reasons["cash_in_lieu"] and run.skipped > 0
    assert [f for f in c["e_fills"] if f[5] == "cash_in_lieu"] == [f for f in c["h_fills"] if f[5] == "cash_in_lieu"]
    assert sum(k == "split" for k, _ in run.ledger.entries) >= 5
    sold = {(f["i"], f["symbol"]) for f in run.fills if f["side"] == "sell"}
    assert {(i, s) for i, k, s, _ in run.orders if k == "sell"} - sold  # a sale that found no bar waited for one


def test_the_sector_cap_binds_on_the_decade_and_both_apply_it_the_same_way(decade, monkeypatch):
    """Amendment 4's 40% cap is reached on this decade (ATR sizing makes low-volatility names large): without it
    the orders differ; with it the engine and the harness agree exactly (above). Both read the same constant."""
    import kaibot.engine.daily as daily

    assert mc.SECTOR_CAP_PCT == BOOK_HARD_CEILINGS.max_sector_pct == 40.0
    with_cap = run_pair(decade, "mom_12_1_band")
    monkeypatch.setattr(mc, "SECTOR_CAP_PCT", 100.0)
    lim = dataclasses.replace(BOOK_HARD_CEILINGS, max_sector_pct=100.0)
    monkeypatch.setattr(daily, "BOOK_HARD_CEILINGS", lim)
    monkeypatch.setattr(PortfolioBuilder, "__post_init__", lambda self: None)  # past the ceiling: this test only
    m, data, i0, i1 = decade
    without = tem.compare(m, data, "mom_12_1_band", i0, i1)
    assert without["orders_equal"] and without["fills_equal"]  # the two still agree without the cap
    assert without["h_orders"] != with_cap["h_orders"]  # and the cap changed the orders


def test_waiting_orders_no_longer_change_the_harness_s_other_orders(decade):
    """Amendment 4, item 2: the sale of D140 found no bar at its fill (session 1064) and waited; the buy of D328
    after it is no longer refused by a names re-check at the fill (it was before the amendment)."""
    m, data, i0, i1 = decade
    c = run_pair(decade, "mom_12_1_band_nofilter")
    assert not np.isfinite(data.raw_c[1064, data.col("D140")])
    assert (1064, "sell", "D140") in {x[:3] for x in c["h_orders"]}
    assert any(f[:3] == (1064, "D328", "buy") for f in c["h_fills"])


# ---------------------------------------------------------------- the configuration


def test_each_rule_is_configured_with_its_pre_registered_settings():
    """Amendment 4: the rule's own limits within the hard ceilings, not the provisional level values."""
    lv = load_book_limits(research=True)["balanced"]
    for _, strat in [*mom.VERSIONS.items(), *[(s.name, s) for v in ("trend_momentum", "mom_12_1_band")
                                              for s in mom.neighbours(v)]]:
        for cap in (30.0, 60.0, 100.0):
            b = momentum_builder(strat, POL, cap)
            lim = b.limits
            assert b.fill == "ranked" and b.cash_reserve == 0.03 and b.band == mom.RESIZE_BUFFER == 0.10
            assert b.k == strat.atr_risk and min(b.k, lim.atr_risk_pct / 100) == b.k
            assert lim.max_names == strat.max_names and lim.max_name_pct == POL.max_position_pct == 15
            assert lim.max_gross_pct == lim.max_overnight_pct == cap
            assert lim.max_sector_pct == mc.SECTOR_CAP_PCT == 40.0
            assert (lim.max_gap_loss_pct, lim.min_cash_pct) == (BOOK_HARD_CEILINGS.max_gap_loss_pct,
                                                                BOOK_HARD_CEILINGS.min_cash_pct)
            assert momentum_limits(strat, POL, cap) == lim
    assert (lv.max_names, lv.max_sector_pct) == (20, 30.0)  # the level's provisional values are not used


def test_resize_weeks_reach_the_builder_through_the_target_list(fx_data):
    _, data, i0, i1 = fx_data
    run = run_daily(mom.VERSIONS["trend_momentum"], data, POL, i0, i1)
    weeks = {tl.resize for tl in run.targets.values()}
    assert weeks == {True, False}
    assert all(not tl.resize for tl in run_daily(mom.VERSIONS["mom_12_1_band"], data, POL, i0, i1).targets.values())


# ---------------------------------------------------------------- the fixture: every neighbour


@pytest.fixture(scope="module")
def fx_data():
    m, info = tem.dr.load_fixture(tem.FIXTURE)
    data = DailyData.from_market(m)
    i0 = int(np.searchsorted(data.dates, np.datetime64(info["window"][0])))
    return m, data, i0, len(data.dates) - 1


@pytest.mark.parametrize("version", VERSIONS)
def test_every_pre_registered_neighbour_is_reproduced_on_the_fixture(fx_data, version):
    m, data, i0, i1 = fx_data
    base = version if version != "mom_12_1_band_nofilter" else "mom_12_1_band"
    specs = {s.name.split("~")[1]: s for s in mc.neighbours(version if version in mc.NEIGHBOURS else base)}
    strats = {s.name.split("~")[1]: s for s in mom.neighbours(version if version in mom.NEIGHBOURS else base)}
    if version == "mom_12_1_band_nofilter":  # its neighbours: the same five settings without the filter
        specs = {n: dataclasses.replace(s, market_filter=False) for n, s in specs.items()}
        strats = {n: s.with_params(market_filter=False) for n, s in strats.items()}
    assert set(specs) == set(strats) and len(specs) == 5
    for n in specs:
        c = tem.compare(m, data, version, i0, i1, spec=specs[n], strategy=strats[n])
        assert c["orders_equal"] and c["fills_equal"] and c["digest_equal"], (version, n)
        assert c["max_equity_gap"] <= 0.01 and c["run"].guards == [], (version, n)


# ---------------------------------------------------------------- the ledger


def test_the_book_is_the_ledgers_fold_of_the_journal(fx_data):
    m, data, i0, i1 = fx_data
    c = tem.compare(m, data, "mom_12_1_band", i0, i1)
    run, h = c["run"], c["book"]
    led = run.ledger
    for again in (Ledger.fold(led.entries), Ledger.fold(run.om.journal.entries(kinds=LEDGER_KINDS))):
        assert again.snapshot(led.run_id) == led.ledger.snapshot(led.run_id)
    kinds = Counter(k for k, _ in led.entries)
    assert {"run", "fill", "dividend", "split", "mark"} <= set(kinds)
    assert all(b["run_id"] == led.run_id and b["strategy"] == "mom_12_1_band" for _, b in led.entries)
    marks = [b for k, b in led.entries if k == "mark"]
    assert all(all(key.startswith("mom_12_1_band|") for key in b["prices"]) for b in marks)
    assert not [b for k, b in led.entries if k == "fill" and b.get("reason") == "cash_in_lieu"]
    splits = [b for k, b in led.entries if k == "split"]
    assert splits and all(b.get("price") for b in splits)  # cash in lieu is booked through the split's price
    # QA-Ops' check on #164: final cash identical; venue equity identical at every close but the end-of-data row
    closes = led.lb.closes
    assert led.cash == pytest.approx(h.equity, abs=1e-6)
    assert [x.close_equity_venue for x in closes[:-1]] == pytest.approx([r["end"] for r in h.daily[:-1]], abs=1e-6)
    assert run.book.daily[-1]["end"] == pytest.approx(h.daily[-1]["end"], abs=1e-6)  # rewritten by the sale


def test_buys_book_no_fees_so_the_ledgers_cost_basis_is_the_harness_entry(fx_data):
    """The ledger puts buy fees into the cost; the venue charges none on buys (regulatory fees are on sales), so the
    ledger's average cost of every open sleeve is the harness's entry price."""
    m, data, i0, i1 = fx_data
    run = run_daily(mom.VERSIONS["trend_momentum"], data, POL, i0, i1 - 30)
    assert all(b["fees"] == 0.0 for k, b in run.ledger.entries if k == "fill" and b["side"] == "buy")
    for s, st in run.ledger.stats.items():
        sleeve = run.ledger.lb.sleeves[("trend_momentum", s)]
        assert sleeve.qty == st.qty
