"""The report reader and the gate v3 re-score (M11 tickets 2.5, 2.1, 2.2): on small report folders written in the
layouts the research scripts write, the reader finds every account and the re-score scores them without running
anything."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kaibot.engine.config import ROOT
from kaibot.evaluation import results, tbill

sys.path.insert(0, str(ROOT / "scripts"))
import gate_v3_rescore  # noqa: E402

IDX = pd.bdate_range("2017-05-01", "2026-11-06")
RNG = np.random.default_rng(21)
SPY = RNG.normal(0.0005, 0.011, len(IDX))


def write_daily(path: Path, edge: float, exposure: float, seed: int, cost_bp: float = 0.02) -> None:
    ret = exposure * SPY + np.random.default_rng(seed).normal(edge / 252, 0.0006, len(IDX))
    end = 100_000.0 * np.cumprod(1 + ret)
    start = np.r_[100_000.0, end[:-1]]
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": IDX, "start": start, "end": end, "pnl": end - start, "exposure": exposure,
                  "cost": start * cost_bp / 1e4, "traded": 0.0}).to_csv(path, index=False)


def core_report(folder: Path, rule: str, stamp: str, *, pit: bool, edge: float, percentile: float) -> Path:
    """The layout of scripts/momentum_core.py: rows with their files, a control per label, spy_daily.csv."""
    tag = f"core_balanced_{rule}{'_pit' if pit else ''}_{stamp}"
    rows = []
    for spec, cost, kind in ((rule, 0.0, "break_even_input"), (rule, 1.0, "grid"), (rule, 2.0, "grid"),
                             (f"{rule}~reg60", 1.0, "neighbour"), (f"{rule}~exit30", 1.0, "neighbour")):
        label = f"{spec}|base|{cost:g}x|cap100|hold"
        safe = label.replace("|", "_").replace("~", "-")
        write_daily(folder / tag / f"daily_{safe}.csv", edge, 0.4, seed=len(rows) + 1, cost_bp=0.02 * cost)
        rows.append({"label": label, "kind": kind, "spec": spec, "fill": "base", "cost": cost,
                     "exposure_pct": 100.0, "cagr_pct": 8.0, "sharpe": 0.6, "avg_exposure_pct": 40.0,
                     "files": {"daily": f"{tag}/daily_{safe}.csv"}})
    base = f"{rule}|base|1x|cap100|hold"
    rows[1]["control"] = {"kind": "exposure_matched", "runs": 500, "sharpe_percentile": percentile, "matched": True}
    pd.DataFrame({"date": IDX, "ret": SPY, "ret_total": SPY, "ret_price_only": SPY - 0.00005}).to_csv(
        folder / tag / "spy_daily.csv", index=False)
    rep = {"run_at": "2026-11-13T10:00:00+00:00", "code_sha": "7c0123e", "set": "core", "costs": "v2",
           "version": rule, "rule": rule, "universe_mode": "point_in_time" if pit else "today",
           "bars": "store" if pit else "minute", "base_label": base, "rows": rows,
           "oos_window": ["2017-05-01", "2026-11-06"], "prereg": {"sha256": "ab" * 32},
           "control": {base: {"kind": "exposure_matched", "runs": 500, "matched": True,
                              "control_result": {"percentile": percentile, "runs": 500}}},
           "gate_b": [{"mark": 2, "verdict": "fail"}], "files": {"spy_daily": f"{tag}/spy_daily.csv"}}
    f = folder / f"{tag}.json"
    f.write_text(json.dumps(rep))
    return f


def spy_report(folder: Path, stamp: str) -> Path:
    """The layout of scripts/spy_noise_band.py: one row per cost scenario, no SPY file, a timing control at one
    cost, an intraday daily file without an exposure column."""
    tag = f"spy_balanced_spy_noise_band_{stamp}"
    rows = []
    for cost, kind in (("0 (gross)", "gross"), ("0.5 bp", "grid"), ("model v1", "grid")):
        safe = f"spy_noise_band_{cost}".replace(" ", "")
        ret = np.random.default_rng(len(rows)).normal(0.00002, 0.001, len(IDX))
        end = 100_000.0 * np.cumprod(1 + ret)
        start = np.r_[100_000.0, end[:-1]]
        (folder / tag).mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"date": IDX, "start": start, "end": end, "pnl": end - start, "invested": True,
                      "carry_in": False, "carry_out": False}).to_csv(folder / tag / f"daily_{safe}.csv", index=False)
        rows.append({"label": f"spy_noise_band|{cost}", "kind": kind, "spec": "spy_noise_band", "cost": cost,
                     "cagr_pct": 0.1, "files": {"daily": f"{tag}/daily_{safe}.csv"}})
    rep = {"run_at": "2026-11-14T10:00:00+00:00", "set": "spy", "version": "spy_noise_band", "rows": rows,
           "timing_control": {"control": {"kind": "random_timing", "cost": 0.5, "runs": 500, "matched": True,
                                          "percentile": 99.8, "per_run": [1, 2]}}}
    f = folder / f"{tag}.json"
    f.write_text(json.dumps(rep))
    return f


@pytest.fixture
def data(tmp_path: Path) -> Path:
    reports = tmp_path / "reports"
    reports.mkdir()
    core_report(reports, "trend_momentum", "20261112T100000", pit=False, edge=0.03, percentile=99.8)
    core_report(reports, "trend_momentum", "20261113T100000", pit=True, edge=0.03, percentile=99.8)
    core_report(reports, "trend_momentum", "20261110T100000", pit=True, edge=-0.05, percentile=10.0)  # an older run
    core_report(reports, "mom_12_1_band", "20261113T110000", pit=True, edge=-0.02, percentile=40.0)
    spy_report(reports, "20261114T100000")
    (reports / "gate_b_trend_momentum_20261113T120000.json").write_text(json.dumps({"gate": "B", "marks": {}}))
    (reports / "oneshots_orb_stocks_in_play_skipped.json").write_text(json.dumps({"reason": "no data"}))
    rows = [{"ts": "2026-11-13T10:00:00+00:00", "trial_id": f"t{i}", "strategy": "trend_momentum", "n_trials": 1,
             "metrics": {"daily_sharpe_ann": 0.5 + 0.03 * i}} for i in range(6)]
    rows.append({"ts": "2026-11-01T10:00:00+00:00", "trial_id": "wf", "strategy": "dip_reversal", "n_trials": 8286})
    (tmp_path / "trials.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    tbill.write_cache(tmp_path / tbill.FILE, [{"date": str(d.date()), "value": 4.0}
                                              for d in pd.bdate_range("2017-01-02", "2026-11-06")])
    return tmp_path


# ---------------------------------------------------------------- the reader


def test_the_reader_finds_every_account_and_its_role(data: Path):
    found = results.reports(data / "reports")
    assert [f.name[:22] for f, _ in found][0] == "core_balanced_trend_mo" and len(found) == 5
    assert results.other_files(data / "reports") == ["gate_b_trend_momentum_20261113T120000.json",
                                                     "oneshots_orb_stocks_in_play_skipped.json"]
    rows = [r for f, rep in found for r in results.rows(f, rep)]
    core = [r for r in rows if r.report.startswith("core_balanced_trend_momentum_pit_20261113")]
    assert [r.role for r in core] == ["cost", "base", "cost", "neighbour", "neighbour"]
    assert [r.neighbour for r in core if r.role == "neighbour"] == ["reg60", "exit30"]
    base = core[1]
    assert base.rule == "trend_momentum" and base.meta["universe_mode"] == "point_in_time" and base.cost == 1.0
    assert base.control["sharpe_percentile"] == 99.8 and base.control["control_result"]["runs"] == 500
    assert results.run_kind(base) == "core, point_in_time, costs v2"
    d = results.daily(base)
    assert list(d.columns) == ["ret", "exposure", "cost"] and len(d) == len(IDX)
    assert d["cost"].iloc[5] == pytest.approx(0.02 / 1e4)


def test_the_newest_report_of_each_kind_of_run_wins(data: Path):
    rows = [r for f, rep in results.reports(data / "reports") for r in results.rows(f, rep)]
    base = results.newest_per_rule(rows)
    assert base[("trend_momentum", "core, point_in_time, costs v2")].report.startswith(
        "core_balanced_trend_momentum_pit_20261113")
    assert ("trend_momentum", "core, today, costs v2") in base  # today's list is another kind of run: kept
    assert {k[0] for k in base} == {"trend_momentum", "mom_12_1_band", "spy_noise_band"}


def test_a_report_without_a_base_row_gives_one_per_cost_scenario(data: Path):
    f, rep = next(x for x in results.reports(data / "reports") if x[0].name.startswith("spy_balanced"))
    rows = results.rows(f, rep)
    assert [(r.label, r.role) for r in rows] == [("spy_noise_band|0 (gross)", "cost"),
                                                 ("spy_noise_band|0.5 bp", "base"),
                                                 ("spy_noise_band|model v1", "base")]
    half, v1 = rows[1], rows[2]
    assert half.control["percentile"] == 99.8 and "per_run" not in half.control
    assert v1.control is None, "the timing control was run at 0.5 bp: it does not belong to the v1 scenario"
    assert results.run_kind(half) == "spy, point_in_time, 0.5 bp"
    d = results.daily(half)
    assert list(d.columns) == ["ret", "exposure"] and (d["exposure"] == 0.0).all()  # flat at every close
    spy = results.spy(f, rep, results.spy_pool(data / "reports"))
    assert spy[1] == "spy_total_return" and spy[0].size == len(IDX)  # borrowed from a report that wrote one
    assert results.spy(f, rep) is None


def test_a_missing_daily_file_is_listed_not_dropped(data: Path):
    f, rep = results.reports(data / "reports")[0]
    for p in (f.parent / f.stem).glob("daily_*base_1x*"):
        p.unlink()
    row = next(r for r in results.rows(f, rep) if r.role == "base")
    assert row.daily_file is None and results.daily(row) is None and row.summary["cagr_pct"] == 8.0


# ---------------------------------------------------------------- the re-score


def test_the_rescore_scores_existing_reports_without_running_anything(data: Path, tmp_path: Path, capsys):
    out = tmp_path / "out"
    res = gate_v3_rescore.main(["--data", str(data), "--out", str(out)])
    by = {(r["rule"], r["run"]): r for r in res["rows"]}
    assert set(by) == {("trend_momentum", "core, point_in_time, costs v2"), ("trend_momentum", "core, today, costs v2"),
                       ("mom_12_1_band", "core, point_in_time, costs v2"),
                       ("spy_noise_band", "spy, point_in_time, 0.5 bp"),
                       ("spy_noise_band", "spy, point_in_time, model v1")}
    tm = by[("trend_momentum", "core, point_in_time, costs v2")]
    g = tm["gate_v3"]
    assert tm["report"].startswith("core_balanced_trend_momentum_pit_20261113"), "the newest report, not the older"
    assert g["statistics"]["window"] == ["2017-05-01", "2024-12-31"] and g["sessions_in_holdout_dropped"] > 400
    m = {k: v["met"] for k, v in g["marks"].items()}
    # amendment 1 (decision 16 s.15): a 500-run control in a family of five rules is too small, so S3 (and S5,
    # which asks S1 to S4 on the point-in-time universe) is unknown, no longer failed
    assert m["s1"] is True and m["s2"] is True and m["s3"] is None and m["s5"] is None
    # the real pre-registration declares five neighbours and this folder holds two: the others are "not supplied"
    assert m["s6"] is None and "no result supplied" in g["marks"]["s6"]["why"]
    assert g["marks"]["s6"]["numbers"]["results"]["reg60"] is True
    assert g["marks"]["s1"]["numbers"]["annualised"] == pytest.approx(0.03, abs=0.01)
    assert g["marks"]["s4"]["numbers"]["configurations"] == 6  # the family's, not the 8,292 of the registry
    assert "500 runs, 2000 needed" in g["marks"]["s3"]["why"]
    assert g["verdict"] in ("provisional", "fail", "incomplete") and not g["can_pass"]
    assert tm["gate_v2"] == [{"mark": 2, "verdict": "fail"}]
    assert g["statistics"]["cash"] == "Treasury bills on both sides"
    assert by[("mom_12_1_band", "core, point_in_time, costs v2")]["gate_v3"]["marks"]["s1"]["met"] is False
    assert by[("mom_12_1_band", "core, point_in_time, costs v2")]["gate_v3"]["verdict"] == "fail"
    # gate v3.1 (#704, decision 18 s.8): these runs are the research harness, 500-run controls whose windows ran
    # into the hold-out, so their p-values are greyed out: shown, never in Holm (where the family counts as 1)
    assert res["families"]["cross_sectional_momentum"]["p"] is None
    assert res["families"]["spy_intraday_momentum"]["p"] is None and res["rule_p"] == {}
    grey = res["families"]["cross_sectional_momentum"]["greyed"]["trend_momentum"]
    assert grey["p"] == pytest.approx(2 / 501) and any("2000 needed" in w for w in grey["why"])
    assert any("into the hold-out" in w for w in grey["why"]) and any("engine is" in w for w in grey["why"])
    assert res["families"]["spy_intraday_momentum"]["greyed"]["spy_noise_band"]["p"] == pytest.approx(2 / 501)
    assert res["families"]["news_drift"]["p"] is None and len(res["families"]) == 12
    assert not any(h["rejected"] for h in res["holm"].values())
    assert tm["holm_input"]["enters_holm"] is False
    spy = by[("spy_noise_band", "spy, point_in_time, 0.5 bp")]["gate_v3"]
    assert spy["statistics"]["kind"] == "timing" and spy["marks"]["s3"]["numbers"]["drew_from"] == "random_timing"
    assert by[("spy_noise_band", "spy, point_in_time, model v1")]["gate_v3"]["marks"]["s3"]["met"] is None
    text = (out / "gate_v3_rescore.md").read_text()
    assert text in capsys.readouterr().out and "This is a re-score, not a verdict" in text
    assert "| trend_momentum | core, point_in_time, costs v2 |" in text and "| cross_sectional_momentum |" in text
    # the stored control p-value covers the report's whole window: where that ran into the hold-out it is marked
    assert f"_{2 / 501:.4f}{gate_v3_rescore.DAGGER} (greyed)_ |" in text and "it has seen hold-out sessions" in text
    assert "| Greyed out (not in Holm) |" in text and "control p greyed out, not in Holm:" in text
    assert json.loads((out / "gate_v3_rescore.json").read_text())["gate_sha"] == res["gate_sha"]
    assert not (data / "trials.jsonl").read_text().count("rescore"), "a re-score logs no trial"
    assert sorted(p.name for p in data.iterdir()) == ["inputs", "out", "reports", "trials.jsonl"]  # only --out


def test_the_rescore_says_what_it_could_not_score(data: Path, tmp_path: Path):
    for p in (data / "reports").glob("core_*/spy_daily.csv"):
        p.unlink()
    res = gate_v3_rescore.rescore(data, as_of=datetime(2026, 11, 22, tzinfo=UTC))
    assert res["rows"] == [] and len(res["skipped"]) == 5
    assert {s["why"] for s in res["skipped"]} == {"no SPY series for its sessions"}
    assert "Runs that could not be scored" in gate_v3_rescore.render(res)
    with pytest.raises(SystemExit, match="reads the reports of earlier runs"):
        gate_v3_rescore.main(["--data", str(tmp_path / "nowhere")])


def test_the_rescore_changes_no_file_under_data_so_it_logs_no_trial(data: Path, tmp_path_factory):
    import hashlib

    def snapshot() -> dict:
        return {str(f.relative_to(data)): hashlib.sha256(f.read_bytes()).hexdigest()
                for f in sorted(data.rglob("*")) if f.is_file()}

    before = snapshot()
    assert "trials.jsonl" in before
    gate_v3_rescore.main(["--data", str(data), "--out", str(tmp_path_factory.mktemp("rescore_out"))])
    assert snapshot() == before, "a re-score reads data/ and writes only under --out: no trial row, no report"

