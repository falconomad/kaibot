"""The two gate v2 scripts (issue #126), end to end on temporary files: the registry count / copy, and the Gate B
report from the files a walk-forward writes."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_v2 as g2

sys.path.insert(0, str(ROOT / "scripts"))
import gate_v2_report  # noqa: E402
import trial_registry  # noqa: E402
from test_prereg import filled, git, put  # noqa: E402
from test_trial_registry import PINNED, SAMPLE, Store  # noqa: E402

# ---------------------------------------------------------------- scripts/trial_registry.py


@pytest.fixture
def data(tmp_path: Path) -> Path:
    (tmp_path / "trials.jsonl").write_text(SAMPLE.read_text())
    return tmp_path


def test_count_prints_the_number_everyone_uses(data: Path, capsys):
    out = trial_registry.main(["count", "--data", str(data)])
    assert out["configurations"] == PINNED and json.loads(capsys.readouterr().out)["rows"] == 9
    store = Store({"trial": [], "trial_registry": [{"trial_id": "only_in_db", "strategy": "x", "n_trials": 40,
                                                    "row": {}}]})
    merged = trial_registry.main(["count", "--data", str(data), "--env", "ignored"], store_factory=lambda _: store)
    assert merged["configurations"] == PINNED + 40


def test_push_is_a_dry_run_until_yes(data: Path):
    store = Store({"trial": [], "trial_registry": []})
    dry = trial_registry.main(["push", "--data", str(data), "--env", "x"], store_factory=lambda _: store)
    assert dry["to_add"] == 9 and dry["added"] == 0 and store.tables["trial_registry"] == []
    done = trial_registry.main(["push", "--data", str(data), "--env", "x", "--yes"], store_factory=lambda _: store)
    assert done["added"] == 9 and len(store.tables["trial_registry"]) == 9
    again = trial_registry.main(["push", "--data", str(data), "--env", "x", "--yes"], store_factory=lambda _: store)
    assert again["to_add"] == 0 and again["added"] == 0 and again["already_in_database"] == 9


def test_push_says_so_when_the_migration_is_not_applied(data: Path):
    with pytest.raises(SystemExit, match="migration 0015"):
        trial_registry.main(["push", "--data", str(data), "--env", "x"], store_factory=lambda _: Store({}))
    with pytest.raises(SystemExit, match="needs --env"):
        trial_registry.main(["push", "--data", str(data)])


# ---------------------------------------------------------------- scripts/gate_v2_report.py


def write_run(folder: Path, seed: int = 4) -> dict[str, Path]:
    """What a multi-day walk-forward leaves behind: the account's daily CSV, its trades, plus SPY per session."""
    idx = pd.bdate_range("2017-05-01", "2026-11-06")
    rng = np.random.default_rng(seed)
    spy = rng.normal(0.0005, 0.011, len(idx))
    ret = 0.6 * spy + rng.normal(0.0002, 0.004, len(idx))
    end = 100_000.0 * np.cumprod(1 + ret)
    start = np.r_[100_000.0, end[:-1]]
    pd.DataFrame({"date": idx, "start": start, "end": end, "pnl": end - start, "trades": 1, "positions": 5,
                  "exposure": 0.6}).to_csv(folder / "daily.csv", index=False)
    pd.DataFrame({"date": idx, "ret": spy}).to_csv(folder / "spy.csv", index=False)
    entries = idx[::10][:-2]
    pd.DataFrame({"symbol": "AAA", "date": entries, "exit_date": entries + pd.offsets.BDay(8), "cost_spread": 4.0,
                  "cost_impact": 2.0, "cost_slippage": 50.0, "cost_fees": 1.0}).to_csv(folder / "trades.csv",
                                                                                       index=False)
    (folder / "control.json").write_text(json.dumps({"percentile": 96.0, "runs": 500, "matched": True}))
    return {k: folder / f for k, f in (("daily", "daily.csv"), ("spy", "spy.csv"), ("trades", "trades.csv"),
                                       ("random", "control.json"))}


def test_costs_from_trades_keep_the_total_and_leave_gap_slippage_out(tmp_path: Path):
    f = write_run(tmp_path)
    daily, trades = gate_v2_report.read_daily(f["daily"]), pd.read_csv(f["trades"])
    costs = gate_v2_report.costs_from_trades(trades, daily)
    assert float((costs * daily["start"]).sum()) == pytest.approx(len(trades) * (4.0 + 2.0 + 1.0))  # no slippage
    first = pd.Timestamp(trades["date"].iloc[0])
    assert costs[first] * daily["start"][first] == pytest.approx(3.0)  # half of spread and impact on the entry day
    assert (costs >= 0).all() and len(costs) == len(daily)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_gate_b_report_from_walk_forward_files(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    root, data = tmp_path / "repo", tmp_path / "data"
    (root / "config").mkdir(parents=True)
    data.mkdir()
    shutil.copy(ROOT / "config" / "gate_v2.yaml", root / "config" / "gate_v2.yaml")
    put(root, "my_rule", filled())
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "pre-register my_rule")
    f = write_run(data)
    (data / "trials.jsonl").write_text(json.dumps({"ts": "2030-01-01T00:00:00+00:00", "trial_id": "t1",
                                                   "strategy": "my_rule", "n_trials": 1035}) + "\n")
    args = ["--rule", "my_rule", "--daily", str(f["daily"]), "--spy", str(f["spy"]), "--trades", str(f["trades"]),
            "--random", str(f["random"]), "--neighbour", f"n0={f['daily']}", "--neighbour", f"n1={f['daily']}",
            "--data", str(data), "--root", str(root)]
    rep = gate_v2_report.main(args)
    assert rep["gate_sha"] == g2.GateV2.load(ROOT).sha and len(rep["prereg"]["sha256"]) == 64
    assert rep["verdict"] in ("provisional", "incomplete", "fail") and not rep["can_pass"]  # the research harness
    assert rep["source"]["cost_series"] == "approximated from the trade table"
    assert rep["source"]["engine"] == "research_harness" and rep["marks"]["mark_4"]["met"] is not None
    assert rep["marks"]["mark_3"]["numbers"]["percentile"] == 96.0 and rep["marks"]["mark_5"]["met"] is None
    assert rep["marks"]["mark_6"]["numbers"]["declared"] == ["n0", "n1"]
    assert rep["statistics"]["deflated_sharpe"]["n_trials"] == 1035
    assert rep["gate_a"]["committed_before_first_run"] is True  # the first logged run is after the commit
    written = sorted(p.name for p in (data / "reports").iterdir())
    assert len(written) == 2 and written[0].startswith("gate_b_my_rule_") and written[1].endswith(".md")
    text = (data / "reports" / written[1]).read_text()
    assert text in capsys.readouterr().out and rep["gate_sha"] in text and "(cannot pass)" in text
    assert json.loads((data / "reports" / written[0]).read_text())["rule"] == "my_rule"

    # the same run declared final, on the point-in-time universe: nothing provisional is left
    final = gate_v2_report.main([*args, "--universe", "point_in_time", "--engine", "simbroker", "--cost-model", "v2",
                                 "--data-repairs", "--benchmark", "spy_total_return"])
    assert final["provisional_reasons"] == [] and final["can_pass"] and final["marks"]["mark_5"]["met"] is not None
    assert final["verdict"] in ("pass", "fail")


def test_report_without_a_pre_registration_is_incomplete(tmp_path: Path, capsys):
    f = write_run(tmp_path)
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config" / "gate_v2.yaml", tmp_path / "config" / "gate_v2.yaml")
    rep = gate_v2_report.main(["--rule", "unregistered_rule", "--daily", str(f["daily"]), "--spy", str(f["spy"]),
                               "--data", str(tmp_path), "--root", str(tmp_path)])
    assert rep["prereg"] is None and rep["verdict"] in ("incomplete", "fail") and not rep["can_pass"]
    assert rep["marks"]["mark_4"]["met"] is None and rep["source"]["cost_series"] == "none"
    assert "no pre-registration for rule 'unregistered_rule'" in capsys.readouterr().err


def test_scripts_run_from_the_command_line(data: Path):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "trial_registry.py"), "count", "--data", str(data)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and json.loads(r.stdout)["configurations"] == PINNED
