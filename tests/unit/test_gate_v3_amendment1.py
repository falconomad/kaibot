"""Gate v3, amendment 1 (decision 16 section 15; M11 ticket 2.12, issue #514): the follow-ups QA raised on #499.

- S4's variance of the trial Sharpes never below 1/(T-1);
- a family of two or more rules needs at least 2,000 control runs (500 stays the floor for a one-rule family);
- the selection benchmark's first session holds the exposure of the close before the window, not 0% SPY;
- the evidence-based tier of section 14 (label, S1/S2/S5/S6 judged, S3/S4 printed, one 10% budget per book);
- a book is listed under `books:`, not in a family, and its Gate A reads that entry.

Synthetic inputs whose answer is known, as in tests/unit/test_gate_v3.py."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_v3 as g3
from kaibot.evaluation import mtm
from kaibot.evaluation.prereg import Prereg
from kaibot.evaluation.trials import Registry

GATE = g3.GateV3.load(ROOT)
IDX = pd.bdate_range("2017-05-01", "2024-12-31")
RNG = np.random.default_rng(514)
SPY = pd.Series(RNG.normal(0.0005, 0.011, len(IDX)), index=IDX)
BILLS = pd.Series(4.0, index=pd.bdate_range("2017-01-02", "2024-12-31"))
FINAL = {"engine": "simbroker", "cost_model": "v2", "data_repairs": True, "benchmark": "spy_total_return"}
RULE = "trend_momentum"  # four rules in cross_sectional_momentum
CONTROL = {"percentile": 99.95, "runs": 2000, "matched": True, "p_value": 1 / 2001, "pool": "candidate_pool"}


def cfg() -> dict:
    return yaml.safe_load((ROOT / "config" / "gate_v3.yaml").read_text())


def gate_with(change) -> g3.GateV3:
    c = cfg()
    change(c)
    return g3.GateV3.from_bytes(yaml.safe_dump(c).encode())


def sleeve(edge=0.0, exposure=0.4, noise=0.0015, seed=1, index=IDX, **kw) -> g3.Account:
    e = pd.Series(exposure, index=index)
    spy = SPY.reindex(index).fillna(0.0)
    own = np.random.default_rng(seed).normal(edge / 252, noise, len(index))
    net = pd.Series(mtm.exposure_matched(spy, e) + own, index=index)
    kw.setdefault("costs", pd.Series(0.00002, index=index))
    kw.setdefault("control", CONTROL)
    kw.setdefault("universe", "point_in_time")
    return g3.Account(net, spy, e, **kw)


EVIDENCE_SECTIONS = {"evidence": "Jegadeesh and Titman (1993); Asness, Moskowitz and Pedersen (2013).",
                     "hypothesis": "An evidence-based, not proven here sleeve (decision 16 section 14)."}


def prereg(rule=RULE, neighbours=("n1", "n2"), sections=EVIDENCE_SECTIONS) -> Prereg:
    return Prereg(rule, Path(f"docs/prereg/{rule}.md"), "cd" * 32, "pre-registered", date(2026, 11, 23),
                  tuple(neighbours), dict(sections), datetime(2026, 11, 23, tzinfo=UTC))


def registry(n=6, strategy=RULE, spread=0.0) -> Registry:
    rows = tuple({"trial_id": f"t{i}", "strategy": strategy, "n_trials": 1,
                  "metrics": {"daily_sharpe_ann": 0.5 + spread * i}} for i in range(n))
    return Registry(rows, ("test",), 0)


def judge(gate=GATE, acc=None, rule=RULE, **kw) -> dict:
    kw.setdefault("prereg", prereg(rule))
    kw.setdefault("neighbours", {"n1": sleeve(0.03, seed=2), "n2": sleeve(0.03, seed=3)})
    kw.setdefault("source", FINAL)
    kw.setdefault("registry", registry(strategy=rule))
    kw.setdefault("first_run_at", datetime(2026, 11, 25, tzinfo=UTC))
    kw.setdefault("tbill", BILLS)
    return g3.evaluate_sleeve(gate, rule=rule, account=acc or sleeve(0.03), as_of=datetime(2026, 12, 1, tzinfo=UTC),
                              **kw)


# ---------------------------------------------------------------- the config and the decision's text


def test_amendment_1_is_in_the_config_and_in_the_decision():
    c = GATE.cfg
    text = (ROOT / "docs" / "decisions" / "16_gate_v3.md").read_text()
    assert c["sleeve"]["s3_control"]["random_runs_min"] == 500
    assert c["sleeve"]["s3_control"]["random_runs_min_several_rules"] == 2000
    assert c["sleeve"]["s4_deflated_sharpe"]["variance_floor"] == "one_over_t_minus_1"
    e = c["evidence_based"]
    assert e["label"] == "evidence-based, not proven here" and e["book_budget_max_pct"] == 10.0
    assert e["marks_required"] == ["s1", "s2", "s5", "s6"] and e["reported_not_judged"] == ["s3", "s4"]
    assert "books" in c
    assert "## 15. Amendment 1" in text
    for phrase in ("1/(T-1)", "2,000", "evidence-based, not proven here", "`books:`", "close before the window"):
        assert phrase in text, phrase


# ---------------------------------------------------------------- S4: the variance floor


def test_s4_the_variance_never_falls_below_one_over_t_minus_one():
    """Near-identical configurations have almost no spread of Sharpes: without the floor the hurdle almost
    vanishes and a no-skill sleeve passes S4 far more often than 1 in 10 (QA on #499: 7.5% instead of 0.5%)."""
    tight = registry(20, spread=1e-6)
    m = judge(acc=sleeve(0.0, noise=0.004), registry=tight)["marks"]["s4"]
    t = m["numbers"]["sessions_judged"]
    assert m["numbers"]["variance_floor"] == pytest.approx(1.0 / (t - 1))
    assert m["numbers"]["trial_sharpe_var"] == pytest.approx(m["numbers"]["variance_floor"])
    assert m["numbers"]["variance_of_trial_sharpes"] < m["numbers"]["variance_floor"]
    wide = registry(20, spread=0.2)  # a real spread above the floor is used as it is
    w = judge(acc=sleeve(0.0, noise=0.004), registry=wide)["marks"]["s4"]["numbers"]
    assert w["trial_sharpe_var"] == pytest.approx(w["variance_of_trial_sharpes"])
    assert w["variance_of_trial_sharpes"] > w["variance_floor"] and not w["variance_floor_binds"]


def test_s4_with_the_floor_no_skill_passes_at_most_about_the_level():
    """Many no-skill sleeves against a family of 20 near-identical configurations: the share passing S4 is held
    near the strict reading's, well under 10%."""
    tight = registry(20, spread=1e-6)
    passed = []
    for seed in range(60):
        acc = sleeve(0.0, noise=0.004, seed=100 + seed)
        a = g3.active_return(GATE, "selection", acc, None)["active"]
        passed.append(g3.s4(GATE, RULE, a, tight)["met"])
    assert np.mean(passed) <= 0.05


# ---------------------------------------------------------------- S3: 2,000 runs for a family of several rules


@pytest.mark.parametrize("rule, runs, state, why", [
    ("trend_momentum", 500, None, "500 runs, 2000 needed"),   # four rules in its family
    ("trend_momentum", 2000, True, ""),
    ("spy_trend_filter", 500, True, ""),                        # one rule in its family: 500 stays enough
])
def test_s3_runs_needed_depend_on_the_family_size(rule, runs, state, why):
    control = {**CONTROL, "runs": runs, "p_value": 1 / (runs + 1),
               "pool": "random_timing" if rule == "spy_trend_filter" else "candidate_pool",
               "kind": "random_timing" if rule == "spy_trend_filter" else None}
    acc = sleeve(0.03, control=control)
    m = g3.s3(GATE, rule, acc.control, None, None)
    assert m["met"] is state and why in m["why"]
    assert m["limit"]["runs_min"] == (2000 if rule == "trend_momentum" else 500)


# ---------------------------------------------------------------- the benchmark's first session


def test_the_selection_benchmark_holds_the_exposure_of_the_close_before_the_window():
    """A sleeve that holds exactly SPY, fully invested since before the window, has an active return of zero on
    EVERY session, the first one included (before the fix the first session was compared with 0% SPY)."""
    idx = pd.bdate_range("2017-03-01", "2024-12-31")
    spy = pd.Series(np.random.default_rng(3).normal(0.0005, 0.011, len(idx)), index=idx)
    e = pd.Series(1.0, index=idx)
    net = pd.Series(mtm.exposure_matched(spy, e), index=idx)  # flat before the first session of the series only
    acc = g3.Account(net, spy, e, costs=pd.Series(0.0, index=idx), universe="point_in_time")
    for rf in (None, BILLS.reindex(pd.bdate_range("2017-01-02", "2024-12-31"))):
        a = g3.active_return(GATE, "selection", acc, rf)
        assert a["active"].index[0] == pd.Timestamp("2017-05-01")
        assert float(a["active"].abs().max()) < 1e-15
        assert a["held"][0] == 1.0
    # a series that starts on the window's first session has no earlier close: flat going into it, as before
    late = g3.Account(net["2017-05-01":], spy, e["2017-05-01":], universe="point_in_time")
    assert g3.active_return(GATE, "selection", late, None)["held"][0] == 0.0


# ---------------------------------------------------------------- the evidence-based tier


EVIDENCE = "Jegadeesh and Titman (1993); Asness, Moskowitz and Pedersen (2013)"


def evidence_gate(rule=RULE, evidence=EVIDENCE) -> g3.GateV3:
    def change(c):
        fam = next(f for f, b in c["families"].items() if rule in b["rules"])
        c["families"][fam]["rules"][rule].update(v3_declared=True, tier="evidence_based", evidence=evidence)
    return gate_with(change)


def test_an_evidence_based_sleeve_is_judged_on_s1_s2_s5_s6_and_labelled():
    weak_control = {**CONTROL, "percentile": 80.0, "p_value": 0.2}
    rep = judge(evidence_gate(), sleeve(0.03, control=weak_control))
    assert rep["marks"]["s3"]["met"] is False and rep["marks"]["s3"]["judged"] is False
    assert rep["marks"]["s1"]["met"] and rep["marks"]["s2"]["met"] and rep["marks"]["s5"]["met"]
    assert rep["verdict"] == "evidence_based" and rep["tier"] == "evidence_based"
    assert rep["label"] == "evidence-based, not proven here"
    assert rep["needs_holdout_check"] is True
    text = g3.render_sleeve(rep)
    assert "evidence-based, not proven here" in text and "pass" not in text.split("**Verdict:")[1].split("**")[0]


def test_an_evidence_based_sleeve_still_fails_on_any_other_mark():
    assert judge(evidence_gate(), sleeve(-0.02))["verdict"] == "fail"  # S1
    thin = sleeve(0.004, costs=pd.Series(0.00002, index=IDX))
    assert judge(evidence_gate(), thin)["verdict"] == "fail"  # S2
    assert judge(evidence_gate(), sleeve(0.03, universe="today"))["verdict"] == "incomplete"  # S5 unknown


def test_with_every_mark_an_evidence_based_sleeve_passes_the_full_gate():
    rep = judge(evidence_gate(), sleeve(0.03))
    assert all(m["met"] for m in rep["marks"].values()) and rep["verdict"] == "pass"


def test_the_tier_needs_its_evidence_and_is_never_taken_by_a_default():
    rep = judge(evidence_gate(evidence=""), sleeve(0.03, control={**CONTROL, "percentile": 80.0, "p_value": 0.2}))
    assert rep["verdict"] == "provisional" and any("cites no evidence" in r for r in rep["provisional_reasons"])
    plain = gate_with(lambda c: c["families"]["cross_sectional_momentum"]["rules"][RULE].update(v3_declared=True))
    rep = judge(plain, sleeve(0.03, control={**CONTROL, "percentile": 80.0, "p_value": 0.2}))
    assert rep["verdict"] == "fail" and rep["tier"] == "full"


# ---------------------------------------------------------------- books


def book_gate(sleeves=None, tiered=(RULE, "news_gap_week")) -> g3.GateV3:
    def change(c):
        for rule in tiered:
            fam = next(f for f, b in c["families"].items() if rule in b["rules"])
            c["families"][fam]["rules"][rule].update(tier="evidence_based", evidence=EVIDENCE)
        c["books"] = {"core_book": {"components": ["index_core", RULE], "sleeves": sleeves or {RULE: 10.0},
                                    "v3_declared": True}}
    return gate_with(change)


def book(gate, sleeves, **kw):
    steady = SPY * 0.6 + 0.00005
    acc = g3.Account(steady, SPY, costs=pd.Series(0.00001, index=IDX), universe="point_in_time")
    kw.setdefault("prereg", prereg("core_book", ("n1",)))
    kw.setdefault("neighbours", {"n1": steady})
    return g3.evaluate_book(gate, book="core_book", account=acc, sleeves=sleeves, source=FINAL,
                            first_run_at=datetime(2026, 11, 25, tzinfo=UTC), as_of=datetime(2026, 12, 1, tzinfo=UTC),
                            **kw)


def test_a_book_listed_under_books_passes_gate_a_and_is_not_a_family():
    rep = book(book_gate(), {RULE: "pass"})
    assert rep["gate_a"]["listed_as_book"] is True and rep["gate_a"]["declares_gate_v3"] is True
    assert not any("no family" in r for r in rep["provisional_reasons"])
    assert len(book_gate().families) == len(GATE.families)  # the correction's count is unchanged
    unlisted = book(GATE, {RULE: "pass"})
    assert any("not listed under `books:`" in r for r in unlisted["provisional_reasons"])


def test_b6_admits_evidence_based_sleeves_inside_one_10_percent_budget():
    m = book(book_gate({RULE: 10.0}), {RULE: "evidence_based"})["marks"]["b6"]
    assert m["met"] is True and m["numbers"]["evidence_based_budget_pct"] == 10.0
    assert m["numbers"]["labels"] == {RULE: "evidence-based, not proven here"}
    two = book_gate({RULE: 6.0, "news_gap_week": 6.0})
    m = book(two, {RULE: "evidence_based", "news_gap_week": "evidence_based"})["marks"]["b6"]
    assert m["met"] is False and "12% of the book" in m["why"]  # 10% for all such sleeves together, not each
    assert book(book_gate({RULE: 10.0}), {RULE: "fail"})["marks"]["b6"]["met"] is False
    nobudget = book_gate({})
    nobudget.cfg["books"]["core_book"]["sleeves"] = {}
    m = book(nobudget, {RULE: "evidence_based"})["marks"]["b6"]
    assert m["met"] is False and "no budget" in m["why"]


def test_a_book_config_entry_must_be_well_formed():
    with pytest.raises(ValueError, match="books"):
        gate_with(lambda c: c.update(books={"bad": {"sleeves": {RULE: 10.0}}}))
    with pytest.raises(ValueError, match="budget"):
        gate_with(lambda c: c.update(books={"bad": {"components": ["index_core"], "sleeves": {RULE: -1},
                                                   "v3_declared": True}}))
    with pytest.raises(ValueError, match="both a book and a rule"):
        gate_with(lambda c: c.update(books={RULE: {"components": [], "sleeves": {}, "v3_declared": True}}))


def test_every_number_is_finite_and_serialisable():
    import json

    rep = judge(evidence_gate(), sleeve(0.03))
    json.dumps(rep)
    assert all(math.isfinite(v) for v in rep["marks"]["s4"]["numbers"].values() if isinstance(v, float))


# ---------------------------------------------------------------- QA on #542: the tier is enforced, not only recorded


def test_the_tier_needs_the_citation_and_the_label_in_the_pre_registration():
    weak = sleeve(0.03, control={**CONTROL, "percentile": 80.0, "p_value": 0.2})
    rep = judge(evidence_gate(), weak, prereg=prereg(sections={"hypothesis": "evidence-based, not proven here"}))
    assert rep["verdict"] == "provisional" and rep["label"] is None
    assert any("published-evidence section is empty" in r for r in rep["provisional_reasons"])
    rep = judge(evidence_gate(), weak, prereg=prereg(sections={"evidence": EVIDENCE}))
    assert rep["verdict"] == "provisional" and any("does not carry the label" in r for r in rep["provisional_reasons"])
    assert rep["gate_a"]["prereg_cites_evidence"] is True and rep["gate_a"]["prereg_carries_label"] is False


def test_b6_does_not_take_the_tier_from_the_verdict_string_alone():
    m = book(book_gate({RULE: 5.0}, tiered=()), {RULE: "evidence_based"})["marks"]["b6"]
    assert m["met"] is False and "does not take the evidence-based tier" in m["why"]


def test_b6_reads_a_listed_books_budgets_not_the_callers():
    m = book(book_gate({RULE: 6.0, "news_gap_week": 6.0}), {RULE: "evidence_based", "news_gap_week": "evidence_based"},
             budgets={RULE: 1.0, "news_gap_week": 1.0})["marks"]["b6"]
    assert m["met"] is False and m["numbers"]["evidence_based_budget_pct"] == 12.0


def test_b6_is_unknown_when_a_listed_sleeve_has_no_verdict():
    m = book(book_gate({RULE: 5.0, "news_gap_week": 5.0}), {RULE: "evidence_based"})["marks"]["b6"]
    assert m["met"] is None and m["numbers"]["missing_verdicts"] == ["news_gap_week"]
