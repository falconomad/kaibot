"""#458 / #459, Amendment 4 of docs/prereg/index_core.md and Amendment 3 of docs/prereg/index_core_trend_momentum.md
(Product, 25 Nov 2026): the index core re-tested with the index fund allowed up to 80% of the book, RESEARCH ONLY.

What must hold:
- the live cap and the hard ceiling stay 27% (`BOOK_HARD_CEILINGS`, `config/index_core.yaml`); a book above 27%
  is refused unless a builder in index mode opts into the research ceiling, which loosens `max_index_pct` only
  and never past 97%;
- nothing in the live engine sets that research ceiling;
- the scripts take `--index-cap` only at a pre-registered value (27, 80, or 97 since #710), record it in the report
  and in every trial row, and the runner's allow-list accepts exactly those values (97: test_index_core_cap97.py).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import replace

import pytest

from kaibot.engine.config import ROOT
from kaibot.risk import index_core as ic
from kaibot.risk.gate import BOOK_HARD_CEILINGS
from kaibot.risk.portfolio import RESEARCH_INDEX_CEILING_PCT, PortfolioBuilder

sys.path.insert(0, str(ROOT / "scripts"))
import index_core as core_script  # noqa: E402
import index_core_book as book_script  # noqa: E402
import research_runner as rr  # noqa: E402


def _limits(idx: float, **kw):
    return replace(BOOK_HARD_CEILINGS, max_name_pct=15.0, max_index_pct=idx, **kw)


# ---------------------------------------------------------------- the live ceiling holds


def test_the_live_hard_ceiling_and_the_config_stay_at_27():
    assert BOOK_HARD_CEILINGS.max_index_pct == 27.0
    cfg = ic.load()
    assert {lv.max_index_pct for lv in cfg.levels.values()} == {27.0} and cfg.enabled is False


def test_a_book_above_27_is_refused_without_the_research_ceiling():
    with pytest.raises(ValueError, match="hard ceiling"):
        PortfolioBuilder(_limits(80.0), fill="ranked", cash_reserve=0.03, index_symbol="SPY")


def test_the_research_ceiling_needs_the_index_mode():
    with pytest.raises(ValueError, match="index mode"):
        PortfolioBuilder(_limits(80.0), fill="ranked", cash_reserve=0.03, index_ceiling_pct=80.0)


@pytest.mark.parametrize("ceiling", [26.9, 97.01, 100.0, float("nan"), float("inf")])
def test_the_research_ceiling_is_between_27_and_97(ceiling):
    with pytest.raises(ValueError):
        PortfolioBuilder(_limits(27.0), fill="ranked", cash_reserve=0.03, index_symbol="SPY",
                         index_ceiling_pct=ceiling)


def test_the_research_ceiling_loosens_the_index_cap_only():
    b = PortfolioBuilder(_limits(80.0), fill="ranked", cash_reserve=0.03, index_symbol="SPY", index_ceiling_pct=80.0)
    assert b.limits.max_index_pct == 80.0
    with pytest.raises(ValueError, match="hard ceiling"):  # above the research ceiling it names
        PortfolioBuilder(_limits(81.0), fill="ranked", cash_reserve=0.03, index_symbol="SPY",
                         index_ceiling_pct=80.0)
    for loosened in ({"max_name_pct": 30.0}, {"max_sector_pct": 50.0}, {"max_names": 40},
                     {"max_gap_loss_pct": 20.0}, {"min_cash_pct": 0.5}):
        with pytest.raises(ValueError):
            PortfolioBuilder(replace(_limits(80.0), **loosened), fill="ranked", cash_reserve=0.03,
                             index_symbol="SPY", index_ceiling_pct=80.0)
    assert RESEARCH_INDEX_CEILING_PCT == 97.0


def test_nothing_in_the_live_engine_sets_the_research_ceiling():
    allowed = {ROOT / "src/kaibot/risk/portfolio.py", ROOT / "src/kaibot/risk/index_core.py"}
    hits = [p for p in (ROOT / "src" / "kaibot").rglob("*.py")
            if p not in allowed and re.search(r"index_ceiling_pct|index_cap_pct|RESEARCH_INDEX_CEILING", p.read_text())]
    assert hits == []
    from kaibot.engine import live_daily

    src = (ROOT / "src/kaibot/engine/live_daily.py").read_text()
    assert "index_core" not in src.replace("index_core.yaml", "")
    assert "PortfolioBuilder" in src and live_daily is not None


# ---------------------------------------------------------------- the research book limits


def test_book_limits_at_80_are_cut_by_each_levels_exposure_cap_less_the_reserve():
    cfg = ic.load()
    got = {lv: ic.book_limits(lv, cfg, index_cap_pct=80.0).max_index_pct for lv in cfg.levels}
    assert got == {"aggressive": 80.0, "balanced": 57.0, "conservative": 27.0}
    assert {lv: ic.book_limits(lv, cfg).max_index_pct for lv in cfg.levels} == {lv: 27.0 for lv in cfg.levels}
    b = ic.builder("aggressive", cfg, index_cap_pct=80.0)
    assert b.limits.max_index_pct == 80.0 and b.index_ceiling_pct == 80.0
    assert ic.builder("conservative", cfg, index_cap_pct=80.0).index_ceiling_pct is None  # 27: the live ceiling
    assert ic.builder("aggressive", cfg).index_ceiling_pct is None
    s = ic.sleeve_builder("aggressive", 10.0, 0.0001, 1.5, cfg, index_cap_pct=80.0)
    assert s.limits.max_index_pct == 80.0 and s.sleeve_budget_pct == 10.0 and s.limits.max_name_pct == 1.5


@pytest.mark.parametrize("cap", [0.0, -1.0, 97.5, float("nan"), True])
def test_book_limits_refuse_a_research_cap_outside_0_to_97(cap):
    with pytest.raises(ValueError):
        ic.book_limits("aggressive", index_cap_pct=cap)


# ---------------------------------------------------------------- the scripts


def test_the_scripts_take_only_the_pre_registered_caps():
    for got in (core_script.INDEX_CAPS, book_script.core_run.INDEX_CAPS):
        assert got == (27.0, 80.0, 97.0)  # 97: #710, Amendment 6 of both pre-registrations
    for main in (core_script.main, book_script.main):
        with pytest.raises(SystemExit):
            main(["--fixture", "--trials", "", "--index-cap", "50"])


def test_the_core_at_80_on_the_fixture_holds_more_spy_than_27_and_records_the_cap(tmp_path):
    t27, t80 = tmp_path / "t27.jsonl", tmp_path / "t80.jsonl"
    o27 = core_script.main(["--fixture", "--data", str(tmp_path), "--out", str(tmp_path / "o27"), "--trials",
                            str(t27)])
    o80 = core_script.main(["--fixture", "--data", str(tmp_path), "--out", str(tmp_path / "o80"), "--trials",
                            str(t80), "--index-cap", "80"])
    assert o27["index_cap_pct"] == 27.0 and o80["index_cap_pct"] == 80.0
    assert "_cap80_" in o80["file"] and "_cap" not in o27["file"].rsplit("/", 1)[1]
    by = {r["label"]: r for r in o80["rows"]}
    assert by["base|1x"]["max_index_pct"] == 80.0 and by["level|balanced"]["max_index_pct"] == 57.0
    assert by["level|conservative"]["max_index_pct"] == 27.0
    rows = [json.loads(x) for x in t80.read_text().splitlines()]
    assert len(rows) == 12 and sum(r["n_trials"] for r in rows) == 6  # six new configurations count
    assert {r["params"]["index_cap_pct"] for r in rows} == {80.0}
    assert {r["params"]["max_index_pct"] for r in rows} == {80.0, 57.0, 27.0}
    assert all("cap80" in r["trial_id"] for r in rows)

    def peak_spy(out_dir, out) -> float:
        import pandas as pd

        d = pd.read_csv(out_dir / out["file"].rsplit("/", 1)[1][:-5] / "daily_base_1x.csv")
        return float(d["exposure"].max())

    hi27, hi80 = peak_spy(tmp_path / "o27", o27), peak_spy(tmp_path / "o80", o80)
    assert hi27 <= 0.27 * 1.1 + 1e-9 < hi80 <= 0.80 * 1.1 + 1e-9
    for r in o80["rows"]:
        assert r["guard_cuts"] == 0 and r["overdraft_days"] == 0


def test_the_book_at_80_on_the_fixture_records_the_cap(tmp_path):
    trials = tmp_path / "t.jsonl"
    out = book_script.main(["--fixture", "--data", str(tmp_path), "--out", str(tmp_path / "o"), "--trials",
                            str(trials), "--control-runs", "20", "--index-cap", "80"])
    assert out["index_cap_pct"] == 80.0 and "_cap80_" in out["file"]
    rows = [json.loads(x) for x in trials.read_text().splitlines()]
    book = [r for r in rows if r["strategy"] == book_script.BOOK]
    assert sum(r["n_trials"] for r in book) == 6 and {r["params"]["index_cap_pct"] for r in book} == {80.0}
    assert {r["params"]["index_cap_pct"] for r in rows if r["strategy"] != book_script.BOOK} == {None}
    assert out["budgets"]["engine"] == {book_script.SLEEVE: 10.0}


@pytest.mark.parametrize("main", [core_script.main, book_script.main])
def test_the_hold_out_guard_still_runs_at_80(tmp_path, monkeypatch, main):
    """tests/conftest.py stubs the guard for synthetic bars; here it refuses, so a run at 80 that skipped it, or
    asked it about another end, would not exit (the guard itself: tests/unit/test_holdout.py)."""
    from kaibot.evaluation import holdout

    asked = []

    def refuse(end, **kw):
        asked.append(str(end))
        raise holdout.HoldoutError("inside the hold-out")

    monkeypatch.setattr(holdout, "check", refuse)
    with pytest.raises(SystemExit):
        main(["--fixture", "--data", str(tmp_path), "--trials", "", "--end", "2025-01-02", "--index-cap", "80"])
    assert asked == ["2025-01-02"]


# ---------------------------------------------------------------- the runner's allow-list


@pytest.mark.parametrize("script", ["scripts/index_core.py", "scripts/index_core_book.py"])
def test_the_allow_list_takes_exactly_27_80_or_97(script):
    spec = rr.load_allowed().specs[script]
    for ok in ("27", "80", "97"):
        assert rr.check_args(spec, ["--end", "2024-12-31", "--index-cap", ok])[-1] == ok
    for bad in ("50", "80.0", "97.0", "800", "8", " 80", "80;", "080", "98", "100"):
        with pytest.raises(rr.JobError):
            rr.check_args(spec, ["--index-cap", bad])


def test_other_replay_scripts_do_not_get_the_flag():
    assert "--index-cap" not in rr.load_allowed().specs["scripts/trend_momentum_gbm.py"].flags


def test_the_two_cap80_jobs_are_queued_on_the_issues():
    cfg = rr.load_allowed()
    jobs = {j["id"]: rr.check_job(j, cfg) for j in rr.load_queue()}
    a, b = jobs["index-core-80-2026-11-25"], jobs["index-core-book-80-2026-11-25"]
    assert (a.spec.target, a.issue) == ("scripts/index_core.py", 458)
    assert (b.spec.target, b.issue) == ("scripts/index_core_book.py", 459)
    for j in (a, b):
        assert j.args == ("--end", "2024-12-31", "--index-cap", "80")
