"""M12 ticket 3.12 (#474): the gradient-boosted rank overlay, docs/prereg/trend_momentum_gbm.md with Amendment 1.
No run on real data: the engine runs here are on the committed fixture."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date

import numpy as np
import pytest

from kaibot.engine.config import ROOT
from kaibot.strategies import momentum as mom
from kaibot.strategies import momentum_gbm as gb
from kaibot.strategies.base import Event
from kaibot.strategies.momentum import DailyContext

pytest.importorskip("sklearn")

sys.path.insert(0, str(ROOT / "scripts"))
import trend_momentum_gbm as script  # noqa: E402


@pytest.fixture(scope="module")
def fx():
    from kaibot.engine import daily_report as dr
    from kaibot.engine.daily import DailyData

    m, info = dr.load_fixture(dr.FIXTURE)
    data = DailyData.from_market(m)
    i0 = int(np.searchsorted(data.dates, np.datetime64(info["window"][0])))
    return data, i0, len(data.dates) - 1


def test_the_settings_are_the_pre_registered_ones():
    assert gb.GBM_PARAMS == {"max_iter": 200, "learning_rate": 0.05, "max_depth": 3, "min_samples_leaf": 200,
                             "l2_regularization": 1.0, "early_stopping": False, "random_state": 474}
    assert (gb.HORIZON, gb.EMBARGO, gb.MIN_DECISION_DAYS) == (21, 21, 52) and len(gb.FEATURES) == 8
    assert set(gb.NEIGHBOURS) == {"depth2", "lr10", "noscore", "refit6m", "ridge"}
    r = gb.TrendMomentumGbm()
    assert r.refit_months == (1,) and r.model == "gbm" and r.rule == "trend_momentum_gbm"
    text = (ROOT / "docs" / "prereg" / "trend_momentum_gbm.md").read_text()
    for phrase in ("max_iter=200", "learning_rate=0.05", "max_depth=3", "min_samples_leaf=200",
                   "l2_regularization=1.0", "random_state=474", "52 decision days", "21-session embargo"):
        assert phrase in text, phrase


def test_features_read_only_closes_at_or_before_the_decision():
    rng = np.random.default_rng(474)
    c = 100 * np.cumprod(1 + rng.normal(0, 0.01, size=(400, 25)), axis=0)
    adv = np.abs(rng.normal(1e8, 1e7, size=c.shape))
    cols = np.arange(25)
    a = gb.features_at(c, adv, 300, cols)
    later, adv2 = c.copy(), adv.copy()
    later[301:] *= 4.0
    adv2[301:] = 1.0
    assert np.allclose(a, gb.features_at(later, adv2, 300, cols), equal_nan=True)
    assert a.shape == (25, 8) and np.nanmin(a) > 0 and np.nanmax(a) <= 1  # ranks
    assert gb.features_at(c, adv, 300, cols, use_score=False).shape == (25, 7)


def test_training_targets_end_21_sessions_before_the_refit_and_come_from_weekly_decisions(fx, monkeypatch):
    data, _, i1 = fx
    seen = []
    real = gb.target_at

    def spy(c, spy_col, t, cols):
        seen.append(t)
        return real(c, spy_col, t, cols)

    monkeypatch.setattr(gb, "target_at", spy)
    k = i1
    x, y, days = gb.TrendMomentumGbm().training_rows(data.view(k), k)
    assert seen and days == len(seen) and max(seen) + gb.HORIZON <= k - gb.EMBARGO
    assert all(mom.is_weekly_decision(data.day(t), data.day(t + 1)) for t in seen)
    assert x.shape[0] == y.shape[0] and x.shape[1] == 8


def test_the_model_changes_only_the_order_of_the_new_buys(fx):
    data, i0, i1 = fx
    r = gb.TrendMomentumGbm(min_decision_days=10)
    base = mom.VERSIONS["trend_momentum"]
    k = next(k for k in range(i1 - 60, i1) if base.decision_day(data.day(k), data.day(k + 1)))
    r._last_refit = None
    ctx = DailyContext(Event.AFTER_CLOSE, data.day(k), data.day(k + 1), data.view(k), {})
    t = r.on_event(ctx)
    t0 = base.on_event(DailyContext(Event.AFTER_CLOSE, data.day(k), data.day(k + 1), data.view(k), {}))
    assert r.fitted is not None and r.refits[-1]["used"] and t.features["order"] == "model"
    assert set(t.weights) == set(t0.weights) and t.resize == t0.resize  # the same names, sizes, exits
    assert sorted(t.features["buy"]) == sorted(t0.features["buy"])


def test_refits_once_a_year_or_twice_for_refit6m():
    r, h = gb.TrendMomentumGbm(), gb.TrendMomentumGbm(refit_months=(1, 7))
    fills = [date(2023, m, 15) for m in range(1, 13)] + [date(2024, 1, 10)]
    assert [f for f in fills if r._refit_due(f)] == [date(2023, 1, 15), date(2024, 1, 10)]
    assert [f for f in fills if h._refit_due(f)] == [date(2023, 1, 15), date(2023, 7, 15), date(2024, 1, 10)]


def test_below_52_decision_days_the_regression_order_stands(fx):
    data, i0, i1 = fx
    r = gb.TrendMomentumGbm()  # the frozen 52: the fixture's two years never reach it in the window
    from kaibot.engine.daily import run_daily
    from kaibot.risk.profiles import load_profiles

    run = run_daily(r, data, load_profiles()["balanced"], i0, i1)
    assert r.refits and not any(x["used"] for x in r.refits) and r.fitted is None
    assert set(r.order_days) == {"regression"}  # reported: every decision day kept the regression order
    ref = run_daily(mom.VERSIONS["trend_momentum"], data, load_profiles()["balanced"], i0, i1)
    assert run.orders == ref.orders  # without a model it is trend_momentum, order for order


def test_scikit_learn_never_reaches_live_code():
    code = ("import sys; import kaibot.engine.__main__, kaibot.engine.core, kaibot.engine.live_daily, "
            "kaibot.engine.daily, kaibot.risk.gate, kaibot.strategies.registry, kaibot.strategies.momentum_gbm; "
            "assert 'sklearn' not in sys.modules, 'sklearn imported'")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT,
                       env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    users = [p.relative_to(ROOT).as_posix() for p in (ROOT / "src" / "kaibot").rglob("*.py")
             if "sklearn" in p.read_text()]
    assert users == ["src/kaibot/strategies/momentum_gbm.py"]
    from kaibot.strategies import registry

    assert "trend_momentum_gbm" not in {s.name for s in registry.MULTI_DAY}
    assert "scikit-learn" not in (ROOT / "requirements-engine.txt").read_text()
    assert "scikit-learn" not in (ROOT / "requirements-research.txt").read_text()


def test_the_dependency_is_pinned_and_hashed_in_its_own_group():
    import tomllib

    py = tomllib.loads((ROOT / "pyproject.toml").read_text())
    ml = py["project"]["optional-dependencies"]["ml"]
    assert len(ml) == 1 and ml[0].startswith("scikit-learn==")
    text = (ROOT / "requirements-ml.txt").read_text()
    block = text[text.index("scikit-learn=="):].split("\n\n")[0]
    assert "--hash=sha256:" in block
    assert "requirements-ml.txt" in (ROOT / "scripts" / "lock_deps.sh").read_text()
    assert "requirements-ml.txt" in (ROOT / ".github" / "workflows" / "supply-chain.yml").read_text()


def test_the_script_on_the_fixture(tmp_path):
    trials = tmp_path / "t.jsonl"
    out = script.main(["--fixture", "--data", str(tmp_path), "--out", str(tmp_path / "o"), "--trials", str(trials),
                       "--control-runs", "20", "--min-decision-days", "10"])
    rows = [json.loads(x) for x in trials.read_text().splitlines()]
    assert sum(r["n_trials"] for r in rows) == 6 and len(rows) == 11
    assert {r["prereg_sha256"] is not None for r in rows} == {True}
    assert out["control"]["pool"] == "candidate_pool" and out["cost_model"] == "v1"
    assert out["gate_v3_sleeve"]["tier"] == "full" and "vs_rule" in out and "suspect" in out["leak_check"]
    assert any(r["used"] for row in out["rows"] for r in row["refits"])
    rule = next(row for row in out["rows"] if row["label"] == "rule|1x")
    assert set(rule["order_days"]) <= {"regression", "model"} and rule["order_days"].get("regression", 0) > 0
    with pytest.raises(SystemExit, match="fixture only"):
        script.main(["--data", str(tmp_path), "--min-decision-days", "10", "--trials", ""])


def _future_changed(data, k: int):
    """A copy of `data` whose bars after row k are different (every price and volume scaled per row and column, a
    third of the names delisted);
    rows <= k, and every derived array's rows <= k, are the same."""
    import dataclasses

    rng = np.random.default_rng(4741)
    kw = {}
    for f in ("sig_c", "sig_h", "sig_l", "raw_o", "raw_c", "raw_h", "raw_l", "raw_v", "v_first", "v_last",
              "sig_unfilled_c"):
        a = np.array(getattr(data, f), dtype=float, copy=True)
        a[k + 1:] *= rng.uniform(0.3, 3.0, size=a[k + 1:].shape)
        kw[f] = a
    gone = rng.random(len(data.symbols)) < 0.3  # and a third of the names have no bar at all after k
    gone[data.spy] = False
    kw["raw_c"][k + 1:, gone] = np.nan
    return dataclasses.replace(data, **kw)


def test_no_decision_or_refit_reads_a_bar_after_the_decision_close(fx):
    """QA (#586): the whole decision at row k (refit rows, targets, the universe, the features and the prediction)
    is the same when every bar after k is different."""
    data, i0, i1 = fx
    base = mom.VERSIONS["trend_momentum"]
    ks = [k for k in range(i1 - 40, i1 - 5) if base.decision_day(data.day(k), data.day(k + 1))][-3:]
    checked = 0
    for k in ks:
        other = _future_changed(data, k)
        out = []
        for d in (data, other):
            r = gb.TrendMomentumGbm(min_decision_days=10)
            t = r.on_event(DailyContext(Event.AFTER_CLOSE, d.day(k), d.day(k + 1), d.view(k), {}))
            x, y, days = r.training_rows(d.view(k), k)
            out.append((t.features["buy"], t.features["order"], r.refits, x, y, days))
        (b1, o1, f1, x1, y1, n1), (b2, o2, f2, x2, y2, n2) = out
        assert b1 == b2 and o1 == o2 and f1 == f2 and n1 == n2, k
        assert np.array_equal(x1, x2, equal_nan=True) and np.array_equal(y1, y2, equal_nan=True), k
        checked += o1 == "model" and len(b1) > 1
    assert len(ks) == 3 and checked >= 2  # the model's order was really used on these days


def test_a_training_row_reads_nothing_after_its_target_and_its_universe_nothing_after_its_day(fx):
    data, _, i1 = fx
    r = gb.TrendMomentumGbm()
    t = next(t for t in range(i1 - 120, i1) if mom.is_weekly_decision(data.day(t), data.day(t + 1)))
    other = _future_changed(data, t)
    assert np.array_equal(r._universe(data.view(t), t), r._universe(other.view(t), t))
    other = _future_changed(data, t + gb.HORIZON)
    cols = r._universe(data.view(t), t)
    assert np.array_equal(gb.target_at(data.sig_c, data.spy, t, cols), gb.target_at(other.sig_c, other.spy, t, cols),
                          equal_nan=True)


def test_the_embargo_boundary_is_exact_on_every_session(fx):
    """Every weekly decision row t with t + 21 <= k - 21 (and all its data) is a training row, and no later one:
    checked for 30 consecutive refit rows k, so an off-by-one on either side fails."""
    data, _, i1 = fx
    for k in range(i1 - 30, i1):
        seen = []
        real = gb.target_at

        def spy(c, spy_col, t, cols, seen=seen, real=real):
            seen.append(t)
            return real(c, spy_col, t, cols)

        gb.target_at = spy
        try:
            gb.TrendMomentumGbm().training_rows(data.view(k), k)
        finally:
            gb.target_at = real
        last = max(t for t in range(gb.LOOKBACK, k) if t + gb.HORIZON <= k - gb.EMBARGO
                   and mom.is_weekly_decision(data.day(t), data.day(t + 1)))
        assert max(seen) == last, k


def test_cost_model_v2_is_refused_before_any_data_is_read(tmp_path, monkeypatch):
    def no_read(*a, **k):
        raise AssertionError("data read")

    monkeypatch.setattr(script.dr, "load_store", no_read)
    monkeypatch.setattr(script.dr, "load_fixture", no_read)
    if "cost_model" in script.inspect.signature(script.run_daily).parameters:
        pytest.skip("#572 merged: the runner takes v2")
    with pytest.raises(SystemExit, match="#572"):
        script.main(["--data", str(tmp_path), "--cost-model", "v2", "--trials", ""])


def test_a_sharpe_above_one_is_a_leak_and_no_verdict():
    assert script.judge("pass", True, 1.01) == (True, "suspect: data leak")
    assert script.judge("fail", False, 1.5) == (True, "suspect: data leak")
    assert script.judge("pass", True, 1.0) == (False, "pass")
    assert script.judge("pass", False, 0.4) == (False, "fail")  # a pass that does not beat trend_momentum
    assert script.judge("fail", True, 0.4) == (False, "fail")
    assert script.judge("pass", True, float("nan")) == (False, "pass")


def test_0x_costs_fill_at_the_close_and_differ_from_1x_only_through_cash(fx):
    """#474 (24 Nov run: rule 0x CAGR 9.78% below 1x 10.32%): the cost multiple is wired the right way round and
    0x is not a special case. At 0x every fill is at the session's close (spread and impact zero, only the
    regulatory fees on sales); at 1x a buy pays more and a sale gets less. The two accounts start identical and
    differ only by the cash the costs took, so until a whole-share count rounds differently they send the same
    orders; the first order that differs is the same name and side with at least as many shares at 0x (more cash
    on a larger account). From there the books part ways (path dependence through cash and share counts), which
    is how a cost-free run can end below the costed one on real data. No bug: this pins the mechanism."""
    from kaibot.engine.daily import run_daily
    from kaibot.risk.profiles import load_profiles

    data, i0, i1 = fx
    pol = load_profiles()["balanced"]
    r0, r1 = (run_daily(gb.TrendMomentumGbm(min_decision_days=10), data, pol, i0, i1, cost=c) for c in (0.0, 1.0))
    priced = [f for f in r0.fills if f["reason"] != "delisted"]
    assert priced and all(f["px"] == pytest.approx(float(data.raw_c[f["i"], data.col(f["symbol"])]), abs=1e-9)
                          for f in priced)
    assert sum(t.cost_spread + t.cost_impact for t in r0.book.trades) == 0.0
    assert sum(t.cost_spread + t.cost_impact for t in r1.book.trades) > 0.0
    first0, first1 = r0.fills[0], r1.fills[0]
    assert (first0["i"], first0["symbol"], first0["side"], first0["qty"]) == (
        first1["i"], first1["symbol"], first1["side"], first1["qty"])
    assert first0["side"] == "buy" and first1["px"] > first0["px"]
    sold = next(f for f in r1.fills if f["side"] == "sell" and f["reason"] != "delisted")
    assert sold["px"] < float(data.raw_c[sold["i"], data.col(sold["symbol"])])
    eq0 = [float(d["end"]) for d in r0.book.daily]
    eq1 = [float(d["end"]) for d in r1.book.daily]
    k = next(n for n, (a, b) in enumerate(zip(r0.orders, r1.orders, strict=False)) if a != b)
    assert r0.orders[:k] == r1.orders[:k]
    (s0, kind0, sym0, q0), (s1, kind1, sym1, q1) = r0.orders[k], r1.orders[k]
    assert (s0, kind0, sym0) == (s1, kind1, sym1) and kind0 in ("buy", "resize") and q0 > q1
    day = next(n for n, d in enumerate(r0.book.daily) if d["date"] == data.dates[s0])
    assert all(a >= b for a, b in zip(eq0[:day], eq1[:day], strict=True))  # 0x is the larger account until then
