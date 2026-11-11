"""M5 S48: the config replay queue. Without history the job waits (never a fake result); with a replay it logs a
trial row; a broken job does not stop the queue."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from fakes import FakeStore

from kaibot.replay.config_replay import NoData, have_data

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_config_replays", ROOT / "scripts" / "run_config_replays.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def seeded():
    st = FakeStore()
    st.insert("run", {"id": 2, "level": "balanced", "status": "running"})
    st.insert("config", {"run_id": 2, "version": 1, "body": {"risk": {"max_positions": 3}}, "status": "applied"})
    st.insert("config", {"run_id": 2, "version": 2, "body": {"risk": {"risk_per_trade_pct": 9}}, "status": "applied"})
    st.insert("replay_job", {"run_id": 2, "config_version": 1, "status": "queued"})
    st.insert("replay_job", {"run_id": 2, "config_version": 2, "status": "queued"})
    return st


def test_no_history_waits_for_data(tmp_path):
    st = seeded()
    out = mod.process(st, tmp_path)
    jobs = {j["config_version"]: j for j in st.tables["replay_job"]}
    assert jobs[1]["status"] == "waiting_for_data" and "fetch_history" in jobs[1]["result"]["reason"]
    assert jobs[2]["status"] == "error"  # a looser config can't even be replayed
    assert not st.tables.get("trial")
    assert {o["status"] for o in out} == {"waiting_for_data", "error"}
    assert not have_data(tmp_path, ["SPY"])


def test_replay_logs_a_trial(tmp_path):
    st = seeded()
    seen = []

    def fake_replay(rules, cfg, data_dir, fomc):
        seen.append(rules.policy.max_positions)
        return {"trades": 42, "net_return_pct": 0.5, "t_stat_per_trade": 0.8, "daily_sharpe_ann": 0.4}

    mod.process(st, tmp_path, replay=fake_replay)
    j1 = next(j for j in st.tables["replay_job"] if j["config_version"] == 1)
    assert j1["status"] == "done" and j1["result"]["trades"] == 42 and seen == [3]
    (trial,) = st.tables["trial"]
    assert trial["params"]["config_version"] == 1 and trial["metrics"]["daily_sharpe_ann"] == 0.4
    assert (tmp_path / "trials.jsonl").read_text().count("config_r2_v1") == 1


def test_waiting_job_is_retried(tmp_path):
    st = seeded()
    mod.process(st, tmp_path)

    def ok(rules, cfg, data_dir, fomc):
        return {"trades": 1, "net_return_pct": 0, "t_stat_per_trade": 0, "daily_sharpe_ann": 0}

    mod.process(st, tmp_path, replay=ok)
    assert next(j for j in st.tables["replay_job"] if j["config_version"] == 1)["status"] == "done"


def test_nodata_is_a_runtime_error():
    assert issubclass(NoData, RuntimeError)
