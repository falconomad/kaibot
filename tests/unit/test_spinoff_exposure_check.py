"""scripts/spinoff_exposure_check.py (#262): which held positions crossed a spin-off, and the missing credit."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("spinoff_exposure_check", ROOT / "scripts" / "spinoff_exposure_check.py")
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


def test_spin_steps_keep_spin_offs_and_large_steps_but_not_reverse_splits_or_duplicates():
    steps = pd.DataFrame({"key": ["CTVA", "DD", "X", "Y", "Z"], "date": ["2026-10-01"] * 5,
                          "class": ["spin_off", "unexplained", "unexplained", "dropped_duplicate", "spin_off"],
                          "alpaca_factor": [0.15, 0.42, 0.97, 0.5, 1.4], "table_factor": [1.0] * 5})
    assert list(S.spin_steps(steps)["key"]) == ["CTVA", "DD"]


def test_a_former_member_matches_by_its_key_and_a_current_one_by_symbol():
    t = pd.DataFrame({"symbol": ["DD@2019-06-03", "DD", "CTVA"], "qty": [10, 5, 7],
                      "date": pd.to_datetime(["2019-01-02", "2025-10-01", "2026-09-01"]),
                      "exit_date": pd.to_datetime(["2019-05-01", "2025-12-01", "2026-09-30"])})
    ev = S.spin_steps(pd.DataFrame({"key": ["DD@2019-06-03", "DD", "CTVA"],
                                    "date": ["2019-04-01", "2025-11-03", "2026-10-01"],
                                    "class": ["spin_off"] * 3, "alpaca_factor": [0.5, 0.42, 0.15],
                                    "table_factor": [1.0] * 3}))
    res = S.check_run(t, 1000.0, ev, lambda key, d: 100.0)
    assert res["hits"] == 2  # CTVA was sold the day before its spin-off
    assert res["missing_credit"] == round(10 * 100 * 0.5 + 5 * 100 * 0.58)
    assert res["pct_of_mean_equity"] == round(100 * res["missing_credit"] / 1000.0, 2)


def test_a_step_without_a_close_before_is_reported_not_added():
    t = pd.DataFrame({"symbol": ["A"], "qty": [1], "date": pd.to_datetime(["2024-01-02"]),
                      "exit_date": pd.to_datetime(["2024-03-01"])})
    ev = S.spin_steps(pd.DataFrame({"key": ["A"], "date": ["2024-02-01"], "class": ["spin_off"],
                                    "alpaca_factor": [0.8], "table_factor": [1.0]}))
    res = S.check_run(t, float("nan"), ev, lambda key, d: np.nan)
    assert res["hits"] == 0 and res["missing_credit"] == 0 and res["no_close"] == "A 2024-02-01"
    assert res["pct_of_mean_equity"] is None
