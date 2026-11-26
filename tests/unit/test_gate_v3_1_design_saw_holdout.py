"""Gate v3.1, ticket 6.5 (#705, decision 18 section 9): a pre-registration's `design_saw_holdout` field. True, or
not declared, forces the final check on the hold-out to be labelled a consistency check, never "out-of-sample"."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
import yaml

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_v3 as g3
from kaibot.evaluation import prereg as pr

GATE = g3.GateV3.load(ROOT)
IDX = pd.bdate_range("2017-05-01", "2026-11-06")
A = pd.Series(np.random.default_rng(5).normal(0.0001, 0.002, len(IDX)), index=IDX)
RESEARCH, HOLD = A[A.index <= "2024-12-31"], A[A.index > "2024-12-31"]
AFTER = datetime(2027, 1, 5, tzinfo=UTC)  # first run after the 21 Nov 2026 freeze


def check(**kw) -> dict:
    return g3.holdout_check(GATE, research=RESEARCH, holdout=HOLD, **{"first_run_at": AFTER, **kw})


def test_only_a_declared_false_after_the_freeze_is_out_of_sample():
    assert check(design_saw_holdout=False)["label"] == "out-of-sample check"
    assert check(design_saw_holdout=False)["clean"] is True
    saw = check(design_saw_holdout=True)
    assert saw["clean"] is False and saw["label"].startswith("consistency check: the design saw the hold-out")
    unknown = check()
    assert unknown["clean"] is False and "does not declare design_saw_holdout" in unknown["label"]
    early = check(design_saw_holdout=False, first_run_at=datetime(2026, 11, 13, tzinfo=UTC))
    assert early["clean"] is False and "first run before the hold-out was frozen" in early["label"]


def test_index_core_is_labelled_a_consistency_check():
    """The verification of #705: index_core's first run (24 Nov) came after the freeze, but its design saw the
    hold-out, which its Amendment 5 declares."""
    for book in ("index_core", "index_core_trend_momentum"):
        p = pr.load(book, ROOT)
        assert p.design_saw_holdout is True
        out = check(prereg=p, first_run_at=datetime(2026, 11, 24, 12, tzinfo=UTC))
        assert out["clean"] is False and out["label"].startswith("consistency check: the design saw the hold-out")
        assert out["design_saw_holdout"] is True


@pytest.mark.parametrize("value, want", [("true", True), ("false (nothing read 2025 on)", False),
                                         ("`yes`", True), ("no", False)])
def test_the_field_is_read_and_the_last_one_wins(tmp_path, value, want):
    d = tmp_path / "docs" / "prereg"
    d.mkdir(parents=True)
    src = (ROOT / "docs" / "prereg" / "index_core.md").read_text()
    (d / "index_core.md").write_text(src + f"\n## Amendment 9 (test)\n\n- **design_saw_holdout:** {value}\n")
    assert pr.load("index_core", tmp_path, committed=False).design_saw_holdout is want


def test_any_other_value_counts_as_undeclared(tmp_path):
    d = tmp_path / "docs" / "prereg"
    d.mkdir(parents=True)
    src = (ROOT / "docs" / "prereg" / "index_core.md").read_text()
    (d / "index_core.md").write_text(src + "\n## Amendment 9 (test)\n\n- **design_saw_holdout:** maybe\n")
    p = pr.load("index_core", tmp_path, committed=False)
    assert p.design_saw_holdout is None
    assert check(prereg=p)["clean"] is False  # undeclared: the safe reading, a consistency check


def test_the_template_asks_for_it_and_the_amendments_only_append():
    assert "- **design_saw_holdout:** <<true or false" in (ROOT / "docs" / "prereg" / "TEMPLATE.md").read_text()
    for book in ("index_core", "index_core_trend_momentum"):
        p = pr.load(book, ROOT)
        assert any(a for _, _, a in p.versions[:-1])  # today's file only amends earlier committed versions
        assert p.summary()["design_saw_holdout"] is True


def test_the_config_names_both_conditions():
    assert sorted(GATE.cfg["holdout_check"]["clean_needs"]) == ["design_saw_holdout_false", "first_run_after_freeze"]
    cfg = yaml.safe_load((ROOT / "config" / "gate_v3.yaml").read_text())
    cfg["holdout_check"]["clean_needs"] = ["first_run_after_freeze"]
    with pytest.raises(ValueError, match="clean_needs"):
        g3.GateV3.from_bytes(yaml.safe_dump(cfg).encode())
