"""S38: the automatic gate report. Pre-registered bar, frozen-code window, kill rules, random baseline, engine
day-end hook that never breaks trading."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from test_engine import fake_candidate, make, run_day, synth_frames

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_report as gr

BAR = gr.Bar.load(ROOT)
T0 = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def trades(n, mean, sd, start=T0, days=130, seed=1, strategy="dip_reversal"):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        t = start + timedelta(days=days * i / max(n, 1), minutes=5)
        out.append({"strategy": strategy, "entry_ts": t.isoformat(), "exit_ts": (t + timedelta(minutes=40)).isoformat(),
                    "pnl_usd": float(rng.normal(mean, sd))})
    return out


def sessions(start=T0, days=140, spy_sd=0.01, seed=2):
    rng = np.random.default_rng(seed)
    return [{"session": str((start + timedelta(days=i)).date()), "spy_ret": float(rng.normal(0.0, spy_sd))}
            for i in range(days)]


def random_for(tr, total_scale=0.0, fp="F1", runs=1000, seed=3):
    rng = np.random.default_rng(seed)
    days = sorted({t["entry_ts"][:10] for t in tr})
    return [{"strategy": "dip_reversal", "session": d, "fingerprint": fp,
             "totals": list(rng.normal(total_scale, 5.0, runs))} for d in days]


def ev(tr, rnd=None, starts=None, fp="F1", dd_cap=12.0, sess=None, trials=None):
    return gr.evaluate(BAR, strategies=["dip_reversal"], trades=tr, sessions=sess if sess is not None else sessions(),
                       random_days=rnd if rnd is not None else random_for(tr), starts=starts or [
                           {"ts": T0.isoformat(), "fingerprint": fp}], fingerprint_now=fp, amount=5000.0,
                       dd_cap_pct=dd_cap, trials=trials, as_of=T0 + timedelta(days=200))


def test_bar_matches_prd():
    b = BAR.cfg["bar"]
    got = (b["min_trades"], b["min_months"], b["t_min"], b["random_pct_min"], b["random_runs"])
    assert got == (900, 3.0, 3.0, 95.0, 1000)
    assert len(BAR.sha) == 64


def test_fingerprint_tracks_listed_files_only(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "docs.md").write_text("hello")
    f1 = gr.fingerprint(tmp_path, ["src/*.py"])
    (tmp_path / "docs.md").write_text("changed")
    assert gr.fingerprint(tmp_path, ["src/*.py"]) == f1
    (tmp_path / "src" / "a.py").write_text("x = 2\n")
    assert gr.fingerprint(tmp_path, ["src/*.py"]) != f1


def test_frozen_since_uses_last_unbroken_stretch():
    s = [{"ts": "2026-01-01T14:00:00Z", "fingerprint": "A"}, {"ts": "2026-01-02T14:00:00Z", "fingerprint": "A"},
         {"ts": "2026-01-03T14:00:00Z", "fingerprint": "B"}, {"ts": "2026-01-04T14:00:00Z", "fingerprint": "A"},
         {"ts": "2026-01-05T14:00:00Z", "fingerprint": "A"}]
    assert gr.frozen_since(s, "A") == datetime(2026, 1, 4, 14, tzinfo=UTC)
    assert gr.frozen_since(s, "C") is None


def test_provisional_below_900_never_passes():
    r = ev(trades(300, 5.0, 10.0))["strategies"]["dip_reversal"]
    assert r["status"] == "provisional" and r["trades_to_go"] == 600
    assert r["criteria"]["trades"]["met"] is False


def test_clear_edge_passes_and_opens_a_discussion():
    tr = trades(950, 4.0, 10.0)
    out = ev(tr, rnd=random_for(tr, total_scale=-1.0))
    r = out["strategies"]["dip_reversal"]
    assert r["status"] == "pass", r["criteria"]
    assert out["overall"] == "discuss"
    assert all(c["met"] for c in r["criteria"].values())


def test_missing_random_baseline_fails_closed():
    tr = trades(950, 4.0, 10.0)
    rnd = random_for(tr)[1:]  # one trade day without a baseline
    r = ev(tr, rnd=rnd)["strategies"]["dip_reversal"]
    assert r["criteria"]["random_pct"]["met"] is None and r["random_missing_days"]
    assert r["status"] == "fail"


def test_negative_expectancy_kills_after_50_trades():
    r = ev(trades(80, -6.0, 10.0))["strategies"]["dip_reversal"]
    assert r["status"] == "kill" and any("t-stat" in k for k in r["kills"])


def test_49_trades_cannot_kill_on_t():
    r = ev(trades(49, -6.0, 10.0))["strategies"]["dip_reversal"]
    assert r["status"] == "provisional" and not r["kills"]


def test_drawdown_breach_kills():
    tr = trades(60, -40.0, 5.0)  # -$2,400 on $5,000: beyond a 12% cap
    r = ev(tr)["strategies"]["dip_reversal"]
    assert r["status"] == "kill" and any("drawdown" in k for k in r["kills"])


def test_trades_before_code_change_are_excluded():
    old = trades(500, 4.0, 10.0, start=T0 - timedelta(days=200), days=150)
    new = trades(100, 4.0, 10.0)
    starts = [{"ts": (T0 - timedelta(days=200)).isoformat(), "fingerprint": "F0"}, {"ts": T0.isoformat(),
                                                                                    "fingerprint": "F1"}]
    r = ev(old + new, starts=starts)["strategies"]["dip_reversal"]
    assert r["n_trades"] == 100 and r["excluded_older_code"] == 500


def test_random_rows_from_other_code_are_ignored():
    tr = trades(950, 4.0, 10.0)
    r = ev(tr, rnd=random_for(tr, fp="OLD"))["strategies"]["dip_reversal"]
    assert r["criteria"]["random_pct"]["value"] is None


def test_deterministic():
    tr = trades(400, 1.0, 10.0)
    assert ev(tr) == ev(tr)


def test_trials_deflate_the_sharpe():
    tr = trades(950, 1.2, 10.0)
    lone = ev(tr)["strategies"]["dip_reversal"]["criteria"]["deflated_sharpe"]["value"]
    many = ev(tr, trials={"dip_reversal": list(np.random.default_rng(5).normal(0, 0.08, 200))})
    assert many["strategies"]["dip_reversal"]["criteria"]["deflated_sharpe"]["value"] < lone
    assert many["strategies"]["dip_reversal"]["n_trials"] == 200


def test_load_trials_reads_daily_sharpe(tmp_path: Path):
    f = tmp_path / "trials.jsonl"
    f.write_text('{"strategy": "dip_reversal", "metrics": {"daily_sharpe_ann": 1.587}}\nnot json\n')

    class S:
        def select(self, *a):
            return [{"strategy": "dip_reversal", "metrics": {"daily_sharpe_ann": None}}]

    out = gr.load_trials(S(), f)
    assert out == {"dip_reversal": [pytest.approx(1.587 / np.sqrt(252))]}


def test_engine_writes_random_baseline_and_report(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc)
    rnd = store.tables.get("gate_random", [])
    rep = store.tables.get("gate_report", [])
    assert len(rnd) == 1 and len(rnd[0]["totals"]) == 1000 and rnd[0]["fingerprint"] == eng.fingerprint
    assert rnd[0]["n_trades"] == 1 and np.std(rnd[0]["totals"]) > 0  # a real simulation, not a zero placeholder
    assert len(rep) == 1 and rep[0]["bar_sha"] == BAR.sha
    st = rep[0]["report"]["strategies"]["dip_reversal"]
    assert st["status"] == "provisional"
    starts = [e for e in store.tables["event"] if e["type"] == "engine_start"]
    assert starts and starts[0]["payload"]["fingerprint"] == eng.fingerprint


def test_gate_failure_never_breaks_the_day(tmp_path, monkeypatch):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    monkeypatch.setattr(gr, "day_random", lambda *a, **k: 1 / 0)
    run_day(eng, broker, clock, open_utc)
    assert eng.day.finalized
    assert not store.tables.get("gate_report")


def test_restart_never_recomputes_a_days_baseline(tmp_path):
    eng, broker, clock, store, open_utc = make(tmp_path, synth_frames())
    fake_candidate(eng)
    run_day(eng, broker, clock, open_utc)
    first = list(store.tables["gate_random"][0]["totals"])
    gr.on_day_end(eng, ROOT)  # e.g. a restart after the close finalises again
    assert len(store.tables["gate_random"]) == 1 and store.tables["gate_random"][0]["totals"] == first
    assert len(store.tables["gate_report"]) == 2  # reports are appended, each one a snapshot
