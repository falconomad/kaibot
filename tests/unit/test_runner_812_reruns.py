"""#812: the re-runs of the point-in-time results that used the 501-key view (#817's list), as runner jobs: the
strict allow-list entry of scripts/momentum_core.py, the label every re-run carries (queue.yaml `note`, printed by
the runner in the comment and the results PR), the research window, and the re-score last."""

from __future__ import annotations

import sys

import pytest
from test_research_runner import RESULTS, FakeRun, export_job, job, tick_in

from kaibot.engine.config import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import research_runner as rr  # noqa: E402


def test_a_jobs_note_labels_its_comment_and_its_results_pr(tmp_path):
    """#812: `note` in queue.yaml is printed with the results: the comment and the results PR (body and commit)."""
    run = FakeRun(gh_ok=True, job_changes={RESULTS + "a.md": "??"})
    j = {**export_job(), "note": "re-run after #812 (726-key universe)"}
    tick_in(tmp_path, [j], run, files={RESULTS + "a.md": "x\n"})
    assert "**Label:** re-run after #812 (726-key universe)" in run.posted[0][1]
    assert "Label: re-run after #812 (726-key universe)." in run.prs[0]["body"]
    assert "Label: re-run after #812 (726-key universe)." in run.commits[0]["message"]


@pytest.mark.parametrize("note", [7, "two\nlines", "`code`", "x" * 201, "<b>", "[link](http://x)"])
def test_a_note_that_is_not_one_plain_line_is_rejected(note):
    with pytest.raises(rr.JobError, match="note"):
        rr.check_job({**job(), "note": note}, rr.load_allowed())


def test_a_job_without_a_note_has_no_label():
    assert rr.check_job(job(), rr.load_allowed()).note == ""


def test_momentum_core_is_allowed_the_three_core_pit_reruns_only():
    """#812: the core pit runs of the cross-sectional momentum rules, research window, cost model v1, earnings off."""
    spec = rr.load_allowed().specs["scripts/momentum_core.py"]
    assert set(spec.flags) == {"--version", "--universe", "--end", "--costs", "--earnings"} and spec.heavy
    assert not spec.env and not spec.writes
    for v in ("trend_momentum", "mom_12_1_band", "mom_12_1_band_nofilter"):
        assert rr.check_args(spec, ["--version", v, "--universe", "pit", "--end", "2024-12-31", "--costs", "v1",
                                    "--earnings", "off"])
    for bad in (["--version", "dip_reversal"], ["--universe", "today"], ["--end", "2025-06-30"],
                ["--end", "2026-11-27"], ["--costs", "v2"], ["--earnings", "auto"], ["--start", "2016-01-01"],
                ["--control-runs", "0"], ["--summary"], ["--level", "aggressive"]):
        with pytest.raises(rr.JobError):
            rr.check_args(spec, bad)


def test_the_812_reruns_are_labelled_stay_in_the_research_window_and_rescore_last():
    """#812: every re-run carries the label, ends on the last research session (a re-score has no window), and the
    gate v3.1 re-score comes after every re-run and after the stage-6 re-score."""
    cfg = rr.load_allowed()
    jobs = rr.load_queue()
    ids = [j["id"] for j in jobs]
    mine = [rr.check_job(j, cfg) for j in jobs if "after812" in j["id"]]
    assert len(mine) == 9 and all(j.note == "re-run after #812 (726-key universe)" for j in mine)
    for j in mine:
        if j.spec.target != "scripts/gate_v3_rescore.py":
            assert j.args[j.args.index("--end") + 1] == "2024-12-31"
            assert "--universe" not in j.args or j.args[j.args.index("--universe") + 1] == "pit"
    last = ids.index("gate-v3-1-rescore-after812-2026-11-27")
    assert last > ids.index("gate-v3-1-rescore-stage6-2026-11-27")
    assert all(ids.index(j.id) < last for j in mine if j.id != ids[last])
