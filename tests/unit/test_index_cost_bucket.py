"""The index-fund bucket of cost model v2, its calibration script and the cost re-run (M11 ticket 2.3, #454).
The bucket is off by default, has no value until one is measured, and nothing here claims a result."""

from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from kaibot.broker import costs_v2 as cv
from kaibot.broker.costs_v2 import CostV2, FillCtx, V2Params, get_model
from kaibot.replay import sim_costs

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import calibrate_index_costs as cal  # noqa: E402
import spy_cost_rerun as rerun  # noqa: E402
import spy_engine as se  # noqa: E402
from test_spy_engine import FIRST, TEST_START, bars  # noqa: E402, F401  (the module's synthetic SPY bars)

SIG = 0.02 / math.sqrt(390)
INDEX = V2Params(index_symbols=("SPY",), index_half_bp=0.25)
NOW = datetime(2026, 11, 20, 22, 0, tzinfo=UTC)  # a Friday evening


@pytest.fixture(autouse=True)
def _no_broker_url_from_another_test(monkeypatch):
    """The calibration reads ALPACA_BASE_URL from the environment. Other tests load .env files with
    `load_env_file`, which leaves that variable set in the process, so these tests start without it."""
    monkeypatch.delenv("ALPACA_BASE_URL", raising=False)


def ctx(symbol=None, mid=600.0, adv=40e9, venue="continuous") -> FillCtx:
    return FillCtx(mid, 20, SIG, 1e6, adv, venue, symbol=symbol)


# ---------------------------------------------------------------- the model


def test_the_bucket_is_off_by_default_so_v2_is_unchanged():
    assert V2Params().index_symbols == () and V2Params().index_half_bp is None
    plain = CostV2()
    for sym in (None, "SPY", "AAPL"):
        assert plain.fill("buy", ctx(sym)) == plain.fill("buy", ctx())
    assert plain.half_spread_bp(40e9, "SPY") == 1.75 == plain.half_spread_bp(40e9)
    assert cv.MODELS == ("v1", "v2"), "the research scripts' --costs choices do not change"


def test_with_the_bucket_an_index_fund_pays_it_and_a_stock_pays_as_before():
    m, plain = CostV2(INDEX), CostV2()
    assert m.half_spread_bp(40e9, "SPY") == 0.25 and m.half_spread_bp(1e6, "SPY") == 0.25  # by symbol, not by ADV
    assert m.half_spread_bp(40e9, "AAPL") == 1.75 and m.half_spread_bp(40e9) == 1.75
    buy = m.fill("buy", ctx("SPY"))
    assert buy.half == pytest.approx(600.0 * 0.25 / 1e4) and buy.impact == plain.fill("buy", ctx()).impact
    assert m.fill("buy", ctx("AAPL")) == plain.fill("buy", ctx("AAPL"))
    assert m.fill("sell", ctx(None)) == plain.fill("sell", ctx(None)), "no symbol: never the bucket by guesswork"
    # the venue multipliers are v2's: the open pays twice the bucket, the close once
    assert m.fill("buy", ctx("SPY", venue="open")).half == pytest.approx(2 * buy.half)
    assert m.fill("buy", ctx("SPY", venue="close")).half == pytest.approx(buy.half)
    # the tick floor still holds on a cheap fund: half a cent on $10 is 5 bp
    assert m.fill("buy", ctx("SPY", mid=10.0)).half == pytest.approx(0.005)
    assert m.sell_fees(20, 600.0) == plain.sell_fees(20, 600.0)
    gapped = m.stop_fill(590.0, 580.0, 575.0, ctx("SPY"))
    assert gapped.half == pytest.approx(580.0 * 0.25 / 1e4 * 2)  # the symbol is carried into a stop's fill


@pytest.mark.parametrize("kw", [{"index_symbols": ("SPY",)}, {"index_half_bp": 0.3},
                                {"index_symbols": ("SPY",), "index_half_bp": 0.0},
                                {"index_symbols": ("SPY",), "index_half_bp": 25.0}])
def test_a_half_set_or_absurd_bucket_is_refused(kw):
    with pytest.raises(ValueError, match="index"):
        V2Params(**kw)


def test_the_committed_bucket_is_the_measured_one():
    """Calibrated 23 Nov 2026 (#454): the value and its measurement are committed together, and the table is in
    docs/research/results. The value follows the pre-fixed rule: the mean quoted half-spread rounded UP to 0.05 bp."""
    import math
    b = cv.load_index_bucket(ROOT)
    text = (ROOT / "config" / "costs_index_bucket.yaml").read_text()
    assert "rounded UP to the next 0.05 bp" in text and "NOT CALIBRATED YET" not in text
    assert (ROOT / "docs" / "research" / "results" / "index_cost_calibration.md").exists()
    import yaml
    cfg = yaml.safe_load(text)
    assert cfg["source"]["method"] == "quotes" and cfg["source"]["rows"] == 260
    assert cfg["half_spread_bp"] == pytest.approx(math.ceil(cfg["source"]["mean_quoted_half_bp"] / 0.05) * 0.05)
    assert b is not None
    get_model("v2_index")
    sim_costs.costs_for("v2idx")


def write_bucket(root: Path, value: str = "0.25") -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "costs_index_bucket.yaml").write_text(
        f"version: 1\nsymbols: [SPY]\nhalf_spread_bp: {value}\nsource: {{method: quotes, rows: 260}}\n")


def test_a_value_without_its_measurement_is_refused(tmp_path: Path, monkeypatch):
    """Inert until MEASURED: a bare number typed into the file is not a calibration."""
    cfg = tmp_path / "config" / "costs_index_bucket.yaml"
    cfg.parent.mkdir(parents=True)
    monkeypatch.setattr(cv, "ROOT", tmp_path)
    for tail in ("", "source:\n", "source: {rows: 260}\n", "source: {method: guess}\n", "source: fills\n"):
        cfg.write_text(f"version: 1\nsymbols: [SPY]\nhalf_spread_bp: 0.25\n{tail}")
        with pytest.raises(cv.NotCalibrated, match="source.method"):
            cv.load_index_bucket(tmp_path)
        with pytest.raises(cv.NotCalibrated):
            get_model("v2_index")
        with pytest.raises(cv.NotCalibrated):
            sim_costs.costs_for("v2idx", {"SPY": {}})


def test_a_committed_value_builds_the_model(tmp_path: Path, monkeypatch):
    write_bucket(tmp_path)
    b = cv.load_index_bucket(tmp_path)
    assert b["symbols"] == ("SPY",) and b["half_bp"] == 0.25 and len(b["sha256"]) == 64
    monkeypatch.setattr(cv, "ROOT", tmp_path)
    m = get_model("v2_index")
    assert m.name == "v2_index" and m.half_spread_bp(40e9, "SPY") == 0.25
    costs = sim_costs.costs_for("v2idx", {"SPY": {}})
    assert costs.params.index_symbols == ("SPY",) and costs.mult == 1.0
    assert sim_costs.costs_for("v2", {"SPY": {}}).params == V2Params()  # the plain scenario is untouched
    with pytest.raises(ValueError, match="unknown cost model"):
        get_model("v3")


def test_live_code_does_not_read_the_bucket():
    for pkg in ("engine", "risk", "strategies", "agents", "features", "news"):
        for f in (ROOT / "src" / "kaibot" / pkg).rglob("*.py"):
            text = f.read_text()
            assert "costs_index_bucket" not in text and "v2_index" not in text and "v2idx" not in text, f


# ---------------------------------------------------------------- the calibration script


class FakeAlpaca:
    """The two GET endpoints the script reads. Quotes: bid/ask one cent apart around 600 (half-spread 0.0833 bp),
    two cents in the first half hour."""

    def __init__(self, fills=(), no_quote_on=()):
        self.fills, self.no_quote_on, self.calls = list(fills), set(no_quote_on), []

    def call(self, method, url, params=None, body=None):
        self.calls.append((method, url, dict(params or {})))
        assert method == "GET" and body is None
        if url.endswith("/v2/account/activities"):
            assert params["activity_types"] == "FILL"
            return [f for f in self.fills if "page_token" not in params]
        assert "/v2/stocks/" in url and url.endswith("/quotes") and params["feed"] == "sip" and params["sort"] == "desc"
        end = cal._when(params["end"])
        et = end.astimezone(cal.ET)
        if et.date().isoformat() in self.no_quote_on:
            return {"quotes": [], "next_page_token": None}
        wide = 0.02 if et.hour == 9 else 0.01
        return {"quotes": [{"t": cal._stamp(end - timedelta(milliseconds=40)), "bp": 0, "ap": 600.0},  # one-sided
                           {"t": cal._stamp(end - timedelta(milliseconds=80)), "bp": 600.0 - wide / 2,
                            "ap": 600.0 + wide / 2}]}


def fill(day: str, hhmm: str, side: str, price: float, symbol="SPY", n=0) -> dict:
    t = pd.Timestamp(f"{day} {hhmm}", tz="America/New_York").tz_convert("UTC") + pd.Timedelta(seconds=n)
    return {"activity_type": "FILL", "id": f"x::{day}{hhmm}{n}", "order_id": "secret-order-id", "symbol": symbol,
            "side": side, "qty": "10", "price": str(price), "type": "fill",
            "transaction_time": t.strftime("%Y-%m-%dT%H:%M:%S.%f000Z")}


def many_fills() -> list[dict]:
    days = [d.date().isoformat() for d in pd.bdate_range("2026-11-09", "2026-11-18")]  # 8 sessions
    out = []
    for d in days:
        out += [fill(d, "10:30", "buy", 600.005), fill(d, "15:50", "sell", 599.995),
                fill(d, "09:40", "buy", 600.01), fill(d, "11:00", "sell", 599.995)]
    return out


def run_cal(tmp_path, alpaca, *argv):
    rc = cal.main(["--data", str(tmp_path), "--per-min", "0", *argv], trading=alpaca, data_http=alpaca, now=NOW)
    return rc, json.loads((tmp_path / "reports" / "index_cost_calibration.json").read_text())


def test_fills_against_quotes_give_the_quoted_spread_and_what_was_paid(tmp_path: Path, capsys):
    alpaca = FakeAlpaca(many_fills() + [fill("2026-11-18", "10:30", "buy", 190.0, symbol="AAPL"),
                                         fill("2026-11-18", "08:00", "buy", 600.0),  # before the open: left out
                                         fill("2026-11-20", "16:45", "buy", 600.0)])  # after the close
    rc, res = run_cal(tmp_path, alpaca)
    s = res["symbols"][0]
    assert rc == 0 and s["symbol"] == "SPY" and s["method"] == "fills"
    assert s["fill"]["n"] == 32 and s["fill"]["sessions"] == 8 and s["quote"]["n"] == 0
    q = s["fill"]["quoted_half_bp"]
    one_cent, two_cent = 0.005 / 600 * 1e4, 0.01 / 600 * 1e4
    assert q["median"] == pytest.approx(one_cent) and q["mean"] == pytest.approx((3 * one_cent + two_cent) / 4)
    assert s["fill"]["by_time"]["first half hour"]["mean"] == pytest.approx(two_cent)
    assert s["fill"]["by_time"]["last half hour"]["n"] == 8
    assert s["fill"]["paid_bp"]["mean"] == pytest.approx((one_cent * 3 + two_cent) / 4)  # filled at the quote
    assert s["fill"]["inside_quote_share"] == 1.0 and s["fill"]["median_quote_age_s"] == pytest.approx(0.08)
    # the rule fixed in config/costs_index_bucket.yaml: the mean, rounded UP to the next 0.05 bp
    assert s["half_spread_bp"] == 0.15 and cal.round_up(0.1042) == 0.15 and cal.round_up(0.10) == 0.10
    assert "half_spread_bp: 0.15" in res["bucket"] and "method: fills" in res["bucket"] and "rows: 32" in res["bucket"]
    out = capsys.readouterr().out
    assert "**0.15 bp a side**" in out and "1.75 bp today" in out and "is not used for the bucket" in out
    assert "secret-order-id" not in out and "secret-order-id" not in json.dumps(res)
    assert all(m == "GET" for m, _, _ in alpaca.calls)
    assert sum("/quotes" in u for _, u, _ in alpaca.calls) == 32  # one per kept fill: no quote sample was needed


def test_too_few_fills_fall_back_to_quotes_at_the_check_times(tmp_path: Path):
    alpaca = FakeAlpaca(many_fills()[:8])  # two sessions
    rc, res = run_cal(tmp_path, alpaca, "--sessions", "6")
    s = res["symbols"][0]
    assert rc == 0 and s["method"] == "quotes" and s["fill"]["n"] == 8
    assert s["quote"]["n"] == 6 * 13 and s["quote"]["sessions"] == 6  # 10:00 .. 15:30 and 15:50 on six sessions
    assert s["quote"]["last"] == "2026-11-19", "today's session is not sampled: it must be fully over"
    assert s["half_spread_bp"] == 0.10 and "method: quotes" in res["bucket"]


def test_quotes_only_reads_no_fill(tmp_path: Path):
    alpaca = FakeAlpaca(many_fills())
    rc, res = run_cal(tmp_path, alpaca, "--quotes-only", "--sessions", "5")
    assert rc == 0 and res["symbols"][0]["fill"]["n"] == 0 and res["symbols"][0]["method"] == "quotes"
    assert not any("activities" in u for _, u, _ in alpaca.calls)


def test_too_little_to_measure_gives_no_value(tmp_path: Path, capsys):
    days = {d.date().isoformat() for d in pd.bdate_range("2026-09-01", "2026-11-19")}
    rc, res = run_cal(tmp_path, FakeAlpaca(no_quote_on=days), "--quotes-only")
    assert rc == 3 and res["bucket"] is None and res["symbols"][0]["half_spread_bp"] is None
    assert "**not measured**" in capsys.readouterr().out


def test_the_script_is_read_only_and_paper_only(tmp_path: Path, monkeypatch):
    guard = cal._Counting(FakeAlpaca())
    with pytest.raises(RuntimeError, match="read-only"):
        guard.call("POST", f"{cal.PAPER_TRADING_URL}/v2/orders", None, {"symbol": "SPY"})
    with pytest.raises(RuntimeError, match="read-only"):
        guard.call("DELETE", f"{cal.PAPER_TRADING_URL}/v2/account/activities")
    with pytest.raises(RuntimeError, match="refused"):
        guard.call("GET", f"{cal.PAPER_TRADING_URL}/v2/orders")
    with pytest.raises(RuntimeError, match="refused"):
        guard.call("GET", "https://api.alpaca.markets/v2/account/activities")  # the live host
    monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
    with pytest.raises(SystemExit, match="paper-only"):
        cal.main(["--data", str(tmp_path)], trading=FakeAlpaca(), data_http=FakeAlpaca(), now=NOW)
    source = (ROOT / "scripts" / "calibrate_index_costs.py").read_text()
    assert '"POST"' not in source and "submit" not in source and "cancel" not in source


def test_requests_are_paced():
    slept = []
    guard = cal._Counting(FakeAlpaca(), per_min=40, sleep=slept.append)
    for _ in range(3):
        guard.call("GET", f"{cal.DATA_URL}/v2/stocks/SPY/quotes",
                   {"feed": "sip", "sort": "desc", "end": "2026-11-18T15:00:00.000000Z"})
    assert slept == [1.5, 1.5] and guard.n == 3


def test_timestamps_with_nanoseconds_and_recent_fills():
    assert cal._when("2026-11-18T15:30:00.123456789Z") == datetime(2026, 11, 18, 15, 30, 0, 123456, tzinfo=UTC)
    assert cal._when("2026-11-18T10:30:00-05:00") == datetime(2026, 11, 18, 15, 30, tzinfo=UTC)
    recent = fill("2026-11-20", "15:55", "buy", 600.0)
    got, cut = cal.fills(FakeAlpaca([recent]), cal.PAPER_TRADING_URL, {"SPY"},
                         datetime(2026, 11, 20, 21, 5, tzinfo=UTC), 90)  # ten minutes after the fill
    assert got == [] and cut is False, "SIP quotes younger than 15 minutes are not on the free plan"


# ---------------------------------------------------------------- the cost re-run


def test_the_rerun_refuses_until_the_bucket_is_measured(bars, tmp_path: Path, monkeypatch):  # noqa: F811
    root = bars[0]
    write_bucket(tmp_path, "")  # an uncalibrated file, as before 23 Nov 2026
    real = cv.load_index_bucket
    monkeypatch.setattr(cv, "load_index_bucket", lambda root=None: real(tmp_path))
    with pytest.raises(SystemExit, match="half_spread_bp is not set"):
        rerun.main(["--data", str(root), "--start", FIRST, "--test-start", TEST_START, "--end", "2017-05-19"])
    assert not list((root / "reports").glob("spy_cost_rerun_*")) if (root / "reports").exists() else True


def test_the_rerun_is_one_engine_run_one_trial_and_no_verdict(bars, tmp_path: Path, monkeypatch, capsys):  # noqa: F811
    root, frame, sd, days = bars
    write_bucket(tmp_path, "0.25")
    monkeypatch.setattr(cv, "ROOT", tmp_path)
    monkeypatch.setattr(rerun, "ROOT", ROOT)
    real = cv.load_index_bucket
    monkeypatch.setattr(cv, "load_index_bucket", lambda root=None: real(tmp_path))
    before = (root / "trials.jsonl").read_text().count("\n") if (root / "trials.jsonl").exists() else 0
    assert rerun.main(["--data", str(root), "--start", FIRST, "--test-start", TEST_START, "--end", "2017-05-19"]) == 0
    f = sorted((root / "reports").glob("spy_cost_rerun_balanced_*.json"))[-1]
    rep = json.loads(f.read_text())
    assert rep["set"] == "spy_cost_rerun" and rep["window"] == [TEST_START, "2017-05-19"] and rep["scenario"] == "v2idx"
    assert "not a verdict" in rep["status"] and "gate_b" not in rep and "verdict" not in rep
    row = rep["row"]
    assert row["trades"] > 0 and (root / "reports" / row["files"]["daily"]).is_file()
    # the same round trips as the plain v2 run, at a lower cost a side: only the spread bucket differs
    plain = se.run_stats(se.engine_run(frame, se.calendar_of(sd), [d for d in days if d <= "2017-05-19"],
                                       "spy_noise_band", "v2", level="balanced", vix=pd.Series(dtype=float),
                                       events={}, covers=None, adv={"SPY": sim_costs.adv_table(frame)}), sd)
    assert row["trades"] == plain["trades"]
    assert row["mean_cost_bp_per_side"] < plain["mean_cost_bp_per_side"] - 1.0
    assert row["mean_cost_bp_per_side"] > 0.25  # the bucket plus fees and impact
    lines = (root / "trials.jsonl").read_text().splitlines()
    assert len(lines) == before + 1
    trial = json.loads(lines[-1])
    assert trial["n_trials"] == 1 and trial["cost_model"] == "v2_index" and trial["strategy"] == "spy_noise_band"
    assert trial["params"]["index_half_bp"] == 0.25 and len(trial["params"]["bucket_sha256"]) == 64
    page = (root / "reports" / rerun.SUMMARY).read_text()
    assert page in capsys.readouterr().out and "**Not a verdict.**" in page and "0.25 bp a side" in page
    assert "n_trials 1" in page
