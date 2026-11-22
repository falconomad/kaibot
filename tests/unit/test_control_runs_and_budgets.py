"""#547 (QA of #542): the research command takes its default control runs from config/gate_v3.yaml, and a book's
pre-registered sleeve budgets are checked against the budgets the engine ran."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
import yaml

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_v3 as g3

GATE = g3.GateV3.load(ROOT)
IDX = pd.bdate_range("2017-05-01", "2024-12-31")
SPY = pd.Series(np.random.default_rng(547).normal(0.0005, 0.011, len(IDX)), index=IDX)


def with_book(sleeves) -> g3.GateV3:
    cfg = yaml.safe_load((ROOT / "config" / "gate_v3.yaml").read_text())
    cfg["books"] = {"core_book": {"components": ["index_core"], "sleeves": sleeves, "v3_declared": True}}
    return g3.GateV3.from_bytes(yaml.safe_dump(cfg).encode())


def test_the_control_runs_follow_the_family_size():
    assert g3.control_runs_for(GATE, "trend_momentum") == 2000  # a family of several rules
    assert g3.control_runs_for(GATE, "spy_trend_filter") == 500  # one rule in its family
    assert g3.control_runs_for(GATE, "no_such_rule") == 500


def test_the_research_command_defaults_to_the_gate_config(monkeypatch, tmp_path):
    """On the store the default is the gate's, not 500; on the fixture it stays 0; an explicit value is kept."""
    from kaibot.engine import daily_report as dr
    from kaibot.engine import research

    seen = {}

    def fake_report(strategy, market, info, **kw):
        seen["runs"] = kw["control_runs"]
        raise SystemExit(0)

    monkeypatch.setattr(dr, "report", fake_report)
    monkeypatch.setattr(dr, "load_store", lambda *a, **k: (None, {}))
    with pytest.raises(SystemExit):
        research.main(["--strategy", "trend_momentum", "--universe", "pit", "--data", str(tmp_path),
                       "--end", "2024-12-31"])
    assert seen["runs"] == 2000
    with pytest.raises(SystemExit):
        research.main(["--strategy", "trend_momentum", "--universe", "pit", "--data", str(tmp_path),
                       "--end", "2024-12-31", "--control-runs", "700"])
    assert seen["runs"] == 700
    monkeypatch.setattr(dr, "load_fixture", lambda *a, **k: (None, {}))
    with pytest.raises(SystemExit):
        research.main(["--strategy", "trend_momentum"])
    assert seen["runs"] == 0


def test_the_report_itself_defaults_to_the_gate_config():
    import inspect

    from kaibot.engine import daily_report as dr

    assert inspect.signature(dr.report).parameters["control_runs"].default is None


def test_budget_problems_name_every_difference():
    gate = with_book({"trend_momentum": 10.0})
    assert g3.book_budget_problems(gate, "core_book", {"trend_momentum": 10.0}) == []
    assert g3.book_budget_problems(gate, "core_book", {"trend_momentum": 5.0}) == [
        "trend_momentum: pre-registered 10% of the book, the engine ran 5%"]
    assert g3.book_budget_problems(gate, "core_book", {}) == ["trend_momentum: pre-registered at 10%, not in the run"]
    assert "not pre-registered" in g3.book_budget_problems(gate, "core_book", {"trend_momentum": 10.0,
                                                                               "news_gap_week": 2.0})[0]
    assert "not listed" in g3.book_budget_problems(GATE, "core_book", {})[0]


def test_a_book_run_at_other_budgets_fails_b6_and_cannot_carry_a_verdict():
    gate = with_book({"trend_momentum": 10.0})
    acc = g3.Account(SPY * 0.6 + 0.00005, SPY, costs=pd.Series(0.00001, index=IDX), universe="point_in_time")
    kw = dict(book="core_book", account=acc, sleeves={"trend_momentum": "pass"}, first_run_at=datetime(2026, 11, 25,
                                                                                                   tzinfo=UTC))
    ok = g3.evaluate_book(gate, **kw, ran_budgets={"trend_momentum": 10.0})
    assert ok["marks"]["b6"]["met"] is True and ok["gate_a"]["budgets_as_pre_registered"] is True
    bad = g3.evaluate_book(gate, **kw, ran_budgets={"trend_momentum": 20.0})
    assert bad["marks"]["b6"]["met"] is False and "the engine ran 20%" in bad["marks"]["b6"]["why"]
    assert any("other sleeve budgets" in r for r in bad["provisional_reasons"]) and bad["verdict"] == "fail"
    unchecked = g3.evaluate_book(gate, **kw)  # QA #553: a listed book whose run budgets are not given cannot pass
    assert unchecked["gate_a"]["budgets_as_pre_registered"] is None and unchecked["can_pass"] is False
    assert any("not checked against" in r for r in unchecked["provisional_reasons"])
    assert "budgets_as_pre_registered" not in g3.evaluate_book(GATE, **kw)["gate_a"]  # an unlisted book: not asked


def test_a_listed_books_sleeve_without_a_pre_registered_budget_fails_b6():
    """QA #553: missing pre-registered budgets for a listed book never pass, with or without `ran_budgets`."""
    gate = with_book({"trend_momentum": 10.0})
    acc = g3.Account(SPY * 0.6 + 0.00005, SPY, costs=pd.Series(0.00001, index=IDX), universe="point_in_time")
    kw = dict(book="core_book", account=acc, first_run_at=datetime(2026, 11, 25, tzinfo=UTC))
    for ran in (None, {"trend_momentum": 10.0}, {"trend_momentum": 10.0, "mom_12_1_band": 5.0}):
        m = g3.evaluate_book(gate, **kw, sleeves={"trend_momentum": "pass", "mom_12_1_band": "pass"},
                             ran_budgets=ran)["marks"]["b6"]
        assert m["met"] is False and "mom_12_1_band" in m["why"], ran
    empty = with_book({})
    m = g3.evaluate_book(empty, **kw, sleeves={"trend_momentum": "pass"}, ran_budgets={})["marks"]["b6"]
    assert m["met"] is False and "no budget" in m["why"]
