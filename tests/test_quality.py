"""Point-in-time quality score: no look-ahead, sensible ranking, original (not restated) figures."""

import json

import numpy as np
import pandas as pd

from kaibot.replay.quality import company_metrics, quality_panel


def _fact(start, end, filed, val, form="10-K"):
    return {"start": start, "end": end, "filed": filed, "val": val, "form": form, "fp": "FY"}


def _company(path, ni, rev, assets, ocf, filed2="2025-02-15"):
    def dur(v1, v2):
        return [_fact("2023-01-01", "2023-12-31", "2024-02-15", v1), _fact("2024-01-01", "2024-12-31", filed2, v2)]

    def inst(v1, v2):
        return [{"end": "2023-12-31", "filed": "2024-02-15", "val": v1, "form": "10-K", "fp": "FY"},
                {"end": "2024-12-31", "filed": filed2, "val": v2, "form": "10-K", "fp": "FY"}]

    facts = {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": dur(*ni)}},
        "Revenues": {"units": {"USD": dur(*rev)}},
        "Assets": {"units": {"USD": inst(*assets)}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": dur(*ocf)}},
    }}}
    path.write_text(json.dumps(facts))


def test_metrics_and_ranking(tmp_path):
    _company(tmp_path / "GOOD.json", ni=(10, 20), rev=(100, 130), assets=(100, 100), ocf=(12, 24))
    _company(tmp_path / "BAD.json", ni=(1, -5), rev=(100, 90), assets=(100, 100), ocf=(1, -2))
    m = company_metrics(tmp_path / "GOOD.json")
    last = m.iloc[-1]
    assert abs(last["roa"] - 0.2) < 1e-9 and abs(last["growth"] - 0.3) < 1e-9 and abs(last["cashq"] - 1.2) < 1e-9
    q = quality_panel(tmp_path, ["GOOD", "BAD"], np.array(["2025-03-01"], dtype="datetime64[D]"))
    assert q.loc["2025-03-01", "GOOD"] > q.loc["2025-03-01", "BAD"]


def test_no_lookahead_on_filing_day(tmp_path):
    # FY2024 10-K filed 2025-02-15: BAD turns from best to worst, but only from the day AFTER filing.
    _company(tmp_path / "A.json", ni=(5, 5), rev=(100, 100), assets=(100, 100), ocf=(5, 5))
    _company(tmp_path / "B.json", ni=(9, -9), rev=(100, 50), assets=(100, 100), ocf=(9, -9))
    dates = np.array(["2025-02-14", "2025-02-15", "2025-02-16"], dtype="datetime64[D]")
    q = quality_panel(tmp_path, ["A", "B"], dates)
    assert q.loc["2025-02-15", "B"] > q.loc["2025-02-15", "A"]  # filing day itself: still old data
    assert q.loc["2025-02-16", "B"] < q.loc["2025-02-16", "A"]


def test_restatement_ignored(tmp_path):
    _company(tmp_path / "R.json", ni=(10, 20), rev=(100, 130), assets=(100, 100), ocf=(12, 24))
    facts = json.loads((tmp_path / "R.json").read_text())
    # a later 10-K restates FY2024 net income to 99; the original (20) must be kept
    ni_rows = facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]
    ni_rows.append(_fact("2024-01-01", "2024-12-31", "2026-02-15", 99))
    (tmp_path / "R.json").write_text(json.dumps(facts))
    m = company_metrics(tmp_path / "R.json")
    assert m.loc[m["end"] == pd.Timestamp("2024-12-31"), "roa"].iloc[0] == 0.2
