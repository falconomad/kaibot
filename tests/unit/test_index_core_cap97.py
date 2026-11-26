"""#710 (M17 ticket 6.10): the original 97% index core, run ONCE as a pre-registered consistency check, RESEARCH ONLY
(Amendment 6 of docs/prereg/index_core.md and of docs/prereg/index_core_trend_momentum.md, Product 26 Nov 2026).

What must hold:
- the live cap, the hard ceiling and config/index_core.yaml stay at 27%; 97 is only a script argument;
- both amendments are appended (append-only), declare `design_saw_holdout: true`, and call the run hindsight and a
  consistency check, never clean; the verdict's hold-out label is a consistency check;
- both scripts take `--index-cap 97`, record it, and count six new configurations each;
- the control book of B7 at a 97% core is cut to 97% (core 97 + budget 10 = 107 is above Hunter's bound) and runs;
  at 27% and 80% the cut does not bind, so those runs do not change;
- the allow-list gives `--index-cap 97` to the two index-core scripts only, and the two jobs are queued on #710
  with `--end 2024-12-31` (the hold-out is not read).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_v3 as g3
from kaibot.evaluation import prereg as prereg_mod
from kaibot.risk import index_core as ic
from kaibot.risk.gate import BOOK_HARD_CEILINGS
from kaibot.strategies.index_core import ControlBook, control_weight

sys.path.insert(0, str(ROOT / "scripts"))
import index_core as core_script  # noqa: E402
import index_core_book as book_script  # noqa: E402
import research_runner as rr  # noqa: E402

PREREGS = {"index_core": "docs/prereg/index_core.md",
           "index_core_trend_momentum": "docs/prereg/index_core_trend_momentum.md"}


# ---------------------------------------------------------------- nothing live moves


def test_the_live_cap_and_the_hard_ceiling_stay_at_27():
    assert BOOK_HARD_CEILINGS.max_index_pct == 27.0
    cfg = ic.load()
    assert {lv.max_index_pct for lv in cfg.levels.values()} == {27.0} and cfg.enabled is False
    with pytest.raises(ValueError):  # 97 is the research ceiling, nothing above it
        ic.book_limits("aggressive", cfg, index_cap_pct=97.01)


def test_book_limits_at_97_are_cut_by_each_levels_exposure_cap_less_the_reserve():
    cfg = ic.load()
    got = {lv: ic.book_limits(lv, cfg, index_cap_pct=97.0).max_index_pct for lv in cfg.levels}
    assert got == {"aggressive": 97.0, "balanced": 57.0, "conservative": 27.0}


# ---------------------------------------------------------------- the amendments


@pytest.mark.parametrize("rule", sorted(PREREGS))
def test_amendment_6_is_appended_and_labels_the_run_hindsight_and_a_consistency_check(rule):
    text = (ROOT / PREREGS[rule]).read_text()
    heads = re.findall(r"^## Amendment (\d+)", text, flags=re.M)
    assert heads == [str(i) for i in range(1, len(heads) + 1)] and heads[-1] == "6"
    a6 = text.split("## Amendment 6", 1)[1]
    head = a6.splitlines()[0]
    assert "issue #710" in head and "after the runs" in head and "consistency check" in head
    assert re.search(r"^- \*\*design_saw_holdout:\*\* true\b", a6, flags=re.M)
    for phrase in ("**hindsight**", "**consistency check**", "**not read**", "--index-cap 97",
                   "2017-05-01 to 2024-12-31", "#731", "#710"):
        assert phrase in a6, phrase
    assert "clean test" in a6 and "never" in a6  # it says what it is not
    p = prereg_mod.load(rule, ROOT, committed=False)
    assert p.design_saw_holdout is True
    assert len(p.amendments) == 6


def test_the_hold_out_label_of_either_book_is_a_consistency_check():
    idx = pd.bdate_range("2017-05-01", "2026-11-06")
    a = pd.Series(np.random.default_rng(710).normal(0.0001, 0.002, len(idx)), index=idx)
    for rule in PREREGS:
        p = prereg_mod.load(rule, ROOT, committed=False)
        lab = g3.holdout_check(g3.GateV3.load(ROOT), research=a[a.index <= "2024-12-31"],
                               holdout=a[a.index > "2024-12-31"], first_run_at=datetime(2027, 1, 5, tzinfo=UTC),
                               prereg=p)
        assert lab["clean"] is False and lab["label"].startswith("consistency check")


def test_the_amendments_count_the_configurations():
    core = (ROOT / PREREGS["index_core"]).read_text().split("## Amendment 6", 1)[1]
    book = (ROOT / PREREGS["index_core_trend_momentum"]).read_text().split("## Amendment 6", 1)[1]
    assert "Six new configurations count" in core and "**18 configurations**" in core
    assert "Six new configurations count" in book and "**24 configurations**" in book
    assert "index-core-97-2026-11-26" in core and "index-core-book-97-2026-11-26" in book


# ---------------------------------------------------------------- the control book at 97


def test_the_control_book_at_97_is_cut_to_the_levels_bound_and_unchanged_at_27_and_80():
    cfg = ic.load()
    bound = cfg.levels["aggressive"].exposure_pct - cfg.cash_reserve_pct
    assert bound == 97.0
    for cap, total in ((27.0, 37.0), (80.0, 90.0), (97.0, 97.0)):
        c = ControlBook.of(book_script.book_of(), cap, cut_pct=bound)
        assert c.total_cap_pct == total
        if cap < 97:  # the cut does not bind: the same control as before #710
            assert ControlBook.of(book_script.book_of(), cap).total_cap_pct == total
        b = book_script.control_builder(c, cfg)
        assert b.limits.max_index_pct == total
    # the uncut 97% control asks for 107%, which the builder refuses: the cut is what lets it run
    with pytest.raises(ValueError):
        book_script.control_builder(ControlBook.of(book_script.book_of(), 97.0), cfg)


def test_control_weight_with_the_cut_holds_min_of_core_plus_budget_and_the_bound():
    # unchanged without the cut
    assert control_weight(0.5, 80, 10) == pytest.approx((0.5 * 80 + 10) / 90)
    assert control_weight(0.5, 80, 10, 90) == pytest.approx(control_weight(0.5, 80, 10))
    # at 97: SPY % of equity = min(w x 97 + 10, 97)
    for w in (0.0, 0.3, 0.6, 0.8, 0.9, 1.0):
        assert control_weight(w, 97, 10, 97) * 97 == pytest.approx(min(w * 97 + 10, 97))
    for bad in (0.0, -1.0, 107.01):
        with pytest.raises(ValueError):
            control_weight(0.5, 97, 10, bad)


# ---------------------------------------------------------------- the scripts on the fixture


def test_the_core_at_97_on_the_fixture_records_the_cap_and_counts_six(tmp_path):
    t = tmp_path / "t.jsonl"
    out = core_script.main(["--fixture", "--data", str(tmp_path), "--out", str(tmp_path / "o"), "--trials", str(t),
                            "--index-cap", "97"])
    assert out["index_cap_pct"] == 97.0 and "_cap97_" in out["file"]
    by = {r["label"]: r for r in out["rows"]}
    assert by["base|1x"]["max_index_pct"] == 97.0 and by["level|balanced"]["max_index_pct"] == 57.0
    assert by["level|conservative"]["max_index_pct"] == 27.0
    rows = [json.loads(x) for x in t.read_text().splitlines()]
    assert sum(r["n_trials"] for r in rows) == 6 and {r["params"]["index_cap_pct"] for r in rows} == {97.0}
    for r in out["rows"]:
        assert r["guard_cuts"] == 0 and r["overdraft_days"] == 0


def test_the_book_at_97_on_the_fixture_runs_its_cut_control_book_and_counts_six(tmp_path):
    t = tmp_path / "t.jsonl"
    out = book_script.main(["--fixture", "--data", str(tmp_path), "--out", str(tmp_path / "o"), "--trials", str(t),
                            "--control-runs", "20", "--index-cap", "97"])
    assert out["index_cap_pct"] == 97.0 and "_cap97_" in out["file"]
    cb = out["control_book"]
    assert cb["core_cap_pct"] == 97.0 and cb["index_cap_pct"] == 97.0 and cb["cut_pct"] == 97.0
    assert out["gate_v3_book"]["marks"]["b7"]["numbers"]["cagr_points_over_control"]["n"] == 5
    rows = [json.loads(x) for x in t.read_text().splitlines()]
    book = [r for r in rows if r["strategy"] == book_script.BOOK]
    assert sum(r["n_trials"] for r in book) == 6 and {r["params"]["index_cap_pct"] for r in book} == {97.0}
    assert sum(r["n_trials"] for r in rows if r["strategy"] != book_script.BOOK) == 0
    by = {r["label"]: r for r in out["rows"]}
    for k in (0, 5, 10, 15, 20):
        assert by[f"control|{k}"]["max_index_pct"] == 97.0
    for r in out["rows"]:
        assert r["guard_cuts"] == 0 and r["overdraft_days"] == 0, r["label"]


# ---------------------------------------------------------------- the runner


@pytest.mark.parametrize("script", ["scripts/index_core.py", "scripts/index_core_book.py"])
def test_the_allow_list_takes_97_for_the_two_index_core_scripts(script):
    spec = rr.load_allowed().specs[script]
    assert rr.check_args(spec, ["--end", "2024-12-31", "--index-cap", "97"])[-1] == "97"


def test_no_other_script_gets_the_flag():
    specs = rr.load_allowed().specs
    have = sorted(k for k, s in specs.items() if "--index-cap" in s.flags)
    assert have == ["scripts/index_core.py", "scripts/index_core_book.py"]


def test_the_two_cap97_jobs_are_queued_on_710_inside_the_research_window():
    cfg = rr.load_allowed()
    jobs = {j["id"]: rr.check_job(j, cfg) for j in rr.load_queue()}
    a, b = jobs["index-core-97-2026-11-26"], jobs["index-core-book-97-2026-11-26"]
    assert (a.spec.target, a.issue) == ("scripts/index_core.py", 710)
    assert (b.spec.target, b.issue) == ("scripts/index_core_book.py", 710)
    for j in (a, b):
        assert j.args == ("--end", "2024-12-31", "--index-cap", "97")
