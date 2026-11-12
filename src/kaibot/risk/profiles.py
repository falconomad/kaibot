"""Load aggressiveness profiles from config/profiles.yaml into validated RiskPolicy objects."""

from __future__ import annotations

from pathlib import Path

import yaml

from kaibot.risk.gate import BookLimits, RiskPolicy, validate_book, validate_policy

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "profiles.yaml"


def load_profiles(path: Path = DEFAULT_PATH) -> dict[str, RiskPolicy]:
    cfg = yaml.safe_load(path.read_text())
    common = cfg["all_levels"]
    out: dict[str, RiskPolicy] = {}
    for name, lv in cfg["levels"].items():
        pol = RiskPolicy(
            risk_per_trade_pct=lv["risk_per_trade_pct"],
            max_position_pct=lv["max_position_pct"],
            # PRD v2: open risk may never exceed the room left before the daily stop
            max_open_risk_pct=lv["daily_stop_pct"],
            max_daily_loss_pct=lv["daily_stop_pct"],
            max_positions=lv["max_positions"],
            max_entries_per_day=lv["max_entries_per_day"],
            run_drawdown_end_pct=lv["run_drawdown_end_pct"],
            max_quote_age_s=common["max_quote_age_s"],
            allow_short=common["allow_short"],
        )
        validate_policy(pol)
        out[name] = pol
    return out


def load_strategies(path: Path = DEFAULT_PATH) -> dict[str, list[str]]:
    """Which live strategies each level runs (profiles.yaml `strategies`). Shadow strategies are separate."""
    cfg = yaml.safe_load(path.read_text())
    from kaibot.strategies.registry import DEFAULT_LIVE

    return {name: list(lv.get("strategies") or DEFAULT_LIVE) for name, lv in cfg["levels"].items()}


def load_book_limits(path: Path = DEFAULT_PATH, *, research: bool = False) -> dict[str, BookLimits]:
    """Book limits per level for multi-day sleeves (profiles.yaml `multi_day`, PROVISIONAL until the stakeholder
    confirms them, decision 15 section 6), validated against BOOK_HARD_CEILINGS.

    Live (`research=False`): while `all_levels.allow_overnight` is false no multi-day sleeve may hold anything over
    the close, so `max_overnight_pct` is 0. `research=True` (the ten-year test with SimBroker) keeps the values."""
    cfg = yaml.safe_load(path.read_text())
    md = cfg["multi_day"]["levels"]
    overnight_ok = bool(cfg["all_levels"].get("allow_overnight", False))
    out: dict[str, BookLimits] = {}
    for name in cfg["levels"]:
        lv = dict(md[name])
        lim = BookLimits(**{k: (int(v) if k == "max_names" else float(v)) for k, v in lv.items()})
        if not (research or overnight_ok):
            lim = BookLimits(**{**lim.__dict__, "max_overnight_pct": 0.0})
        validate_book(lim)
        out[name] = lim
    return out


def book_limits_provisional(path: Path = DEFAULT_PATH) -> bool:
    return bool(yaml.safe_load(path.read_text())["multi_day"].get("provisional", True))
