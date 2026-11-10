"""Load aggressiveness profiles from config/profiles.yaml into validated RiskPolicy objects."""

from __future__ import annotations

from pathlib import Path

import yaml

from kaibot.risk.gate import RiskPolicy, validate_policy

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
    return {name: list(lv.get("strategies") or ["dip_reversal"]) for name, lv in cfg["levels"].items()}
