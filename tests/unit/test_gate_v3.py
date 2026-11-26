"""Evidence gate v3 (decision 16, M11 ticket 2.5): the sleeve test, the book test, the correction across rule
families and the hold-out check, on synthetic inputs whose answer is known."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_v2 as g2
from kaibot.evaluation import gate_v3 as g3
from kaibot.evaluation import mtm
from kaibot.evaluation import random_control as rc
from kaibot.evaluation.prereg import Prereg
from kaibot.evaluation.trials import Registry

GATE = g3.GateV3.load(ROOT)
MOMENTUM = len(GATE.families["cross_sectional_momentum"]["rules"])  # grows with each new pre-registration
IDX = pd.bdate_range("2017-05-01", "2026-11-06")  # reaches into the hold-out on purpose
RESEARCH = IDX[IDX <= "2024-12-31"]
FINAL = {"engine": "simbroker", "cost_model": "v2", "data_repairs": True, "benchmark": "spy_total_return"}
RNG = np.random.default_rng(16)
SPY = pd.Series(RNG.normal(0.0005, 0.011, len(IDX)), index=IDX)
BILLS = pd.Series(4.0, index=pd.bdate_range("2017-01-02", "2026-11-06"))
RULE = "trend_momentum"  # in the family cross_sectional_momentum (its size: MOMENTUM below)
# amendment 1 (decision 16 s.15): a family of several rules needs 2,000 runs
GOOD_CONTROL = {"percentile": 99.95, "runs": 2000, "matched": True, "p_value": 1 / 2001, "pool": "candidate_pool",
                "drawdown_percentile": 71.0}


def sleeve(edge_a_year=0.0, exposure=0.4, noise=0.0015, seed=1, **kw) -> g3.Account:
    """A part-time account: `exposure` of SPY plus a daily edge and its own noise."""
    e = pd.Series(exposure, index=IDX)
    own = np.random.default_rng(seed).normal(edge_a_year / 252, noise, len(IDX))
    net = pd.Series(mtm.exposure_matched(SPY, e) + own, index=IDX)
    kw.setdefault("costs", pd.Series(0.00002, index=IDX))
    kw.setdefault("control", GOOD_CONTROL)
    kw.setdefault("universe", "point_in_time")
    return g3.Account(net, SPY, e, **kw)


def prereg(neighbours=("n1", "n2"), rule=RULE) -> Prereg:
    return Prereg(rule, Path(f"docs/prereg/{rule}.md"), "ab" * 32, "pre-registered", date(2026, 11, 1),
                  tuple(neighbours), {}, datetime(2026, 11, 1, tzinfo=UTC))


def registry(n=6, strategy=RULE) -> Registry:
    rows = tuple({"trial_id": f"t{i}", "strategy": strategy, "n_trials": 1,
                  "metrics": {"daily_sharpe_ann": 0.5 + 0.02 * i}} for i in range(n))
    return Registry(rows, ("test",), 0)


def judge(acc=None, **kw) -> dict:
    kw.setdefault("prereg", prereg())
    kw.setdefault("neighbours", {"n1": sleeve(0.03, seed=2), "n2": sleeve(0.03, seed=3)})
    kw.setdefault("source", FINAL)
    kw.setdefault("registry", registry())
    kw.setdefault("first_run_at", datetime(2026, 11, 25, tzinfo=UTC))
    kw.setdefault("tbill", BILLS)
    return g3.evaluate_sleeve(GATE, rule=RULE, account=acc or sleeve(0.03), as_of=datetime(2026, 12, 1, tzinfo=UTC),
                              **kw)


def met(rep: dict) -> dict:
    return {k: v["met"] for k, v in rep["marks"].items()}


def declared(gate: g3.GateV3 = GATE, rule=RULE) -> g3.GateV3:
    """The committed gate with `rule` marked as declaring its gate v3 fields (as a new pre-registration would)."""
    cfg = yaml.safe_load((ROOT / "config" / "gate_v3.yaml").read_text())
    cfg["families"][gate.family_of(rule)]["rules"][rule]["v3_declared"] = True
    return g3.GateV3.from_bytes(yaml.safe_dump(cfg).encode())


# ---------------------------------------------------------------- the pre-registered marks


def test_marks_are_those_of_decision_16():
    text = (ROOT / "docs" / "decisions" / "16_gate_v3.md").read_text()
    c = GATE.cfg
    assert c["version"] == "3.1" and c["decision"] == "docs/decisions/16_gate_v3.md"  # decision 18 (#701)
    assert c["level"] == 0.10 and "one in ten" in text
    assert str(c["window"]["research_from"]) == "2017-05-01" and str(c["window"]["research_to"]) == "2024-12-31"
    assert "2017-05-01 to 2024-12-31" in text
    s, b = c["sleeve"], c["book"]
    assert s["benchmark"] == {"selection": "path_matched", "timing": "average_matched"}
    assert s["s1_active_return"]["annualised_above"] == 0.0 and s["s2_costs"]["costs_multiple"] == 2.0
    assert s["s3_control"]["random_runs_min"] == 500 and s["s3_control"]["across_families"] == "holm"
    assert s["s3_control"]["within_family"] == "bonferroni" and s["s3_control"]["pool"] == "candidate_pool"
    assert s["s4_deflated_sharpe"] == {"series": "active_return", "trials": "family", "dsr_min": 0.90,
                                       "variance_floor": "one_over_t_minus_1"}  # amendment 1
    assert b["b1_return"]["positive_years_share_min"] == 0.6 and b["b1_return"]["years_min"] == 8
    # the absolute test is gate v2's mark 2, number for number
    assert b["b2_spy"] == g2.GateV2.load(ROOT).cfg["gate_b"]["mark_2"]
    assert b["b3_costs"]["costs_multiple"] == 2.0 and b["cash"] == "zero"
    assert s["final_requires"] == b["final_requires"] == g2.GateV2.load(ROOT).cfg["gate_b"]["final_requires"]
    assert c["holdout_check"]["band"] == 0.05 and c["bootstrap"]["runs"] == 2000 and c["bootstrap"]["seed"] == 16
    for phrase in ("Holm", "500 runs", "0.90", "0.75", "5 of 8", "5th percentile", "twelve families"):
        assert phrase in text, phrase
    assert len(GATE.families) == 12 and f"| {len(GATE.families)} |" in text
    for fam in GATE.families:
        assert f"`{fam}`" in text, f"{fam} is not in the decision's family table"


def test_every_pre_registration_is_in_exactly_one_family():
    """A rule's pre-registration is in one family; a book's (amendment 1) is listed under `books:` instead. Every
    rule registered before decision 16 (21 Nov 2026) is marked as not declaring gate v3."""
    from kaibot.evaluation import prereg as pr

    names = {p.stem for p in (ROOT / "docs" / "prereg").glob("*.md")} - {"TEMPLATE"}
    listed = {r for body in GATE.families.values() for r in body["rules"]}
    books = set(GATE.cfg.get("books") or {})
    assert names <= listed | books, f"pre-registrations in no family and no book: {sorted(names - listed - books)}"
    assert not listed & books
    for body in GATE.families.values():
        for rule, r in body["rules"].items():
            if r["v3_declared"]:
                p = pr.load(rule, ROOT, committed=False)
                assert p.date >= date(2026, 11, 21), f"{rule} predates decision 16 and cannot declare gate v3"


def test_the_research_window_ends_where_the_hold_out_starts():
    f = ROOT / "config" / "holdout.yaml"
    if not f.is_file():
        pytest.skip("config/holdout.yaml arrives with ticket 2.6")
    h = yaml.safe_load(f.read_text())
    assert str(h["research_window_end"]) == str(GATE.cfg["window"]["research_to"])


def test_gate_v2_is_untouched():
    assert g2.GateV2.load(ROOT).cfg["version"] == 2
    assert "gate_v3" not in (ROOT / "config" / "gate_v2.yaml").read_text()


def test_a_bad_config_fails_loudly():
    cfg = yaml.safe_load((ROOT / "config" / "gate_v3.yaml").read_text())
    cfg["families"]["news_drift"]["rules"]["trend_momentum"] = {"kind": "selection"}
    with pytest.raises(ValueError, match="two families"):
        g3.GateV3.from_bytes(yaml.safe_dump(cfg).encode())
    cfg = yaml.safe_load((ROOT / "config" / "gate_v3.yaml").read_text())
    del cfg["sleeve"]["s3_control"]
    with pytest.raises(ValueError, match="sleeve lacks s3_control"):
        g3.GateV3.from_bytes(yaml.safe_dump(cfg).encode())


# ---------------------------------------------------------------- the correction


def test_holm_by_hand():
    # level 0.10, four tests: thresholds 0.025, 0.0333, 0.05, 0.10
    h = g3.holm({"a": 0.01, "b": 0.03, "c": 0.06, "d": None}, 0.10)
    assert [h[k]["rejected"] for k in "abcd"] == [True, True, False, False]
    assert h["a"]["adjusted"] == pytest.approx(0.04) and h["b"]["adjusted"] == pytest.approx(0.09)
    assert h["c"]["adjusted"] == pytest.approx(0.12) and h["d"]["adjusted"] == 1.0 and not h["d"]["known"]
    assert h["a"]["threshold"] == pytest.approx(0.025)
    # step-down: once one fails, every larger p fails, even one below its own threshold
    h = g3.holm({"a": 0.03, "b": 0.031, "c": 0.04}, 0.10)
    assert [h[k]["rejected"] for k in "abc"] == [True, True, True]
    h = g3.holm({"a": 0.04, "b": 0.041, "c": 0.045}, 0.10)
    assert [h[k]["rejected"] for k in "abc"] == [False, False, False]


def test_with_no_other_p_value_holm_is_bonferroni_over_all_families():
    m = len(GATE.families)
    alone = {f: None for f in GATE.families}
    assert g3.holm({**alone, "news_drift": 0.10 / m - 1e-9}, 0.10)["news_drift"]["rejected"]
    assert not g3.holm({**alone, "news_drift": 0.10 / m + 1e-6}, 0.10)["news_drift"]["rejected"]


def test_benjamini_hochberg_by_hand():
    bh = g3.benjamini_hochberg({"a": 0.01, "b": 0.03, "c": 0.06, "d": 0.5}, 0.10)
    assert [round(bh[k]["adjusted"], 4) for k in "abcd"] == [0.04, 0.06, 0.08, 0.5]
    assert [bh[k]["rejected"] for k in "abcd"] == [True, True, True, False]


def test_the_family_wise_error_is_held_with_twelve_no_skill_families():
    """Twelve families of no skill, each a uniform p-value: gate v2's rule (each alone at 0.10) passes at least
    one about 72% of the time; Holm at 0.10 at most 10%."""
    rng = np.random.default_rng(0)
    p = rng.random((20_000, 12))
    assert (p <= 0.10).any(axis=1).mean() == pytest.approx(1 - 0.9**12, abs=0.01)
    any_pass = np.mean([any(v["rejected"] for v in g3.holm(dict(enumerate(row)), 0.10).values())
                        for row in p[:4000]])
    assert any_pass <= 0.115 and any_pass == pytest.approx(0.10, abs=0.02)


def test_the_stored_percentile_gives_the_same_p_value_as_the_runs():
    sharpes = np.linspace(-1, 1, 500)
    assert rc.p_value(sharpes, 5.0) == pytest.approx(1 / 501)
    assert rc.p_value(sharpes, -5.0) == 1.0
    own = 0.9
    pct = float((sharpes < own).mean() * 100)
    assert rc.p_from_percentile(pct, 500) == pytest.approx(rc.p_value(sharpes, own))
    n = g3.control_numbers({"kind": "exposure_matched", "runs": 500, "matched": True, "sharpe_percentile": 94.6,
                            "control_result": {"percentile": 97.0, "percentile_simulated": 94.6, "runs": 500}})
    assert n["percentile"] == 94.6 and n["p_value"] == pytest.approx((1 + 27) / 501)


def test_the_momentum_family_only_grows():
    """QA-Ops (#556): the family size is read from the config, so a rule taken out of the list would quietly shrink
    the Bonferroni factor of every verdict in it. The list is append-only: every rule it has named stays."""
    named = {"trend_momentum", "mom_12_1_band", "mom_12_1_band_nofilter", "rel_strength", "trend_momentum_band",
             "trend_momentum_gbm", "trend_momentum_sleeve"}
    assert named <= set(GATE.families["cross_sectional_momentum"]["rules"]) and len(named) <= MOMENTUM


def test_family_p_is_bonferroni_inside_the_family():
    p = g3.family_p(GATE, {"trend_momentum": 0.01, "mom_12_1_band": 0.2, "spy_trend_filter": 0.03})
    assert p["cross_sectional_momentum"] == pytest.approx(MOMENTUM * 0.01)  # every listed rule counts
    assert p["index_trend_timing"] == pytest.approx(0.03) and p["news_drift"] is None


# ---------------------------------------------------------------- the random control's new numbers


def test_the_control_reports_p_value_drawdown_percentile_and_pool():
    rng = np.random.default_rng(5)
    rets = rng.normal(0.0, 0.01, (60, 400))
    schedule = [rc.Slot(0, 399, 1.0)]
    own = rets[7] + 0.002  # planted skill
    res = rc.matched_random_control(own, schedule, lambda t: np.arange(60), lambda sel: rets[int(sel[0])],
                                    runs=200, seed=1, pool="candidate_pool")
    assert res.pool == "candidate_pool" and res.p_value == pytest.approx((1 + round(200 * (1 - res.percentile / 100)))
                                                                         / 201)
    assert res.p_value <= 0.02 and 0.0 <= res.drawdown_percentile <= 100.0
    assert res.strategy_max_drawdown == pytest.approx(mtm.max_drawdown(own))
    assert rc.matched_random_control(own, schedule, lambda t: np.arange(60), lambda sel: rets[int(sel[0])],
                                     runs=20).pool == "universe"
    with pytest.raises(ValueError, match="pool must be one of"):
        rc.matched_random_control(own, schedule, lambda t: np.arange(60), lambda sel: rets[int(sel[0])], runs=5,
                                  pool="headlines")


# ---------------------------------------------------------------- the sleeve test


def test_being_part_time_invested_is_neither_punished_nor_rewarded():
    """The audit's point: SPY at 40% exposure with no edge fails gate v2's mark 2 by arithmetic. Under gate v3 its
    active return is zero whatever the exposure: it does not pass S1, and not because of its cash."""
    for exposure in (0.1, 0.4, 0.9):
        a = g3.active_return(GATE, "selection", sleeve(0.0, exposure, noise=0.0), BILLS)["active"]
        assert abs(float(a.mean() * 252)) < 1e-12
    a = g3.active_return(GATE, "selection", sleeve(0.03, 0.4, noise=0.0), BILLS)["active"]
    assert float(a.mean() * 252) == pytest.approx(0.03, rel=1e-6)


def test_the_bill_rate_cancels_for_a_selection_sleeve_and_matters_for_a_timing_sleeve():
    acc = sleeve(0.02)
    with_bills = g3.active_return(GATE, "selection", acc, BILLS)["active"]
    without = g3.active_return(GATE, "selection", acc, None)["active"]
    assert np.allclose(with_bills, without)
    half = pd.Series(np.where(np.arange(len(IDX)) % 2 == 0, 1.0, 0.0), index=IDX)  # in on alternate sessions
    timing = g3.Account(pd.Series(mtm.exposure_matched(SPY, half), index=IDX), SPY, half)
    t_bills = g3.active_return(GATE, "timing", timing, BILLS)
    assert t_bills["benchmark"].std() < SPY.std() * 0.6  # half of SPY all the time, not the rule's timetable
    assert not np.allclose(t_bills["active"], g3.active_return(GATE, "timing", timing, None)["active"])


def test_the_hold_out_is_dropped_before_anything_is_computed():
    rep = judge()
    assert rep["sessions"] == len(RESEARCH) and rep["sessions_in_holdout_dropped"] == len(IDX) - len(RESEARCH)
    assert rep["statistics"]["window"] == ["2017-05-01", "2024-12-31"]
    spoiled = sleeve(0.03)
    net = spoiled.net.copy()
    net[net.index > "2024-12-31"] = -0.5  # a disaster in the hold-out changes nothing
    again = judge(g3.Account(net, SPY, spoiled.exposure, spoiled.costs, spoiled.control, spoiled.universe))
    assert again["marks"] == rep["marks"]


def test_a_sleeve_with_an_edge_passes_every_mark_and_a_declared_rule_gets_a_pass():
    rep = judge()
    assert met(rep) == {k: True for k in ("s1", "s2", "s3", "s4", "s5", "s6")}
    assert rep["verdict"] == "provisional" and not rep["can_pass"]
    assert any("predates decision 16" in r for r in rep["provisional_reasons"])
    final = g3.evaluate_sleeve(declared(), rule=RULE, account=sleeve(0.03), prereg=prereg(),
                               neighbours={"n1": sleeve(0.03, seed=2), "n2": sleeve(0.03, seed=3)}, source=FINAL,
                               registry=registry(), first_run_at=datetime(2026, 11, 25, tzinfo=UTC), tbill=BILLS)
    assert final["verdict"] == "pass" and final["provisional_reasons"] == []
    n1 = rep["marks"]["s1"]["numbers"]
    assert n1["annualised"] == pytest.approx(0.03, abs=0.02) and n1["interval"][0] < n1["annualised"] < \
        n1["interval"][1]
    json.dumps(rep)


def test_no_edge_fails_s1_and_costs_can_fail_s2():
    assert judge(sleeve(-0.02))["marks"]["s1"]["met"] is False
    thin = sleeve(0.004, costs=pd.Series(0.00002, index=IDX))  # 0.4% a year of edge, 0.5% a year of costs
    rep = judge(thin)
    assert rep["marks"]["s2"]["met"] is False and rep["verdict"] == "fail"
    assert rep["marks"]["s2"]["numbers"]["breakeven_multiple"] < 2.5
    assert judge(sleeve(0.03, costs=None))["marks"]["s2"]["met"] is None


@pytest.mark.parametrize("control, state, why", [
    (None, None, "no matched random control"),
    ({**GOOD_CONTROL, "runs": 200}, None, "200 runs, 2000 needed"),
    ({**GOOD_CONTROL, "runs": 500, "p_value": 1 / 501}, None, "500 runs, 2000 needed"),
    ({**GOOD_CONTROL, "matched": False}, None, "not matched"),
    ({**GOOD_CONTROL, "pool": "universe"}, True, ""),  # the rule's entry says its existing control is its pool
    ({**GOOD_CONTROL, "percentile": 93.0, "p_value": 141 / 2001}, False, "Holm, 12 families"),
])
def test_s3_needs_a_matched_control_from_the_pool_and_survives_the_correction(control, state, why):
    m = judge(sleeve(0.03, control=control))["marks"]["s3"]
    assert m["met"] is state and why in m["why"]


def test_s3_a_93rd_percentile_passed_gate_v2_and_fails_gate_v3():
    """93rd percentile: gate v2's mark 3 passes it. Under gate v3 the family has four rules or more and there are twelve
    families: the family p is 4 x 0.072 = 0.29, far above 0.10 / 12."""
    control = {"percentile": 93.0, "runs": 2000, "matched": True, "pool": "candidate_pool"}
    assert g2.mark_3(g2.GateV2.load(ROOT), control)["met"] is True
    m = judge(sleeve(0.03, control=control))["marks"]["s3"]
    assert m["met"] is False and m["numbers"]["family_p"] == pytest.approx(MOMENTUM * 141 / 2001)
    assert m["numbers"]["holm_threshold"] == pytest.approx(0.10 / 12) and m["numbers"]["family_members"] == MOMENTUM
    assert m["numbers"]["bh_adjusted"] >= m["numbers"]["family_p"]


def test_s3_the_step_down_uses_the_other_families():
    control = {"percentile": 99.6, "runs": 2000, "matched": True, "pool": "candidate_pool"}  # p 9/2001, family 0.018
    alone = judge(sleeve(0.03, control=control))["marks"]["s3"]
    assert alone["met"] is False  # 0.024 > 0.10 / 12
    others = {f: 0.0005 for f in GATE.families if f != "cross_sectional_momentum"}  # eleven clear rejections
    after = judge(sleeve(0.03, control=control), other_family_p=others)["marks"]["s3"]
    assert after["met"] is True and after["numbers"]["holm_rank"] == 12  # the last step tests at the full 0.10


def test_s3_an_event_rule_scored_against_the_whole_universe_is_unknown():
    rep = g3.evaluate_sleeve(GATE, rule="news_gap_week", account=sleeve(0.03, control={
        "kind": "exposure_matched", "runs": 2000, "matched": True, "sharpe_percentile": 99.95}), tbill=BILLS)
    m = rep["marks"]["s3"]
    assert m["met"] is None and "drew from universe" in m["why"] and rep["verdict"] == "incomplete"


def test_s3_a_timing_sleeve_uses_the_random_timing_control():
    e = pd.Series(0.5, index=IDX)
    acc = g3.Account(pd.Series(0.5 * SPY + 0.0002, index=IDX), SPY, e, control={
        "kind": "random_timing", "runs": 500, "matched": True, "percentile": 99.8})
    rep = g3.evaluate_sleeve(GATE, rule="spy_trend_filter", account=acc, tbill=BILLS)
    assert rep["statistics"]["kind"] == "timing" and rep["marks"]["s3"]["numbers"]["drew_from"] == "random_timing"
    assert rep["marks"]["s3"]["met"] is True  # one rule in its family: p = 1/501 < 0.10/12


def test_s4_counts_the_family_not_the_whole_registry():
    """The retired dip rule's 8,286 configurations are in the registry and do not count against a momentum rule."""
    dip = {"trial_id": "wf", "strategy": "dip_reversal", "n_trials": 8286, "metrics": {"daily_sharpe_ann": 0.1}}
    reg = Registry((*registry(6).rows, dip), ("test",), 0)
    m = judge(registry=reg)["marks"]["s4"]
    assert m["numbers"]["configurations"] == 6 and m["numbers"]["whole_registry_configurations"] == 8292
    assert m["met"] is True and m["numbers"]["n_trials"] == 6
    same_family = Registry((*registry(6).rows, {**dip, "strategy": "mom_12_1_band"}), ("test",), 0)
    assert judge(registry=same_family)["marks"]["s4"]["numbers"]["configurations"] == 8292
    assert judge(registry=None)["marks"]["s4"]["met"] is None
    assert judge(registry=Registry((), ("test",), 0))["marks"]["s4"]["met"] is None  # the run itself is not logged
    weak = judge(sleeve(0.004, noise=0.004))["marks"]["s4"]
    assert weak["met"] is False and weak["numbers"]["dsr"] < 0.90


def test_s5_and_s6():
    today = sleeve(0.03, universe="today")
    assert judge(today)["marks"]["s5"]["met"] is None
    assert judge(today, pit_account=sleeve(0.03))["marks"]["s5"]["met"] is True
    assert judge(today, pit_account=sleeve(-0.03))["marks"]["s5"]["met"] is False
    m = judge(neighbours={"n1": sleeve(0.03, seed=2), "n2": sleeve(-0.03, seed=3)})["marks"]["s6"]
    assert m["met"] is False and m["numbers"]["results"] == {"n1": True, "n2": False}
    assert judge(neighbours={"n1": sleeve(0.03, seed=2)})["marks"]["s6"]["met"] is None  # n2 was not supplied
    assert judge(prereg=prereg(neighbours=()))["marks"]["s6"]["met"] is False  # no plateau check declared


def test_a_sleeve_needs_its_exposure():
    acc = sleeve(0.03)
    with pytest.raises(ValueError, match="needs its exposure series"):
        judge(g3.Account(acc.net, acc.spy))


def test_the_harness_and_an_unlisted_rule_cannot_pass():
    rep = judge(source={"engine": "research_harness", "cost_model": "v1", "data_repairs": False,
                        "benchmark": "spy_price_only"})
    assert rep["verdict"] == "provisional" and len(rep["provisional_reasons"]) >= 5
    new = g3.evaluate_sleeve(GATE, rule="brand_new_rule", account=sleeve(0.03), tbill=BILLS)
    assert new["marks"]["s3"]["met"] is None and new["gate_a"]["in_a_family"] is False
    assert any("in no family" in r for r in new["provisional_reasons"])


def test_the_sleeve_report_reads_as_markdown():
    text = g3.render_sleeve(judge())
    for phrase in ("Gate v3, sleeve test: trend_momentum (2017-05-01 to 2024-12-31)", "**Verdict: provisional**",
                   "S3. Skill against chance", "Holm-adjusted", "Benjamini-Hochberg-adjusted",
                   "drawdown percentile 71.0", "lie in the hold-out", "Treasury bills on both sides",
                   "of the family (the whole registry holds"):
        assert phrase in text, phrase
    assert not re.search(r"\bnan\b", text)


# ---------------------------------------------------------------- the book test


def by_year(yearly: dict[int, float]) -> pd.Series:
    out = pd.Series(0.0, index=IDX)
    for year, total in yearly.items():
        days = IDX.year == year
        out[days] = (1.0 + total) ** (1.0 / days.sum()) - 1.0
    return out


SPY_YEARS = dict(zip(range(2017, 2027), [0.15, -0.06, 0.30, 0.18, 0.28, -0.19, 0.26, 0.25, 0.17, 0.14],
                     strict=True))
BOOK_SPY = by_year(SPY_YEARS)
STEADY = {y: (r - 0.02 if r > 0 else r / 2) for y, r in SPY_YEARS.items()}  # the defensive branch


def book_registry(n=6, strategy="index_core_book") -> Registry:
    """#701: the book's own logged configurations, which B2's chance test (gate v3.1) needs for its N."""
    return Registry(tuple({"trial_id": f"b{i}", "strategy": strategy, "n_trials": 1} for i in range(n)), ("t",), 0)


def book(yearly=None, **kw) -> dict:
    acc = g3.Account(by_year(yearly or STEADY), BOOK_SPY, costs=pd.Series(0.00002, index=IDX),
                     universe=kw.pop("universe", "point_in_time"))
    kw.setdefault("prereg", prereg(rule="index_core_book"))
    kw.setdefault("neighbours", {"n1": by_year(STEADY), "n2": by_year(STEADY)})
    kw.setdefault("sleeves", {})
    kw.setdefault("source", FINAL)
    kw.setdefault("first_run_at", datetime(2026, 11, 25, tzinfo=UTC))
    kw.setdefault("registry", book_registry())
    return g3.evaluate_book(GATE, book="index_core_book", account=acc, as_of=datetime(2026, 12, 1, tzinfo=UTC),
                            **kw)


def test_the_book_test_is_gate_v2s_absolute_test_on_the_research_window():
    rep = book()
    assert met(rep) == {k: True for k in ("b1", "b2", "b3", "b4", "b5", "b6")}
    n = rep["marks"]["b1"]["numbers"]
    assert n["years"] == 8 and n["positive_years"] == 6 and rep["marks"]["b1"]["limit"]["positive_years_min"] == 5
    b2 = rep["marks"]["b2"]["numbers"]
    assert b2["branch"] == {"defensive": True, "growth": False} and b2["max_drawdown_vs_spy"] == pytest.approx(0.5,
                                                                                                             abs=0.02)
    assert rep["sessions_in_holdout_dropped"] > 400 and rep["statistics"]["window"][1] == "2024-12-31"
    # the same series under gate v2's own mark 2 on the same window gives the same branch result
    cut = GATE.cut(by_year(STEADY))[0]
    v2 = g2.mark_2(g2.GateV2.load(ROOT), cut, BOOK_SPY.reindex(cut.index))
    assert v2["numbers"]["branch"] == b2["branch"] and v2["numbers"]["cagr"] == pytest.approx(b2["cagr"])
    assert rep["verdict"] == "provisional"  # the book is in no family yet: it must be added with its pre-registration


def test_a_book_that_trails_spy_or_falls_as_far_fails_b2():
    lagging = {y: r - 0.05 for y, r in SPY_YEARS.items()}
    rep = book(lagging)
    assert rep["marks"]["b2"]["met"] is False and rep["verdict"] == "fail"
    assert book(dict(SPY_YEARS))["marks"]["b2"]["met"] is False  # SPY itself: same return, same drawdown


def test_b1_needs_eight_counted_years_and_sixty_percent_positive():
    few = {y: (0.05 if y in (2019, 2020, 2021, 2023) else -0.02) for y in SPY_YEARS}  # 4 of 8 positive
    m = book(few)["marks"]["b1"]
    assert m["met"] is False and "4 positive years, 5 needed" in m["why"]
    short = g3.Account(by_year(STEADY)["2019-01-01":], BOOK_SPY, universe="point_in_time")
    rep = g3.evaluate_book(GATE, book="x", account=short, sleeves={})
    assert "6 counted calendar years, the mark needs 8" in rep["marks"]["b1"]["why"]


def test_b3_to_b6():
    assert book(universe="today")["marks"]["b4"]["met"] is None
    assert book(sleeves=None)["marks"]["b6"]["met"] is None
    m = book(sleeves={"news_gap_week": "pass", "trend_momentum": "fail"})["marks"]["b6"]
    assert m["met"] is False and "trend_momentum: fail" in m["why"]
    assert book(sleeves={"news_gap_week": "provisional"})["marks"]["b6"]["met"] is False
    assert book(neighbours={"n1": by_year(STEADY)})["marks"]["b5"]["met"] is None
    acc = g3.Account(by_year(STEADY), BOOK_SPY, universe="point_in_time")
    assert g3.evaluate_book(GATE, book="x", account=acc, sleeves={})["marks"]["b3"]["met"] is None


def test_the_book_intervals_are_printed_and_seeded():
    rng = np.random.default_rng(2)
    noisy = g3.Account(pd.Series(0.7 * SPY + rng.normal(0.0001, 0.002, len(IDX)), index=IDX), SPY,
                       costs=pd.Series(0.00001, index=IDX), universe="point_in_time")
    a = g3.evaluate_book(GATE, book="x", account=noisy, sleeves={}, tbill=BILLS)
    b = g3.evaluate_book(GATE, book="x", account=noisy, sleeves={}, tbill=BILLS)
    n = a["marks"]["b2"]["numbers"]
    assert n == b["marks"]["b2"]["numbers"]
    lo, hi = n["max_drawdown_vs_spy_interval"]
    assert lo < n["max_drawdown_vs_spy"] < hi and 0.0 <= n["bootstrap_share_meeting"]["either"] <= 1.0
    assert n["bootstrap_share_shallower_drawdown"] > 0.9  # 70% of SPY: almost always a shallower fall
    assert a["statistics"]["cash_yield"]["tbill_cash"]["rule"] is None  # no exposure series was given
    text = g3.render_book(a)
    assert "Gate v3, book test" in text and "Share of resampled decades that meet B2" in text


# ---------------------------------------------------------------- the hold-out check


def test_the_hold_out_check_asks_for_consistency_not_significance():
    acc = sleeve(0.03)
    a = pd.Series(acc.net - mtm.exposure_matched(SPY, acc.exposure), index=IDX)
    research, hold = a[a.index <= "2024-12-31"], a[a.index > "2024-12-31"]
    ok = g3.holdout_check(GATE, research=research, holdout=hold, first_run_at=datetime(2027, 1, 5, tzinfo=UTC))
    assert ok["met"] is True and ok["clean"] is True and ok["label"] == "out-of-sample check"
    c = ok["checks"]["active_return"]
    assert c["floor"] < c["research"] and c["holdout"] >= c["floor"]
    bad = g3.holdout_check(GATE, research=research, holdout=hold - 0.0006,  # 15% a year worse than the research window
                           first_run_at=datetime(2026, 11, 13, tzinfo=UTC))
    assert bad["met"] is False and bad["clean"] is False and bad["label"].startswith("consistency check")
    with pytest.raises(ValueError, match="must start after it"):
        g3.holdout_check(GATE, research=research, holdout=research[-50:], first_run_at=None)


def test_the_hold_out_check_of_a_book_has_a_return_floor_and_a_drawdown_ceiling():
    b = pd.Series(0.7 * SPY + 0.0001, index=IDX)
    r, h = b[b.index <= "2024-12-31"], b[b.index > "2024-12-31"]
    out = g3.holdout_check(GATE, research=r, holdout=h, first_run_at=None, spy_research=SPY[r.index],
                           spy_holdout=SPY[h.index])
    assert set(out["checks"]) == {"cagr_vs_spy_points", "max_drawdown"} and out["clean"] is False
    crash = h.copy()
    crash.iloc[:30] = -0.02
    worse = g3.holdout_check(GATE, research=r, holdout=crash, first_run_at=None, spy_research=SPY[r.index],
                             spy_holdout=SPY[h.index])
    assert worse["checks"]["max_drawdown"]["met"] is False and worse["met"] is False
