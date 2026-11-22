"""The results export (M11 tickets 2.1 and 2.2): every pre-registration gets a page from the reports already on
disk, with exposure, beta, alpha and the neighbours, and nothing is re-run or invented."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from kaibot.engine.config import ROOT
from kaibot.evaluation import results

sys.path.insert(0, str(ROOT / "scripts"))
import export_results  # noqa: E402
from test_results_rescore import core_report, data  # noqa: E402, F401  (the synthetic report folder)

PREREGS = sorted(p.stem for p in (ROOT / "docs" / "prereg").glob("*.md") if p.stem != "TEMPLATE")


@pytest.fixture
def out(data: Path, tmp_path_factory) -> tuple[Path, Path, dict]:  # noqa: F811
    (data / "reports" / "experiments_core_pit_summary.md").write_text("# the table the run printed\n")
    (data / "reports" / "market_alarm_backfill.md").write_text("# the study's own report\n")
    folder = tmp_path_factory.mktemp("results")
    res = export_results.main(["--data", str(data), "--out", str(folder)])
    return data, folder, res


def test_a_page_exists_for_every_pre_registration(out):
    _, folder, res = out
    assert len(PREREGS) == 20  # #556: trend_momentum_band; #472: dip_reversal and spy_late_momentum (retroactive)
    for rule in PREREGS:
        assert (folder / f"{rule}.md").is_file() and (folder / f"{rule}.json").is_file(), rule
    index = (folder / "README.md").read_text()
    assert all(f"[{rule}]({rule}.md)" in index for rule in PREREGS)
    assert "| [news_gap_week](news_gap_week.md) | yes | news_drift | 0 | - | no report on this machine |" in index
    none = (folder / "news_gap_week.md").read_text()
    assert "No account of this rule is in `data/reports` on this machine" in none
    assert "Nothing is shown rather than a number from memory" in none


def test_the_page_has_marks_alpha_exposure_matched_spy_percentile_and_trial_count(out):
    _, folder, res = out
    page = json.loads((folder / "trend_momentum.json").read_text())
    assert page["trials"] == {"rule": 6, "family": 6, "registry": 8292} and page["family"] == "cross_sectional_momentum"
    runs = {r["run"]: r for r in page["runs"]}
    assert list(runs) == ["core, point_in_time, costs v2", "core, today, costs v2"], "the best evidence leads"
    pit = runs["core, point_in_time, costs v2"]
    assert pit["report"].startswith("core_balanced_trend_momentum_pit_20261113"), "the newest report of its kind"
    own, research = pit["account"]["own_window"], pit["account"]["research_window"]
    assert own["window"] == ["2017-05-01", "2026-11-06"] and research["window"] == ["2017-05-01", "2024-12-31"]
    assert research["mean_exposure"] == pytest.approx(0.4)
    assert research["alpha_beta"]["beta"] == pytest.approx(0.4, abs=0.02)
    assert research["alpha_beta"]["alpha"] == pytest.approx(0.03, abs=0.012) and research["alpha_beta"]["alpha_t"] > 2
    assert research["active_return"]["annualised"] == pytest.approx(0.03, abs=0.01)
    assert research["exposure_matched_spy"]["cagr"] < research["spy"]["cagr"]
    assert research["cash"]["tbill_cash"]["rule"]["cagr"] > research["cash"]["zero_cash"]["rule"]["cagr"]
    assert pit["control"]["percentile"] == 99.8 and pit["control"]["p_value"] == pytest.approx(2 / 501)
    assert pit["control_drew_from"] == "candidate_pool" and pit["gate_v2"] == [{"mark": 2, "verdict": "fail"}]
    text = (folder / "trend_momentum.md").read_text()
    for needle in ("## core, point_in_time, costs v2", "| As run: 2017-05-01 to 2026-11-06 |",
                   "| Research window: 2017-05-01 to 2024-12-31 |", "Idle cash, both versions",
                   "Matched random control: percentile 99.8 of 500 runs", "drew from: candidate_pool",
                   "Gate v2 marks as the report stored them: 2: fail",
                   "Trials logged: 6 configurations for this rule, 6 for its family, 8,292 in the whole registry",
                   "experiments_core_pit_summary.md"):
        assert needle in text, needle


def test_the_neighbours_answer_the_lookback_and_exit_band_questions(out):
    """Ticket 2.2: one page with the neighbours' exposure, beta and alpha beside the frozen rule."""
    _, folder, _ = out
    page = json.loads((folder / "trend_momentum.json").read_text())
    nb = {n["neighbour"]: n for n in page["runs"][0]["neighbours"]}
    assert set(nb) == {"reg60", "exit30"}  # the two this folder holds
    assert nb["reg60"]["change"] == "regression window 60 sessions (the 15%-move window stays 90)"
    assert nb["exit30"]["change"].startswith("sell when out of the top 30%")
    assert nb["reg60"]["research_window"]["alpha_beta"]["beta"] == pytest.approx(0.4, abs=0.02)
    text = (folder / "trend_momentum.md").read_text()
    assert "### Neighbours (the plateau check)" in text and "| reg60 | regression window 60 sessions" in text
    assert "points |" in text and "What it changes" in text


def test_the_scripts_own_pages_are_copied_unchanged(out):
    data, folder, res = out  # noqa: F811
    assert res["as_reported"] == ["experiments_core_pit_summary.md", "market_alarm_backfill.md"]
    for name in res["as_reported"]:
        assert (folder / "as_reported" / name).read_text() == (data / "reports" / name).read_text()
    assert "market_alarm_backfill.md" in (folder / "market_alarm_study.md").read_text()
    assert "as_reported/" in (folder / "README.md").read_text()


def test_nothing_is_written_under_data_and_no_trial_is_logged(out):
    data, _, _ = out  # noqa: F811
    assert sorted(p.name for p in data.iterdir()) == ["inputs", "reports", "trials.jsonl"]
    assert len((data / "trials.jsonl").read_text().splitlines()) == 7


def test_an_unreadable_account_is_shown_as_such(data: Path, tmp_path_factory):  # noqa: F811
    for p in (data / "reports").glob("core_balanced_mom_12_1_band_pit_*/daily_*base_1x*"):
        p.unlink()
    folder = tmp_path_factory.mktemp("results2")
    export_results.main(["--data", str(data), "--out", str(folder), "--rule", "mom_12_1_band"])
    text = (folder / "mom_12_1_band.md").read_text()
    assert "The account's daily file could not be read: no daily file on this machine" in text
    assert '"cagr_pct": 8.0' in text, "the report's own summary numbers are shown in its place"
    assert not (folder / "README.md").exists(), "--rule writes one page and leaves the index alone"
    with pytest.raises(SystemExit, match="reads the reports of earlier runs"):
        export_results.main(["--data", str(folder / "nowhere")])


def test_the_intraday_one_shot_layout_is_read(tmp_path: Path):
    """oneshots.py writes `variants.<name>.account` for its two intraday rules and daily files with `ret` only."""
    import numpy as np
    import pandas as pd

    reports = tmp_path / "reports"
    tag = "oneshots_orb_stocks_in_play_20261119T182503"
    (reports / tag).mkdir(parents=True)
    idx = pd.bdate_range("2017-05-01", "2019-12-31")
    for name in ("orb_stocks_in_play", "orb_stocks_in_play~top10"):
        pd.DataFrame({"date": idx, "ret": np.random.default_rng(1).normal(-0.0001, 0.002, len(idx))}).to_csv(
            reports / tag / f"daily_{name}.csv", index=False)
    (reports / f"{tag}.json").write_text(json.dumps({
        "set": "oneshots", "kind": "intraday", "rule": "orb_stocks_in_play", "universe_mode": "today",
        "variants": {"base": {"trades": {"n": 10}, "account": {"cagr_pct": -2.5, "sharpe": -0.8}},
                     "gross": {"trades": {"n": 10}, "note": "no account"},
                     "top10": {"trades": {"n": 5}, "account": {"cagr_pct": -3.0}}}, "verdict": "DELETE"}))
    f, rep = results.reports(reports)[0]
    rows = results.rows(f, rep)
    assert [(r.label, r.role, r.neighbour) for r in rows] == [("orb_stocks_in_play", "base", None),
                                                              ("orb_stocks_in_play~top10", "neighbour", "top10")]
    d = results.daily(rows[0])
    assert list(d.columns) == ["ret"] and len(d) == len(idx)
    res = export_results.export(tmp_path, only="orb_stocks_in_play")
    run = res["pages"]["orb_stocks_in_play"]["runs"][0]
    assert run["verdict_as_reported"] == "DELETE" and run["account"]["own_window"]["mean_exposure"] is None
    assert run["account"]["why"] is None and run["account"]["own_window"]["spy"] is None  # no SPY series anywhere
    assert "SPY series: none found" in export_results.render_page(res["pages"]["orb_stocks_in_play"],
                                                                 res["research_window"])
