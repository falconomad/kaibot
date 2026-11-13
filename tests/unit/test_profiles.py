from kaibot.risk.profiles import load_profiles


def test_profiles_load_and_respect_ceilings():
    p = load_profiles()
    assert set(p) == {"conservative", "balanced", "aggressive"}
    assert p["conservative"].max_daily_loss_pct == 0.75 and p["aggressive"].max_position_pct == 20
    assert all(not x.allow_short and x.max_quote_age_s == 5 for x in p.values())
    # daily stop = 3R at every level
    assert all(abs(x.max_daily_loss_pct / x.risk_per_trade_pct - 3) < 1e-9 for x in p.values())


def test_book_limits_load_confirmed_and_inside_the_ceilings():
    """#119: multi-day book limits per level only tighten BOOK_HARD_CEILINGS; live, overnight exposure stays 0 while
    allow_overnight is false. #206: confirmed by the stakeholder on 13 Nov 2026 as the pre-registered limits."""
    from dataclasses import asdict

    from kaibot.risk.gate import BOOK_HARD_CEILINGS, validate_book
    from kaibot.risk.profiles import book_limits_provisional, load_book_limits

    assert not book_limits_provisional()
    research, live, pol = load_book_limits(research=True), load_book_limits(), load_profiles()
    assert set(research) == set(live) == set(pol)
    for name, lim in research.items():
        validate_book(lim, BOOK_HARD_CEILINGS)
        assert lim.max_name_pct == pol[name].max_position_pct  # the per-name cap comes from the level
        assert lim.atr_risk_pct == 0.1
        assert (lim.max_names, lim.max_sector_pct, lim.min_cash_pct, lim.max_gap_loss_pct) == (30, 40, 1, 15)
        assert live[name].max_overnight_pct == 0
        assert {k: v for k, v in asdict(live[name]).items() if k != "max_overnight_pct"} == {
            k: v for k, v in asdict(lim).items() if k != "max_overnight_pct"}
    # decision 15: tests run at Balanced with a 100% exposure cap
    assert research["balanced"].max_gross_pct == 100 and research["balanced"].max_overnight_pct == 100


def test_book_limits_that_loosen_a_ceiling_do_not_load(tmp_path):
    import pytest
    import yaml

    from kaibot.risk.profiles import DEFAULT_PATH, load_book_limits

    cfg = yaml.safe_load(DEFAULT_PATH.read_text())
    cfg["multi_day"]["levels"]["aggressive"]["max_sector_pct"] = 60
    p = tmp_path / "profiles.yaml"
    p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError, match="max_sector_pct"):
        load_book_limits(p, research=True)
