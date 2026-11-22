"""The hold-out window (M11 ticket 2.6, issue #457): a research run that reads it fails; an opening is the only
way in and every read under one is logged."""

from __future__ import annotations

import ast
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml

from kaibot.engine.config import ROOT
from kaibot.evaluation import holdout as ho

SCRIPTS = ROOT / "scripts"
NOW = datetime(2027, 2, 1, 12, 0, tzinfo=UTC)
OPENING = {"id": "2027-02-01.my_rule.final", "rule": "my_rule", "purpose": "final_check",
           "scripts": ["momentum_core.py"], "max_runs": 2, "decided_by": "Product", "decided_on": "2027-02-01",
           "reason": "passed gate v3 on the research window; the final check its pre-registration declared"}


def cfg(*openings, start="2025-01-01", end="2024-12-31") -> ho.Holdout:
    return ho.from_bytes(yaml.safe_dump({"version": 1, "holdout_from": start, "research_window_end": end,
                                         "openings": list(openings)}).encode())


def check(end, tmp_path, *, h=None, script="momentum_core.py", rule="my_rule", opening=None):
    return ho.check(end, script=script, rule=rule, data=tmp_path, opening=opening, holdout=h or cfg(OPENING), now=NOW,
                    argv=["--end", str(end)])


# ---------------------------------------------------------------- the committed window


def test_the_committed_window_is_18_to_24_months_and_has_no_opening():
    h = ho.load(ROOT)
    assert h.start == date(2025, 1, 1) and h.research_end == date(2024, 12, 31)
    months = (date(2026, 11, 20) - h.start).days / 30.44  # the history ends on 20 Nov 2026
    assert 18.0 <= months <= 24.0
    assert h.openings == (), "an opening is added by its own pull request, never with other work"
    raw = (ROOT / "config" / "holdout.yaml").read_bytes()
    assert h.sha == __import__("hashlib").sha256(raw).hexdigest()


# ---------------------------------------------------------------- a research run that reads the hold-out fails


def test_a_run_inside_the_research_window_is_not_logged(tmp_path: Path):
    assert check("2024-12-31", tmp_path) is None
    assert check(date(2017, 5, 1), tmp_path) is None
    assert not (tmp_path / ho.ACCESS_LOG).exists()


@pytest.mark.parametrize("end", ["2025-01-01", "2026-11-06", "2099-12-31", None])
def test_a_run_that_reaches_the_hold_out_fails(tmp_path: Path, end, monkeypatch):
    monkeypatch.delenv(ho.ENV_OPENING, raising=False)
    with pytest.raises(ho.HoldoutError, match="inside the hold-out") as e:
        check(end, tmp_path)
    assert "--end 2024-12-31" in str(e.value) and "--open-holdout" in str(e.value)
    assert not (tmp_path / ho.ACCESS_LOG).exists(), "a refused run reads nothing and logs nothing"


def test_enforce_exits_non_zero_so_a_refused_run_is_a_failed_command(tmp_path: Path, monkeypatch):
    monkeypatch.delenv(ho.ENV_OPENING, raising=False)
    with pytest.raises(SystemExit) as e:
        ho.enforce(script="scripts/momentum_core.py", end="2026-11-06", rule="trend_momentum", data=tmp_path, root=ROOT)
    assert str(e.value.code).startswith("hold-out: ") and e.value.code != 0
    assert ho.enforce(script="scripts/momentum_core.py", end="2024-12-31", rule="trend_momentum", data=tmp_path,
                      root=ROOT) is None


def test_an_unreadable_end_date_is_refused_not_passed(tmp_path: Path):
    with pytest.raises(ho.HoldoutError, match="not a date"):
        check("soon", tmp_path)


# ---------------------------------------------------------------- an opening, and its log


def test_an_opening_lets_the_run_read_and_logs_it(tmp_path: Path, capsys):
    row = check("2026-11-06", tmp_path, opening=OPENING["id"])
    assert row["opening"] == OPENING["id"] and row["rule"] == "my_rule" and row["script"] == "momentum_core.py"
    assert row["end"] == "2026-11-06" and row["holdout_from"] == "2025-01-01" and row["read"] == 1
    assert row["ts"] == NOW.isoformat(timespec="seconds") and row["argv"] == ["--end", "2026-11-06"]
    assert len(row["holdout_sha256"]) == 64
    assert ho.read_log(tmp_path) == [row]
    assert "HOLD-OUT OPENED" in capsys.readouterr().err
    stamp = ho.stamp(row, cfg(OPENING))
    assert stamp["read"] is True and stamp["opening"] == OPENING["id"] and stamp["purpose"] == "final_check"
    assert ho.stamp(None, cfg(OPENING)) == {"holdout_from": "2025-01-01", "holdout_sha256": cfg(OPENING).sha,
                                            "read": False}


def test_the_opening_can_come_from_the_environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(ho.ENV_OPENING, OPENING["id"])
    assert check("2026-11-06", tmp_path)["opening"] == OPENING["id"]


def test_an_opening_counts_its_reads_and_stops_at_its_limit(tmp_path: Path):
    assert check("2026-11-06", tmp_path, opening=OPENING["id"])["read"] == 1
    assert check(None, tmp_path, opening=OPENING["id"])["read"] == 2
    with pytest.raises(ho.HoldoutError, match="allows 2 reads and 2 are logged"):
        check("2026-11-06", tmp_path, opening=OPENING["id"])
    assert len(ho.read_log(tmp_path)) == 2
    # a line that cannot be read still counts as a read: the log cannot be spoiled into more reads
    other = tmp_path / "other"
    other.mkdir()
    (other / ho.ACCESS_LOG).write_text("not json\nnot json either\n")
    with pytest.raises(ho.HoldoutError, match="2 are logged"):
        check("2026-11-06", other, opening=OPENING["id"])


@pytest.mark.parametrize("kw, why", [
    ({"opening": "2027-02-01.other"}, "is not in config"),
    ({"opening": OPENING["id"], "rule": "other_rule"}, "is for the rule 'my_rule'"),
    ({"opening": OPENING["id"], "rule": None}, "is for the rule 'my_rule'"),
    ({"opening": OPENING["id"], "script": "scripts/oneshots.py"}, "not this script"),
])
def test_an_opening_is_for_one_rule_and_its_scripts_only(tmp_path: Path, kw, why):
    with pytest.raises(ho.HoldoutError, match=why):
        check("2026-11-06", tmp_path, **kw)
    assert ho.read_log(tmp_path) == []


def test_a_read_that_cannot_be_logged_does_not_happen(tmp_path: Path):
    blocked = tmp_path / "file"
    blocked.write_text("")  # the data "directory" is a file: the log cannot be created
    with pytest.raises(ho.HoldoutError, match="could not be logged"):
        check("2026-11-06", blocked, opening=OPENING["id"])


# ---------------------------------------------------------------- the config fails loudly


@pytest.mark.parametrize("change, why", [
    (lambda o: o.pop("reason"), "lacks reason"),
    (lambda o: o.update(purpose="research"), "purpose must be one of"),
    (lambda o: o.update(max_runs=0), "max_runs"),
    (lambda o: o.update(max_runs=True), "max_runs"),
    (lambda o: o.update(scripts=[]), "needs its scripts"),
    (lambda o: o.update(decided_on="soon"), "not a date"),
])
def test_a_malformed_opening_is_an_error(change, why):
    o = dict(OPENING)
    change(o)
    with pytest.raises(ho.HoldoutError, match=why):
        cfg(o)


def test_config_errors():
    with pytest.raises(ho.HoldoutError, match="listed twice"):
        cfg(OPENING, OPENING)
    with pytest.raises(ho.HoldoutError, match="must be before"):
        cfg(start="2025-01-01", end="2025-01-01")
    with pytest.raises(ho.HoldoutError, match="`holdout_from` is missing"):
        ho.from_bytes(b"version: 1\nopenings: []\nresearch_window_end: 2024-12-31\n")


# ---------------------------------------------------------------- every research script is guarded

# Scripts that read price history or run a rule but are not research runs, each with its reason. A new script
# that imports the replay or the research runner must call holdout.enforce or be added here with a reason.
EXEMPT = {
    "run_config_replays.py": "the dashboard's what-if replays of the owner's settings: a product feature on recent "
                             "sessions, not a research run (each is logged as a config trial)",
    "bench_scan.py": "a speed benchmark of the scan; prints timings only",
    "experiments_summary.py": "reads reports that guarded runs already wrote",
    "fetch_earnings_dates.py": "data collection and its coverage report; no rule result",
    "fetch_spinoffs.py": "data collection; no rule result",
    "fetch_spread_calibration.py": "plans and summarises a quote sample; no rule result",
    "news_coverage.py": "counts stored headlines",
    "spinoff_exposure_check.py": "a data check on corporate actions",
}
RESEARCH_IMPORTS = ("kaibot.replay", "kaibot.engine.runner", "kaibot.engine.daily", "kaibot.engine.shadow",
                    "kaibot.ingest.daily_store")


def _imports(tree: ast.AST) -> set[str]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out.update(f"{node.module}.{n.name}" for n in node.names)
        elif isinstance(node, ast.Import):
            out.update(n.name for n in node.names)
    return out


def _calls_enforce(tree: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "enforce"
               and isinstance(n.func.value, ast.Name) and n.func.value.id == "holdout" for n in ast.walk(tree))


def test_every_script_that_reads_history_is_guarded_or_exempt_with_a_reason():
    unguarded, guarded = [], []
    for f in sorted(SCRIPTS.glob("*.py")):
        tree = ast.parse(f.read_text())
        reads = any(i == m or i.startswith(m + ".") for i in _imports(tree) for m in RESEARCH_IMPORTS)
        if _calls_enforce(tree):
            guarded.append(f.name)
        elif reads and f.name not in EXEMPT:
            unguarded.append(f.name)
    assert unguarded == [], f"research scripts without holdout.enforce: {unguarded}"
    assert {"momentum_core.py", "oneshots.py", "spy_engine.py", "spy_trend.py", "spy_noise_band.py",
            "walk_forward.py", "walk_forward_multiday.py", "run_replay.py", "run_shadow_replay.py",
            "run_spy_momentum_replay.py", "filing_flags_study.py", "market_alarm_report.py"} <= set(guarded)
    assert not set(EXEMPT) & set(guarded), "a guarded script needs no exemption"
    assert all((SCRIPTS / name).is_file() for name in EXEMPT), "an exemption for a script that is gone"
    stale = [n for n in EXEMPT if not any(i == m or i.startswith(m + ".") for m in RESEARCH_IMPORTS
                                          for i in _imports(ast.parse((SCRIPTS / n).read_text())))]
    assert stale == [], f"exemptions no longer needed: {stale}"


def test_the_experiment_runner_ends_before_the_hold_out():
    sh = (SCRIPTS / "run_experiments.sh").read_text()
    h = ho.load(ROOT)
    assert f'END="${{KAIBOT_EXP_END:-{h.research_end}}}"' in sh


# ---------------------------------------------------------------- a real script, end to end


def test_a_research_script_asked_for_the_hold_out_fails_before_it_reads_anything(tmp_path: Path, monkeypatch):
    """The ticket's check: a research run that reads the hold-out fails. The data directory is empty, so a run
    that got past the guard would fail on missing bars with another message."""
    monkeypatch.delenv(ho.ENV_OPENING, raising=False)
    sys.path.insert(0, str(SCRIPTS))
    import spy_trend

    with pytest.raises(SystemExit) as e:
        spy_trend.main(["--data", str(tmp_path), "--end", "2026-11-06"])
    assert str(e.value.code).startswith("hold-out: spy_trend.py would read to 2026-11-06, inside the hold-out")
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(SystemExit) as e:  # inside the research window the guard is silent: the run gets to the data
        spy_trend.main(["--data", str(tmp_path), "--end", "2024-12-31"])
    assert "hold-out" not in str(e.value.code)
    with pytest.raises(SystemExit) as e:
        spy_trend.main(["--data", str(tmp_path), "--end", "2026-11-06", "--open-holdout", "no-such-opening"])
    assert "is not in config" in str(e.value.code)
    import filing_flags_study

    with pytest.raises(SystemExit) as e:  # no --end: it reads to the newest bar
        filing_flags_study.main(["--data", str(tmp_path)])
    assert "would read to the newest bar, inside the hold-out" in str(e.value.code)
    assert json.dumps(ho.read_log(tmp_path)) == "[]"


def test_the_engine_research_command_on_the_store_is_guarded_too(tmp_path: Path, monkeypatch):
    """`python -m kaibot.engine.research --universe today|pit` is the go/no-go run of a multi-day rule (decision
    15) and is not a file in scripts/, so the scan above does not see it. Without --end it reads to today."""
    monkeypatch.delenv(ho.ENV_OPENING, raising=False)
    from kaibot.engine import research

    for extra in ([], ["--end", "2026-11-06"], ["--end", "2025-01-01"]):
        with pytest.raises(SystemExit) as e:
            research.main(["--strategy", "trend_momentum", "--universe", "pit", "--data", str(tmp_path), *extra])
        assert str(e.value.code).startswith("hold-out: research.py would read to "), e.value.code
    assert list(tmp_path.iterdir()) == []
    with pytest.raises((SystemExit, Exception)) as e:  # inside the research window the guard is silent: no store
        research.main(["--strategy", "trend_momentum", "--universe", "pit", "--data", str(tmp_path),
                       "--end", "2024-12-31"])
    assert "hold-out" not in str(getattr(e.value, "code", e.value))


def test_the_momentum_band_run_refuses_the_hold_out(tmp_path: Path, monkeypatch):
    """#460: scripts/trend_momentum_band.py never reads 2025 on."""
    monkeypatch.delenv(ho.ENV_OPENING, raising=False)
    sys.path.insert(0, str(SCRIPTS))
    import trend_momentum_band

    with pytest.raises(SystemExit) as e:
        trend_momentum_band.main(["--data", str(tmp_path), "--end", "2025-02-28", "--trials", ""])
    assert str(e.value.code).startswith("hold-out: trend_momentum_band.py would read to 2025-02-28")


def test_the_screen_refuses_the_hold_out(tmp_path: Path, monkeypatch):
    """#462: scripts/screen_batch.py never reads 2025 on."""
    import yaml

    monkeypatch.delenv(ho.ENV_OPENING, raising=False)
    sys.path.insert(0, str(SCRIPTS))
    import screen_batch

    f = tmp_path / "b.yaml"
    f.write_text(yaml.safe_dump({"id": "h", "date": "2026-11-22", "family": "cross_sectional_momentum",
                                 "candidates": [{"name": "screen_x", "signal": "momentum"}]}))
    with pytest.raises(SystemExit) as e:
        screen_batch.main(["--batch", str(f), "--data", str(tmp_path / "d"), "--end", "2025-06-30", "--trials", ""])
    assert str(e.value.code).startswith("hold-out: screen_batch.py would read to 2025-06-30")


def test_the_gbm_overlay_run_refuses_the_hold_out(tmp_path: Path, monkeypatch):
    """#474: scripts/trend_momentum_gbm.py never reads 2025 on."""
    pytest.importorskip("sklearn")
    monkeypatch.delenv(ho.ENV_OPENING, raising=False)
    sys.path.insert(0, str(SCRIPTS))
    import trend_momentum_gbm

    with pytest.raises(SystemExit) as e:
        trend_momentum_gbm.main(["--data", str(tmp_path), "--end", "2025-03-31", "--trials", ""])
    assert str(e.value.code).startswith("hold-out: trend_momentum_gbm.py would read to 2025-03-31")
