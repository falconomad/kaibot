import numpy as np

from kaibot.replay.data import N_MIN, SymbolDays
from kaibot.replay.spy_momentum import run
from kaibot.risk.profiles import load_profiles


def spy(n=120, seed=0, drift_after_10=0.0):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.0004, (n, N_MIN))
    r[:, 30:] += drift_after_10
    gap = rng.normal(0, 0.004, n)
    c = np.empty((n, N_MIN))
    p = 400.0
    for i in range(n):
        c[i] = p * (1 + gap[i]) * np.exp(np.cumsum(r[i]))
        p = c[i, -1]
    o = np.c_[c[:, :1], c[:, :-1]]
    dates = np.arange(np.datetime64("2025-01-02"), np.datetime64("2025-01-02") + np.timedelta64(n, "D"))
    return SymbolDays("SPY", dates, o, np.maximum(o, c) + 0.02, np.minimum(o, c) - 0.02, c, np.full_like(c, 5e5), c)


def test_one_trade_max_per_day_and_sized_by_gate():
    pol = load_profiles()["balanced"]
    sd = spy()
    trades, daily = run(sd, sd.dates[70], sd.dates[-1], pol)
    assert len(daily) == 50 and len(trades) <= 50 and len({t["date"] for t in trades}) == len(trades)
    assert trades, "fixture should trigger some days"
    for t in trades:
        assert t["z"] > 0.5 and t["reason"] in ("moc", "stop")
        assert t["risk"] <= 100_000 * pol.risk_per_trade_pct / 100 * 1.1  # equity drifts a little
        assert t["qty"] * t["entry"] <= 1.1 * 100_000 * pol.max_position_pct / 100


def test_deterministic():
    pol = load_profiles()["aggressive"]
    sd = spy(seed=4)
    assert run(sd, sd.dates[70], sd.dates[-1], pol) == run(sd, sd.dates[70], sd.dates[-1], pol)
