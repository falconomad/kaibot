from kaibot.risk.profiles import load_profiles


def test_profiles_load_and_respect_ceilings():
    p = load_profiles()
    assert set(p) == {"conservative", "balanced", "aggressive"}
    assert p["conservative"].max_daily_loss_pct == 0.75 and p["aggressive"].max_position_pct == 20
    assert all(not x.allow_short and x.max_quote_age_s == 5 for x in p.values())
    # daily stop = 3R at every level
    assert all(abs(x.max_daily_loss_pct / x.risk_per_trade_pct - 3) < 1e-9 for x in p.values())
