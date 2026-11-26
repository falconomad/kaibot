"""#456 (M11 2.5): the gate v3 re-score covers the book test. A book report (scripts/index_core.py,
scripts/index_core_book.py) is re-scored with `evaluate_book` from its own daily files, one row per book, index cap
and universe, and never as a sleeve: its rows stay out of the sleeve table and the family correction. Nothing is
re-run and no trial is logged."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kaibot.engine.config import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import gate_v3_rescore  # noqa: E402
import index_core as core_script  # noqa: E402
import index_core_book as book_script  # noqa: E402

AS_OF = datetime(2026, 11, 25, tzinfo=UTC)


@pytest.fixture(scope="module")
def data(tmp_path_factory) -> Path:
    """A data folder holding what the Mac holds after the 24 Nov runs and the 25 Nov 80% runs (on the fixture)."""
    d = tmp_path_factory.mktemp("books")
    common = ["--fixture", "--data", str(d), "--out", str(d / "reports"), "--trials", str(d / "trials.jsonl")]
    old = core_script.main(common)  # as on 24 Nov, before --index-cap: the report records no cap
    f = Path(old["file"])
    rep = json.loads(f.read_text())
    rep.pop("index_cap_pct")
    f.write_text(json.dumps(rep))
    core_script.main([*common, "--index-cap", "80"])
    book_script.main([*common, "--control-runs", "20", "--index-cap", "80"])
    return d


def test_each_book_and_cap_is_rescored_once_and_matches_its_own_run(data: Path):
    res = gate_v3_rescore.rescore(data, as_of=AS_OF)
    by = {(b["book"], b["index_cap"], b["universe"]): b for b in res["books"]}
    assert set(by) == {("index_core", "27%", "fixture"), ("index_core", "80%", "fixture"),
                       ("index_core_trend_momentum", "80%", "fixture")}
    for b in res["books"]:
        g = b["gate_v3"]
        assert g["gate"] == "B-book" and set(g["marks"]) == {"b1", "b2", "b3", "b4", "b5", "b6", "b7"}
        # the re-score from the daily files and the run's own SPY gives the run's own verdict and marks
        assert g["verdict"] == b["at_run"]["verdict"]
        assert {k: v["met"] for k, v in g["marks"].items()} == b["at_run"]["marks"]
        assert b["spy"] in ("spy_total_return", "spy_price_only")
        assert g["statistics"]["window"][1] <= "2024-12-31"
    core = by[("index_core", "80%", "fixture")]["gate_v3"]
    assert core["marks"]["b6"]["met"] is True and by[("index_core", "80%", "fixture")]["sleeve_test"] is None
    book = by[("index_core_trend_momentum", "80%", "fixture")]
    assert book["sleeve_test"]["rule"] == "trend_momentum_sleeve"
    assert book["gate_v3"]["marks"]["b6"]["numbers"]["sleeves"] == {"trend_momentum_sleeve":
                                                                    book["sleeve_test"]["verdict"]}
    assert book["gate_v3"]["gate_a"]["budgets_as_pre_registered"] is True
    # the 80% core holds more of the book in SPY than the 27% one, so its CAGR moves further from 0
    c27 = by[("index_core", "27%", "fixture")]["gate_v3"]["marks"]["b2"]["numbers"]["cagr"]
    assert abs(core["marks"]["b2"]["numbers"]["cagr"]) > abs(c27)


def test_book_reports_never_enter_the_sleeve_table_or_the_correction(data: Path):
    res = gate_v3_rescore.rescore(data, as_of=AS_OF)
    assert res["rows"] == []  # the folder holds book reports only: the core, the book and its sleeve test
    assert res["rule_p"] == {} and all(f["p"] is None for f in res["families"].values())
    assert res["reports_read"] == 3 and res["skipped"] == []


def test_the_page_has_the_books_table(data: Path, tmp_path: Path):
    res = gate_v3_rescore.main(["--data", str(data), "--out", str(tmp_path)])
    text = (tmp_path / "gate_v3_rescore.md").read_text()
    assert "## Books (the book test, decision 16 section 5)" in text
    assert "| index_core | 80% | fixture |" in text and "| index_core | 27% | fixture |" in text
    assert "| index_core_trend_momentum | 80% | fixture |" in text and "trend_momentum_sleeve:" in text
    assert "(differs" not in text
    assert json.loads((tmp_path / "gate_v3_rescore.json").read_text())["books"][0]["gate_v3"]["gate"] == "B-book"
    assert len(res["books"]) == 3


def test_a_stored_verdict_that_differs_from_the_rescore_is_flagged(data: Path, tmp_path: Path):
    res = gate_v3_rescore.rescore(data, as_of=AS_OF)
    b = res["books"][0]
    b["at_run"] = {**b["at_run"], "verdict": "pass", "gate_sha": "0" * 64}
    assert "pass (differs; scored under another gate_v3.yaml)" in gate_v3_rescore.render(res)


def test_a_book_without_its_spy_file_uses_the_folders_series_or_says_why(data: Path, tmp_path: Path):
    import shutil

    d = tmp_path / "d"
    shutil.copytree(data, d)
    for p in (d / "reports").glob("*/spy_daily.csv"):
        p.unlink()
    res = gate_v3_rescore.rescore(d, as_of=AS_OF)
    assert res["books"] == [] and len(res["skipped"]) == 3
    assert {s["why"] for s in res["skipped"]} == {"no SPY series for its sessions"}
    assert "Runs that could not be scored" in gate_v3_rescore.render(res)


def test_the_book_rescore_writes_nothing_under_data(data: Path, tmp_path: Path):
    def snap() -> dict:
        return {str(f.relative_to(data)): hashlib.sha256(f.read_bytes()).hexdigest()
                for f in sorted(data.rglob("*")) if f.is_file()}

    before = snap()
    gate_v3_rescore.main(["--data", str(data), "--out", str(tmp_path / "o")])
    assert snap() == before


def test_the_book_scripts_write_the_spy_series_they_were_judged_on(data: Path):
    files = sorted((data / "reports").glob("*/spy_daily.csv"))
    assert len(files) == 3
    import pandas as pd

    d = pd.read_csv(files[0])
    assert list(d.columns) == ["date", "ret", "ret_kind"] and d["date"].max() <= "2024-12-31"


def test_the_rescore_job_is_allowed_and_queued_after_the_80_percent_runs():
    import research_runner as rr

    cfg = rr.load_allowed()
    spec = cfg.specs["scripts/gate_v3_rescore.py"]
    assert spec.flags == {} and spec.writes == ("docs/research/results/",) and not spec.env
    jobs = rr.load_queue()
    ids = [j["id"] for j in jobs]
    job = rr.check_job(jobs[ids.index("gate-v3-rescore-books-2026-11-25")], cfg)
    assert job.args == () and job.issue == 456
    assert ids.index("gate-v3-rescore-books-2026-11-25") > ids.index("index-core-book-80-2026-11-25")


def test_the_v3_1_rescore_runs_again_after_the_stage_6_runs():
    """#708 left the #701 re-score to run after its re-runs; #706's control book and #710's 97% runs come first too,
    so the published v3.1 table counts them (the runner takes the queue in file order)."""
    import research_runner as rr

    cfg = rr.load_allowed()
    jobs = rr.load_queue()
    ids = [j["id"] for j in jobs]
    rerun = "gate-v3-1-rescore-stage6-2026-11-27"
    job = rr.check_job(jobs[ids.index(rerun)], cfg)
    assert (job.spec.target, job.args, job.issue) == ("scripts/gate_v3_rescore.py", (), 701)
    before = [i for i in ids if i.endswith("-exposure-2026-11-26")]
    before += ["index-core-book-80-control-2026-11-26", "index-core-97-2026-11-26", "index-core-book-97-2026-11-26"]
    assert len(before) == 11
    assert all(ids.index(rerun) > ids.index(i) for i in before)
    assert ids.index(rerun) > ids.index("gate-v3-1-rescore-2026-11-26")


def test_qa_only_book_reports_are_split_off_and_a_sleeve_report_stays_in_the_sleeve_table():
    """QA-Ops (#456): the split is by the report, so a sleeve report (its control p-value feeds the family
    correction) is never taken for a book, and a book report never reaches the correction through its sleeve test."""
    from kaibot.evaluation import gate_v3 as g3

    gate = g3.GateV3.load()
    for sleeve in ({"rule": "trend_momentum"}, {"rule": "trend_momentum", "gate_v3_sleeve": {"verdict": "fail"}},
                   {"rule": "trend_momentum_sleeve"}, {}):
        assert not gate_v3_rescore.is_book_report(gate, sleeve)
    for book in ({"rule": "index_core"}, {"book": "index_core_trend_momentum", "sleeve": "trend_momentum"},
                 {"rule": "anything", "gate_v3_book": {"rule": "x"}}):
        assert gate_v3_rescore.is_book_report(gate, book)
