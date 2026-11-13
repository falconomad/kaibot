"""Issue #202: the Gate B go/no-go run of `spy_noise_band` through the engine with SimBroker + SimClock on cost
model v2 (scripts/spy_engine.py). The rule as an engine strategy must make exactly the harness's round trips when
the engine's own halts are out of the way; cost model v2 prices SimBroker's fills; the research rule can never
reach the live path; the script prints Gate B and the prereg's marks. Small seeded synthetic SPY bars only."""

import json
import math
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kaibot.engine.core import Engine
from kaibot.replay import spy_noise_band as nb
from kaibot.replay.data import from_frame
from kaibot.replay.sim_costs import V2SimCosts, adv_table, costs_for, daily_dollar_volume
from kaibot.strategies import registry
from kaibot.strategies.noise_band import STOP_FRAC, NoiseBand

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import spy_engine as se  # noqa: E402

FIRST, N_SESSIONS = "2017-01-03", 100
TEST_START = "2017-05-01"  # decision 15: the test starts 2017-05 (after the 14-session warm-up)
EARLY = "2017-05-10"  # a 13:00 close inside the test window (prereg choice 3)


def make_bars(root: Path, seed: int = 11) -> pd.DataFrame:
    """SPY minute bars: a random walk with a trend drawn per session (so the band breaks often), one early close."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(FIRST, periods=N_SESSIONS)
    rows, px = [], 225.0
    for d in days:
        n_min = 210 if str(d.date()) == EARLY else 390
        px *= math.exp(rng.normal(0, 0.004))
        drift = rng.choice([-1.0, 0.0, 1.0]) * rng.uniform(0.00002, 0.00006)
        c = px * np.exp(np.cumsum(drift + rng.normal(0, 0.0004, n_min)))
        o = np.r_[px, c[:-1]]
        ts = (pd.Timestamp(f"{d.date()} 09:30", tz="America/New_York").tz_convert("UTC")
              + pd.to_timedelta(np.arange(n_min), "min"))
        rows.append(pd.DataFrame({"ts": ts, "o": o, "h": np.maximum(o, c) * 1.0002, "l": np.minimum(o, c) * 0.9998,
                                  "c": c, "v": rng.uniform(5e4, 2e5, n_min), "vw": (o + c) / 2}))
        px = float(c[-1])
    df = pd.concat(rows, ignore_index=True)
    out = root / "bars_1m" / "symbol=SPY"
    out.mkdir(parents=True, exist_ok=True)
    for m, g in df.groupby(df["ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m")):
        g.reset_index(drop=True).to_parquet(out / f"{m}.parquet")
    return df


@pytest.fixture(scope="module")
def bars(tmp_path_factory):
    root = tmp_path_factory.mktemp("spy_engine")
    make_bars(root)
    frame = se.load_frame(root, "SPY", FIRST, "2017-12-31")
    sd = from_frame("SPY", frame)
    days = [str(d) for d in sd.dates if str(d) >= TEST_START]
    return root, frame, sd, days


def _run(bars, scenario: str, monkeypatch=None, version: str = "spy_noise_band", events=None):
    root, frame, sd, days = bars
    return se.engine_run(frame, se.calendar_of(sd), days, version, scenario, level="balanced",
                         vix=pd.Series(dtype=float), events={} if events is None else events, covers=None,
                         adv={"SPY": adv_table(frame)})


# ---------------------------------------------------------------- 1. the engine makes the harness's round trips


@pytest.mark.parametrize("version", ["spy_noise_band", "spy_noise_band~check15", "spy_noise_band~exit1555"])
def test_without_the_engines_halts_the_engine_makes_exactly_the_harness_round_trips(bars, monkeypatch, version):
    root, frame, sd, days = bars
    monkeypatch.setattr(Engine, "_pause_rule", lambda self: (10**9, 0))  # no pause after losses
    run = _run(bars, "0", version=version)
    assert run["errors"] == []
    trips = se.trips_of(run, sd)
    i0, i1 = int(np.searchsorted(sd.dates, np.datetime64(days[0]))), len(sd.dates) - 1
    spec = registry.research_intraday()[version].spec
    want = nb.signals(sd, spec, i0, i1)
    assert len(trips) == len(want) >= 10
    cm = nb.close_minute(sd)
    for t, w in zip(trips, want, strict=True):
        assert (t["i_entry"], t["decide_m"], t["entry_m"]) == (w.i_entry, w.decide_m, w.entry_m)
        assert t["entry_mid"] == pytest.approx(w.entry_mid) and t["exit_mid"] == pytest.approx(w.exit_mid)
        if w.reason == "close":
            assert t["close_exit"] and t["i_exit"] == w.i_exit and cm[w.i_exit] == w.exit_m
        else:
            assert not t["close_exit"] and t["exit_m"] == w.exit_m, (t, w)
        assert t["gross_ret"] == pytest.approx(t["net_ret"])  # 0 bp: no cost, no fee
    early = int(np.searchsorted(sd.dates, np.datetime64(EARLY)))
    assert cm[early] == 209 and all(t["exit_m"] <= 209 for t in trips if t["i_entry"] == early)


def test_the_risk_gate_sizes_every_entry_at_the_levels_cap(bars, monkeypatch):
    root, frame, sd, days = bars
    monkeypatch.setattr(Engine, "_pause_rule", lambda self: (10**9, 0))
    run = _run(bars, "0.5")
    st = se.run_stats(run, sd)
    assert 13.5 <= st["mean_position_pct"] <= st["max_position_pct"] <= 15.0  # Balanced: 15% per position
    assert all(abs(f["qty"] - round(f["qty"])) < 1e-9 for f in run["fills"])  # whole shares
    stops = [r["stop_px"] for r in run["trades"] if r.get("stop_px")]
    assert stops and all(s < r["entry_px"] for s, r in zip(stops, run["trades"], strict=False))
    assert st["mean_cost_bp_per_side"] == pytest.approx(0.5, abs=1e-6)


def test_an_fomc_day_blacks_out_the_checks_around_14_00(bars, monkeypatch):
    root, frame, sd, days = bars
    monkeypatch.setattr(Engine, "_pause_rule", lambda self: (10**9, 0))
    i0 = int(np.searchsorted(sd.dates, np.datetime64(days[0])))
    want = nb.signals(sd, nb.BASE, i0, len(sd.dates) - 1)
    hit = want[len(want) // 2]
    day = sd.dates[hit.i_entry].astype(object)
    ev = {day: [("fomc_statement_1400", hit.decide_m + 10)]}  # a release 10 minutes after that check
    run = _run(bars, "0", events=ev)
    got = {(t["i_entry"], t["entry_m"]) for t in se.trips_of(run, sd)}
    assert (hit.i_entry, hit.entry_m) not in got
    rec = se.reconcile(se.trips_of(run, sd), want, ev, sd)
    assert rec["harness_only_why"].get("event blackout (+-30 min of a scheduled release)", 0) >= 1


# ---------------------------------------------------------------- 2. cost model v2 at the simulated venue


def test_v2_prices_spy_fills_at_its_adv_bucket_with_2026_fees():
    t = datetime(2024, 1, 2, 15, tzinfo=UTC)
    c = V2SimCosts(adv={"SPY": {date(2024, 1, 2): 3e10}}, clock=lambda: t)
    buy = c.market("buy", 470.0, 30, 0.0, 1e6)
    assert buy == pytest.approx(470.0 * (1 + 1.75e-4))  # over $500M ADV: 1.75 bp, impact zero without volatility
    sell = c.auction_fill("sell", 470.0, 30, "SPY", date(2024, 1, 2), 1e6)
    assert sell == pytest.approx(470.0 * (1 - 1.75e-4))  # the close: one full half-spread
    assert c.fees("sell", 30, sell) == pytest.approx(0.30 + 0.01)  # SEC $0.29 and TAF $0.006, each up to a cent
    assert [x["venue"] for x in c.log] == ["continuous", "close"] and c.log[0]["t"] == t
    two = costs_for("v2x2", c.adv, clock=lambda: t)
    assert two.market("buy", 470.0, 30, 0.0, 1e6) - 470.0 == pytest.approx(2 * (buy - 470.0))
    assert two.fees("sell", 30, 470.0) == c.fees("sell", 30, 470.0)  # fees are never scaled
    unknown = V2SimCosts(adv={"SPY": {}}, clock=lambda: t)
    assert unknown.market("buy", 470.0, 30, 0.0, 1e6) == pytest.approx(470.0 * 1.001)  # unknown ADV: 10 bp
    with pytest.raises(ValueError, match="one-symbol"):
        V2SimCosts(adv={"SPY": {}, "QQQ": {}}, clock=lambda: t).market("buy", 1.0, 1, 0.0, 1.0)
    f = costs_for("0.5")
    assert f.market("buy", 100.0, 1, 0, 1) == pytest.approx(100.005) and f.fees("sell", 1, 100.0) == 0.0


def test_adv_is_point_in_time(bars):
    root, frame, sd, days = bars
    adv = adv_table(frame)
    dv = daily_dollar_volume(frame)
    d = list(dv.index)
    assert math.isnan(adv[d[19]]) and adv[d[20]] == pytest.approx(dv.iloc[:20].mean())
    later = frame.copy()
    later.loc[pd.to_datetime(later["ts"]).dt.tz_convert("America/New_York").dt.date == d[30], "v"] *= 100
    assert adv_table(later)[d[30]] == adv[d[30]]  # a session's own volume never enters its ADV


def test_the_v2_run_pays_more_than_half_a_bp(bars):
    root, frame, sd, days = bars
    st = se.run_stats(_run(bars, "v2"), sd)
    assert st["trades"] > 0 and st["mean_cost_bp_per_side"] > 1.75  # spread bucket plus fees per side


# ---------------------------------------------------------------- 3. never on the live path


def test_the_research_rule_never_reaches_the_live_path():
    names = list(registry.research_intraday())
    assert names[0] == "spy_noise_band" and len(names) == 6
    assert registry.live(names) == () and registry.live([*names, "dip_reversal"]) == (registry.BY_NAME[
        "dip_reversal"],)
    assert not set(names) & registry.owner_toggles() and not set(names) & set(registry.names())
    assert all(registry.get(n) is registry.research_intraday()[n] for n in names)
    assert registry.multi_day_names(names) == []  # intraday: never refused as multi-day, never loaded either
    with pytest.raises(ValueError, match="multi-day"):
        NoiseBand(nb.VERSIONS["spy_noise_band_carry"])
    assert NoiseBand().stop_frac == STOP_FRAC
    prof = (ROOT / "config" / "profiles.yaml").read_text()
    assert "spy_noise_band" not in prof


# ---------------------------------------------------------------- 4. the stakeholder's command


@pytest.fixture(scope="module")
def report(bars):
    root, frame, sd, days = bars
    args = ["--data", str(root), "--start", FIRST, "--test-start", TEST_START, "--end", "2017-05-19",
            "--control-runs", "20", "--no-neighbours", "--jobs", "1"]
    assert se.main(args) == 0
    f, rep = se.find_report(root / "reports", "balanced")
    return root, args, f, rep


def test_the_script_writes_gate_b_and_the_prereg_marks(report, capsys):
    root, args, f, rep = report
    assert rep["set"] == "spy_engine" and rep["window"][0] == TEST_START and rep["sessions"] == 15
    g = rep["gate_b"]
    assert g["source"] == {"engine": "simbroker", "cost_model": "v2", "data_repairs": True,
                           "benchmark": "spy_price_only"}
    assert g["verdict"] in ("fail", "incomplete", "provisional") and not g["can_pass"]  # price-only SPY, 15 sessions
    assert any("benchmark" in r for r in g["provisional_reasons"])
    assert g["marks"]["mark_3"]["met"] is None and "500 needed" in g["marks"]["mark_3"]["why"]
    assert g["marks"]["mark_6"]["met"] is None  # neighbours skipped
    assert [m["mark"] for m in rep["prereg_marks_as_written"]] == [1, 2, 3, 4, 5, "3 (amendment 1)"]
    assert {r["label"] for r in rep["rows"]} == {"spy_noise_band|v2", "spy_noise_band|0.5", "spy_noise_band|1"}
    assert rep["prereg"]["sha256"] == __import__("hashlib").sha256(
        (ROOT / "docs" / "prereg" / "spy_noise_band.md").read_bytes()).hexdigest()
    page = (root / "reports" / se.SUMMARY).read_text()
    for needle in ("## Decision 15 Gate B on cost model v2", "## The pre-registration's marks",
                   "## Engine against the research harness", "Harness 0.5 bp", "quoted in issue #202",
                   "| spy_noise_band|v2 |"):
        assert needle in page, needle
    rows = [json.loads(x) for x in (root / "trials.jsonl").read_text().splitlines()]
    assert len(rows) == 3 and {r["n_trials"] for r in rows} == {0} and {r["status"] for r in rows} == {
        "engine_simbroker"}
    assert all(r["prereg_sha256"] == rep["prereg"]["sha256"] for r in rows)
    for r in rep["rows"]:
        assert (root / "reports" / r["files"]["fills"]).is_file()


def test_has_and_summary(report, capsys):
    root, args, f, rep = report
    assert se.main([*args, "--has"]) == 0
    assert se.main([*[a for a in args if a != "--no-neighbours"], "--has"]) == 1  # a different grid
    assert se.main(["--data", str(root), "--summary"]) == 0
    assert "Gate B go/no-go" in capsys.readouterr().out


def test_the_harness_report_is_shown_side_by_side_when_it_exists(report):
    root, args, f, rep = report
    fake = {"set": "spy", "version": "spy_noise_band", "rows": [
        {"spec": "spy_noise_band", "cost": "0.5 bp", "kind": "grid", "net_bp_per_trip": 2.48, "t_net": 2.12,
         "cagr_pct": 2.87, "max_drawdown_pct": 5.35, "trades": 4321}], "timing_control": None, "marks": []}
    (root / "reports" / "spy_balanced_spy_noise_band_20261113T000000.json").write_text(json.dumps(fake))
    h = se.harness_report(root, "balanced")
    assert h["rows"]["0.5 bp"]["trades"] == 4321
    rep2 = {**rep, "harness": h}
    assert "| Round trips | 4321 |" in se.page(rep2) and "harness report `spy_balanced_" in se.page(rep2)


def test_run_experiments_has_the_spy_engine_set():
    sh = (ROOT / "scripts" / "run_experiments.sh").read_text()
    for needle in ('"${1:-}" = "spy_engine"', "scripts/spy_engine.py", "experiments_spy_engine.md"):
        assert needle in sh, needle
