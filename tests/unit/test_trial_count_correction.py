"""#266, trend_momentum.md Amendment 7 / mom_12_1_band.md Amendment 6 item 4 (QA-Ops found the code counted 1): the
first runs on spin-off-corrected inputs repeat configurations an earlier finished report counted, so they log
`n_trials` 0 even though their events differ; later reruns on the corrected data follow the #203 rule (same label
and same events: 0). Written before the code."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import momentum_core as core_script  # noqa: E402

LABEL = "trend_momentum|base|1x|100|hold"


def prior(corrections=None, events="old"):
    rep = {"rows": [{"label": LABEL, "events_sha256": events}]}
    if corrections is not None:
        rep["data_corrections"] = corrections
    return rep


def test_the_report_records_the_data_correction():
    assert "spin_offs" in core_script.DATA_CORRECTIONS


def test_a_first_run_on_corrected_data_repeats_a_counted_configuration():
    n = core_script.n_trials_of(LABEL, "new", "base", 1.0, prior())  # the prior report predates the correction
    assert n == 0


def test_a_new_configuration_still_counts_one():
    assert core_script.n_trials_of("trend_momentum~reg60|base|1x|100|hold", "x", "base", 1.0, prior()) == 1
    assert core_script.n_trials_of(LABEL, "new", "base", 1.0, None) == 1  # no earlier report at all


def test_after_the_correction_the_203_rule_applies_again():
    p = prior(corrections=list(core_script.DATA_CORRECTIONS), events="same")
    assert core_script.n_trials_of(LABEL, "same", "base", 1.0, p) == 0
    assert core_script.n_trials_of(LABEL, "changed", "base", 1.0, p) == 1


def test_stress_reports_count_zero_as_before():
    assert core_script.n_trials_of(LABEL, "new", "stress", 1.0, None) == 0
    assert core_script.n_trials_of(LABEL, "new", "base", 2.0, None) == 0
