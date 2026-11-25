"""Treasury-bill rate as the return on idle cash and in the Sharpe (M11 ticket 2.4, issue #455), on inputs whose
answer is known. Gate v2's marks must not move: the bill rate adds a block to the report, nothing else."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from kaibot.engine.config import ROOT
from kaibot.evaluation import gate_v2 as g2
from kaibot.evaluation import mtm, tbill

sys.path.insert(0, str(ROOT / "scripts"))
import fetch_tbill  # noqa: E402
import gate_v2_report  # noqa: E402
from test_gate_v2 import GATE, IDX, SPY, judge, met, run  # noqa: E402
from test_gate_v2_scripts import write_run  # noqa: E402

DAYS = pd.date_range("2016-12-01", "2026-11-20", freq="D")
FLAT_4 = pd.Series(4.0, index=DAYS[DAYS.dayofweek < 5])  # a 4% discount rate on every weekday


# ---------------------------------------------------------------- the rate, point in time


def test_discount_rate_becomes_the_higher_investment_yield():
    assert mtm.discount_to_yield(5.0) == pytest.approx(0.05 * 365 / (360 - 0.05 * 91))
    assert round(float(mtm.discount_to_yield(5.0)) * 100, 2) == 5.13
    assert mtm.discount_to_yield(0.0) == 0.0


def test_a_session_earns_the_rate_published_before_it_for_the_calendar_days_since_the_last_session():
    obs = pd.Series([1.0, 2.0, 3.0, 9.0], index=pd.to_datetime(["2024-01-03", "2024-01-04", "2024-01-05",
                                                                "2024-01-08"]))
    sessions = pd.to_datetime(["2024-01-04", "2024-01-05", "2024-01-08"])  # Thursday, Friday, Monday
    rf = mtm.tbill_daily(obs, sessions)
    y = mtm.discount_to_yield
    assert rf.iloc[0] == pytest.approx(y(1.0) / 365)  # the first session: one day at the rate of the day before
    assert rf.iloc[1] == pytest.approx(y(2.0) / 365)  # Friday earns Thursday's rate, not its own
    assert rf.iloc[2] == pytest.approx(y(3.0) * 3 / 365)  # Monday: Friday's rate for three calendar days
    # no look-ahead: the rate of a session's own day, and of any later day, does not change that session
    later = obs.copy()
    later.loc["2024-01-08"] = 0.0
    later.loc["2024-01-05"] = 7.0
    assert mtm.tbill_daily(later, sessions).iloc[:2].tolist() == rf.iloc[:2].tolist()


def test_a_newer_file_gives_the_same_numbers_for_the_sessions_both_cover():
    old = mtm.tbill_daily(FLAT_4[:"2024-06-28"], IDX[IDX <= "2024-06-28"])
    new = mtm.tbill_daily(FLAT_4, IDX)
    assert new[old.index].tolist() == old.tolist()


def test_a_hole_or_a_start_after_the_first_session_is_an_error_not_an_old_rate():
    with pytest.raises(ValueError, match="no Treasury-bill rate before"):
        mtm.tbill_daily(FLAT_4["2018-01-01":], IDX)
    holed = FLAT_4.drop(FLAT_4["2020-03-01":"2020-03-31"].index)
    with pytest.raises(ValueError, match="days old"):
        mtm.tbill_daily(holed, IDX)
    with pytest.raises(ValueError, match="empty"):
        mtm.tbill_daily(FLAT_4[:0], IDX)


# ---------------------------------------------------------------- idle cash


def test_an_idle_account_earns_bills_and_a_fully_invested_one_is_unchanged():
    rf = mtm.tbill_daily(FLAT_4, IDX).to_numpy()
    idle = mtm.with_cash_yield(np.zeros(len(IDX)), np.zeros(len(IDX)), rf)
    assert idle.tolist() == rf.tolist()
    # about 4.1% a year (the yield of a 4% discount), on calendar days
    years = (IDX[-1] - IDX[0]).days / 365
    assert mtm.total_return(idle) == pytest.approx((1 + float(mtm.discount_to_yield(4.0)) / 365) ** (years * 365) - 1,
                                                    rel=2e-3)
    assert mtm.sharpe_hac(idle, rf=rf).sharpe == 0.0  # cash has no excess return
    full = SPY.to_numpy()
    invested = np.ones(len(IDX))
    got = mtm.with_cash_yield(full, invested, rf)
    assert got[1:].tolist() == full[1:].tolist()  # invested from the first close on: no interest
    assert got[0] == full[0] + rf[0]  # before the first session the book was flat


def test_idle_share_is_yesterdays_and_never_negative():
    assert mtm.idle_share([0.25, 1.02, 0.5]).tolist() == [1.0, 0.75, 0.0]


def test_half_in_spy_half_in_bills_has_no_alpha_and_gains_half_the_bill_rate():
    rf = mtm.tbill_daily(FLAT_4, IDX).to_numpy()
    rng = np.random.default_rng(3)
    spy = rng.normal(0.0005, 0.01, len(IDX))
    half = np.full(len(IDX), 0.5)
    rule = mtm.exposure_matched(spy, half)  # half of SPY, the rest idle at 0%
    v = mtm.cash_views(rule, spy, half, rf)
    assert v["mean_idle_share"] == pytest.approx(0.5, abs=1e-3)
    bill = v["tbill_cash"]
    # zero but for the first session, on which the book is still flat while SPY moves (about 0.1% a year here)
    assert bill["alpha_beta"]["alpha"] == pytest.approx(0.0, abs=1.5e-3) and abs(bill["alpha_beta"]["alpha_t"]) < 1
    assert bill["alpha_beta"]["beta"] == pytest.approx(0.5, abs=1e-3)
    # half the bill rate, compounding on top of the rule's own growth: (1 + g) * (1 + c) - (1 + g) = c * (1 + g)
    assert bill["cash_added_cagr"] == pytest.approx(
        0.5 * v["rf"]["mean_ann"] * (1 + v["zero_cash"]["rule"]["cagr"]), rel=0.03)
    assert bill["rule"]["cagr"] == pytest.approx(bill["exposure_matched"]["cagr"])  # it IS SPY at its exposure
    # the benchmark's return is the same in both versions; only its Sharpe is measured above bills
    assert bill["benchmark"]["cagr"] == v["zero_cash"]["benchmark"]["cagr"]
    assert bill["benchmark"]["sharpe"]["sharpe"] < v["zero_cash"]["benchmark"]["sharpe"]["sharpe"]


def test_without_an_exposure_series_only_the_excess_sharpe_is_shown():
    rf = mtm.tbill_daily(FLAT_4, IDX).to_numpy()
    v = mtm.cash_views(SPY.to_numpy() * 0.5, SPY.to_numpy(), None, rf)
    assert v["tbill_cash"]["rule"] is None and v["mean_idle_share"] is None
    assert v["tbill_cash"]["simulated_excess"]["sharpe"]["sharpe"] < v["zero_cash"]["rule"]["sharpe"]["sharpe"]


def test_the_risk_free_rate_is_one_number_or_one_value_per_session():
    r = np.random.default_rng(0).normal(0.0005, 0.01, 300)
    assert mtm.sharpe_hac(r, rf=np.full(300, 0.0001)).sharpe == pytest.approx(mtm.sharpe_hac(r, rf=0.0001).sharpe)
    assert mtm.sharpe_hac(r).sharpe == mtm.sharpe_hac(r, rf=0.0).sharpe
    with pytest.raises(ValueError, match="one per session"):
        mtm.sharpe_hac(r, rf=np.zeros(299))
    with pytest.raises(ValueError, match="one per session"):
        mtm.alpha_beta(r, r, rf=np.zeros(5))


# ---------------------------------------------------------------- gate v2: both versions, no mark moved


def test_the_bill_rate_adds_a_block_and_moves_no_gate_v2_mark():
    exposure = pd.Series(0.6, index=IDX)
    plain, both = judge(run(exposure=exposure)), judge(run(exposure=exposure), tbill=FLAT_4)
    assert met(plain) == met(both) and plain["verdict"] == both["verdict"]
    drop = lambda rep: {k: v for k, v in rep["statistics"].items() if k != "cash_yield"}  # noqa: E731
    assert drop(plain) == drop(both) and plain["marks"] == both["marks"]
    assert plain["statistics"]["cash_yield"] is None
    c = both["statistics"]["cash_yield"]
    assert c["zero_cash"]["rule"] == both["statistics"]["rule"]  # the old version, as it always was
    assert c["tbill_cash"]["rule"]["cagr"] > c["zero_cash"]["rule"]["cagr"]
    assert c["tbill_cash"]["rule"]["cagr"] - c["zero_cash"]["rule"]["cagr"] == pytest.approx(
        0.4 * c["rf"]["mean_ann"] * (1 + c["zero_cash"]["rule"]["cagr"]), rel=0.03)  # 40% idle, compounding
    text = g2.render_gate_b(both)
    assert "Cash at 0%, Sharpe against 0" in text and "Cash in bills, Sharpe above bills" in text
    assert "The marks above use the 0% version" in text
    assert "not computed (no bill-rate file" in g2.render_gate_b(plain)
    json.dumps(both)  # still plain JSON


def test_gate_v2_yaml_is_untouched_by_this_ticket():
    cfg = yaml.safe_load((ROOT / "config" / "gate_v2.yaml").read_text())
    assert "cash" not in json.dumps(cfg, default=str).lower() and GATE.cfg == cfg


# ---------------------------------------------------------------- the file and the scripts


def fred(rows) -> list[dict]:
    return [{"realtime_start": "2026-11-21", "date": d, "value": v} for d, v in rows]


def test_fetch_writes_the_cache_and_status_reads_it(tmp_path: Path, capsys):
    rows = [("2026-11-18", "3.80"), ("2026-11-19", "."), ("2026-11-20", "3.79")]  # "." is FRED's closed day
    out = fetch_tbill.main(["--data", str(tmp_path)], fetch=lambda: fred(rows))
    assert out["cached"] and out["observations"] == 2 and out["last"] == "2026-11-20" and out["last_rate_pct"] == 3.79
    s = tbill.find(tmp_path)
    assert s.tolist() == [3.80, 3.79] and str(s.index[0].date()) == "2026-11-18"
    assert fetch_tbill.main(["--data", str(tmp_path), "--status"])["fetched_at"] == out["fetched_at"]
    assert fetch_tbill.main(["--data", str(tmp_path / "none"), "--status"]) == {
        "file": str(tmp_path / "none" / tbill.FILE), "cached": False}
    with pytest.raises(SystemExit, match="needs --env"):
        fetch_tbill.main(["--data", str(tmp_path)])
    assert tbill.find(tmp_path / "none") is None


def test_a_failed_fetch_exits_in_one_line_and_never_shows_the_key(tmp_path: Path):
    def boom():
        raise RuntimeError("GET https://api.stlouisfed.org/fred/series/observations?api_key=SECRETKEY123 failed")

    with pytest.raises(SystemExit) as e:
        fetch_tbill.main(["--data", str(tmp_path)], fetch=boom)
    assert str(e.value) == "FAILED: RuntimeError; nothing was written" and tbill.find(tmp_path) is None


def test_a_wrong_series_or_unit_is_refused(tmp_path: Path):
    with pytest.raises(ValueError, match="outside -1 to 25"):
        tbill.write_cache(tmp_path / "x.json", fred([("2026-11-20", "379")]))  # basis points, not percent
    with pytest.raises(ValueError, match="no observation"):
        tbill.write_cache(tmp_path / "x.json", fred([("2026-11-20", ".")]))
    with pytest.raises(ValueError, match="same date twice"):
        tbill.write_cache(tmp_path / "x.json", fred([("2026-11-20", "1"), ("2026-11-20", "2")]))
    (tmp_path / "y.json").write_text(json.dumps({"series": "VIXCLS", "observations": []}))
    with pytest.raises(ValueError, match="expected DTB3"):
        tbill.read_cache(tmp_path / "y.json")


def test_the_fred_source_is_declared_under_the_fred_budget():
    src = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
    flat = json.dumps(src)
    assert "fred_dtb3" in flat and "counts against fred" in flat and "never in the live path" in flat


def test_gate_b_report_script_shows_both_versions_when_the_file_is_there(tmp_path: Path):
    root, data = tmp_path / "repo", tmp_path / "data"
    (root / "config").mkdir(parents=True)
    data.mkdir()
    (root / "config" / "gate_v2.yaml").write_bytes((ROOT / "config" / "gate_v2.yaml").read_bytes())
    f = write_run(data)
    args = ["--rule", "no_such_rule", "--daily", str(f["daily"]), "--spy", str(f["spy"]), "--data", str(data),
            "--root", str(root)]
    before = gate_v2_report.main(args)
    assert before["statistics"]["cash_yield"] is None and before["inputs"]["tbill"] is None
    tbill.write_cache(data / tbill.FILE, [{"date": str(d.date()), "value": 4.0} for d in FLAT_4.index],
                      datetime(2026, 11, 21, tzinfo=UTC))
    after = gate_v2_report.main(args)
    c = after["statistics"]["cash_yield"]
    assert c["mean_idle_share"] == pytest.approx(0.4, abs=1e-3) and after["inputs"]["tbill"].endswith("fred_dtb3.json")
    assert {k: v["met"] for k, v in after["marks"].items()} == {k: v["met"] for k, v in before["marks"].items()}
    assert gate_v2_report.main([*args, "--no-tbill"])["statistics"]["cash_yield"] is None


# ---------------------------------------------------------------- every gate call gets the rate (#455)

GATE_ENTRIES = ("evaluate_gate_b", "evaluate_sleeve", "evaluate_book")


def test_every_script_that_runs_a_gate_hands_it_the_bill_rate():
    """A run script that calls a gate without `tbill=` writes a report with idle cash at 0% only. spy_engine.py
    (the SimBroker go/no-go run) and spy_trend.py did until #455; this keeps a new script from doing it."""
    import ast

    missing, calls = [], 0
    for f in sorted((ROOT / "scripts").glob("*.py")):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in GATE_ENTRIES:
                calls += 1
                if "tbill" not in {k.arg for k in node.keywords}:
                    missing.append(f"{f.name}:{node.lineno} {node.func.attr}")
    assert calls >= 8 and missing == []


def test_rates_after_the_last_session_never_change_a_gate_report():
    """Point in time: a bill file that goes on past the run (here into the hold-out, at an absurd 25%) gives the
    same cash block as one cut before the last session. Session t only ever reads a rate dated before t."""
    net, spy = run().net, SPY
    idx = net.index
    cut = FLAT_4[FLAT_4.index < idx[-1]]
    longer = pd.concat([cut, pd.Series(25.0, index=pd.bdate_range(idx[-1], "2026-11-20"))])
    exposure = pd.Series(0.6, index=idx)
    a = g2.statistics(GATE, net, spy, exposure=exposure, tbill=cut)["cash_yield"]
    b = g2.statistics(GATE, net, spy, exposure=exposure, tbill=longer)["cash_yield"]
    assert a == b and a["mean_idle_share"] == pytest.approx(0.4, abs=1e-3)
