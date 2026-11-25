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
import gate_v3_rescore  # noqa: E402
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
    # #556: trend_momentum_band; #472: dip_reversal, spy_late_momentum (retroactive); #474: trend_momentum_gbm;
    # #556: trend_momentum_band; #472: dip_reversal, spy_late_momentum (retroactive); #474: trend_momentum_gbm;
    # #471: preopen_negative_news_veto; #458: index_core; #459: index_core_trend_momentum, trend_momentum_sleeve
    assert len(PREREGS) == 25
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


def _files_with(folder: Path, needle: str) -> list[str]:
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file() and needle in p.read_text())


@pytest.mark.parametrize("where", ["home", "repository"])
def test_no_generated_file_holds_this_machines_home_folder(data: Path, tmp_path_factory, monkeypatch,  # noqa: F811
                                                           where: str):
    """Issue #594: the registry's source line and the copied pages carried /Users/<name>/...; #452: and then
    ~/Projects/kaibot/data/... when the data folder was not under the root the script ran from. Every file both
    scripts write names the data folder `data/`, wherever it is."""
    home = data.parent  # the fixture's data folder sits in this "home folder"
    monkeypatch.setenv("HOME", str(home))
    root = ROOT
    if where == "repository":  # a clone in the home folder with the data inside it, as on the research Mac
        root = home
        for part in ("config", "docs"):
            if not (home / part).exists():
                (home / part).symlink_to(ROOT / part)
    (data / "reports" / "experiments_core_pit_summary.md").write_text(f"read {data / 'trials.jsonl'}\n")
    folder = tmp_path_factory.mktemp("results_portable")
    export_results.main(["--data", str(data), "--out", str(folder), "--root", str(root)])
    gate_v3_rescore.main(["--data", str(data), "--out", str(folder), "--root", str(root)])
    written = sorted(p.name for p in folder.rglob("*") if p.is_file())
    assert "gate_v3_rescore.json" in written and "README.md" in written and "experiments_core_pit_summary.md" in written
    assert _files_with(folder, str(home)) == [] and _files_with(folder, str(home.resolve())) == []
    source = "data/trials.jsonl"
    assert _files_with(folder, "~/") == []
    assert f'"file {source}: 7 rows"' in (folder / "gate_v3_rescore.json").read_text()
    assert f"read {source}" in (folder / "as_reported" / "experiments_core_pit_summary.md").read_text()


def test_portable_matches_whole_paths_only(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path / "ab"))
    repo = tmp_path / "ab" / "kaibot"
    text = f"{repo}/data/x.json {repo} {tmp_path}/ab/notes {tmp_path}/abc/other"
    assert results.portable(text, repo) == f"data/x.json . ~/notes {tmp_path}/abc/other"


def test_portable_names_the_data_folder_and_knows_the_tilde_spelling(monkeypatch, tmp_path: Path):
    """#452: a path an earlier run already shortened to ~/... still becomes repo-relative or `data/`."""
    monkeypatch.setenv("HOME", str(tmp_path / "ab"))
    repo, store = tmp_path / "ab" / "wt", tmp_path / "ab" / "kaibot" / "data"
    text = (f"{store}/trials.jsonl ~/kaibot/data/trials.jsonl ~/wt/docs/x.md ~/wt ~/wtx/y "
            f"{repo}/config/gate_v3.yaml ~/kaibot/data-old/z {store}")
    assert results.portable(text, repo, store) == ("data/trials.jsonl data/trials.jsonl docs/x.md . ~/wtx/y "
                                                  "config/gate_v3.yaml ~/kaibot/data-old/z data")


def _tree(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def test_the_same_reports_give_the_same_files_byte_for_byte(data: Path, tmp_path_factory):  # noqa: F811
    """#452: the time on every page is the newest report's own, never the clock, so a rerun changes nothing."""
    one, two = tmp_path_factory.mktemp("first"), tmp_path_factory.mktemp("second")
    for folder in (one, two):
        export_results.main(["--data", str(data), "--out", str(folder)])
        gate_v3_rescore.main(["--data", str(data), "--out", str(folder)])
    assert _tree(one) == _tree(two) and len(_tree(one)) > 10
    newest = results.reports_as_of(results.reports(data / "reports"))
    assert newest and f"Written {newest} by" in (one / "README.md").read_text()
    assert json.loads((one / "gate_v3_rescore.json").read_text())["generated_at"] == newest


def test_every_run_gets_a_small_summary_on_the_research_window_only(out):
    """#452: one file per report (not only the newest of each kind), marks on 2017-05-01..2024-12-31 only."""
    store, folder, res = out
    with_base = sorted(f.stem for f, rep in results.reports(store / "reports")
                       if any(r.role == "base" for r in results.rows(f, rep)))
    assert sorted(p.stem for p in (folder / "runs").glob("*.json")) == with_base and len(with_base) >= 2
    assert "[`runs/`](runs/)" in (folder / "README.md").read_text()
    for f in (folder / "runs").glob("*.json"):
        assert f.stat().st_size < 20_000, f.name
        s = json.loads(f.read_text())
        assert s["report"] == f"{f.stem}.json" and s["research_window"] == ["2017-05-01", "2024-12-31"]
        for acc in s["accounts"]:
            w = acc["research_window"]
            assert w is None or w["window"][1] <= "2024-12-31", "no hold-out session in a summary"
            assert acc["trials_for_rule"] >= 0
    tm = [json.loads(f.read_text()) for f in (folder / "runs").glob("core_balanced_trend_momentum_pit_*.json")]
    acc = tm[-1]["accounts"][0]
    assert acc["rule"] == "trend_momentum" and acc["control_as_stored"]["percentile"] is not None
    w = acc["research_window"]
    assert {"cagr", "alpha", "alpha_t", "beta", "mean_exposure", "exposure_matched_spy_cagr"} <= set(w)
    assert w["mean_exposure"] == pytest.approx(0.4) and acc["neighbours"]


HOME_PATH = r"(/Users/[^/\s]+|/home/[^/\s]+|~)/"


def test_no_committed_result_names_a_home_folder():
    """#452: the committed results carry no path of the research Mac (HOW_TO.md is the owner's own commands)."""
    import re

    folder = ROOT / "docs" / "research" / "results"
    bad = [f"{p.relative_to(folder)}:{i}" for p in sorted(folder.rglob("*")) if p.is_file() and p.name != "HOW_TO.md"
           for i, line in enumerate(p.read_text().splitlines(), 1) if re.search(HOME_PATH, line)]
    assert bad == []


def test_the_time_on_a_page_skips_a_report_whose_time_cannot_be_read(tmp_path: Path):
    found = [(tmp_path / "a_20261110T100000.json", {"run_at": "not a time"}), (tmp_path / "b.json", {}),
             (tmp_path / "c.json", {"run_at": "2026-11-12T09:00:00+01:00"})]
    assert results.reports_as_of(found) == "2026-11-12T08:00:00+00:00"
    assert results.reports_as_of([(tmp_path / "b.json", {})]) is None
