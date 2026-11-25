"""Issue #677: the walk-forward, one-shot, spy_engine and spy_cost_rerun daily files carry `exposure` (the share of
equity invested at the close), so gate v3's re-score (`scripts/gate_v3_rescore.py`) can score their future runs.

Each writer's own daily-file code builds a fixture over the research window, in the layout that script writes; the
re-score must then score it, not skip it with "the daily file has no exposure column". Nothing is run: the files
are synthetic. The end-to-end checks on the real writers are beside their own tests (test_walkforward.py,
test_oneshots.py, test_spy_engine.py and test_index_cost_bucket.py)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from kaibot.engine.config import ROOT
from kaibot.evaluation import results

sys.path.insert(0, str(ROOT / "scripts"))
import gate_v3_rescore  # noqa: E402
import oneshots  # noqa: E402
import spy_engine  # noqa: E402
import walk_forward  # noqa: E402

IDX = pd.bdate_range("2017-05-01", "2024-12-31")
SPY = np.random.default_rng(677).normal(0.0004, 0.011, len(IDX))
AS_OF = datetime(2026, 11, 25, tzinfo=UTC)


def _rets(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).normal(0.0001, 0.002, len(IDX))


def _equity_rows(seed: int) -> list[dict]:
    """Session rows as the books write them: start, end and pnl in dollars."""
    end = 100_000.0 * np.cumprod(1 + _rets(seed))
    start = np.r_[100_000.0, end[:-1]]
    return [{"date": d.date(), "start": s, "end": e, "pnl": e - s} for d, s, e in zip(IDX, start, end, strict=True)]


def walkforward_report(folder: Path, drop_exposure: bool = False) -> Path:
    """scripts/walk_forward.py: `files.tuned` / `files.default`, the daily file of each from `daily_frame`."""
    tag = "wf_A_exp-control_20261126T100000"
    files = {}
    for k, which in enumerate(("tuned", "default")):
        rows = [{**r, "trades": 1, "halted": False} for r in _equity_rows(10 + k)]  # replay/sim.py's row
        folds = [SimpleNamespace(daily=rows[:900]), SimpleNamespace(daily=rows[900:])]
        d = walk_forward.daily_frame(folds)
        if drop_exposure:
            d = d.drop(columns="exposure")
        files[which] = {"trades": f"trades_balanced_{tag}_{which}.csv", "daily": f"daily_balanced_{tag}_{which}.csv"}
        d.to_csv(folder / files[which]["daily"], index=False)
    f = folder / f"walkforward_balanced_{tag}.json"
    f.write_text(json.dumps({"run_at": "2026-11-26T10:00:00+00:00", "variant": "A", "experiment": "control",
                             "universe": "u.yaml", "files": files, "summary": {}}))
    return f


def oneshots_report(folder: Path) -> Path:
    """scripts/oneshots.py, an intraday rule: `variants.<name>.account`, daily files from `intraday_daily`."""
    tag = "oneshots_orb_stocks_in_play_20261126T100000"
    (folder / tag).mkdir()
    variants = {}
    for k, name in enumerate(("base", "top10")):
        label = "orb_stocks_in_play" if name == "base" else f"orb_stocks_in_play~{name}"
        oneshots.intraday_daily(IDX.to_numpy().astype("datetime64[D]"), _rets(20 + k)).to_csv(
            folder / tag / f"daily_{label}.csv", index=False)
        variants[name] = {"trades": {"trades": 900}, "account": {"cagr_pct": 1.0, "sharpe": 0.5}}
    f = folder / f"{tag}.json"
    f.write_text(json.dumps({"set": "oneshots", "kind": "intraday", "rule": "orb_stocks_in_play",
                             "run_at": "2026-11-26T10:00:00+00:00", "universe_mode": "today",
                             "variants": variants}))
    return f


class _Broker:
    def __init__(self, equity: float, cash: float):
        self.a = {"equity": str(equity), "cash": str(cash)}

    def account(self) -> dict:
        return self.a


def engine_rows(seed: int) -> list[dict]:
    """spy_engine.engine_run's session row, its exposure from `_exposure` on the broker after the session."""
    return [{**r, "cost": 1.0, "invested": True, "exposure": spy_engine._exposure(_Broker(r["end"], r["end"])),
             "traded": 10_000.0} for r in _equity_rows(seed)]


def engine_report(folder: Path, set_: str, scenarios: tuple[str, ...]) -> Path:
    """scripts/spy_engine.py and scripts/spy_cost_rerun.py: `rows[]` with `files.daily`."""
    tag = f"{set_}_balanced_20261126T100000"
    (folder / tag).mkdir()
    rows = []
    for k, sc in enumerate(scenarios):
        rel = f"{tag}/daily_spy_noise_band_{sc}.csv"
        pd.DataFrame(engine_rows(30 + k)).to_csv(folder / rel, index=False)
        rows.append({"label": f"spy_noise_band|{sc}", "spec": "spy_noise_band", "cost": sc, "trades": 500,
                     "files": {"daily": rel}})
    if set_ == "spy_engine":  # the one SPY series of the folder: the others take it from the pool
        pd.DataFrame({"date": IDX, "ret": SPY, "ret_total": SPY}).to_csv(folder / tag / "spy_daily.csv",
                                                                       index=False)
    f = folder / f"{tag}.json"
    f.write_text(json.dumps({"set": set_, "rule": "spy_noise_band", "run_at": "2026-11-26T10:00:00+00:00",
                             "rows": rows, "window": [str(IDX[0].date()), str(IDX[-1].date())]}))
    return f


@pytest.fixture
def data(tmp_path: Path) -> Path:
    reports = tmp_path / "reports"
    reports.mkdir()
    walkforward_report(reports)
    oneshots_report(reports)
    engine_report(reports, "spy_engine", ("v2", "0.5", "1"))
    engine_report(reports, "spy_cost_rerun", ("v2idx",))
    (tmp_path / "trials.jsonl").write_text("")
    return tmp_path


def test_each_writer_emits_the_exposure_column_the_scorer_reads(data: Path):
    found = results.reports(data / "reports")
    assert {rep.get("set") or "walkforward" for _, rep in found} == {"walkforward", "oneshots", "spy_engine",
                                                                    "spy_cost_rerun"}
    for f, rep in found:
        for row in results.rows(f, rep):
            assert row.daily_file is not None, row.label
            assert "exposure" in pd.read_csv(row.daily_file, nrows=1).columns, row.label
            d = results.daily(row)
            assert d is not None and "exposure" in d and len(d) == len(IDX), row.label
            assert (d["exposure"] == 0.0).all(), f"{row.label}: flat at every close"


def test_the_scorer_scores_a_fixture_from_each_writer(data: Path):
    res = gate_v3_rescore.rescore(data, as_of=AS_OF)
    scored = {(r["rule"], r["run"].split(",")[0]) for r in res["rows"]}
    assert {("control", "walkforward"), ("orb_stocks_in_play", "oneshots"), ("spy_noise_band", "spy_engine"),
            ("spy_noise_band", "spy_cost_rerun")} <= scored
    assert not [s for s in res["skipped"] if "exposure" in s["why"]], res["skipped"]
    for r in res["rows"]:
        g = r["gate_v3"]
        assert g["statistics"]["mean_exposure"] == 0.0 and g["verdict"] in ("provisional", "fail", "incomplete")
        assert g["statistics"]["window"] == ["2017-05-01", "2024-12-31"]


def test_without_the_column_the_run_stays_unscored(tmp_path: Path):
    """The case of the 11 older runs of #456: the same walk-forward without `exposure` is listed, not scored."""
    reports = tmp_path / "reports"
    reports.mkdir()
    walkforward_report(reports, drop_exposure=True)
    engine_report(reports, "spy_engine", ("v2",))  # the folder's SPY series
    (tmp_path / "trials.jsonl").write_text("")
    res = gate_v3_rescore.rescore(tmp_path, as_of=AS_OF)
    assert ("control", "walkforward, today") in {(s["rule"], s["run"]) for s in res["skipped"]
                                                 if s["why"] == "the daily file has no exposure column"}
    assert "control" not in {r["rule"] for r in res["rows"]}


def test_the_engine_exposure_is_positions_over_equity():
    assert spy_engine._exposure(_Broker(100_000.0, 100_000.0)) == 0.0
    assert spy_engine._exposure(_Broker(100_000.0, 85_000.0)) == pytest.approx(0.15)
    assert spy_engine._exposure(_Broker(0.0, 0.0)) == 0.0
