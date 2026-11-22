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
    with pytest.raises(SystemExit, match="fixture only"):
        script.main(["--data", str(tmp_path), "--min-decision-days", "10", "--trials", ""])
