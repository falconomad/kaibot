"""#454 (M11 2.3): the re-run that prices a daily rule's near-close orders at the 15:50 price
(docs/prereg/trend_momentum.md Amendment 31; decision 15 section 12, question 1).

What must hold:
- the default execution and every existing run are unchanged; the 15:50 pricing is opt-in (`execution="at_1550"`)
  and refuses to run without 15:50 prices;
- a near-close order then fills at the 15:50 minute's price (the open of the first minute bar at or after 15:50,
  v2's continuous venue) and, where a session has no such minute, at the closing print, counted;
- at zero costs and a 15:50 price equal to the close the two executions give the same account;
- the minute reader never reads a month after `end` (the hold-out) and puts the minute history on the store's raw
  basis;
- the script counts one configuration, writes its report, and is on the runner's allow-list with a strict pattern.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kaibot.engine import daily_report as dr
from kaibot.engine.config import ROOT
from kaibot.engine.daily import DailyData, run_daily, sessions_window
from kaibot.replay import minute_1550 as m1550
from kaibot.risk.profiles import load_profiles
from kaibot.strategies import momentum as mom

sys.path.insert(0, str(ROOT / "scripts"))
import near_close_1550_rerun as script  # noqa: E402
import research_runner as rr  # noqa: E402

RULE = "trend_momentum"


@pytest.fixture(scope="module")
def fixture_data():
    market, info = dr.load_fixture(dr.FIXTURE)
    data = DailyData.from_market(market)
    i0, i1 = sessions_window(data, info["window"][0], info["window"][1])
    return data, i0, i1


def _run(data, i0, i1, **kw):
    return run_daily(mom.VERSIONS[RULE], data, load_profiles()["balanced"], i0, i1, cost_model="v2", **kw)


def _with(data, p, v):
    return data.with_1550(p, v)


# ---------------------------------------------------------------- the engine


def test_the_default_execution_is_unchanged_and_at_1550_needs_prices(fixture_data):
    data, i0, i1 = fixture_data
    assert data.raw_1550 is None and data.v_1550 is None
    with pytest.raises(ValueError, match="15:50"):
        _run(data, i0, i1, execution="at_1550")
    with pytest.raises(ValueError, match="base fill"):
        _run(_with(data, data.raw_c, data.v_last), i0, i1, execution="at_1550", fill="stress")
    with pytest.raises(ValueError):
        data.with_1550(data.raw_c[:-1], data.v_last)  # the shape must be the data's


def test_at_zero_costs_a_1550_price_equal_to_the_close_gives_the_same_account(fixture_data):
    data, i0, i1 = fixture_data
    near = _run(data, i0, i1, cost=0.0)
    at = _run(_with(data, data.raw_c.copy(), data.v_last.copy()), i0, i1, cost=0.0, execution="at_1550")
    assert near.fills and [(f["i"], f["symbol"], f["side"], f["qty"]) for f in near.fills] == \
        [(f["i"], f["symbol"], f["side"], f["qty"]) for f in at.fills]
    assert [f["px"] for f in near.fills] == pytest.approx([f["px"] for f in at.fills])
    assert [r["end"] for r in near.book.daily] == pytest.approx([r["end"] for r in at.book.daily])


def test_a_near_close_order_fills_at_the_1550_price_and_at_the_close_without_one(fixture_data):
    data, i0, i1 = fixture_data
    p = data.raw_c * 1.01  # 1% above the close at 15:50
    v = data.v_last.copy()
    spy = data.col("SPY")
    no = [data.col(s) for s in data.symbols[:5]]  # five names without a 15:50 minute: the closing print
    p[:, no] = np.nan
    run = _run(_with(data, p, v), i0, i1, cost=0.0, execution="at_1550")
    assert run.fills
    seen = {"at_1550": 0, "fallback": 0}
    for f in run.fills:
        if f["reason"] in script.NOT_VENUE:  # the ledger's delisting and end-of-data sales are not orders
            continue
        i, j = f["i"], data.col(f["symbol"])
        if j in no:
            assert f["px"] == pytest.approx(data.raw_c[i, j])
            seen["fallback"] += 1
        else:
            assert f["px"] == pytest.approx(data.raw_c[i, j] * 1.01)
            seen["at_1550"] += 1
    assert seen["at_1550"] > 0 and seen["fallback"] > 0 and spy not in no


def test_a_1550_fill_pays_the_continuous_venue(fixture_data):
    data, i0, i1 = fixture_data
    near = _run(data, i0, i1)
    at = _run(_with(data, data.raw_c.copy(), data.v_last.copy()), i0, i1, execution="at_1550")
    # same mid, but v2's continuous venue (full impact) costs more than the closing auction (half the impact)
    paid = lambda r: sum(float(x["cost"]) for x in r.book.daily)  # noqa: E731
    assert paid(at) > paid(near) > 0


# ---------------------------------------------------------------- the minute reader


def _write_minutes(root: Path, data: DailyData, symbols, months, shift: float = 0.002, split_scale: float = 1.0):
    """Two minute bars a session (15:50 and 15:59 ET), on the minute history's basis (raw x `split_scale`)."""
    for s in symbols:
        j = data.col(s)
        folder = root / "bars_1m" / f"symbol={s}"
        folder.mkdir(parents=True, exist_ok=True)
        rows = []
        for i, d in enumerate(data.dates.astype("datetime64[D]")):
            c = data.raw_c[i, j]
            if not np.isfinite(c):
                continue
            day = pd.Timestamp(d)
            for hh, mm, o, cl, vol in ((15, 50, c * (1 + shift), c * (1 + shift), 1_000.0),
                                       (15, 59, c, c, 5_000.0)):
                ts = (day + pd.Timedelta(hours=hh, minutes=mm)).tz_localize("America/New_York").tz_convert("UTC")
                rows.append({"ts": ts, "o": o * split_scale, "h": o * split_scale, "l": cl * split_scale,
                             "c": cl * split_scale, "v": vol / split_scale})
        df = pd.DataFrame(rows)
        df["month"] = df["ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m")
        for mon, g in df.groupby("month"):
            if months is None or mon in months:
                g.drop(columns="month").to_parquet(folder / f"{mon}.parquet")


def test_minute_files_off_the_stores_basis_are_dropped_not_used(tmp_path, fixture_data):
    data, *_ = fixture_data
    _write_minutes(tmp_path, data, ["P00", "SPY"], None, split_scale=2.0)  # 2x, with no split in the store
    one = m1550.session_1550(tmp_path, "P00", end=str(data.dates[-1]))
    assert set(one.columns) == {"symbol", "date", "c", "c1555", "v_first", "v_last"}
    p, v, notes = m1550.arrays(dr.FIXTURE, data, tmp_path, end=str(data.dates[-1]))
    assert np.isnan(p[:, data.col("P00")]).all() and notes["intraday_scale_mismatch"] > 0
    assert notes["symbols_with_minutes"] == 2


def test_the_reader_matches_the_store_basis_and_leaves_names_without_minutes_empty(tmp_path, fixture_data):
    data, i0, i1 = fixture_data
    _write_minutes(tmp_path, data, ["P00", "SPY"], None, shift=0.002)
    p, v, notes = m1550.arrays(dr.FIXTURE, data, tmp_path, end=str(data.dates[-1]))
    j, k = data.col("P00"), data.col("P01")
    ok = np.isfinite(p[:, j])
    assert ok.sum() > 200
    assert p[ok, j] == pytest.approx(data.raw_c[ok, j] * 1.002)
    assert v[ok, j] == pytest.approx(1_000.0)
    assert np.isnan(p[:, k]).all()  # no minute files: the closing print (counted by the script)


def test_the_reader_never_reads_a_month_after_end(tmp_path, fixture_data, monkeypatch):
    data, *_ = fixture_data
    _write_minutes(tmp_path, data, ["P00"], None)
    late = tmp_path / "bars_1m" / "symbol=P00" / "2025-01.parquet"
    pd.DataFrame({"ts": [pd.Timestamp("2025-01-02 20:50", tz="UTC")], "o": [1.0], "h": [1.0], "l": [1.0],
                  "c": [1.0], "v": [1.0]}).to_parquet(late)
    read = []
    real = pd.read_parquet

    def spy(path, *a, **kw):
        read.append(Path(path).name)
        return real(path, *a, **kw)

    monkeypatch.setattr(m1550.pd, "read_parquet", spy)
    got = m1550.session_1550(tmp_path, "P00", end="2024-12-31")
    assert "2025-01.parquet" not in read and read
    assert got["date"].max() <= pd.Timestamp("2024-12-31")
    with pytest.raises(ValueError, match="end"):
        m1550.session_1550(tmp_path, "P00", end=None)


def test_a_1550_without_trades_takes_the_next_minute_and_none_in_the_last_ten_is_empty(tmp_path):
    folder = tmp_path / "bars_1m" / "symbol=XX"
    folder.mkdir(parents=True)
    et = lambda d, h, m: pd.Timestamp(f"{d} {h:02d}:{m:02d}", tz="America/New_York").tz_convert("UTC")  # noqa: E731
    rows = [{"ts": et("2024-03-01", 15, 49), "o": 9.0, "h": 9.0, "l": 9.0, "c": 9.0, "v": 1.0},
            {"ts": et("2024-03-01", 15, 52), "o": 10.0, "h": 10.0, "l": 10.0, "c": 10.0, "v": 7.0},
            {"ts": et("2024-03-01", 15, 59), "o": 11.0, "h": 11.0, "l": 11.0, "c": 11.0, "v": 9.0},
            {"ts": et("2024-03-04", 15, 40), "o": 12.0, "h": 12.0, "l": 12.0, "c": 12.0, "v": 1.0},
            {"ts": et("2024-03-04", 16, 5), "o": 13.0, "h": 13.0, "l": 13.0, "c": 13.0, "v": 1.0}]
    pd.DataFrame(rows).to_parquet(folder / "2024-03.parquet")
    got = m1550.session_1550(tmp_path, "XX", end="2024-12-31").set_index("date")
    assert got.loc["2024-03-01", "c1555"] == 10.0 and got.loc["2024-03-01", "v_last"] == 7.0
    assert got.loc["2024-03-01", "c"] == 11.0
    assert np.isnan(got.loc["2024-03-04", "c1555"]) and got.loc["2024-03-04", "c"] == 12.0  # after-hours ignored


# ---------------------------------------------------------------- the script and the runner


def test_the_script_on_the_fixture_counts_one_configuration_and_reports_the_share_priced_at_1550(tmp_path,
                                                                                                  fixture_data):
    data, *_ = fixture_data
    _write_minutes(tmp_path, data, [s for s in data.symbols if s != "P00"], None, shift=-0.001)
    trials = tmp_path / "trials.jsonl"
    out = script.main(["--fixture", "--data", str(tmp_path), "--out", str(tmp_path / "out"), "--trials",
                       str(trials)])
    labels = [r["label"] for r in out["rows"]]
    assert labels == ["close|1x", "close|2x", "at_1550|1x", "at_1550|2x"]
    rows = [json.loads(x) for x in trials.read_text().splitlines()]
    assert len(rows) == 4 and sum(r["n_trials"] for r in rows) == 1
    assert {r["strategy"] for r in rows} == {RULE} and {r["cost_model"] for r in rows} == {"v2"}
    assert next(r for r in rows if r["n_trials"] == 1)["params"]["execution"] == "at_1550"
    assert {r["prereg_sha256"] for r in rows} == {out["prereg_sha256"]}
    s = out["at_1550"]
    assert 0 < s["share_of_fills_at_1550"] < 1 and s["fills_fallback_close"] > 0
    assert s["move_1550_to_close_bp"]["buy"] is not None
    page = (tmp_path / "out" / (Path(out["file"]).stem + ".md")).read_text()
    assert out["verdict"] is None and "not a verdict" in page
    assert out["window"][1] <= "2024-12-31"


def test_the_script_refuses_without_minutes(tmp_path):
    with pytest.raises(SystemExit, match="minute"):
        script.main(["--fixture", "--data", str(tmp_path), "--trials", ""])


def test_the_hold_out_guard_runs(tmp_path, monkeypatch):
    from kaibot.evaluation import holdout

    asked = []

    def refuse(end, **kw):
        asked.append(str(end))
        raise holdout.HoldoutError("inside the hold-out")

    monkeypatch.setattr(holdout, "check", refuse)
    with pytest.raises(SystemExit):
        script.main(["--fixture", "--data", str(tmp_path), "--trials", "", "--end", "2025-03-01"])
    assert asked == ["2025-03-01"]


def test_the_job_is_allowed_with_strict_flags_and_queued():
    cfg = rr.load_allowed()
    spec = cfg.specs["scripts/near_close_1550_rerun.py"]
    assert set(spec.flags) == {"--start", "--end", "--universe", "--fixture"} and not spec.env and not spec.writes
    for bad in (["--universe", "all"], ["--end", "2024-12-31x"], ["--data", "x"]):
        with pytest.raises(rr.JobError):
            rr.check_args(spec, bad)
    jobs = {j["id"]: j for j in rr.load_queue()}
    job = rr.check_job(jobs["near-close-1550-2026-11-25"], cfg)
    assert job.issue == 454 and job.args == ("--end", "2024-12-31")


def test_live_code_never_asks_for_the_1550_execution():
    hits = [p for p in (ROOT / "src" / "kaibot").rglob("*.py")
            if p.name not in ("daily.py", "minute_1550.py") and ("at_1550" in p.read_text()
                                                                  or "minute_1550" in p.read_text())]
    assert hits == []


# ---------------------------------------------------------------- QA-Ops: the default path is bit-for-bit the old one

# Fingerprints of fixture runs computed on the engine BEFORE #454's change (commit 59c332e, PR #668's head) and
# frozen here: each is sha256 over every fill (session, symbol, side, qty, px to 1e-6, fees to 1e-6, reason) and
# every session's end equity (to 1e-4). The default execution, `close`, the stress fill, a cost multiple, cost
# models v1 and v2 and the index core's builder must all give exactly these. A deliberate later engine change
# updates them in its own pull request and says why.
PRE_1550 = {"near_close_v1": "9f88cbe1fc118043", "near_close_v2": "3dd4000218901bf4",
            "close_v2": "3dd4000218901bf4", "stress_v2": "8f80086c9b2ad133",
            "near_close_v2_2x": "b82a8b0b91946a3a", "index_core_v2": "4f21469c01d42a3e"}


def _digest(r) -> str:
    import hashlib

    rows = [[f["i"], f["symbol"], f["side"], f["qty"], round(float(f["px"]), 6), round(float(f.get("fees") or 0), 6),
             str(f.get("reason"))] for f in r.fills]
    rows += [[round(float(d["end"]), 4)] for d in r.book.daily]
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def test_qa_every_default_run_is_identical_to_the_engine_before_the_1550_option(fixture_data):
    from kaibot.risk import index_core as ic
    from kaibot.strategies.index_core import IndexCore

    data, i0, i1 = fixture_data
    pol, tm = load_profiles(), mom.VERSIONS[RULE]
    cases = {"near_close_v1": {}, "near_close_v2": {"cost_model": "v2"},
             "close_v2": {"cost_model": "v2", "execution": "close"},
             "stress_v2": {"cost_model": "v2", "fill": "stress"},
             "near_close_v2_2x": {"cost_model": "v2", "cost": 2.0}}
    got = {k: _digest(run_daily(tm, data, pol["balanced"], i0, i1, **kw)) for k, kw in cases.items()}
    got["index_core_v2"] = _digest(run_daily(IndexCore(), data, pol["aggressive"], i0, i1, cost_model="v2",
                                             builder=ic.builder("aggressive")))
    assert got == PRE_1550


def test_qa_the_default_venue_has_two_bars_a_session_and_no_1550_bar(fixture_data):
    data, _, _ = fixture_data
    bars = data.bar_history()
    assert bars.frames
    for f in bars.frames.values():
        et = pd.to_datetime(f["ts"], utc=True).dt.tz_convert("America/New_York")
        assert set(zip(et.dt.hour, et.dt.minute, strict=True)) <= {(9, 30), (15, 59)}
