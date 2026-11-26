"""Gate v3.1, ticket 6.4 (#704, decision 18 section 8): only p-values from runs that meet `final_requires` enter
Holm (the rest are greyed out); a pre-registered control tolerance of 0.5% unfilled slots; and the reference run
inside another rule's report is no longer read as that rule's own run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_v3 as g3
from kaibot.evaluation import results

GATE = g3.GateV3.load(ROOT)
FINAL = {"engine": "simbroker", "cost_model": "v2", "data_repairs": True, "benchmark": "spy_total_return"}
RULE = "trend_momentum_gbm"  # cross_sectional_momentum: several rules, 2,000 runs needed


def control(**kw) -> dict:
    """A control block as multiday_core.exposure_matched_control stores it."""
    c = {"kind": "exposure_matched", "runs": 2000, "slots": 946, "matched": True, "pool": "candidate_pool",
         "sharpe_percentile": 83.6, "exposure_tolerance_pts": 2.0,
         "committed_exposure_gap_pts": {"mean": 0.4, "max": 1.1}, "control_result": {"unfilled": 0, "runs": 2000}}
    inner = kw.pop("unfilled", None)
    if inner is not None:
        c["control_result"] = {**c["control_result"], "unfilled": inner}
    return {**c, **kw}


def test_the_config_pre_registers_the_tolerance_and_the_holm_inputs():
    s3 = GATE.cfg["sleeve"]["s3_control"]
    assert s3["unfilled_slots_max_share"] == 0.005 and s3["holm_inputs"] == "final_requires"
    cfg = yaml.safe_load((ROOT / "config" / "gate_v3.yaml").read_text())
    for bad in (0.01, -0.1, True):
        cfg["sleeve"]["s3_control"]["unfilled_slots_max_share"] = bad
        with pytest.raises(ValueError, match="unfilled_slots_max_share"):
            g3.GateV3.from_bytes(yaml.safe_dump(cfg).encode())
    cfg["sleeve"]["s3_control"]["unfilled_slots_max_share"] = 0.005
    cfg["sleeve"]["s3_control"]["holm_inputs"] = "any"
    with pytest.raises(ValueError, match="holm_inputs"):
        g3.GateV3.from_bytes(yaml.safe_dump(cfg).encode())


# ---------------------------------------------------------------- the tolerance


@pytest.mark.parametrize("unfilled, gap, ok", [(1, 1.1, True), (4, 1.1, True), (5, 1.1, False), (1, 2.5, False)])
def test_one_unfilled_slot_of_946_no_longer_voids_s3(unfilled, gap, ok):
    c = control(matched=False, unfilled=unfilled, committed_exposure_gap_pts={"mean": 0.4, "max": gap})
    got, how = g3.control_matched(GATE, g3.control_numbers(c))
    assert got is ok
    m = g3.s3(GATE, RULE, c)
    if ok:
        assert m["met"] is not None and "within the tolerance of 4 (0.5%)" in m["numbers"]["matched_reading"]
    else:
        assert m["met"] is None and ("more than the tolerance" in m["why"] or "committed exposure" in m["why"])


def test_the_gbm_control_of_474_is_judged_and_fails_it_does_not_pass():
    """#474: 1 of 946 slots unfilled, percentile 83.6 of 2,000 (p 0.16). Within the tolerance S3 is judged; times
    the family's rules and under Holm it fails. The tolerance turns an unknown into a fail, not into a pass."""
    m = g3.s3(GATE, RULE, control(matched=False, unfilled=1))
    assert m["met"] is False and m["numbers"]["family_p"] > 0.5


def test_a_control_without_its_counts_stays_unmatched():
    m = g3.s3(GATE, RULE, {"kind": "exposure_matched", "runs": 2000, "sharpe_percentile": 99.0, "matched": False,
                           "pool": "candidate_pool"})
    assert m["met"] is None and "not matched" in m["why"]


# ---------------------------------------------------------------- what enters Holm


def test_only_a_final_requires_run_enters_holm():
    p, why = g3.holm_input(GATE, RULE, control(), FINAL, 0)
    assert p == pytest.approx(g3.control_numbers(control())["p_value"]) and why == []


@pytest.mark.parametrize("change, phrase", [
    ({"source": {**FINAL, "cost_model": "0.5 bp"}}, "cost_model is '0.5 bp', Holm needs 'v2'"),
    ({"source": {**FINAL, "engine": "research_harness"}}, "engine is 'research_harness'"),
    ({"holdout": 464}, "464 sessions into the hold-out"),
    ({"control": control(runs=500)}, "500 control runs, 2000 needed"),
    ({"control": control(matched=False, unfilled=10)}, "more than the tolerance"),
    ({"control": control(pool="universe"), "rule": "news_gap_week"}, "not the rule's own candidate pool"),
])
def test_any_other_p_value_is_greyed_out(change, phrase):
    rule = change.get("rule", RULE)
    p, why = g3.holm_input(GATE, rule, change.get("control", control()), change.get("source", FINAL),
                           change.get("holdout", 0))
    assert p is not None and any(phrase in w for w in why), why


def test_the_spy_family_p_of_the_published_table_would_be_greyed_out():
    """The audit's case: spy_noise_band's 0.5 bp run, a 500-run timing control, a window into the hold-out."""
    c = {"kind": "random_timing", "runs": 500, "matched": True, "percentile": 99.8}
    src = {"engine": "research_harness", "cost_model": "not stated", "data_repairs": True,
           "benchmark": "spy_total_return"}
    p, why = g3.holm_input(GATE, "spy_noise_band", c, src, 464)
    assert p == pytest.approx(g3.control_numbers(c)["p_value"]) and len(why) >= 3


# ---------------------------------------------------------------- the reference row


def gbm_report(tmp_path: Path) -> tuple[Path, dict]:
    """trend_momentum_gbm.py's layout: rows without `kind`, a reference run of trend_momentum, one control."""
    (tmp_path / "t").mkdir()
    for lab in ("rule_1x", "reference_trend_momentum"):
        (tmp_path / "t" / f"daily_{lab}.csv").write_text("date,start,pnl,exposure\n2024-01-02,100,1,0.8\n")
    rep = {"run_at": "2026-11-24T18:57:51+00:00", "rule": RULE, "universe": "pit", "cost_model": "v2",
           "source": {"engine": "simbroker", "cost_model": "v2", "data_repairs": True,
                      "benchmark": "spy_total_return"},
           "rows": [{"label": "rule|1x", "spec": RULE, "fill": "base", "cost": 1.0},
                    {"label": "reference|trend_momentum", "spec": "trend_momentum", "fill": "base", "cost": 1.0}],
           "control": control(matched=False, unfilled=1)}
    f = tmp_path / "trend_momentum_gbm_balanced_pit_20261124T185751.json"
    f.write_text(json.dumps(rep))
    return f, rep


def test_the_reference_run_is_not_a_trend_momentum_row_and_gets_no_control(tmp_path):
    f, rep = gbm_report(tmp_path)
    rows = {r.label: r for r in results.rows(f, rep)}
    ref, rule = rows["reference|trend_momentum"], rows["rule|1x"]
    assert ref.role == "reference" and ref.control is None
    assert rule.role == "base" and rule.control is not None
    assert rule.set == "engine_core" and rule.meta["engine"] == "simbroker" and rule.meta["cost_model"] == "v2"
    assert ("trend_momentum", results.run_kind(ref)) not in results.newest_per_rule(list(rows.values()))
