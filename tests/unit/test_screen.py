"""M11 ticket 2.11 (#462): the tier-one screen (docs/research/tier_one_screen/design.md, config/screen.yaml)."""

from __future__ import annotations

import json
import sys

import numpy as np
import pytest
import yaml

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_v3 as g3
from kaibot.evaluation import screen as sc
from kaibot.evaluation.trials import Registry

CFG = sc.load_config()
B = sc.Budget.from_config(CFG)


def test_the_config_holds_the_design():
    b = CFG["budget"]
    assert (b["mfdr"], b["initial_wealth"], b["spend_fraction"], b["max_level"], b["payout"], b["floor"]) == (
        0.10, 0.09, 0.5, 0.10, 0.10, 0.005)
    s = CFG["statistic"]
    assert (s["horizon_sessions"], s["charge_bp_a_side"], s["top_fraction"]) == (21, 10, 0.20)
    text = (ROOT / "docs" / "research" / "tier_one_screen" / "design.md").read_text()
    assert "q_1 = 0.045" in text and "do not count in S4" in text
    assert "`pass`), credited when that verdict is merged. An `evidence_based` verdict earns no payout" in text
    assert "`screen:<batch id>:<candidate>`" in text


def test_the_batch_levels_halve_without_a_confirmed_survivor_and_the_budget_runs_out():
    w, levels = B.initial_wealth, []
    while B.level(w) > 0:
        q = B.level(w)
        levels.append(q)
        w -= B.cost(q)
    assert levels[0] == pytest.approx(0.045) and levels[1] == pytest.approx(0.0214, abs=1e-4)
    assert all(b < a for a, b in zip(levels, levels[1:], strict=False)) and 3 < len(levels) < 12
    assert B.level(0.004) == 0.0 and B.level(1.0) == 0.10  # the floor and the cap


def test_the_committed_ledger_replays_and_a_wrong_line_is_refused(tmp_path):
    led = sc.read_ledger(ROOT / CFG["ledger"], B)
    assert led["wealth"] == pytest.approx(0.09) and led["batches"] == [] and led["next_level"] == pytest.approx(0.045)
    good = (ROOT / CFG["ledger"]).read_text() + sc.ledger_line("2026-12-01", "b1", 0.09, 0.045, 1, B) + "\n" \
        + sc.payout_line("2027-01-10", "b1", "rule_b1", "pass", 0.042880, B) + "\n"
    f = tmp_path / "l.md"
    f.write_text(good)
    r = sc.read_ledger(f, B)
    assert r["batches"] == ["b1"] and r["wealth"] == pytest.approx(0.142880, abs=1e-6)
    f.write_text(good.replace("| 0.045000 |", "| 0.060000 |"))
    with pytest.raises(ValueError, match="budget allowed"):
        sc.read_ledger(f, B)
    f.write_text(good.replace("0.142880 |\n", "0.2 |\n"))
    with pytest.raises(ValueError, match="should be"):
        sc.read_ledger(f, B)


def test_the_budget_is_refilled_only_by_a_survivor_the_engine_confirmed(tmp_path):
    """QA-Ops (#574), design section 4: 0.10 back only for a survivor of an earlier batch whose engine verdict is
    `pass`; never for `evidence_based`, never without a batch or beyond its survivors, never twice for one rule."""
    head = (ROOT / CFG["ledger"]).read_text()
    b1 = sc.ledger_line("2026-12-01", "b1", 0.09, 0.045, 1, B) + "\n"
    w = 0.09 - B.cost(0.045)
    f = tmp_path / "l.md"

    def refused(text, match):
        f.write_text(head + text)
        with pytest.raises(ValueError, match=match):
            sc.read_ledger(f, B)

    f.write_text(head + b1 + sc.payout_line("2027-01-10", "b1", "rule_b1", "pass", w, B) + "\n")
    assert sc.read_ledger(f, B)["wealth"] == pytest.approx(w + 0.10, abs=1e-6)
    with pytest.raises(ValueError, match="only an engine"):
        sc.payout_line("2027-01-10", "b1", "rule_b1", "evidence_based", w, B)
    refused(b1 + f"| 2027-01-10 | b1 | payout | {w:.6f} | evidence_based | 0.100000 | rule_b1 | {w + 0.1:.6f} |\n",
            "only an engine")
    refused(b1 + f"| 2027-01-10 | b1 | payout | {w:.6f} | - | 0.100000 | - | {w + 0.1:.6f} |\n", "only an engine")
    refused(sc.payout_line("2027-01-10", "b9", "rule_b9", "pass", 0.09, B) + "\n", "no earlier batch")
    two = sc.payout_line("2027-01-10", "b1", "r1", "pass", w, B) + "\n" \
        + sc.payout_line("2027-01-11", "b1", "r2", "pass", w + 0.1, B) + "\n"
    refused(b1 + two, "had 1 survivors and pays 2")
    none = sc.ledger_line("2026-12-01", "b1", 0.09, 0.045, 0, B) + "\n"
    refused(none + sc.payout_line("2027-01-10", "b1", "r1", "pass", w, B) + "\n", "had 0 survivors")
    refused(b1 + f"| 2027-01-10 | b1 | payout | {w:.6f} | pass | 0.200000 | r1 | {w + 0.2:.6f} |\n", "amount")
    refused(b1 + b1.replace("0.090000", f"{w:.6f}"), "budget allowed|twice")
    refused("| 2026-12-02 | - | start | 0.09 | - | 0.09 | - | 0.18 |\n", "only the first line")


def test_the_batch_runs_benjamini_hochberg_at_the_budgets_level_not_a_fixed_one(tmp_path, monkeypatch):
    """QA-Ops (#574): the script passes the ledger's q_b (0.045 for the first batch) to Benjamini-Hochberg."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import screen_batch

    seen = []
    real = sc.screen_batch
    monkeypatch.setattr(screen_batch.sc, "screen_batch", lambda p, level: seen.append(level) or real(p, level))
    f = tmp_path / "b.yaml"
    f.write_text(yaml.safe_dump({"id": "lvl", "date": "2026-11-22", "family": "cross_sectional_momentum",
                                 "candidates": [{"name": "screen_mom_6_1", "signal": "momentum",
                                                 "params": {"back": 126, "skip": 21}}]}))
    rep = screen_batch.main(["--batch", str(f), "--fixture", "--data", str(tmp_path), "--out", str(tmp_path / "o"),
                             "--trials", "", "--committed", "no"])
    assert seen == [pytest.approx(0.045)] and rep["level"] == pytest.approx(0.045)


def test_the_rank_ic_by_hand_and_the_charge_on_top_quintile_entrants():
    score = np.array([[1.0, 2.0, 3.0, 4.0, 5.0]])
    fwd = np.array([[0.01, 0.02, 0.03, 0.04, 0.05]])
    assert sc.rank_ics(score, fwd, min_stocks=5, charge=0.0)[0] == pytest.approx(1.0)
    # the best-scored stock enters the top quintile and pays 2%: its net return drops below every other one
    ic = sc.rank_ics(score, fwd, min_stocks=5, charge=0.06)[0]
    assert ic == pytest.approx(np.corrcoef([1, 2, 3, 4, 5], [2, 3, 4, 5, 1])[0, 1])
    two = sc.rank_ics(np.vstack([score, score]), np.vstack([fwd, fwd]), min_stocks=5, charge=0.06)
    assert two[1] == pytest.approx(1.0)  # held again at the second decision: no charge
    assert np.isnan(sc.rank_ics(score, fwd, min_stocks=6)[0])


def batch_of(rng, n_cand=8, months=90, stocks=120, planted=None):
    p = {}
    for i in range(n_cand):
        s = rng.normal(size=(months, stocks))
        f = rng.normal(0, 0.05, size=(months, stocks))
        if planted == i:
            f = f + 0.02 * s
        p[f"c{i}"] = sc.statistic(sc.rank_ics(s, f, charge=0.0))["p"]
    return p


def test_no_signal_batches_rarely_survive_and_a_planted_signal_does():
    rng = np.random.default_rng(462)
    false = sum(any(v["survives"] for v in sc.screen_batch(batch_of(rng), 0.045).values()) for _ in range(60))
    assert false / 60 <= 0.10
    r = sc.screen_batch(batch_of(rng, planted=3), 0.045)
    assert r["c3"]["survives"] and sum(v["survives"] for v in r.values()) <= 2
    assert not any(v["survives"] for v in sc.screen_batch(batch_of(rng, planted=3), 0.0).values())


def test_signals_read_only_closes_at_or_before_the_decision():
    rng = np.random.default_rng(1)
    c = 100 * np.cumprod(1 + rng.normal(0, 0.01, size=(400, 10)), axis=0)
    ok = np.ones_like(c, dtype=bool)
    rows = np.array([300, 330])
    a = sc.candidate_scores(c, ok, rows, "momentum", {"back": 252, "skip": 21})
    later = c.copy()
    later[301:] *= 3.0  # the future changes; the scores at row 300 must not
    b = sc.candidate_scores(later, ok, rows[:1], "momentum", {"back": 252, "skip": 21})
    assert np.allclose(a[0], b[0])
    with pytest.raises(ValueError, match="unknown signal"):
        sc.candidate_scores(c, ok, rows, "astrology", {})


def test_a_screen_candidate_does_not_change_s4s_count():
    gate = g3.GateV3.load(ROOT)
    rule = {"trial_id": "r", "strategy": "trend_momentum", "n_trials": 1, "metrics": {"daily_sharpe_ann": 0.5}}
    # named as the design names it, even for a candidate called like a listed rule (design section 6)
    cand = {"trial_id": "s", "strategy": "screen:b1:trend_momentum", "variant": "screen",
            "family": "cross_sectional_momentum", "n_trials": 1}
    import pandas as pd

    a = pd.Series(np.random.default_rng(2).normal(0.0002, 0.003, 1900),
                  index=pd.bdate_range("2017-05-01", periods=1900))
    one = g3.s4(gate, "trend_momentum", a, Registry((rule,), ("t",), 0))
    two = g3.s4(gate, "trend_momentum", a, Registry((rule, cand), ("t",), 0))
    assert one["numbers"]["configurations"] == two["numbers"]["configurations"] == 1
    assert two["numbers"]["whole_registry_configurations"] == 2  # still a trial in the registry


def test_the_batch_script_on_the_fixture(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import screen_batch

    f = tmp_path / "b.yaml"
    f.write_text(yaml.safe_dump({"id": "fixture_test", "date": "2026-11-22", "family": "cross_sectional_momentum",
                                 "candidates": [{"name": "screen_mom_6_1", "signal": "momentum",
                                                 "params": {"back": 126, "skip": 21}},
                                                {"name": "screen_rev_5", "signal": "reversal",
                                                 "params": {"back": 5}}]}))
    trials = tmp_path / "t.jsonl"
    with pytest.raises(SystemExit, match="not committed|is not"):
        screen_batch.main(["--batch", str(f), "--fixture", "--data", str(tmp_path), "--trials", ""])
    rep = screen_batch.main(["--batch", str(f), "--fixture", "--data", str(tmp_path), "--out", str(tmp_path / "o"),
                             "--trials", str(trials), "--committed", "no"])
    assert rep["level"] == pytest.approx(0.045) and set(rep["candidates"]) == {"screen_mom_6_1", "screen_rev_5"}
    rows = [json.loads(x) for x in trials.read_text().splitlines()]
    assert {r["variant"] for r in rows} == {"screen"} and all(r["n_trials"] == 1 for r in rows)
    assert {r["strategy"] for r in rows} == {"screen:fixture_test:screen_mom_6_1", "screen:fixture_test:screen_rev_5"}
    assert all(len(r["config_sha256"]) == 64 and r["metrics"]["level"] == pytest.approx(0.045) for r in rows)
    assert "| fixture_test | batch | 0.090000 | 0.045000 |" in rep["ledger_line"]
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"id": "x", "date": "2026-11-22", "family": "cross_sectional_momentum",
                                   "candidates": [{"name": "trend_momentum", "signal": "momentum"}]}))
    with pytest.raises(SystemExit, match="must not be a rule"):
        screen_batch.main(["--batch", str(bad), "--fixture", "--data", str(tmp_path), "--trials", ""])


def test_a_screened_proposal_counts_zero_trials(tmp_path):
    """Product, 22 Nov 2026: a candidate from the counted proposer (#575) was counted at intake."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import screen_batch

    f = tmp_path / "b.yaml"
    f.write_text(yaml.safe_dump({"id": "fixture_prop", "date": "2026-11-22", "family": "cross_sectional_momentum",
                                 "candidates": [{"name": "prop_1", "signal": "momentum", "proposal_id": "p0001",
                                                 "params": {"back": 126, "skip": 21}},
                                                {"name": "own_1", "signal": "reversal", "params": {"back": 5}}]}))
    trials = tmp_path / "t.jsonl"
    screen_batch.main(["--batch", str(f), "--fixture", "--data", str(tmp_path), "--out", str(tmp_path / "o"),
                       "--trials", str(trials), "--committed", "no"])
    rows = {r["candidate"]: r for r in map(json.loads, trials.read_text().splitlines())}
    assert rows["prop_1"]["n_trials"] == 0 and rows["prop_1"]["proposal_id"] == "p0001"
    assert rows["own_1"]["n_trials"] == 1
