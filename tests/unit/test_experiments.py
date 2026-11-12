"""Issue #109: the replay's cost split, the four named walk-forward experiments, the one-command summary, the
trade breakdown script and the turnover warning. Synthetic bars only, no network."""

import json
import math
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from test_walkforward import FIRST, LAST, POL, _regime, _slice, market

from kaibot.broker.shadow_costs import sell_fees
from kaibot.replay.data import N_MIN, SymbolDays
from kaibot.replay.events import events_digest
from kaibot.replay.features import candidates, daily_stats
from kaibot.replay.pipeline import prepare
from kaibot.replay.sim import EXIT_M, first_exit_trail, run_portfolio, trail_exit_level
from kaibot.replay.walkforward import (
    DEFAULT,
    EXPERIMENTS,
    GRIDS,
    Params,
    Window,
    filter_candidates,
    first_train_month,
    make_folds,
    make_grid,
    run_walk_forward,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import analyze_trades  # noqa: E402
import experiments_summary  # noqa: E402
import walk_forward  # noqa: E402

COSTS = ("cost_spread", "cost_impact", "cost_slippage", "cost_fees")
LO, HI = np.datetime64("2024-06-01"), np.datetime64("2024-08-31")
DAY = np.datetime64("2024-06-03")


@pytest.fixture(scope="module")
def mkt():
    return market(11, FIRST, LAST)


@pytest.fixture(scope="module")
def prep(mkt):
    stocks, etfs, sector_of, spy = mkt
    return prepare(stocks, etfs, sector_of, {}, LO, HI), {d: v for d, v in _regime(spy).items() if LO <= d <= HI}


def one_day(path: np.ndarray, volume: float = 5e5) -> dict[str, SymbolDays]:
    """One symbol, one session, one price per minute (open = high = low = close), so a move is a gap."""
    c = path[None, :].astype(float)
    return {"AAA": SymbolDays("AAA", np.array([DAY]), c.copy(), c.copy(), c.copy(), c, np.full(c.shape, volume),
                              c.copy())}


def one_cand(m: int = 20, price: float = 100.0, atr5: float = 0.4) -> pd.DataFrame:
    return pd.DataFrame([{"symbol": "AAA", "date": DAY, "m": m, "price": price, "atr5": atr5, "vwap": price - 1.0,
                          "z": -2.0, "resid_z": 0.0, "turnover": 1.0, "sigma_1m": 0.0005, "news_24h": False}])


def run_one(path, vix=18.0, **kw):
    reg = {DAY: (np.zeros(N_MIN, dtype=bool), vix)}
    return run_portfolio(one_day(path), one_cand(), reg, set(), POL, variant="A", **kw)


def check_identity(trades):
    for t in trades:
        assert t.pnl == pytest.approx(t.gross_pnl - sum(getattr(t, k) for k in COSTS), abs=1e-6)
        assert all(getattr(t, k) >= -1e-9 for k in COSTS)


# ---------------------------------------------------------------- 1. cost split


def test_net_results_unchanged_for_a_fixed_sample(prep):
    """Numbers taken on main (d73927c) before the cost split, same synthetic market and window."""
    p, reg = prep
    for variant, n, net in (("A", 133, 28689.623973493788), ("v1", 133, 20888.47733879249)):
        book = run_portfolio(p.bars, p.cands, reg, set(), POL, variant=variant)
        assert len(book.trades) == n
        assert sum(t.pnl for t in book.trades) == pytest.approx(net, rel=1e-12)
        check_identity(book.trades)
        for t in book.trades:  # the net P&L is still exactly the old formula on the old columns
            assert t.pnl == pytest.approx((t.exit - t.entry) * t.qty - sell_fees(t.qty, t.exit), abs=1e-9)
            assert t.vix == 18.0


def test_cost_split_time_exit_is_all_costs():
    book = run_one(np.full(N_MIN, 100.0))
    (t,) = book.trades
    assert t.reason == "time" and t.exit_m == EXIT_M
    assert t.gross_pnl == 0.0 and t.cost_slippage == 0.0
    assert t.cost_spread == pytest.approx(2 * 0.01 * t.qty)  # 1 bp half spread on $100, both sides
    assert t.cost_impact > 0 and t.cost_fees == pytest.approx(sell_fees(t.qty, t.exit))
    assert t.pnl < 0
    check_identity(book.trades)


def test_cost_split_gap_through_the_stop_is_slippage():
    path = np.full(N_MIN, 100.0)
    path[30:] = 98.0  # bar 30 opens at 98, far below the stop (fill - 0.6)
    (t,) = run_one(path).trades
    assert t.reason == "stop" and t.exit_m == 30
    assert t.cost_slippage == pytest.approx((t.stop - 98.0) * t.qty)
    assert t.gross_pnl == pytest.approx((t.stop - 100.0) * t.qty)  # as if the stop had filled at its level
    check_identity([t])


def test_cost_split_target_is_a_limit_fill():
    path = np.full(N_MIN, 100.0)
    path[40:] = 103.0  # through the 2R target (fill + 1.2)
    (t,) = run_one(path).trades
    assert t.reason == "target" and t.exit == t.target and t.cost_slippage == 0.0
    assert t.cost_spread == pytest.approx(0.01 * t.qty)  # entry side only
    assert t.gross_pnl == pytest.approx((t.target - 100.0) * t.qty)
    check_identity([t])


def test_cost_split_daily_stop_flatten():
    crash = np.full(N_MIN, 100.0)
    crash[30:] = 80.0  # AAA gaps far through its stop: the day's loss limit is hit and BBB is flattened
    bars = {**one_day(crash), "BBB": one_day(np.full(N_MIN, 100.0))["AAA"]}
    cands = pd.concat([one_cand(), one_cand().assign(symbol="BBB")], ignore_index=True)
    reg = {DAY: (np.zeros(N_MIN, dtype=bool), 18.0)}
    book = run_portfolio(bars, cands, reg, set(), POL, variant="A")
    by = {t.symbol: t for t in book.trades}
    assert by["AAA"].reason == "stop" and by["AAA"].cost_slippage == pytest.approx((by["AAA"].stop - 80) * 150)
    t = by["BBB"]
    assert t.reason == "daily_stop" and t.exit_m == 30 and t.gross_pnl == 0.0 and t.cost_slippage == 0.0
    assert t.cost_spread == pytest.approx(2 * 0.01 * t.qty) and t.cost_fees > 0 and t.vix == 18.0
    check_identity(book.trades)


def _old_first_exit_trail(h, l, o, c, start_m, fill, risk_ps, atr, target_r=2.0, trail_atr=1.5):  # noqa: E741
    """Copy of the function as it was on main: the engine's shadow book imports first_exit_trail."""
    stop = fill - risk_ps
    target = fill + target_r * risk_ps
    armed = False
    hi_close = -np.inf
    for m in range(start_m, EXIT_M + 1):
        if l[m] <= stop:
            return m, float(min(stop, o[m])), "trail" if armed else "stop"
        if h[m] >= target + 0.01:
            return m, float(target), "target"
        if m == EXIT_M:
            return m, float(c[m]), "time"
        hi_close = max(hi_close, c[m])
        if not armed and c[m] >= fill + risk_ps:
            armed = True
        if armed:
            stop = max(stop, fill, hi_close - trail_atr * atr)
    return EXIT_M, float(c[EXIT_M]), "time"


def test_first_exit_trail_is_unchanged():
    rng = np.random.default_rng(3)
    seen = set()
    for _ in range(300):
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.0008, N_MIN)))
        o = np.r_[c[0], c[:-1]]
        h, lo = np.maximum(o, c) + 0.01, np.minimum(o, c) - 0.01
        args = (h, lo, o, c, int(rng.integers(1, 300)), float(o[5]), float(rng.uniform(0.1, 0.8)), 0.3)
        old = _old_first_exit_trail(*args)
        assert first_exit_trail(*args) == old
        m, px, why, level = trail_exit_level(*args)
        assert (m, px, why) == old and level >= px
        seen.add(why)
    assert seen == {"stop", "trail", "target", "time"}


# ---------------------------------------------------------------- 2. experiments


def test_experiments_are_small_and_keep_variant_a_as_control():
    assert list(EXPERIMENTS) == ["control", "mornings", "wide_stop", "high_vix", "mornings_wide_stop"]
    assert EXPERIMENTS["control"].grid == make_grid(GRIDS["default"]) and EXPERIMENTS["control"].default == DEFAULT
    assert DEFAULT.label == "z-1.5_atr1.5_tr2"  # the label is hashed into the old runs' digests
    for name, e in EXPERIMENTS.items():
        g = e.grid
        assert g[0] == e.default and len(set(g)) == len(g)
        if name != "control":
            assert len(g) <= 9
            assert all((p.z_max, p.target_r) == (DEFAULT.z_max, DEFAULT.target_r) for p in g)
    assert [p.entry_cutoff_m for p in EXPERIMENTS["mornings"].grid] == [60, 30, 90]
    assert sorted(p.atr_mult for p in EXPERIMENTS["wide_stop"].grid) == [2.5, 3.0, 4.0]
    assert all(p.size_atr == 1.5 for p in EXPERIMENTS["wide_stop"].grid + EXPERIMENTS["mornings_wide_stop"].grid)
    assert sorted(p.vix_min for p in EXPERIMENTS["high_vix"].grid) == [18.0, 22.0, 26.0]
    assert len(EXPERIMENTS["mornings_wide_stop"].grid) == 9
    assert len({p.label for e in EXPERIMENTS.values() for p in e.grid}) == 27 + 3 + 3 + 3 + 9


def test_filter_candidates_only_removes_rows(prep):
    p, reg = prep
    reg = dict(reg)
    days = sorted(reg)
    for k, d in enumerate(days):  # previous-day VIX: 16, 24, 30 in turn, and one day unknown
        reg[d] = (reg[d][0], (16.0, 24.0, 30.0)[k % 3])
    reg[days[0]] = (reg[days[0]][0], math.nan)
    assert filter_candidates(p.cands, reg, DEFAULT).equals(p.cands)
    cut = filter_candidates(p.cands, reg, Params(entry_cutoff_m=90))  # the synthetic dips start after 10:30
    assert 0 < len(cut) < len(p.cands) and (cut["m"] + 1 <= 90).all()
    assert cut.equals(p.cands[p.cands["m"] <= 89])
    assert not len(filter_candidates(p.cands, reg, Params(entry_cutoff_m=30)))
    hv = filter_candidates(p.cands, reg, Params(vix_min=22.0))
    assert 0 < len(hv) < len(p.cands)
    assert all(reg[np.datetime64(d, "D")][1] > 22.0 for d in hv["date"].unique())
    assert np.datetime64(days[0], "D") not in set(hv["date"].to_numpy().astype("datetime64[D]"))
    both = filter_candidates(p.cands, reg, Params(entry_cutoff_m=90, vix_min=22.0))
    assert set(both.index) == set(cut.index) & set(hv.index)


def test_mornings_trades_only_before_the_cutoff(prep):
    p, reg = prep
    book = run_portfolio(p.bars, filter_candidates(p.cands, reg, Params(entry_cutoff_m=90)), reg, set(), POL,
                         variant="A")
    full = run_portfolio(p.bars, p.cands, reg, set(), POL, variant="A")
    assert 0 < len(book.trades) < len(full.trades) and max(t.entry_m for t in book.trades) <= 90


def test_wide_stop_keeps_the_dollar_risk():
    flat = np.full(N_MIN, 100.0)
    (base,) = run_one(flat).trades
    for mult in (2.5, 3.0, 4.0):
        (wide,) = run_one(flat, atr_mult=mult, size_atr=1.5).trades
        assert wide.qty == math.floor(base.qty * 1.5 / mult)
        assert wide.entry - wide.stop == pytest.approx(mult * 0.4)
        assert wide.target - wide.entry == pytest.approx(2.0 * mult * 0.4)  # target still 2R
        assert base.risk - mult * 0.4 < wide.risk <= base.risk + 1e-9  # same $ risk, less one share's rounding
        (unsized,) = run_one(flat, atr_mult=mult).trades  # without the rule the position cap gives more risk
        assert unsized.risk > base.risk and wide.qty < unsized.qty
    assert events_digest(run_one(flat, size_atr=None)) == events_digest(run_one(flat))


def _walk(mkt, name, regime_fn=_regime):
    stocks, etfs, sector_of, spy = mkt

    def load(lo, hi):
        return Window({s: _slice(sd, lo, hi) for s, sd in stocks.items()},
                      {e: _slice(sd, lo, hi) for e, sd in etfs.items()}, _slice(spy, lo, hi))

    exp = EXPERIMENTS[name]
    folds = make_folds(first_train_month(FIRST), LAST, train_months=2)
    return run_walk_forward(load, sector_of, {}, regime_fn, set(), POL, folds, exp.grid, variant="A",
                            default=exp.default, min_train_trades=5), exp


def test_wide_stop_walk_forward_and_report(mkt):
    wf, exp = _walk(mkt, "wide_stop")
    assert len(wf.folds) == 4 and all(f.selection.params in exp.grid for f in wf.folds)
    trades = [t for f in wf.folds for t in f.tuned.trades]
    assert trades and all(t.entry - t.stop >= 2.5 * 0.99 * (t.risk / t.qty) / 4.0 for t in trades)
    check_identity(trades)
    s = walk_forward.summarize(wf, 100_000.0, n_logged=3105, blocks=4, default=exp.default)
    assert s["dsr"]["n_trials"] == 3 * 4 + 3105
    for side in ("tuned", "default"):
        c, m = s[side]["costs"], s[side]
        assert c["net_pnl"] == pytest.approx(c["gross_pnl"] - c["spread"] - c["impact"] - c["slippage_past_stop"]
                                             - c["fees"], abs=0.05)
        assert c["mean_bp_net"] == pytest.approx(m["mean_bp_per_trade"], abs=0.01)
        assert c["mean_bp_gross"] > c["mean_bp_net"]
        assert m["months"] == 4 and 0 <= m["months_positive"] <= 4 and m["months_with_trades"] <= 4
    rep = {"summary": s, "variant": "A", "level": "balanced", "grid_name": "wide_stop", "grid_axes": exp.axes,
           "min_train_trades": 5, "input_sha256": "i", "events_sha256_tuned": "x", "events_sha256_default": "y",
           "code_sha": "z", "caveats": ["c"]}
    md = walk_forward.markdown(rep)
    assert "| Mean bp per trade, gross |" in md and "| Slippage past stops $ |" in md and "| Net return % |" in md


def test_high_vix_walk_forward_counts_what_remains(mkt):
    def regime_fn(spy):  # calm in July and September, stressed otherwise
        return {d: (np.zeros(N_MIN, dtype=bool), 14.0 if str(d)[5:7] in ("07", "09") else 24.0) for d in spy.dates}

    wf, exp = _walk(mkt, "high_vix", regime_fn)
    assert [f.tuned_stats.trades > 0 for f in wf.folds] == [False, True, False, True]  # Jul, Aug, Sep, Oct
    s = walk_forward.summarize(wf, 100_000.0, n_logged=0, blocks=4, default=exp.default)
    assert s["tuned"]["months_with_trades"] == 2 and s["tuned"]["months"] == 4
    assert all(t.vix == 24.0 for f in wf.folds for t in f.tuned.trades)


def test_logged_trials_counts_the_whole_search(tmp_path):
    f = tmp_path / "trials.jsonl"
    assert walk_forward.logged_trials(f) == 0
    f.write_text('{"trial_id": "a", "n_trials": 3105}\n{"trial_id": "b"}\n\n')
    assert walk_forward.logged_trials(f) == 3106


# ---------------------------------------------------------------- 3. the command, end to end (offline)


def _write_history(data: Path, mkt) -> Path:
    stocks, etfs, sector_of, spy = mkt
    minutes = pd.to_timedelta(np.arange(N_MIN) + 9 * 60 + 30, unit="m")
    for sd in (*stocks.values(), *etfs.values(), spy):
        ts = pd.DatetimeIndex(np.repeat(pd.to_datetime(sd.dates), N_MIN)) + np.tile(minutes, len(sd.dates))
        df = pd.DataFrame({"ts": ts.tz_localize("America/New_York").tz_convert("UTC"),
                           **{k: getattr(sd, k).ravel() for k in ("o", "h", "l", "c", "v", "vw")}})
        folder = data / "bars_1m" / f"symbol={sd.symbol}"
        folder.mkdir(parents=True)
        for month, g in df.groupby(np.repeat(sd.dates.astype("datetime64[M]").astype(str), N_MIN)):
            g.to_parquet(folder / f"{month}.parquet", index=False)
    (data / "inputs").mkdir()
    days = pd.date_range("2023-12-01", "2024-11-01")
    (data / "inputs" / "fred_vixcls.json").write_text(json.dumps(
        [{"date": str(d.date()), "value": "14.0" if "2024-06-28" <= str(d.date()) <= "2024-08-30" else "24.0"}
         for d in days]))  # calm through July and August (a close is usable from the next day), stressed otherwise
    uni = data / "universe_test.yaml"
    uni.write_text(yaml.safe_dump({"market": "SPY", "sectors": {
        e: [s for s, x in sector_of.items() if x == e] for e in etfs}}))
    return uni


@pytest.fixture(scope="module")
def runs(mkt, tmp_path_factory):
    """scripts/walk_forward.py --experiment <name> for three experiments, on Parquet history in a temp folder."""
    data = tmp_path_factory.mktemp("data")
    uni = _write_history(data, mkt)
    env = data / "empty.env"
    env.write_text("")
    old = sys.argv
    try:
        for name in ("mornings", "wide_stop", "high_vix"):
            sys.argv = ["walk_forward.py", "--env", str(env), "--data", str(data), "--universe", str(uni),
                        "--start", FIRST, "--end", str(LAST), "--experiment", name, "--train-months", "2",
                        "--min-train-trades", "5"]
            walk_forward.main()
    finally:
        sys.argv = old
    return data, uni


def test_experiment_run_writes_cost_columns_and_trial_rows(runs):
    data, _ = runs
    rows = [json.loads(x) for x in (data / "trials.jsonl").read_text().splitlines()]
    assert [(r["experiment"], r["n_trials"]) for r in rows] == [
        ("mornings", 12), ("mornings", 1), ("wide_stop", 12), ("wide_stop", 1), ("high_vix", 12), ("high_vix", 1)]
    assert all((data / "trials" / f"{r['trial_id']}.csv").exists() for r in rows)
    columns = ["symbol", "date", "entry_m", "exit_m", "qty", "entry", "exit", "stop", "target", "reason", "pnl",
               "risk", "gross_pnl", *COSTS, "vix"]  # the old columns first, unchanged
    (f,) = (data / "reports").glob("trades_balanced_wf_A_exp-wide_stop_*_tuned.csv")
    t = pd.read_csv(f)
    assert list(t.columns) == columns and len(t)
    assert np.allclose(t["pnl"], t["gross_pnl"] - t[list(COSTS)].sum(axis=1), atol=1e-6)
    # the synthetic dips start after 10:30, so the fixed 10:30 cutoff never trades: still a readable file
    (f,) = (data / "reports").glob("trades_balanced_wf_A_exp-mornings_*_default.csv")
    assert list(pd.read_csv(f).columns) == columns and not len(pd.read_csv(f))
    (f,) = (data / "reports").glob("trades_balanced_wf_A_exp-mornings_*_tuned.csv")
    assert not (pd.read_csv(f)["entry_m"] > 90).any()
    find = experiments_summary.find_report
    _, rep = find(data / "reports", "balanced", "mornings")
    assert rep["summary"]["default"]["trades"] == 0 and rep["summary"]["default"]["costs"]["mean_bp_gross"] is None
    _, rep = find(data / "reports", "balanced", "high_vix")
    assert rep["summary"]["dsr"]["n_trials"] == 12 + 26  # its own search plus everything logged before it
    assert rep["summary"]["tuned"]["months_with_trades"] == 2  # VIX was 14 in July and August: no entries
    assert [f["test_trades"] > 0 for f in rep["summary"]["per_fold"]] == [False, False, True, True]
    assert rep["default_params"]["vix_min"] == 22.0 and rep["train_months"] == 2


def test_summary_table_and_resume_check(runs, monkeypatch, capsys):
    data, uni = runs
    reports = data / "reports"
    find = experiments_summary.find_report
    assert find(reports, "balanced", "mornings", FIRST, str(LAST), uni.name)
    assert find(reports, "balanced", "mornings", FIRST, "2026-11-06", uni.name) is None  # another window: run it
    assert find(reports, "balanced", "control") is None
    monkeypatch.setattr(sys, "argv", ["experiments_summary.py", "--data", str(data), "--has", "control"])
    with pytest.raises(SystemExit) as e:
        experiments_summary.main()
    assert e.value.code == 1
    monkeypatch.setattr(sys, "argv", ["experiments_summary.py", "--data", str(data)])
    experiments_summary.main()
    text = (reports / "experiments_summary.md").read_text()
    assert text in capsys.readouterr().out
    lines = [x for x in text.splitlines() if x.startswith("| ")]
    assert lines[0] == "| " + " | ".join(experiments_summary.COLUMNS) + " |"
    assert [x.split(" | ")[0] for x in lines[1:]] == [
        "| mornings (tuned monthly)", "| mornings (fixed cutoff 10:30)", "| wide_stop (tuned monthly)",
        "| wide_stop (fixed stop 3 ATR)", "| high_vix (tuned monthly)", "| high_vix (fixed VIX > 22)"]
    assert lines[2].endswith("| n/a |")  # no trades at the fixed cutoff here: nothing to deflate
    assert experiments_summary.fixed_setting({"entry_cutoff_m": 30, "atr_mult": 2.5}, ["entry_cutoff_m", "atr_mult"]) \
        == "cutoff 10:00, stop 2.5 ATR"
    assert all(len(x.split(" | ")) == len(experiments_summary.COLUMNS) for x in lines)
    assert "Not run yet: control, mornings_wide_stop." in text and "all 39 configurations" in text


def test_walk_forward_sets_the_ca_bundle_itself(runs, monkeypatch):
    """The `runs` fixture called walk_forward.main(); with SSL_CERT_FILE unset it must point at certifi's bundle."""
    import certifi

    from kaibot.ingest.certs import ensure_ca_bundle

    monkeypatch.setenv("SSL_CERT_FILE", "x")
    monkeypatch.delenv("SSL_CERT_FILE")
    assert ensure_ca_bundle() == certifi.where()


def test_run_experiments_script_is_complete():
    sh = (ROOT / "scripts" / "run_experiments.sh").read_text()
    for needle in (".venv/bin/python", "SSL_CERT_FILE", "KAIBOT_ENV_FILE", "$HOME/Projects/kaibot-secrets/.env",
                   "2016-01-01", "2026-11-06", "config/universe_history.yaml",
                   "control mornings wide_stop high_vix mornings_wide_stop", "--has", "experiments_summary.py"):
        assert needle in sh
    assert (ROOT / "scripts" / "run_experiments.sh").stat().st_mode & 0o111
    for script in ("walk_forward.py", "trial_report.py"):
        assert "    ensure_ca_bundle()" in (ROOT / "scripts" / script).read_text()
    assert "scipy" in (ROOT / "requirements-research.txt").read_text()


# ---------------------------------------------------------------- 4. breakdown script


def test_analyze_trades_tables(runs, capsys, monkeypatch):
    data, uni = runs
    (f,) = (data / "reports").glob("trades_balanced_wf_A_exp-wide_stop_*_default.csv")
    t = analyze_trades.add_groups(analyze_trades.load_trades(f), analyze_trades.sectors(uni))
    for col in ("year", "reason", "entry_time", "vix_bucket", "sector"):
        b = analyze_trades.breakdown(t, col, 100_000.0)
        assert b.loc["all", "trades"] == len(t) == b["trades"].iloc[:-1].sum()
        assert b["total_r"].iloc[:-1].sum() == pytest.approx(b.loc["all", "total_r"], abs=0.5)
        assert b.loc["all", "net_bp"] == pytest.approx(t["pnl"].mean() / 10, abs=1e-3)
        assert (b["gross_bp"] >= b["net_bp"]).all()
    assert set(analyze_trades.breakdown(t, "sector", 1e5).index) == {"XLK", "XLF", "all"}
    assert set(analyze_trades.breakdown(t, "vix_bucket", 1e5).index) == {"<15", "15-25", "all"}
    assert analyze_trades.breakdown(t, "reason", 1e5).loc["all", "mean_r"] == pytest.approx(
        (t["pnl"] / t["risk"]).mean(), abs=1e-3)
    cs = analyze_trades.cost_summary(t, 100_000.0)
    assert cs["total_usd"].iloc[:-1].sum() == pytest.approx(cs.loc["net", "total_usd"], abs=0.05)
    monkeypatch.setattr(sys, "argv", ["analyze_trades.py", str(f), "--universe", str(uni)])
    analyze_trades.main()
    out = capsys.readouterr().out
    for title in ("Gross vs net", "By year", "By exit", "By entry time (ET)", "By VIX (previous close)", "By sector"):
        assert f"== {title} ==" in out
    (empty,) = (data / "reports").glob("trades_balanced_wf_A_exp-mornings_*_default.csv")
    monkeypatch.setattr(sys, "argv", ["analyze_trades.py", str(empty)])
    analyze_trades.main()
    assert "0 trades" in capsys.readouterr().out


def test_analyze_trades_reads_files_from_before_the_cost_split(runs, tmp_path, capsys, monkeypatch):
    data, uni = runs
    (f,) = (data / "reports").glob("trades_balanced_wf_A_exp-wide_stop_*_default.csv")
    new = pd.read_csv(f)
    old = tmp_path / "old.csv"
    new.drop(columns=["gross_pnl", *COSTS, "vix"]).to_csv(old, index=False)
    monkeypatch.setattr(sys, "argv", ["analyze_trades.py", str(old), "--universe", str(uni), "--vix",
                                      str(data / "inputs" / "fred_vixcls.json")])
    analyze_trades.main()
    out = capsys.readouterr().out
    assert "no cost columns in this file" in out and "n/a" in out and "== By VIX (previous close) ==" in out
    t = analyze_trades.load_trades(old)
    vix = analyze_trades.vix_prev_close(data / "inputs" / "fred_vixcls.json", t["date"])
    assert np.allclose(vix.to_numpy(), new["vix"].to_numpy())  # the cache gives what the replay saw


# ---------------------------------------------------------------- 5. turnover divide-by-zero


def _quiet_open(sd: SymbolDays, days: slice, upto: int) -> SymbolDays:
    v = sd.v.copy()
    v[days, :upto] = 0.0  # no prints in the first `upto` minutes of these sessions
    return SymbolDays(sd.symbol, sd.dates, sd.o, sd.h, sd.l, sd.c, v, sd.vw)


def test_turnover_zero_volume_gives_no_warning_and_no_candidate(mkt):
    stocks, etfs, sector_of, _ = mkt
    lo = LAST - np.timedelta64(200, "D")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        base = prepare(stocks, etfs, sector_of, {}, lo, LAST).cands
        quiet = _quiet_open(stocks["AAA"], slice(100, 140), 40)
        cands = prepare({**stocks, "AAA": quiet}, etfs, sector_of, {}, lo, LAST).cands
        aaa = candidates(quiet, etfs[sector_of["AAA"]], np.array([], dtype="datetime64[s]"))
    # numbers taken on main (d73927c), where the second line raised "divide by zero encountered in divide"
    assert (len(base), float(base["turnover"].sum())) == (329, pytest.approx(328.7123231923563, rel=1e-12))
    assert (len(cands), float(cands["turnover"].sum())) == (328, pytest.approx(327.48941818070097, rel=1e-12))
    assert int(cands["m"].sum()) == 51572 and np.isfinite(cands["turnover"]).all()
    dates, _, _, _, _, _, _, avgcum = daily_stats(quiet, etfs[sector_of["AAA"]])
    zero = {(dates[d], m) for d, m in zip(*np.nonzero(avgcum == 0), strict=True)}
    assert len(zero) > 500  # 20 sessions x the first 40 minutes
    assert not zero & set(zip(aaa["date"].to_numpy().astype("datetime64[D]"), aaa["m"], strict=True))
    others = cands[cands["symbol"] != "AAA"].reset_index(drop=True)
    assert others.equals(base[base["symbol"] != "AAA"].reset_index(drop=True))
