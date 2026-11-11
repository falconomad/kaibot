"""M5 S43/S49: the owner's config can only tighten. Property tests over random configs x every level: either the
config is rejected, or every resulting rule is at least as strict as the level (and within HARD_CEILINGS)."""

from __future__ import annotations

import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from kaibot.engine.config import ROOT
from kaibot.engine.userconfig import ConfigError, Rules, defaults, is_tightening, tighten
from kaibot.risk.gate import HARD_CEILINGS, validate_policy
from kaibot.risk.profiles import load_profiles, load_strategies

PROFILES, STRATS = load_profiles(), load_strategies()
UCFG = yaml.safe_load((ROOT / "config" / "universe_seed.yaml").read_text())
UNIVERSE = frozenset(s for v in UCFG["sectors"].values() for s in v)
THEMES = {k: v["symbols"] for k, v in UCFG["themes"].items()}


def T(level, body, version=1):
    return tighten(PROFILES[level], STRATS[level], body, version=version, universe=UNIVERSE, themes=THEMES)


# ---------------- examples ----------------
def test_empty_config_is_the_level():
    for lv in PROFILES:
        r = T(lv, {})
        assert r == defaults(PROFILES[lv], STRATS[lv]) and r.version == 0


def test_tighter_values_apply():
    r = T("aggressive", {"risk": {"risk_per_trade_pct": 0.5, "max_positions": 4, "daily_stop_pct": 1.0},
                         "strategies": {"spy_late_momentum": False}, "timing": {"entry_start": "10:00",
                                                                                 "vix_max": 25}})
    assert r.policy.risk_per_trade_pct == 0.5 and r.policy.max_positions == 4
    assert r.policy.max_daily_loss_pct == 1.0 and r.policy.max_open_risk_pct == 1.0  # open risk follows the stop
    assert r.strategies == ("dip_reversal",) and r.first_entry_m == 30 and r.vix_max == 25


def test_rejects_anything_looser():
    bad = [
        {"risk": {"risk_per_trade_pct": 0.6}},                      # Steady's level is 0.25
        {"risk": {"max_positions": 4}},                             # level 3
        {"risk": {"risk_per_trade_pct": 0.05}},                     # below the floor
        {"risk": {"max_positions": 2.5}},                           # not whole
        {"risk": {"risk_per_trade_pct": True}},                     # booleans are not numbers
        {"risk": {"risk_per_trade_pct": float("nan")}},
        {"risk": {"max_quote_age_s": 10}},                          # not configurable at all
        {"strategies": {"spy_late_momentum": True}},                # not part of Steady
        {"strategies": {"dip_reversal": False}},                    # nothing left on
        {"timing": {"entry_start": "09:31"}},                       # before 09:35
        {"timing": {"entry_end": "15:50"}},                         # after 15:45
        {"timing": {"entry_start": "11:00", "entry_end": "11:20"}},  # under 30 minutes
        {"timing": {"pause_losses": 4}}, {"timing": {"pause_minutes": 30}},
        {"timing": {"vix_max": 40}}, {"timing": {"event_window_min": 10}},
        {"advanced": {"min_price_usd": 1}}, {"advanced": {"max_spread_bp": 8}}, {"advanced": {"min_adv_usd": 1e6}},
        {"focus": {"mode": "only", "themes": ["energy"]}},          # 3 symbols < 5
        {"focus": {"themes": ["crypto"]}}, {"focus": {"symbols": ["GME"]}}, {"focus": {"mode": "loose"}},
        {"allow_short": True}, {"schema": 2}, {"risk": []}, [],
        {"focus": {"symbols": ["A"] * 101}},
    ]
    for b in bad:
        try:
            T("conservative", b)
        except ConfigError:
            continue
        raise AssertionError(f"accepted {b}")


def test_focus_modes():
    only = T("balanced", {"focus": {"mode": "only", "themes": ["hyperscalers", "semiconductors"], "exclude": ["INTC"]}})
    assert only.mode == "only" and "INTC" not in only.focus and len(only.focus) == 10
    assert only.tradable("MSFT") and not only.tradable("DUK")
    pre = T("balanced", {"focus": {"mode": "prefer", "themes": ["energy"]}})
    assert pre.tradable("DUK") and pre.rank("XOM") < pre.rank("DUK")
    allm = T("balanced", {"focus": {"mode": "all", "themes": ["energy"]}})
    assert allm.focus == frozenset() and allm.rank("XOM") == allm.rank("DUK")


def test_config_sha_identifies_the_body():
    a = T("balanced", {"risk": {"max_positions": 4}})
    b = T("balanced", {"risk": {"max_positions": 4}})
    c = T("balanced", {"risk": {"max_positions": 3}})
    assert a.config_sha == b.config_sha != c.config_sha


# ---------------- properties ----------------
num = st.one_of(st.floats(min_value=-5, max_value=60), st.sampled_from([float("nan"), float("inf"), -float("inf")]),
                st.integers(min_value=-5, max_value=60), st.booleans(), st.none(), st.text(max_size=3))
risk = st.dictionaries(st.sampled_from(["risk_per_trade_pct", "max_positions", "max_position_pct",
                                        "max_entries_per_day", "daily_stop_pct", "run_drawdown_end_pct",
                                        "max_quote_age_s"]), num, max_size=7)
hhmm = st.builds(lambda h, m: f"{h:02d}:{m:02d}", st.integers(8, 17), st.sampled_from([0, 5, 15, 30, 31, 35, 45, 50]))
timing = st.fixed_dictionaries({}, optional={
    "entry_start": hhmm, "entry_end": hhmm, "pause_losses": num, "pause_minutes": st.integers(0, 400),
    "vix_max": num, "event_window_min": st.integers(0, 200)})
focus = st.fixed_dictionaries({}, optional={
    "themes": st.lists(st.sampled_from([*THEMES, "nope"]), max_size=4),
    "symbols": st.lists(st.sampled_from([*sorted(UNIVERSE), "GME"]), max_size=6),
    "exclude": st.lists(st.sampled_from(sorted(UNIVERSE)), max_size=4),
    "mode": st.sampled_from(["all", "prefer", "only", "x"])})
strategies = st.dictionaries(st.sampled_from(["dip_reversal", "spy_late_momentum", "orb"]), st.booleans(), max_size=3)
advanced = st.fixed_dictionaries({}, optional={
    "min_price_usd": num, "min_adv_usd": st.floats(0, 1e11, allow_nan=False), "max_spread_bp": num})
body = st.fixed_dictionaries({}, optional={"risk": risk, "timing": timing, "focus": focus, "strategies": strategies,
                                           "advanced": advanced, "preset": st.sampled_from(["custom", "calm"]),
                                           "boldness": st.integers(0, 4)})


@settings(max_examples=600, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(level=st.sampled_from(sorted(PROFILES)), b=body)
def test_property_never_loosens(level, b):
    try:
        r = T(level, b)
    except ConfigError:
        return
    assert isinstance(r, Rules)
    assert is_tightening(r, PROFILES[level], STRATS[level]), (level, b, r)
    validate_policy(r.policy, HARD_CEILINGS)
    validate_policy(r.policy, PROFILES[level])


@settings(max_examples=300, deadline=None)
@given(level=st.sampled_from(sorted(PROFILES)),
       vals=st.fixed_dictionaries({k: st.floats(0, 1, allow_nan=False) for k in (
           "risk_per_trade_pct", "max_position_pct", "daily_stop_pct", "run_drawdown_end_pct")}))
def test_property_in_range_values_are_accepted(level, vals):
    """Anything between the floor and the level is a legal tightening: the panel's sliders can never be refused."""
    from kaibot.engine.userconfig import RISK_FIELDS

    lp = PROFILES[level]
    risk_ = {}
    for k, frac in vals.items():
        floor, attr, _ = RISK_FIELDS[k]
        risk_[k] = round(floor + frac * (getattr(lp, attr) - floor), 2)
        risk_[k] = min(max(risk_[k], floor), getattr(lp, attr))
    r = T(level, {"risk": risk_})
    assert is_tightening(r, lp, STRATS[level])


def test_excluded_stocks_are_never_traded_in_any_mode():
    for mode in ("all", "prefer", "only"):
        r = T("balanced", {"focus": {"mode": mode, "themes": ["defensives"], "exclude": ["DUK"]}})
        assert not r.tradable("DUK"), mode
    assert T("balanced", {"focus": {"mode": "all", "exclude": ["DUK"]}}).tradable("NEE")
