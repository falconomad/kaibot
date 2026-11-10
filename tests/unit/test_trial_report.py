import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from trial_report import report  # noqa: E402


def test_trial_report_pbo_and_dsr(tmp_path):
    rng = np.random.default_rng(5)
    dates = pd.bdate_range("2025-02-03", periods=300)
    (tmp_path / "trials").mkdir()
    rows = []
    for k in range(4):
        tid = f"balanced_t{k}"
        pd.DataFrame({"date": dates.astype(str), "ret": rng.normal(0, 0.002, len(dates))}).to_csv(
            tmp_path / "trials" / f"{tid}.csv", index=False)
        rows.append({"trial_id": tid, "variant": "A", "label": "design"})
    rows.append({"ts": "old", "params": "pre-series trial"})  # counted for the DSR, no series
    (tmp_path / "trials.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    rep = report(tmp_path, blocks=16)
    assert rep["n_trials_logged"] == 5 and rep["n_series"] == 4
    (w,) = rep["windows"]
    assert w["blocks"] == 16 and w["splits"] == 12870 and 0 <= w["pbo"] <= 1
    assert all(0 <= t["dsr"] <= 1 for t in rep["trials"])
