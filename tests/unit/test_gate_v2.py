"""Evidence gate v2 (issue #126, decision 15 section 7): the pre-registered marks, the Gate B evaluator, the
Gate C tracking report and the portfolio view, on synthetic inputs whose answer is known."""

from __future__ import annotations

import hashlib
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
from kaibot.evaluation import mtm
from kaibot.evaluation.prereg import Prereg
from kaibot.evaluation.trials import Registry

GATE = g2.GateV2.load(ROOT)
IDX = pd.bdate_range("2017-05-01", "2026-11-06")  # the out-of-sample window of decision 15: ten calendar years
YEARS = list(range(2017, 2027))
SPY_YEARS = dict(zip(YEARS, [0.15, -0.06, 0.30, 0.18, 0.28, -0.19, 0.26, 0.25, 0.17, 0.14], strict=True))
FINAL = {"engine": "simbroker", "cost_model": "v2", "data_repairs": True, "benchmark": "spy_total_return"}
HARNESS = {"engine": "research_harness", "cost_model": "v1", "data_repairs": False, "benchmark": "spy_price_only"}
CONTROL = {"percentile": 93.0, "runs": 500, "matched": True, "strategy_sharpe": 0.8}


def by_year(yearly: dict[int, float], idx: pd.DatetimeIndex = IDX) -> pd.Series:
    """The same return every session of a calendar year, compounding to that year's figure: the year's return
    and (for a losing year) its drawdown are then exactly what the test says."""
    out = pd.Series(0.0, index=idx)
    for year, total in yearly.items():
        days = idx.year == year
        out[days] = (1.0 + total) ** (1.0 / days.sum()) - 1.0
    return out


SPY = by_year(SPY_YEARS)
# Two points a year behind SPY in its good years and half its loss in its bad ones: the "defensive" branch.
STEADY = {y: (r - 0.02 if r > 0 else r / 2) for y, r in SPY_YEARS.items()}


def prereg(neighbours=("n1", "n2"), status="pre-registered", committed=datetime(2026, 11, 1, tzinfo=UTC)) -> Prereg:
    return Prereg("my_rule", Path("docs/prereg/my_rule.md"), "ab" * 32, status, date(2026, 11, 1),
                  tuple(neighbours), {}, committed)


def run(yearly=None, **kw) -> g2.Run:
    kw.setdefault("costs", pd.Series(0.00002, index=IDX))  # 0.2 bp a session, about 0.5% a year
    kw.setdefault("random", CONTROL)
    kw.setdefault("universe", "point_in_time")
    return g2.Run(by_year(yearly or STEADY), kw.pop("spy", SPY), **kw)


def judge(r: g2.Run | None = None, **kw) -> dict:
    kw.setdefault("prereg", prereg())
    kw.setdefault("neighbours", {"n1": by_year(STEADY), "n2": by_year(STEADY)})
    kw.setdefault("source", FINAL)
    kw.setdefault("first_run_at", datetime(2026, 11, 5, tzinfo=UTC))
    return g2.evaluate_gate_b(GATE, rule="my_rule", run=r or run(), as_of=datetime(2026, 11, 12, tzinfo=UTC), **kw)


def met(rep: dict) -> dict:
    return {k: v["met"] for k, v in rep["marks"].items()}


def with_config(change) -> g2.GateV2:
    cfg = yaml.safe_load((ROOT / "config" / "gate_v2.yaml").read_text())
    change(cfg)
    return g2.GateV2.from_bytes(yaml.safe_dump(cfg).encode())


# ---------------------------------------------------------------- the pre-registered marks


def test_marks_are_those_of_decision_15():
    """config/gate_v2.yaml against the text of the decision: change one and this test asks for the other."""
    text = " ".join((ROOT / "docs" / "decisions" / "15_engine_v3.md").read_text().split())
    section = text[text.index("## 7. Evidence gate v2"):text.index("## 8. Order of work")]
    a, b, c, s = (GATE.cfg[k] for k in ("gate_a", "gate_b", "gate_c", "statistics"))
    for phrase, got, want in [
        ("<= 5 neighbours", a["max_neighbours"], 5),
        ("out of sample 2017-05 onward", b["out_of_sample_from"], date(2017, 5, 1)),
        ("in at least 6 of 10 calendar years", (b["mark_1"]["min_positive_years"], b["mark_1"]["of_years"]), (6, 10)),
        ("CAGR >= SPY total-return CAGR - 2 points with max drawdown <= 0.75 x SPY's",
         tuple(b["mark_2"]["defensive"].values()), (-2.0, 0.75)),
        ("CAGR >= SPY + 1 point with max drawdown <= 1.1 x SPY's", tuple(b["mark_2"]["growth"].values()), (1.0, 1.1)),
        ("at or above the 90th percentile of matched random entries", b["mark_3"]["random_percentile_min"], 90.0),
        (">= 500 runs", b["mark_3"]["random_runs_min"], 500),
        ("marks 1-2 still hold at 2x costs (3x and break-even reported)",
         (b["mark_4"]["costs_multiple"], b["mark_4"]["reported_multiples"]), (2.0, [1.0, 2.0, 3.0])),
        ("holds on the point-in-time universe", b["mark_5"]["universe"], "point_in_time"),
        ("every pre-declared neighbour passes mark 1", b["mark_6"]["neighbours_pass_mark"], 1),
        (">= 3 months and >= 60 sessions", (c["min_months"], c["min_sessions"]), (3.0, 60)),
        ("ledger equals broker every day", c["ledger_equals_broker_every_day"], True),
        ("inside the 95% block-bootstrap band of the test", c["drawdown_band"], 0.95),
        ("retirement trigger (99% band, or tracking outside tolerance for 10 sessions)",
         (c["retirement"]["drawdown_band"], c["retirement"]["tracking_breach_sessions"]), (0.99, 10)),
        ("a 50% haircut quoted beside every backtested Sharpe", s["sharpe_haircut"], 0.5),
        ("A backtested Sharpe above about 1.0", s["sharpe_warning_above"], 1.0),
        ("configurations, not rows", s["trial_count"], "configurations"),
    ]:
        assert phrase in section, f"decision 15 no longer says: {phrase}"
        assert got == want, phrase
    assert set(b["mark_2"]) == {"defensive", "growth"} and GATE.cfg["gate_d"]["decided_by"] == "stakeholder"
    assert GATE.cfg["applies_to"] == "multi_day"


def test_the_config_is_hashed_and_the_m4_gate_is_untouched():
    raw = (ROOT / "config" / "gate_v2.yaml").read_bytes()
    assert GATE.sha == hashlib.sha256(raw).hexdigest()
    assert g2.GateV2.from_bytes(raw + b"\n# a comment\n").sha != GATE.sha  # any change shows
    m4 = yaml.safe_load((ROOT / "config" / "gate.yaml").read_text())  # still in force for intraday rules
    assert (m4["bar"]["min_trades"], m4["bar"]["t_min"], m4["bar"]["random_pct_min"]) == (900, 3.0, 95.0)
    assert m4["kill"]["min_trades"] == 50 and m4["random_baseline"] == ["dip_reversal"]


def test_a_config_with_a_mark_missing_is_refused():
    cfg = yaml.safe_load((ROOT / "config" / "gate_v2.yaml").read_text())
    del cfg["gate_b"]["mark_3"]
    with pytest.raises(ValueError, match="gate_b lacks mark_3"):
        g2.GateV2.from_bytes(yaml.safe_dump(cfg).encode())


def test_the_clarifications_of_decision_15_section_10_are_in_the_config():
    """Section 10 (Product, 12 Nov 2026) settled what section 7 left open (#183, #184): change one and this test
    asks for the other."""
    text = " ".join((ROOT / "docs" / "decisions" / "15_engine_v3.md").read_text().split())
    section = text[text.index("## 10. Gate v2 clarifications"):]
    t, boot, m1 = GATE.cfg["gate_c"]["tracking"], GATE.cfg["gate_c"]["bootstrap"], GATE.cfg["gate_b"]["mark_1"]
    for phrase, got, want in [
        ("≤ 10 bp per session, over a rolling 20 sessions", (t["tolerance_bp_per_session"], t["rolling_sessions"]),
         (10.0, 20)),
        ("Mean absolute difference between live daily returns and the `SimBroker` replay", t["measure"],
         "mean_abs_diff"),
        ("10 sessions in a row", (t["breach_rule"], GATE.cfg["gate_c"]["retirement"]["tracking_breach_sessions"]),
         ("consecutive", 10)),
        ("counts only if it has at least 6 months of out-of-sample data", m1["part_year_min_months"], 6),
        ("Stationary bootstrap, mean block length 20 sessions, 2,000 resamples, fixed seed",
         (boot["method"], boot["mean_block_sessions"], boot["runs"], isinstance(boot["seed"], int)),
         ("stationary", 20, 2000, True)),
    ]:
        assert phrase in section, f"decision 15 section 10 no longer says: {phrase}"
        assert got == want, phrase
    assert "NOT in decision 15" not in (ROOT / "config" / "gate_v2.yaml").read_text()


# ---------------------------------------------------------------- Gate B: the verdict


def test_a_rule_that_meets_every_mark_passes():
    rep = judge()
    assert met(rep) == {f"mark_{i}": True for i in range(1, 7)}
    assert rep["verdict"] == "pass" and rep["can_pass"] and not rep["provisional"]
    assert rep["gate_sha"] == GATE.sha and rep["prereg"]["sha256"] == "ab" * 32 and rep["gate"] == "B"
    assert rep["gate_a"] == {"preregistered": True, "not_retroactive": True, "neighbours_at_most": True,
                             "committed_before_first_run": True, "unchanged_since_logged_runs": None,
                             "amended_since_logged_runs": None}
    json.dumps(rep)  # JSON-ready as it is


def test_the_research_harness_is_provisional_and_cannot_pass():
    """Decision 15: until S71, S83-S85 and S75 land, #113 results are provisional and cannot pass Gate B."""
    rep = judge(source=HARNESS)
    assert all(met(rep).values()) and rep["verdict"] == "provisional" and not rep["can_pass"]
    reasons = " ".join(rep["provisional_reasons"])
    for word in ("engine", "simbroker", "cost_model", "data_repairs", "benchmark"):
        assert word in reasons
    assert judge(source=None)["verdict"] == "provisional"  # an unstated source is not a final one


def test_a_retroactive_pre_registration_cannot_pass():
    rep = judge(prereg=prereg(status="retroactive"))
    assert rep["verdict"] == "provisional" and "retroactive" in " ".join(rep["provisional_reasons"])
    assert rep["gate_a"]["not_retroactive"] is False


def test_gate_a_is_checked_too():
    late = judge(first_run_at=datetime(2026, 10, 1, tzinfo=UTC))  # the rule ran a month before it was registered
    assert late["verdict"] == "provisional" and late["gate_a"]["committed_before_first_run"] is False
    unknown = judge(first_run_at=None)
    assert unknown["verdict"] == "provisional" and unknown["gate_a"]["committed_before_first_run"] is None
    many = judge(prereg=prereg(neighbours=[f"n{i}" for i in range(6)]),
                 neighbours={f"n{i}": by_year(STEADY) for i in range(6)})
    assert many["marks"]["mark_6"]["met"] is True and many["verdict"] == "provisional"
    assert "6 neighbours declared" in " ".join(many["provisional_reasons"])
    none = judge(prereg=None)
    assert none["verdict"] == "incomplete" and none["gate_a"]["preregistered"] is False  # mark 6 has no list


def test_the_hash_logged_with_each_run_must_be_todays_pre_registration():
    """`require` logs the file's sha256 with every run. A run logged under another hash means the file was
    edited after that run."""
    same = Registry(({"trial_id": "a", "strategy": "my_rule", "prereg_sha256": "ab" * 32},
                     {"trial_id": "b", "strategy": "another_rule", "prereg_sha256": "cd" * 32}), ())
    ok = judge(registry=same)
    assert ok["verdict"] == "pass" and ok["gate_a"]["unchanged_since_logged_runs"] is True
    edited = Registry(({"trial_id": "a", "strategy": "my_rule", "prereg_sha256": "ab" * 32},
                       {"trial_id": "b", "strategy": "my_rule", "prereg_sha256": "ef" * 32}), ())
    rep = judge(registry=edited)
    assert rep["verdict"] == "provisional" and rep["gate_a"]["unchanged_since_logged_runs"] is False
    assert "changed after a run" in " ".join(rep["provisional_reasons"])


def test_a_failed_mark_fails_whatever_else_holds():
    rep = judge(run(random={"percentile": 50.0, "runs": 500, "matched": True}), source=HARNESS)
    assert rep["verdict"] == "fail" and rep["marks"]["mark_3"]["met"] is False


# ---------------------------------------------------------------- Gate B: mark by mark


def test_mark_1_needs_six_positive_years_of_ten_and_a_positive_total():
    six = {y: (0.08 if i < 6 else -0.03) for i, y in enumerate(YEARS)}
    five = {y: (0.08 if i < 5 else -0.03) for i, y in enumerate(YEARS)}
    assert g2.mark_1(GATE, by_year(six))["met"] is True
    m = g2.mark_1(GATE, by_year(five))
    assert m["met"] is False and m["numbers"]["positive_years"] == 5 and "5 positive years, 6 needed" in m["why"]
    flat = {**six, 2017: 0.0}  # a year of exactly zero is not a positive year
    assert g2.mark_1(GATE, by_year(flat))["numbers"]["positive_years"] == 5
    loser = {y: (0.02 if i < 7 else -0.20) for i, y in enumerate(YEARS)}  # seven small gains, three big losses
    m = g2.mark_1(GATE, by_year(loser))
    assert m["met"] is False and m["numbers"]["positive_years"] == 7 and "not positive" in m["why"]
    assert m["numbers"]["partial_years"] == 2  # 2017 from May (8 months) and 2026 to November (11 months) ...
    assert m["numbers"]["years"] == 10 and m["numbers"]["years_not_counted"] == []  # ... have 6 months: they count


def test_mark_1_needs_ten_calendar_years_and_sixty_percent_beyond_ten():
    nine = by_year({y: 0.08 for y in YEARS[1:]}, pd.bdate_range("2018-01-02", "2026-11-06"))
    m = g2.mark_1(GATE, nine)
    assert m["met"] is False and "spans 9 calendar years" in m["why"]
    idx11 = pd.bdate_range("2017-05-01", "2027-06-30")
    eleven = {y: (0.08 if i < 6 else -0.01) for i, y in enumerate(range(2017, 2028))}
    m = g2.mark_1(GATE, by_year(eleven, idx11))
    assert m["met"] is False and m["limit"]["positive_years_min"] == 7  # 60% of eleven, rounded up
    eleven[2023] = 0.08
    assert g2.mark_1(GATE, by_year(eleven, idx11))["met"] is True


def test_mark_1_a_part_year_under_six_months_is_not_counted():
    """Decision 15 section 10 (#184): a partial calendar year counts only with at least 6 months of out-of-sample
    data; the threshold is then 60% of the counted years, rounded up."""
    four = pd.bdate_range("2017-01-02", "2027-04-30")  # ten full years and a 4-month stub
    yearly = {y: (0.08 if i < 6 else -0.01) for i, y in enumerate(range(2017, 2028))}
    m = g2.mark_1(GATE, by_year({**yearly, 2027: -0.05}, four))
    assert m["numbers"]["years"] == 10 and m["numbers"]["years_not_counted"] == [2027]
    assert m["limit"]["positive_years_min"] == 6 and m["met"] is True  # six of the ten counted years
    assert m["numbers"]["total_return"] > 0  # the stub still counts in the total return
    m = g2.mark_1(GATE, by_year({**yearly, 2027: 0.08}, four))
    assert m["numbers"]["positive_years"] == 6  # a positive stub adds no positive year either
    seven = pd.bdate_range("2017-01-02", "2027-07-30")  # ten full years and a 7-month stub
    m = g2.mark_1(GATE, by_year({**yearly, 2027: -0.05}, seven))
    assert m["numbers"]["years"] == 11 and m["numbers"]["years_not_counted"] == []
    assert m["limit"]["positive_years_min"] == 7 and m["met"] is False  # ceil(0.6 x 11) = 7; six positive
    assert g2.mark_1(GATE, by_year({**yearly, 2027: 0.05}, seven))["met"] is True
    nine_and_stub = pd.bdate_range("2017-05-01", "2026-03-31")  # 2017 from May, 2018-2025, three months of 2026
    m = g2.mark_1(GATE, by_year({y: 0.08 for y in range(2017, 2027)}, nine_and_stub))
    assert m["met"] is False and m["numbers"]["years"] == 9 and "spans 9 calendar years" in m["why"]


def test_the_report_prints_a_year_that_is_not_counted():
    idx = pd.bdate_range("2017-05-01", "2027-03-31")
    spy = by_year({**SPY_YEARS, 2027: 0.02}, idx)
    r = g2.Run(by_year({**STEADY, 2027: 0.01}, idx), spy, costs=pd.Series(0.00002, index=idx), random=CONTROL,
               universe="point_in_time")
    rep = judge(r, neighbours={"n1": by_year(STEADY, idx), "n2": by_year(STEADY, idx)})
    assert rep["marks"]["mark_1"]["numbers"]["years_not_counted"] == [2027]
    years = {y["year"]: y for y in rep["statistics"]["calendar_years"]}
    assert years[2027]["counted"] is False and years[2027]["months"] == 3 and years[2017]["counted"] is True
    md = g2.render_gate_b(rep)
    assert "| 2027 (part, not counted: under 6 months) |" in md and "| 2017 (part) |" in md


def test_mark_2_either_branch():
    periods = 252
    spy_cagr, spy_dd = mtm.cagr(SPY, periods), mtm.max_drawdown(SPY)
    assert spy_dd == pytest.approx(0.19)
    defensive = g2.mark_2(GATE, by_year(STEADY), SPY)
    assert defensive["met"] and defensive["numbers"]["branch"] == {"defensive": True, "growth": False}
    assert -2.0 < defensive["numbers"]["cagr_vs_spy_points"] < 1.0 and defensive["numbers"]["max_drawdown"] < 0.1
    assert defensive["numbers"]["spy_cagr"] == spy_cagr
    # two points and a bit ahead of SPY with the same drawdown: only the second branch
    growth = g2.mark_2(GATE, by_year({y: r + 0.025 if r > 0 else r for y, r in SPY_YEARS.items()}), SPY)
    assert growth["met"] and growth["numbers"]["branch"] == {"defensive": False, "growth": True}
    # behind SPY by more than two points: neither, however shallow the drawdown
    slow = g2.mark_2(GATE, by_year({y: 0.05 for y in YEARS}), SPY)
    assert not slow["met"] and slow["numbers"]["max_drawdown"] == 0.0 and slow["numbers"]["cagr"] < spy_cagr - 0.02
    # ahead of SPY but with a drawdown beyond 1.1 x SPY's: neither
    wild = g2.mark_2(GATE, by_year({y: r + 0.04 if r > 0 else -0.22 for y, r in SPY_YEARS.items()}), SPY)
    assert not wild["met"] and wild["numbers"]["max_drawdown_vs_spy"] > 1.1 and wild["why"]
    # as fast as SPY with 0.9 x its drawdown: too deep for the first branch, too slow for the second
    between = g2.mark_2(GATE, by_year({y: r if r > 0 else r * 0.9 for y, r in SPY_YEARS.items()}), SPY)
    assert not between["met"] and 0.75 < between["numbers"]["max_drawdown_vs_spy"] < 1.1


def test_mark_3_needs_the_90th_percentile_of_at_least_500_matched_runs():
    assert g2.mark_3(GATE, {"percentile": 90.0, "runs": 500, "matched": True})["met"] is True
    assert g2.mark_3(GATE, {"percentile": 89.9, "runs": 500, "matched": True})["met"] is False
    assert g2.mark_3(GATE, {"percentile": 99.0, "runs": 499, "matched": True})["met"] is None  # too few runs
    assert g2.mark_3(GATE, {"percentile": 99.0, "runs": 500, "matched": False})["met"] is None  # slots unfilled
    assert g2.mark_3(GATE, None)["met"] is None
    # the rule's own names through the control's simulator rank lower than its real run: the lower one counts
    both = g2.mark_3(GATE, {"percentile": 95.0, "percentile_simulated": 84.0, "runs": 500, "matched": True})
    assert both["met"] is False and both["numbers"]["percentile_used"] == 84.0
    assert g2.mark_3(GATE, {"percentile": 91.0, "percentile_simulated": 97.0, "runs": 500, "matched": True})["met"]
    assert judge(run(random=None))["verdict"] == "incomplete"  # unknown never passes


def test_mark_4_reruns_marks_1_and_2_at_twice_the_costs():
    thin = {**STEADY, 2019: 0.004, 2020: 0.004, 2021: 0.004, 2023: 0.004}  # eight gains, four of them thin
    costs = pd.Series(0.00002, index=IDX)  # about 0.5% a year: at 2x the thin years turn negative
    r = run(thin, costs=costs)
    net, spy, c, _ = g2._window(GATE, r)
    assert g2.mark_1(GATE, net)["met"] is True
    m4 = g2.mark_4(GATE, net, spy, c)
    assert m4["met"] is False and m4["numbers"]["mark_1"] is False and m4["numbers"]["positive_years"] < 6
    assert m4["numbers"]["breakeven_multiple"] > 1.0
    assert g2.mark_4(GATE, net, spy, None)["met"] is None  # no cost series: unknown, never a pass
    assert judge(run(costs=None))["verdict"] == "incomplete"
    ok = judge()["marks"]["mark_4"]
    assert ok["met"] is True and ok["numbers"]["cagr"] < judge()["marks"]["mark_2"]["numbers"]["cagr"]


def test_mark_5_needs_a_run_on_the_point_in_time_universe():
    today = run(universe="today")
    assert judge(today)["marks"]["mark_5"]["met"] is None and judge(today)["verdict"] == "incomplete"
    both = judge(today, pit_run=run())
    assert both["marks"]["mark_5"]["met"] is True and both["verdict"] == "pass"
    weak = {y: (0.08 if i < 5 else -0.03) for i, y in enumerate(YEARS)}  # fine on today's winners, not in truth
    survivor = judge(today, pit_run=run(weak))
    assert survivor["marks"]["mark_5"]["met"] is False and survivor["verdict"] == "fail"
    assert survivor["marks"]["mark_5"]["numbers"]["mark_1"] is False
    assert judge(today, pit_run=run(random=None))["marks"]["mark_5"]["met"] is None


def test_mark_6_every_declared_neighbour_must_pass_mark_1():
    good, bad = by_year(STEADY), by_year({y: -0.01 for y in YEARS})
    assert judge(neighbours={"n1": good, "n2": good})["marks"]["mark_6"]["met"] is True
    one_bad = judge(neighbours={"n1": good, "n2": bad})["marks"]["mark_6"]
    assert one_bad["met"] is False and one_bad["numbers"]["results"] == {"n1": True, "n2": False}
    missing = judge(neighbours={"n1": good})["marks"]["mark_6"]
    assert missing["met"] is None and "n2: no result supplied" in missing["why"]
    extra = judge(neighbours={"n1": good, "n2": good, "lucky": good})["marks"]["mark_6"]
    assert extra["met"] is True and extra["numbers"]["undeclared"] == ["lucky"]  # not declared: ignored, shown
    no_plateau = judge(prereg=prereg(neighbours=()), neighbours={})["marks"]["mark_6"]
    assert no_plateau["met"] is False and "no plateau check" in no_plateau["why"]


def test_only_the_out_of_sample_window_is_judged():
    early = pd.bdate_range("2016-06-01", "2026-11-06")
    good_then_bad = by_year({2016: 3.0, **{y: -0.02 for y in YEARS}}, early)  # a great 2016 that does not count
    r = g2.Run(good_then_bad, by_year(SPY_YEARS, early), universe="point_in_time", random=CONTROL)
    rep = judge(r)
    assert rep["sessions"] == len(IDX) and rep["sessions_before_window_dropped"] == len(early) - len(IDX)
    assert rep["marks"]["mark_1"]["met"] is False and rep["statistics"]["window"][0] == "2017-05-01"
    with pytest.raises(ValueError, match="no return for"):
        judge(g2.Run(by_year(STEADY), SPY.iloc[:-5]))  # SPY missing for the last sessions: an error, not a guess


# ---------------------------------------------------------------- Gate B: the numbers and the text


def noisy(seed: int = 7) -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    spy = pd.Series(rng.normal(0.0005, 0.011, len(IDX)), index=IDX)
    return 0.6 * spy + pd.Series(rng.normal(0.0002, 0.004, len(IDX)), index=IDX), spy


def test_report_carries_the_statistics_and_every_sharpe_its_haircut():
    net, spy = noisy()
    registry = Registry(({"trial_id": "a", "strategy": "my_rule", "n_trials": 8286},), ("test",))
    r = g2.Run(net, spy, costs=pd.Series(0.00003, index=IDX), random=CONTROL, universe="point_in_time",
               traded=pd.Series(4.0 / 252, index=IDX), exposure=pd.Series(0.6, index=IDX))
    rep = judge(r, registry=registry, source=HARNESS)
    st = rep["statistics"]
    assert st["alpha_beta"]["beta"] == pytest.approx(0.6, abs=0.03) and st["alpha_beta"]["alpha_t"] > 0
    assert st["rule"]["sharpe"]["haircut"] == pytest.approx(st["rule"]["sharpe"]["sharpe"] / 2)
    assert st["rule"]["sharpe"]["lags"] == mtm.nw_lag(len(IDX)) == 8
    assert [y["year"] for y in st["calendar_years"]] == YEARS and st["turnover"] == pytest.approx(4.0)
    assert list(st["costs"]) == ["1x", "2x", "3x", "breakeven_multiple"]
    assert st["exposure_matched_spy"]["mean_exposure"] == pytest.approx(0.6)
    d = st["deflated_sharpe"]
    assert d["n_trials"] == 8286 and d["hurdle"] > 1.0 and d["dsr"] < d["psr"] and "1/(T-1)" in d["var_source"]
    assert d["registry"]["configurations"] == 8286
    json.dumps(rep)
    text = g2.render_gate_b(rep)
    assert GATE.sha in text and "ab" * 32 in text and "(cannot pass)" in text
    assert f"**Verdict: {rep['verdict']}**" in text and "research_harness" in text
    assert "8,286 configurations" in text and "Break-even cost multiple" in text and "| 2026 (part) |" in text
    printed = [x for x in text.splitlines() if re.search(r"Sharpe\b.*?-?\d+\.\d\d", x) or re.match(r"\| \dx \|", x)]
    assert len(printed) >= 6  # the table row, the deflated line, the exposure-matched line, three cost rows
    for line in printed:
        assert "haircut" in line, line  # no backtested Sharpe without its haircut beside it


def test_a_sharpe_above_one_is_printed_with_a_warning():
    rng = np.random.default_rng(3)
    spy = pd.Series(rng.normal(0.0004, 0.011, len(IDX)), index=IDX)
    hot = pd.Series(rng.normal(0.0012, 0.008, len(IDX)), index=IDX)  # an annual Sharpe near 2.4
    rep = judge(g2.Run(hot, spy, universe="point_in_time", random=CONTROL))
    assert rep["statistics"]["rule"]["sharpe"]["warning"] is True and "WARNING" in g2.render_gate_b(rep)
    assert "Deflated Sharpe: not computed" in g2.render_gate_b(rep)  # no registry given: said, not guessed


# ---------------------------------------------------------------- Gate C


def with_tolerance(bp: float | None = 10.0, rule: str = "consecutive") -> g2.GateV2:
    def change(cfg):
        cfg["gate_c"]["tracking"].update(tolerance_bp_per_session=bp, breach_rule=rule)
    return with_config(change)


LIVE_IDX = pd.bdate_range("2027-01-04", periods=70)  # 70 sessions, a little over three months


def paper(seed: int = 21, n: int = 70) -> tuple[pd.Series, pd.Series, np.ndarray]:
    """A quiet live record, its replay within 1 bp a day, and the test returns the bands are drawn from."""
    rng = np.random.default_rng(seed)
    test = rng.normal(0.0004, 0.008, 2400)
    live = pd.Series(rng.normal(0.0004, 0.004, n), index=LIVE_IDX[:n])
    return live, live + rng.normal(0.0, 0.00005, n), test


def track(gate: g2.GateV2, live, replay, test, **kw) -> dict:
    kw.setdefault("reconciled", dict.fromkeys(live.index, True))
    kw.setdefault("trigger_committed_at", datetime(2026, 12, 20, tzinfo=UTC))
    return g2.gate_c_tracking(gate, rule="my_rule", live=live, replay=replay, test_returns=test,
                              as_of=datetime(2027, 4, 12, tzinfo=UTC), **kw)


def test_a_healthy_incubation_passes_gate_c():
    live, replay, test = paper()
    rep = track(with_tolerance(), live, replay, test)
    assert rep["status"] == "pass" and not rep["retire"] and all(m["met"] for m in rep["marks"].values())
    n = rep["marks"]["tracking"]["numbers"]
    assert n["sessions_compared"] == 70 and n["max_abs_diff_bp"] < 3 and n["correlation"] > 0.99
    assert rep["marks"]["duration"]["numbers"]["sessions"] == 70 and rep["marks"]["duration"]["numbers"]["months"] >= 3
    assert rep["gate"] == "C" and rep["gate_sha"] == with_tolerance().sha != GATE.sha
    assert rep["retirement_trigger"] == {"drawdown_outside_band": 0.99, "tracking_breach_sessions": 10,
                                         "tolerance_bp_per_session": 10.0, "rolling_sessions": 20,
                                         "breach_rule": "consecutive"}
    assert track(GATE, live, replay, test)["status"] == "pass"  # the repository's config judges it the same way
    json.dumps(rep)


def test_a_young_incubation_is_still_incubating():
    live, replay, test = paper(n=45)
    rep = track(with_tolerance(), live, replay, test)
    assert rep["status"] == "incubating" and rep["marks"]["duration"]["met"] is False
    assert rep["marks"]["drawdown"]["limit"]["bootstrap"]["horizon"] == 45  # the band is for 45 sessions


def test_a_code_change_restarts_the_incubation():
    """Only sessions that ran wholly on the rule's current code count (the per-rule fingerprint of audit G2)."""
    live, replay, test = paper()
    changed = datetime(2027, 2, 10, 15, 0, tzinfo=UTC)  # 10:00 in New York on 10 February: mid-session
    rep = track(with_tolerance(), live, replay, test, frozen_since=changed,
                trigger_committed_at=datetime(2026, 12, 20, tzinfo=UTC))
    n = rep["marks"]["duration"]["numbers"]
    kept = int((LIVE_IDX > "2027-02-10").sum())
    assert n["sessions"] == kept and n["sessions_on_older_code_excluded"] == 70 - kept
    assert rep["status"] == "incubating" and rep["marks"]["tracking"]["numbers"]["sessions_compared"] == kept
    before_open = track(with_tolerance(), live, replay, test, frozen_since=datetime(2027, 2, 10, 13, 0, tzinfo=UTC))
    assert before_open["marks"]["duration"]["numbers"]["sessions"] == kept + 1  # in place before the open: it counts
    assert track(with_tolerance(), live, replay, test, frozen_since=date(2026, 12, 1))["status"] == "pass"


def test_without_a_tolerance_the_tracking_mark_fails_closed():
    live, replay, test = paper()
    rep = track(with_tolerance(None), live, replay, test)
    assert rep["marks"]["tracking"]["met"] is None and "not set" in rep["marks"]["tracking"]["why"]
    assert rep["status"] == "incomplete" and not rep["retire"]


def drifting(gaps_bp: list[float], seed: int = 21) -> tuple[pd.Series, pd.Series, np.ndarray]:
    """A live record whose replay differs by exactly `gaps_bp[i]` basis points on session i."""
    live, _, test = paper(seed, n=len(gaps_bp))
    return live, live - np.array(gaps_bp) / 1e4, test


def test_tracking_judges_the_rolling_20_session_mean_absolute_difference():
    """Decision 15 section 10 (#183): mean |live - replay| over a rolling 20 sessions, at most 10 bp."""
    live, replay, test = drifting([12.0, -12.0] * 35)  # every session 12 bp off, either way: mean |diff| 12 bp
    rep = track(GATE, live, replay, test)
    n = rep["marks"]["tracking"]["numbers"]
    assert n["rolling_mean_abs_diff_bp_max"] == pytest.approx(12.0) and n["mean_diff_bp"] == pytest.approx(0.0)
    assert n["sessions_outside_tolerance"] == 51 and n["breach_count"] == 51  # sessions 20 to 70, all in a row
    assert rep["status"] == "retire" and rep["marks"]["tracking"]["met"] is False
    assert n["first_judged_session"] == live.index[19].date().isoformat()
    assert rep["marks"]["tracking"]["limit"] == {"tolerance_bp_per_session": 10.0, "rolling_sessions": 20,
                                                 "measure": "mean_abs_diff", "breach_sessions": 10,
                                                 "breach_rule": "consecutive"}


def test_an_isolated_spike_does_not_breach():
    gaps = [1.0] * 70
    gaps[30] = 150.0  # one session 150 bp off: far outside 10 bp on its own day ...
    live, replay, test = drifting(gaps)
    rep = track(GATE, live, replay, test)
    n = rep["marks"]["tracking"]["numbers"]
    assert n["max_abs_diff_bp"] == pytest.approx(150.0)
    assert n["rolling_mean_abs_diff_bp_max"] == pytest.approx((150.0 + 19) / 20)  # ... 8.45 bp over 20 sessions
    assert n["breach_count"] == 0 and rep["status"] == "pass" and rep["marks"]["tracking"]["met"] is True


def test_nine_rolling_breaches_in_a_row_do_not_retire_the_rule_and_ten_do():
    def gaps(drift_sessions: int) -> list[float]:
        return [15.0] * drift_sessions + [0.0] * (70 - drift_sessions)  # 15 bp off at the start, then exact

    # the window of 20 holds k sessions of 15 bp: mean 0.75 k bp, above 10 bp from k = 14
    for drift, breaches in ((22, 9), (23, 10)):
        live, replay, test = drifting(gaps(drift))
        rep = track(GATE, live, replay, test)
        n = rep["marks"]["tracking"]["numbers"]
        assert n["breach_count"] == breaches == n["sessions_outside_tolerance"]
        assert rep["retire"] is (breaches >= 10) and rep["status"] == ("retire" if breaches >= 10 else "pass")
    assert "tracking outside the tolerance" in rep["retirement_reasons"][0] and "in a row" in \
        rep["retirement_reasons"][0]
    # two stretches of nine, apart: eighteen sessions outside, never ten in a row
    live, replay, test = drifting([15.0] * 22 + [0.0] * 17 + [15.0] * 16 + [0.0] * 15)
    rep = track(GATE, live, replay, test)
    n = rep["marks"]["tracking"]["numbers"]
    assert n["sessions_outside_tolerance"] == 18 and n["breach_count"] == 9 and not rep["retire"]
    assert rep["status"] == "pass"
    cumulative = track(with_tolerance(rule="cumulative"), live, replay, test)  # the old reading would retire it
    assert cumulative["retire"]


def test_before_the_twentieth_session_tracking_is_not_judged():
    live, replay, test = paper(n=19)
    rep = track(GATE, live, replay, test)
    t = rep["marks"]["tracking"]
    assert t["met"] is None and "20 sessions" in t["why"] and rep["status"] == "incomplete"
    assert t["numbers"]["rolling_mean_abs_diff_bp_max"] is None and t["numbers"]["first_judged_session"] is None
    live, replay, test = paper(n=20)
    assert track(GATE, live, replay, test)["marks"]["tracking"]["met"] is True


def test_live_drawdown_against_the_bootstrap_bands_of_the_test():
    live, replay, test = paper()
    b = GATE.cfg["gate_c"]["bootstrap"]
    bands = mtm.drawdown_bands(test, horizon=70, runs=b["runs"], mean_block=b["mean_block_sessions"], seed=b["seed"])
    assert 0.02 < bands.q95 < bands.q99 < 0.30

    def falling(depth: float) -> tuple[pd.Series, pd.Series]:
        r = pd.Series(0.0, index=LIVE_IDX)
        r.iloc[10:30] = (1.0 - depth) ** (1 / 20) - 1.0  # one fall of `depth` over twenty sessions
        return r, r.copy()

    inside = track(with_tolerance(), *falling(bands.q95 * 0.9), test)
    assert inside["status"] == "pass" and inside["marks"]["drawdown"]["numbers"]["band_95"] == pytest.approx(bands.q95)
    outside = track(with_tolerance(), *falling((bands.q95 + bands.q99) / 2), test)
    assert outside["status"] == "fail" and outside["marks"]["drawdown"]["met"] is False and not outside["retire"]
    beyond = track(with_tolerance(), *falling(bands.q99 * 1.05), test)
    assert beyond["status"] == "retire" and "outside the 99% band" in beyond["retirement_reasons"][0]
    assert track(GATE, *falling(bands.q99 * 1.05), test)["status"] == "retire"  # needs no tolerance to fire


def test_ledger_must_equal_broker_every_day():
    live, replay, test = paper()
    rec = dict.fromkeys(live.index, True)
    rec[live.index[17]] = False
    rep = track(with_tolerance(), live, replay, test, reconciled=rec)
    assert rep["status"] == "fail" and rep["marks"]["reconciliation"]["numbers"]["days_not_equal"] == \
        [live.index[17].date().isoformat()]
    del rec[live.index[17]]
    rep = track(with_tolerance(), live, replay, test, reconciled=rec)
    assert rep["status"] == "incomplete" and rep["marks"]["reconciliation"]["met"] is None  # a day with no record
    assert track(with_tolerance(), live, replay, test, reconciled=None)["marks"]["reconciliation"]["met"] is None


def test_a_live_day_without_a_replay_and_a_late_trigger_do_not_pass():
    live, replay, test = paper()
    rep = track(with_tolerance(), live, replay.drop(replay.index[5]), test)
    assert rep["marks"]["tracking"]["met"] is None and rep["status"] == "incomplete"
    assert rep["marks"]["tracking"]["numbers"]["sessions_without_replay"] == [live.index[5].date().isoformat()]
    late = track(with_tolerance(), live, replay, test, trigger_committed_at=datetime(2027, 2, 1, tzinfo=UTC))
    assert late["marks"]["trigger_written_before_start"]["met"] is False and late["status"] == "fail"
    unknown = track(with_tolerance(), live, replay, test, trigger_committed_at=None)
    assert unknown["marks"]["trigger_written_before_start"]["met"] is None and unknown["status"] == "incomplete"


# ---------------------------------------------------------------- portfolio view (audit G4)


def test_two_rules_that_only_share_the_market_are_not_the_same_bet():
    rng = np.random.default_rng(31)
    spy = pd.Series(rng.normal(0.0005, 0.011, len(IDX)), index=IDX)
    a = 0.6 * spy + pd.Series(rng.normal(0.0002, 0.004, len(IDX)), index=IDX)
    b = 0.5 * spy + pd.Series(rng.normal(0.0002, 0.004, len(IDX)), index=IDX)
    view = g2.portfolio_view(GATE, {"a": a, "b": b}, spy)
    c = view["correlation"]
    assert c["raw"]["a"]["b"] > 0.6  # both are mostly market
    assert c["excess"]["a"]["b"] > 0.5  # still high: each is "short" the part of SPY it does not hold
    assert abs(c["residual"]["a"]["b"]) < 0.08  # with the market taken out they are unrelated
    assert view["mean_pairwise_residual_correlation"] == pytest.approx(c["residual"]["a"]["b"])
    assert view["gate_sha"] == GATE.sha and view["weights"] == {"a": 0.5, "b": 0.5}
    assert view["combined"]["alpha_beta"]["beta"] == pytest.approx(0.55, abs=0.03)
    assert view["combined"]["vol_vs_weighted_sum_of_vols"] < 1.0  # some of the risk diversifies away
    json.dumps(view)


def test_two_rules_that_share_a_bet_show_it_in_the_residual():
    rng = np.random.default_rng(32)
    spy = pd.Series(rng.normal(0.0005, 0.011, len(IDX)), index=IDX)
    theme = rng.normal(0.0, 0.004, len(IDX))  # e.g. both own the same sector
    a = 0.6 * spy + pd.Series(theme + rng.normal(0.0, 0.002, len(IDX)), index=IDX)
    b = 0.6 * spy + pd.Series(theme + rng.normal(0.0, 0.002, len(IDX)), index=IDX)
    view = g2.portfolio_view(GATE, {"a": a, "b": b}, spy)
    assert view["correlation"]["residual"]["a"]["b"] == pytest.approx(0.8, abs=0.04)  # 0.004^2 / (0.004^2 + 0.002^2)


def test_the_combined_book_is_the_weighted_sum_on_shared_sessions():
    a, spy = noisy(1)
    b, _ = noisy(2)
    view = g2.portfolio_view(GATE, {"a": a, "b": b.iloc[100:]}, spy, weights={"a": 0.5, "b": 0.25})
    assert view["sessions"] == len(IDX) - 100 and view["sessions_dropped"] == {"a": 100, "b": 0}
    want = mtm.total_return(0.5 * a.iloc[100:] + 0.25 * b.iloc[100:])
    assert view["combined"]["total_return"] == pytest.approx(want)
    assert set(view["rules"]) == {"a", "b"} and view["rules"]["a"]["sharpe"]["haircut_share"] == 0.5
    with pytest.raises(ValueError, match="at most 1"):
        g2.portfolio_view(GATE, {"a": a, "b": b}, spy, weights={"a": 0.8, "b": 0.5})  # no leverage
    with pytest.raises(ValueError):
        g2.portfolio_view(GATE, {}, spy)
