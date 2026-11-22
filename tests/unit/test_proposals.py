"""M12 ticket 3.10 (#472): the counted proposer. The registry refuses unregistered runs; proposals are formulas in
a restricted language, three machine proposals an ISO week, duplicates counted; the hurdle is published first."""

from __future__ import annotations

import json
import sys
from datetime import date

import numpy as np
import pytest

from kaibot.engine.config import ROOT
from kaibot.evaluation import proposals as pr
from kaibot.evaluation import trials


def test_the_hurdle_is_published_before_the_first_proposal():
    text = (ROOT / "docs" / "research" / "proposer" / "hurdle.md").read_text()
    for phrase in ("before any proposal exists", "at most 3 per ISO week", "counted again", "Benjamini-Hochberg",
                   "pre-registration"):
        assert phrase in text, phrase
    assert (ROOT / "docs" / "research" / "proposer" / "proposals.jsonl").read_text() == ""  # no proposal yet


@pytest.mark.parametrize("text", ["ret(252, 21)", "rank(ret(252,21)) - rank(vol(63))", "-sma_gap(100) * 2",
                                  "zscore(high_gap(252)) + sign(ret(5))"])
def test_allowed_formulas_parse(text):
    assert pr.parse(text).size() <= pr.MAX_NODES


@pytest.mark.parametrize("text, why", [
    ("__import__('os')", "cannot read|unknown"), ("exp(ret(5))", "unknown function"), ("ret(600)", "n <= 504"),
    ("ret(5, 5)", "skip < n"), ("vol(2)", "between 5"), ("3 + 4", "reads no price"), ("ret(20.5)", "whole number"),
    ("rank(" * 7 + "ret(5)" + ")" * 7 + "+ ret(5) + ret(6) + ret(7) + ret(8)", "nodes"), ("ret(5) 7", "unexpected"),
    ("", "non-empty"), ("ret(5) + 99", "between -10 and 10"),
])
def test_anything_else_is_refused_with_its_reason(text, why):
    with pytest.raises(pr.FormulaError, match=why):
        pr.parse(text)


def test_the_canonical_form_recognises_a_reordered_duplicate():
    a = pr.canonical("rank(ret(252, 21)) + rank(vol(63))")
    b = pr.canonical("rank(vol(63)) + rank(ret(252,21))")
    assert a == b and pr.canonical("rank(vol(63)) - rank(ret(252, 21))") != a


def test_a_formula_reads_only_closes_at_or_before_its_row():
    rng = np.random.default_rng(3)
    c = 100 * np.cumprod(1 + rng.normal(0, 0.01, size=(600, 20)), axis=0)
    sig = pr.as_signal("rank(ret(252, 21)) - zscore(vol(63)) + sma_gap(50)")
    a = sig(c, 400)
    later = c.copy()
    later[401:] *= 5
    assert np.allclose(a, sig(later, 400), equal_nan=True)
    assert np.isnan(pr.as_signal("ret(300)")(c, 100)).all()  # not enough history: no score


def test_machine_proposals_are_capped_at_three_a_week_and_duplicates_count(tmp_path):
    led = tmp_path / "p.jsonl"
    d = date(2026, 11, 23)  # a Monday
    for f in ("ret(252, 21)", "vol(63)", "ret(21, 0) + vol(20)"):
        pr.intake(led, f, proposer="model:x-2026-11", family="cross_sectional_momentum", on=d)
    with pytest.raises(pr.FormulaError, match="3 machine proposals already"):
        pr.intake(led, "sma_gap(50)", proposer="model:x-2026-11", family="cross_sectional_momentum", on=d)
    human = pr.intake(led, "ret(21,0)+vol(20)", proposer="human:quant", family="cross_sectional_momentum", on=d)
    assert human["duplicate_of"] == "p0003"  # the same formula, reordered: a duplicate, accepted and counted
    nxt = pr.intake(led, "vol(63)", proposer="model:x-2026-11", family="cross_sectional_momentum",
                    on=date(2026, 11, 30))
    assert nxt["duplicate_of"] == "p0002" and pr.counted(pr.read_proposals(led)) == 5
    with pytest.raises(pr.FormulaError):
        pr.intake(led, "exp(ret(5))", proposer="human:quant", family="cross_sectional_momentum", on=d)
    assert len(pr.read_proposals(led)) == 5  # a refused formula writes nothing


def test_the_registry_refuses_an_unregistered_run(tmp_path):
    f = tmp_path / "t.jsonl"
    with pytest.raises(ValueError, match="unregistered run"):
        trials.append(f, {"strategy": "my_idea", "n_trials": 1})
    for ok in ({"strategy": "x", "prereg_sha256": "ab"}, {"strategy": "s", "variant": "screen", "batch": "b1"},
               {"strategy": "p", "variant": "proposal", "proposal_id": "p0001"}):
        assert trials.registration(ok) in trials.REGISTRATIONS
        trials.append(f, ok)
    assert trials.registration({"strategy": "s", "variant": "screen"}) is None  # a screen row needs its batch
    with pytest.raises(ValueError, match="not a committed version"):
        trials.append(f, {"strategy": "trend_momentum", "prereg_sha256": "00" * 32}, verify_prereg=True)
    from kaibot.evaluation import prereg

    sha = prereg.load("trend_momentum").sha256
    assert trials.append(f, {"strategy": "trend_momentum", "prereg_sha256": sha}, verify_prereg=True)


def test_the_intake_script_writes_the_ledger_and_a_trial_row(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import propose

    led, reg = tmp_path / "p.jsonl", tmp_path / "t.jsonl"
    row = propose.main(["--formula", "rank(ret(126, 21))", "--family", "cross_sectional_momentum", "--proposer",
                        "model:pinned-1", "--ledger", str(led), "--trials", str(reg), "--date", "2026-11-23"])
    t = json.loads(reg.read_text())
    assert t["variant"] == "proposal" and t["proposal_id"] == row["id"] and t["n_trials"] == 1
    with pytest.raises(SystemExit, match="refused"):
        propose.main(["--formula", "open('x')", "--family", "cross_sectional_momentum", "--proposer", "human:me",
                      "--ledger", str(led), "--trials", ""])
    with pytest.raises(SystemExit, match="not in config"):
        propose.main(["--formula", "ret(5)", "--family", "astrology", "--proposer", "human:me", "--ledger", str(led),
                      "--trials", ""])
