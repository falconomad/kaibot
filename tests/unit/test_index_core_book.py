"""M11 ticket 2.8 (#459): the index core with a momentum sleeve at a fixed 10% budget, one combined book
(docs/prereg/index_core_trend_momentum.md, docs/prereg/trend_momentum_sleeve.md). Written before the run; the ten
years are on the owner's Mac, so the engine runs here are on the committed fixture."""

from __future__ import annotations

import dataclasses
import json
import sys

import numpy as np
import pytest
import yaml

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_v3 as g3
from kaibot.risk import index_core as ic
from kaibot.strategies import momentum as mom
from kaibot.strategies.index_core import IndexCore, OnCore

sys.path.insert(0, str(ROOT / "scripts"))
import index_core_book as script  # noqa: E402


def test_the_book_and_its_sleeve_are_registered_as_pre_registered():
    gate = g3.GateV3.load(ROOT)
    assert gate.book("index_core_trend_momentum")["sleeves"] == {"trend_momentum_sleeve": 10.0}
    entry = gate.rule("trend_momentum_sleeve")
    assert entry["tier"] == "evidence_based" and entry["v3_declared"] and entry["evidence"]
    assert gate.family_of("trend_momentum_sleeve") == "cross_sectional_momentum"
    # six rules on main before the sleeve, seven with it (trend_momentum_sleeve.md, Amendment 1); it only grows
    assert len(gate.families["cross_sectional_momentum"]["rules"]) >= 7
    assert len(gate.families) == 12  # no new family: the count of the correction is unchanged


def test_the_sleeve_is_the_rule_at_one_tenth_and_sees_only_its_own_stocks():
    b = script.book_of()
    assert b.atr_risk == pytest.approx(0.0001) and b.budget_pct == 10.0
    pb = script.book_builder(b, "aggressive", ic.load(), script.load_profiles())
    assert pb.k == pytest.approx(0.0001) and pb.limits.max_name_pct == pytest.approx(1.5)
    assert pb.sleeve_budget_pct == 10.0 and pb.index_symbol == "SPY" and pb.limits.max_index_pct == 27.0  # #600
    assert pb.limits.max_names == 30


def test_budgets_are_checked_against_what_the_engine_runs():
    gate = g3.GateV3.load(ROOT)
    profiles = script.load_profiles()
    b = script.book_of()
    got = script.check_budgets(gate, b, script.book_builder(b, "aggressive", ic.load(), profiles))
    assert got["pre_registered"] == got["engine"] == {"trend_momentum_sleeve": 10.0}
    with pytest.raises(SystemExit, match="pre-registered 10.0% of the book, the engine runs 5.0%"):
        five = script.book_of(5.0)
        script.check_budgets(gate, five, script.book_builder(five, "aggressive", ic.load(), profiles))
    wrong_k = dataclasses.replace(script.book_builder(b, "aggressive", ic.load(), profiles), k=0.001)
    with pytest.raises(SystemExit, match="the sleeve's k"):
        script.check_budgets(gate, b, wrong_k)
    cfg = yaml.safe_load((ROOT / "config" / "gate_v3.yaml").read_text())
    cfg["books"]["index_core_trend_momentum"]["sleeves"] = {"trend_momentum_sleeve": 8.0}
    with pytest.raises(SystemExit, match="pre-registered 8.0%"):
        script.check_budgets(g3.GateV3.from_bytes(yaml.safe_dump(cfg).encode()), b,
                             script.book_builder(b, "aggressive", ic.load(), profiles))


@pytest.fixture(scope="module")
def fixture_data():
    from kaibot.engine import daily_report as dr
    from kaibot.engine.daily import DailyData

    m, info = dr.load_fixture(dr.FIXTURE)
    data = DailyData.from_market(m)
    i0 = int(np.searchsorted(data.dates, np.datetime64(info["window"][0])))
    return data, i0, len(data.dates) - 1


def test_one_combined_book_through_the_engine_keeps_the_sleeve_inside_its_budget(fixture_data):
    from kaibot.engine.daily import run_daily
    from kaibot.risk.profiles import load_profiles

    data, i0, i1 = fixture_data
    profiles = load_profiles()
    b = script.book_of()
    pb = script.book_builder(b, "aggressive", ic.load(), profiles)
    plans = []
    real = type(pb).build

    def build(self, targets, current, market, **kw):
        p = real(self, targets, current, market, **kw)
        plans.append((p, market, kw["equity"]))
        return p

    type(pb).build = build
    try:
        run = run_daily(b, data, profiles["aggressive"], i0, i1, builder=pb)
    finally:
        type(pb).build = real
    assert plans and not run.ledger.overdrafts and not run.refused and run.cash_gap < 0.01
    held_stock = False
    for p, market, eq in plans:
        sleeve = sum(q * market[s].px for (_, s), q in p.targets.items() if s != "SPY")
        index = sum(q * market[s].px for (_, s), q in p.targets.items() if s == "SPY")
        assert sleeve <= 0.10 * eq + 1e-6 and index <= 0.27 * eq + 1e-6
        assert sum(1 for (_, s), q in p.targets.items() if q > 0) <= 30
        held_stock |= sleeve > 0
    assert held_stock and any(f["symbol"] == "SPY" for f in run.fills)


def test_on_core_hands_the_sleeve_its_own_book_and_puts_the_index_last(fixture_data):
    from kaibot.strategies.base import Event
    from kaibot.strategies.momentum import DailyContext

    data, i0, _ = fixture_data
    b = OnCore(mom.VERSIONS["trend_momentum"], IndexCore())
    seen = []

    class Spy:
        name, atr_risk, market_filter, warmup = "trend_momentum", 0.001, True, 200

        def decision_day(self, prev, fill):
            return True

        def on_event(self, ctx):
            seen.append(dict(ctx.held))
            return mom.VERSIONS["trend_momentum"].on_event(ctx) or mom.TargetList("trend_momentum", ctx.fill_session,
                                                                                   {})

    b = dataclasses.replace(b, sleeve=Spy())
    ctx = DailyContext(Event.AFTER_CLOSE, data.day(i0 + 10), data.day(i0 + 11), data.view(i0 + 10),
                       {"SPY": 100.0, data.symbols[0]: 5.0})
    t = b.on_event(ctx)
    assert seen == [{data.symbols[0]: 5.0}] and list(t.weights)[-1] == "SPY" and t.strategy == b.name
    assert 0 < t.weights["SPY"] <= 1 and t.features["budget_pct"] == 10.0


def test_the_script_on_the_fixture_counts_six_configurations(tmp_path):
    trials = tmp_path / "trials.jsonl"
    out = script.main(["--fixture", "--data", str(tmp_path), "--out", str(tmp_path / "out"), "--trials",
                       str(trials), "--control-runs", "20"])
    rows = [json.loads(x) for x in trials.read_text().splitlines()]
    assert {r["cost_model"] for r in rows} == {"v2"}  # both preregs' amendments (#572)
    assert out["gate_v3_book"]["gate_a"]["budgets_as_pre_registered"] is True  # ran_budgets given
    assert sum(r["n_trials"] for r in rows) == 6
    assert {r["strategy"] for r in rows if r["n_trials"]} == {"index_core_trend_momentum"}
    assert out["gate_v3_sleeve"]["tier"] == "evidence_based" and out["gate_v3_book"]["rule"] == script.BOOK
    assert out["gate_v3_book"]["marks"]["b6"]["numbers"]["sleeves"] == {
        "trend_momentum_sleeve": out["gate_v3_sleeve"]["verdict"]}
    assert out["budgets"]["engine"] == {"trend_momentum_sleeve": 10.0}
    assert "20 runs, 2000 needed" in out["gate_v3_sleeve"]["marks"]["s3"]["why"]
    # #702: sleeve rows log the active-return Sharpe S4 reads; book rows (in no family) log none
    active = {r["label"]: r["metrics"].get("daily_active_sharpe_ann") for r in rows}
    sleeve = [k for k, r in zip(active, rows, strict=True) if r["strategy"] == "trend_momentum_sleeve"]
    assert sleeve and all(isinstance(active[k], float) for k in sleeve)
    assert all(active[r["label"]] is None for r in rows if r["strategy"] != "trend_momentum_sleeve")
    # #701: the run's own six configurations are B2's N before its rows were appended
    assert out["gate_v3_book"]["book_configurations"]["configurations"] == 6


def test_the_mac_command_defaults_to_2000_control_runs_and_a_ten_percent_budget():
    """QA on #550: the pre-registered run needs no flag for the 2,000 control runs or the budget."""
    gate = script.g3.GateV3.load()
    assert gate.cfg["sleeve"]["s3_control"]["random_runs_min_several_rules"] == 2000
    assert script.BUDGET == 10.0 == gate.book(script.BOOK)["sleeves"][script.SLEEVE]
    assert script.BUDGET <= script.g3.EVIDENCE_BUDGET_MAX_PCT


def test_the_book_and_the_sleeve_are_priced_with_cost_model_v2_not_only_labelled_so(tmp_path, monkeypatch):
    """QA on #550: the preregs' cost amendments. Every run goes through SimBroker with AuctionCostsV2, both reports
    and every trial row say v2, and the book's fills differ from the same run at v1."""
    from kaibot.broker.sim import AuctionCostsV2

    calls = []
    real = script.run_daily

    def spy(*args, **kw):
        r = real(*args, **kw)
        calls.append((args, kw, r))
        return r

    monkeypatch.setattr(script, "run_daily", spy)
    trials = tmp_path / "trials.jsonl"
    out = script.main(["--fixture", "--data", str(tmp_path), "--out", str(tmp_path / "out"), "--trials",
                       str(trials), "--control-runs", "20"])
    assert calls and all(kw.get("cost_model") == "v2" for _, kw, _ in calls)
    assert all(isinstance(r.broker.costs, AuctionCostsV2) for _, _, r in calls)
    assert {json.loads(x)["cost_model"] for x in trials.read_text().splitlines()} == {"v2"}
    assert out["gate_v3_book"]["source"]["cost_model"] == out["gate_v3_sleeve"]["source"]["cost_model"] == "v2"
    args, kw, v2 = next(c for c in calls if c[1].get("fill", "base") == "base" and c[1].get("cost", 1.0) == 1.0)
    v1 = real(*args, **{**kw, "cost_model": "v1"})
    assert v2.fills and [f["px"] for f in v2.fills] != [f["px"] for f in v1.fills]
