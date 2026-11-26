"""Gate v3.1, ticket 6.2 (#702, decision 18 section 6): S4's trial variance comes from each trial's ACTIVE-return
Sharpe. Trials that logged only an absolute-return Sharpe are counted in N, flagged, and left out of V."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
import yaml

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_v3 as g3
from kaibot.evaluation import mtm
from kaibot.evaluation import trials as tr
from kaibot.evaluation.trials import Registry

GATE = g3.GateV3.load(ROOT)
IDX = pd.bdate_range("2017-05-01", "2024-12-31")
RNG = np.random.default_rng(16)
SPY = pd.Series(RNG.normal(0.0005, 0.011, len(IDX)), index=IDX)
RULE = "trend_momentum_gbm"  # cross_sectional_momentum


def account(edge=0.03, seed=1, exposure=0.8) -> g3.Account:
    e = pd.Series(exposure, index=IDX)
    own = np.random.default_rng(seed).normal(edge / 252, 0.002, len(IDX))
    return g3.Account(pd.Series(mtm.exposure_matched(SPY, e) + own, index=IDX), SPY, e, universe="point_in_time")


def row(i, absolute, active=None, strategy=RULE) -> dict:
    m = {"daily_sharpe_ann": absolute}
    if active is not None:
        m[tr.ACTIVE_SHARPE] = active
    return {"trial_id": f"t{i}", "strategy": strategy, "n_trials": 1, "metrics": m}


def s4(reg: Registry, acc=None) -> dict:
    a = g3.active_return(GATE, "selection", acc or account(), None)["active"]
    return g3.s4(GATE, RULE, a, reg)


def test_a_two_trial_registry_uses_the_active_sharpes_not_the_absolute_ones():
    """The check of #702: two trials whose absolute Sharpes are far apart (1.6 and 0.2) and whose active Sharpes
    are close (0.30 and 0.34). V is the variance of the active ones."""
    reg = Registry((row(1, 1.6, 0.30), row(2, 0.2, 0.34)), ("t",), 0)
    m = s4(reg)["numbers"]
    want = float(np.var([0.30 / math.sqrt(252), 0.34 / math.sqrt(252)], ddof=1))
    wrong = float(np.var([1.6 / math.sqrt(252), 0.2 / math.sqrt(252)], ddof=1))
    assert m["variance_of_trial_sharpes"] == pytest.approx(want) and m["variance_of_trial_sharpes"] < wrong / 100
    assert m["trial_sharpes_used"] == 2 and m["trial_sharpe_series"] == "active_return"
    assert m["trial_sharpes_absolute_only_flagged"] == 0 and m["configurations"] == 2


def test_old_absolute_only_rows_count_in_n_are_flagged_and_stay_out_of_v():
    reg = Registry((row(1, 1.6), row(2, 0.2)), ("t",), 0)
    m = s4(reg)["numbers"]
    assert m["configurations"] == 2 and m["trial_sharpes_absolute_only_flagged"] == 2
    assert m["variance_of_trial_sharpes"] is None and m["variance_floor_binds"] is True
    assert m["trial_sharpes_used"] == 0
    mixed = Registry((row(1, 1.6), row(2, 0.2, 0.30), row(3, 0.5, 0.36)), ("t",), 0)
    n = s4(mixed)["numbers"]
    assert n["configurations"] == 3 and n["trial_sharpes_used"] == 2 and n["trial_sharpes_absolute_only_flagged"] == 1
    assert reg.absolute_only() == {RULE: 2} and mixed.active_daily_sharpes()[RULE] == pytest.approx(
        [0.30 / math.sqrt(252), 0.36 / math.sqrt(252)])


def test_other_families_neither_feed_v_nor_are_flagged():
    reg = Registry((row(1, 1.0, 0.3), row(2, 1.0, 0.4), row(3, 2.0, strategy="dip_reversal")), ("t",), 0)
    m = s4(reg)["numbers"]
    assert m["trial_sharpes_used"] == 2 and m["trial_sharpes_absolute_only_flagged"] == 0


def test_the_logged_active_sharpe_is_the_one_s4_tests():
    acc = account(0.04, seed=3)
    a = g3.active_return(GATE, "selection", acc, None)["active"]
    want = float(a.mean() / a.std(ddof=1) * math.sqrt(252))
    assert g3.active_sharpe_ann(GATE, RULE, acc) == pytest.approx(want)
    assert g3.active_sharpe_ann(GATE, RULE, g3.Account(acc.net, SPY)) is None  # no exposure: cannot be computed
    # cut to the research window like S4: hold-out sessions do not move it
    longer = pd.bdate_range("2017-05-01", "2026-11-06")
    spy2 = SPY.reindex(longer).fillna(0.0)
    e2 = pd.Series(0.8, index=longer)
    net2 = acc.net.reindex(longer).fillna(0.05)
    assert g3.active_sharpe_ann(GATE, RULE, g3.Account(net2, spy2, e2)) == pytest.approx(want)


def test_the_config_says_active_return_and_cannot_say_otherwise():
    assert GATE.cfg["sleeve"]["s4_deflated_sharpe"]["trial_sharpes"] == "active_return"
    cfg = yaml.safe_load((ROOT / "config" / "gate_v3.yaml").read_text())
    cfg["sleeve"]["s4_deflated_sharpe"]["trial_sharpes"] = "absolute_return"
    with pytest.raises(ValueError, match="trial_sharpes must be active_return"):
        g3.GateV3.from_bytes(yaml.safe_dump(cfg).encode())


def test_gate_v2_still_reads_the_absolute_sharpes():
    reg = Registry((row(1, 1.6, 0.30), row(2, 0.2, 0.34)), ("t",), 0)
    assert reg.daily_sharpes()[RULE] == pytest.approx([1.6 / math.sqrt(252), 0.2 / math.sqrt(252)])


def test_the_writers_log_the_active_sharpe():
    for script in ("trend_momentum_gbm.py", "trend_momentum_band.py", "index_core_book.py"):
        assert "trials_mod.ACTIVE_SHARPE: g3.active_sharpe_ann(" in (ROOT / "scripts" / script).read_text(), script
    assert '"daily_sharpe_series": "absolute_return"' in (ROOT / "src" / "kaibot" / "replay" /
                                                          "config_replay.py").read_text()
