"""Issue #75: the replay runner's feed / fills flags, the rerun command and the replay_004 table, all offline on
synthetic Parquet bars (no keys, no network: the VIX cache is pre-written)."""

from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from test_determinism import market

from kaibot.replay import runner, sip_rerun
from kaibot.replay.compare import NOT_RUN, render
from kaibot.replay.data import ET

ROOT = Path(__file__).resolve().parents[2]
WINDOW = ["--start", "2025-04-01", "--end", "2025-06-30", "--data-from", "2025-01-01", "--runs", "3",
          "--variant", "A"]


def write_symbol(data: Path, sd, thin: bool = False) -> None:
    rows = []
    for r, d in enumerate(sd.dates):
        ts = pd.Timestamp(f"{d} 09:30", tz=ET).tz_convert("UTC") + pd.to_timedelta(np.arange(390), unit="m")
        rows.append(pd.DataFrame({"ts": ts, "o": sd.o[r], "h": sd.h[r], "l": sd.l[r], "c": sd.c[r],
                                  "v": sd.v[r] * (0.02 if thin else 1.0), "vw": sd.vw[r]}))
    f = data / "bars_1m" / f"symbol={sd.symbol}"
    f.mkdir(parents=True)
    pd.concat(rows).to_parquet(f / "2025-01.parquet")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """A 'SIP' data directory (bars, VIX cache, .env, universe) and a thin-volume 'IEX' bars directory."""
    data, iex = tmp_path_factory.mktemp("sip"), tmp_path_factory.mktemp("iex")
    stocks, etfs, sector_of, spy = market(11)
    for sd in [*stocks.values(), *etfs.values(), spy]:
        write_symbol(data, sd)
        write_symbol(iex, sd, thin=True)
    (data / "inputs").mkdir()
    days = pd.date_range("2024-12-01", "2025-07-01")
    (data / "inputs" / "fred_vixcls.json").write_text(
        json.dumps([{"date": str(d.date()), "value": "18.0"} for d in days]))
    env = data / ".env"
    env.write_text("# no keys\n")
    uni = data / "universe.yaml"
    sectors: dict[str, list[str]] = {}
    for s, e in sector_of.items():
        sectors.setdefault(e, []).append(s)
    uni.write_text(yaml.safe_dump({"market": "SPY", "sectors": sectors}))
    base = ["--env", str(env), "--data", str(data), "--universe", str(uni), *WINDOW]
    return {"data": data, "iex": iex, "base": base, "env": env, "uni": uni}


def run(world, *extra):
    return runner.run(runner.build_parser().parse_args([*world["base"], *extra]))


def trial_rows(data: Path) -> list[dict]:
    return [json.loads(x) for x in (data / "trials.jsonl").read_text().splitlines()]


@pytest.fixture(scope="module")
def reports(world):
    return {
        "plain": run(world, "--label", "plain"),
        "plain2": run(world, "--label", "plain2"),
        "sip": run(world, "--label", "sip", "--feed", "sip"),
        "real": run(world, "--label", "real", "--feed", "sip", "--fills", "realistic"),
        "costly": run(world, "--label", "costly", "--feed", "sip", "--fills", "realistic", "--slippage-bps", "5"),
        "iex": run(world, "--label", "iex", "--feed", "iex", "--fills", "realistic", "--bars-data", str(world["iex"])),
    }


def test_unflagged_run_has_no_new_fields_and_is_reproducible(world, reports):
    a, b = reports["plain"], reports["plain2"]
    assert a["trades"] >= 5
    assert (a["input_sha256"], a["events_sha256"]) == (b["input_sha256"], b["events_sha256"])
    assert not {"feed", "fills", "bars_sha256"} & set(a)
    row = next(r for r in trial_rows(world["data"]) if r["label"] == "plain")
    assert not {"feed", "fills"} & set(row)
    assert len(a["caveats"]) == 5


def test_feed_label_alone_changes_the_input_hash_but_not_one_event(reports):
    plain, sip = reports["plain"], reports["sip"]
    assert sip["events_sha256"] == plain["events_sha256"] and sip["trades"] == plain["trades"]
    assert sip["input_sha256"] != plain["input_sha256"]  # the feed is part of the recorded input
    assert sip["feed"] == "sip" and sip["fills"] == {"name": "baseline"}


def test_every_run_logs_one_trial_row_with_its_feed_and_fills(world, reports):
    rows = {r["label"]: r for r in trial_rows(world["data"])}
    assert set(reports) <= set(rows) and len(trial_rows(world["data"])) == len(rows)
    assert rows["real"]["feed"] == "sip" and rows["real"]["fills"]["name"] == "realistic"
    assert rows["real"]["fills"]["participation"] == 0.05 and rows["costly"]["fills"]["slippage_bps"] == 5.0
    assert rows["iex"]["feed"] == "iex"
    for label, r in rows.items():
        assert (world["data"] / "trials" / f"{r['trial_id']}.csv").exists(), label
        assert r["window"][0] == "2025-04-01" and r["variant"] == "A"


def test_realistic_fills_and_extra_cost_are_recorded_and_cost_money(reports):
    real, costly = reports["real"], reports["costly"]
    assert real["events_sha256"] != costly["events_sha256"]
    assert costly["net_pnl_usd"] < real["net_pnl_usd"]
    assert "fills (realistic)" in real["caveats"][-1]


def test_bars_can_come_from_another_directory(reports):
    assert reports["iex"]["bars_sha256"] != reports["real"]["bars_sha256"]
    assert reports["iex"]["feed"] == "iex"


def test_sip_bars_of_today_are_refused(world):
    a = runner.build_parser().parse_args([*world["base"], "--feed", "sip", "--label", "tooearly"])
    with pytest.raises(SystemExit, match="15 min"):
        runner.run(a, now=datetime(2025, 6, 30, 20, 30, tzinfo=UTC))  # 16:30 ET on the last session
    assert "tooearly" not in {r["label"] for r in trial_rows(world["data"])}


def test_sip_age_rule_uses_the_new_york_date():
    last = np.datetime64("2025-06-30")
    runner.assert_sip_aged(last, datetime(2025, 7, 1, 4, 1, tzinfo=UTC))  # 00:01 ET the next day
    with pytest.raises(SystemExit):
        runner.assert_sip_aged(last, datetime(2025, 7, 1, 3, 59, tzinfo=UTC))  # still 30 June in New York


def test_script_wrapper_keeps_its_names():
    spec = importlib.util.spec_from_file_location("run_replay", ROOT / "scripts" / "run_replay.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main is runner.main and mod.load_env is runner.load_env and mod.vix_prev_close is runner.vix_prev_close


# --- the rerun command --------------------------------------------------------------------------------------------

def rerun_args(*extra):
    return sip_rerun.build_parser().parse_args(["--env", "e", "--sip-data", "sipdir", *extra])


def test_rerun_uses_the_original_parameters_for_every_window():
    seen = []
    sip_rerun.run_all(rerun_args(), run_fn=lambda a: seen.append(a) or {})
    assert len(seen) == 10  # 5 windows x (baseline, realistic) on SIP
    defaults = runner.build_parser().parse_args(["--env", "e"])
    got = {(a.variant, a.start, a.end) for a in seen}
    assert got == {("v1", "2025-02-01", "2026-11-06"), ("A", "2025-02-01", "2026-06-30"),
                   ("A", "2026-07-01", "2026-11-06"), ("B", "2025-02-01", "2026-06-30"),
                   ("B", "2026-07-01", "2026-11-06")}  # docs/m0/reproduce.md
    for a in seen:
        assert (a.data_from, a.level, a.runs, a.equity, a.universe) == ("2025-01-01", "balanced", 200,
                                                                         defaults.equity, defaults.universe)
        assert a.feed == "sip" and a.bars_data == "sipdir" and a.data == "sipdir"
    assert len({a.label for a in seen if a.variant == "A"}) == 4  # labels keep trial ids apart
    assert {a.fills for a in seen} == {"baseline", "realistic"}


def test_rerun_with_iex_and_a_cost_sweep():
    seen = []
    res = sip_rerun.run_all(rerun_args("--iex-data", "iexdir", "--sweep-bps", "1,5", "--only", "A:holdout"),
                            run_fn=lambda a: seen.append(a) or {"label": a.label})
    assert list(res) == ["A:holdout"]
    assert list(res["A:holdout"]) == ["iex_baseline", "iex_realistic", "sip_baseline", "sip_realistic",
                                      "sip_realistic_1bp", "sip_realistic_5bp"]
    iex = [a for a in seen if a.feed == "iex"]
    assert len(iex) == 2 and all(a.bars_data == "iexdir" and a.data == "sipdir" for a in iex)
    assert [a.slippage_bps for a in seen] == [0, 0, 0, 0, 1, 5]


def test_rerun_refuses_to_compare_a_directory_with_itself_or_an_unknown_window(tmp_path):
    with pytest.raises(SystemExit, match="same directory"):
        sip_rerun.run_all(rerun_args("--iex-data", "./sipdir"), run_fn=lambda a: {})
    with pytest.raises(SystemExit, match="unknown window"):
        sip_rerun.run_all(rerun_args("--only", "C:design"), run_fn=lambda a: {})


def test_rerun_end_to_end_writes_the_table(world, tmp_path, monkeypatch):
    """The real command on synthetic bars, with the windows moved onto the fixture's dates."""
    win = (sip_rerun.Window("002", "A", "design", "2025-04-01", "2025-05-31", "none.json"),
           sip_rerun.Window("002", "A", "holdout", "2025-06-01", "2025-06-30", "none.json"))
    monkeypatch.setattr(sip_rerun, "WINDOWS", win)
    real_args = sip_rerun.replay_args

    def small(w, c, a):
        ns = real_args(w, c, a)
        return argparse.Namespace(**{**vars(ns), "universe": str(world["uni"]), "runs": 2})

    monkeypatch.setattr(sip_rerun, "replay_args", small)
    before = len(trial_rows(world["data"]))
    out = tmp_path / "replay_004.md"
    sip_rerun.main(["--env", str(world["env"]), "--sip-data", str(world["data"]), "--iex-data", str(world["iex"]),
                    "--out", str(out)])
    assert len(trial_rows(world["data"])) == before + 8  # 2 windows x 2 feeds x 2 fill models
    saved = json.loads(out.with_suffix(".json").read_text())["results"]
    md = out.read_text()
    assert "**Status: run " in md and "8 replays" in md
    for w in win:
        for rep in saved[w.key].values():
            assert f"{rep['net_return_pct']:+.2f}%" in md
    # thin 'IEX' volume: the 5% cap bites, so realistic fills change the IEX run more than the SIP run
    d = saved["A:design"]
    assert d["sip_baseline"]["feed"] == "sip" and d["iex_realistic"]["feed"] == "iex"
    assert d["iex_baseline"]["bars_sha256"] != d["sip_baseline"]["bars_sha256"]


# --- the document -------------------------------------------------------------------------------------------------

REP = {"trades": 12, "net_return_pct": -1.234, "t_stat_per_trade": -0.5, "profit_factor": 0.9,
       "strategy_percentile_vs_random": 61.5, "code_sha": "abc1234", "bars_sha256": "x"}


def test_a_run_that_did_not_happen_is_never_given_a_number():
    cfgs = sip_rerun.configs(True)
    md = render(sip_rerun.WINDOWS, {}, {}, cfgs)
    assert "**Status: not run yet.**" in md
    rows = [ln for ln in md.splitlines() if ln.startswith(("| Symbols", "| Trades", "| Net return", "| t-stat",
                                                           "| Profit", "| Percentile"))]
    assert len(rows) == 30
    for ln in rows:
        assert ln.split(" | ")[1:] == [NOT_RUN] * 4 + [f"{NOT_RUN} |"]


def test_measured_cells_come_from_the_reports_only():
    cfgs = sip_rerun.configs(True)
    md = render(sip_rerun.WINDOWS, {"A:design": {"sip_realistic": REP}}, {"A:design": {**REP, "trades": 750}}, cfgs,
                {"run_at": "2026-11-12T01:00:00+00:00", "runs": 200})
    sect = md.split("### Replay 002: variant A, design")[1].split("###")[0]
    assert f"| Trades | 750 | {NOT_RUN} | {NOT_RUN} | {NOT_RUN} | 12 |" in sect
    assert f"| Net return (on $100k) | -1.23% | {NOT_RUN} | {NOT_RUN} | {NOT_RUN} | -1.23% |" in sect
    assert "abc1234" in md and "1 replays" in md
    other = md.split("### Replay 003: variant B, holdout")[1]
    assert "12" not in other.split("## Reading the table")[0]


def test_empty_and_infinite_metrics_render_without_inventing_values():
    rep = {**REP, "trades": 0, "t_stat_per_trade": None, "profit_factor": float("inf")}
    md = render(sip_rerun.WINDOWS[:1], {"v1:full": {"sip_baseline": rep}}, {}, sip_rerun.configs(False))
    assert "| t-stat per trade | not run | — | not run |" in md
    assert "| Profit factor | not run | no losing trades | not run |" in md


def test_identical_iex_and_sip_bars_are_flagged():
    res = {"A:design": {"iex_baseline": REP, "sip_baseline": REP}}
    assert "not an IEX-feed download" in render(sip_rerun.WINDOWS, res, {}, sip_rerun.configs(True))
    res["A:design"]["iex_baseline"] = {**REP, "bars_sha256": "y"}
    assert "Warning" not in render(sip_rerun.WINDOWS, res, {}, sip_rerun.configs(True))


def test_committed_replay_004_is_the_generated_document():
    """Until the real run exists (replay_004.json), the committed page is exactly the generator's 'not run' page:
    the recorded column from docs/m0/replay_00*.json and no other number."""
    docs = ROOT / "docs" / "m0"
    if (docs / "replay_004.json").exists():
        pytest.skip("the rerun has been done; the page now holds its results")
    recorded = sip_rerun.load_recorded(docs)
    assert len(recorded) == 5
    assert (docs / "replay_004.md").read_text() == render(sip_rerun.WINDOWS, {}, recorded, sip_rerun.configs(True))
