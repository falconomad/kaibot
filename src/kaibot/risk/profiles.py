"""Load aggressiveness profiles from config/profiles.yaml into validated RiskPolicy objects."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import yaml

from kaibot.risk.gate import BOOK_HARD_CEILINGS, BookLimits, RiskPolicy, tighten_book, validate_book, validate_policy

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
    """Which live strategies each level runs (profiles.yaml `strategies`). Shadow strategies are separate.

    With KAIBOT_MULTIDAY=on (read now, from the environment) the multi-day rules switched on in `multi_day.rules`
    are added to every level; with it off (the default) the lists are exactly the levels' own."""
    cfg = yaml.safe_load(path.read_text())
    from kaibot.strategies.registry import DEFAULT_LIVE, multi_day_enabled

    md = enabled_multi_day(path) if multi_day_enabled() else []
    return {name: [*(lv.get("strategies") or DEFAULT_LIVE), *md] for name, lv in cfg["levels"].items()}


@dataclass(frozen=True)
class MultiDayRule:
    """One multi-day rule's live settings (profiles.yaml `multi_day.rules`)."""

    name: str
    enabled: bool
    max_exposure_pct: dict[str, float]  # level -> gross and overnight cap of the rule (% of equity)
    retire_drawdown_pct: dict[str, float]  # level -> the experiment's retirement trigger (run drawdown, %)


def load_multi_day_rules(path: Path = DEFAULT_PATH) -> dict[str, MultiDayRule]:
    """`multi_day.rules`, validated: a known multi-day rule; a rule that is `enabled` passed Gate B or is a stakeholder
    experiment (registry.may_run_on_paper) and has a cap and a retirement trigger for every level; every cap is a
    number in (0, BOOK_HARD_CEILINGS.max_gross_pct] (tighten-only). Anything else refuses to load (ValueError)."""
    from kaibot.strategies import registry

    cfg = yaml.safe_load(path.read_text())
    levels = set(cfg["levels"])
    out: dict[str, MultiDayRule] = {}
    for name, body in ((cfg.get("multi_day") or {}).get("rules") or {}).items():
        if name not in registry.RESEARCH_ONLY:
            raise ValueError(f"multi_day.rules: {name!r} is not a multi-day rule")
        if True in body or False in body:  # YAML 1.1 reads a bare `on:` / `off:` key as a boolean
            raise ValueError(f"multi_day.rules.{name}: use `enabled: true|false`, not `on`/`off`")
        on = body.get("enabled", False)
        if not isinstance(on, bool):
            raise ValueError(f"multi_day.rules.{name}.enabled must be true or false")
        maps = {}
        for key in ("max_exposure_pct", "retire_drawdown_pct"):
            raw = body.get(key) or {}
            if set(raw) - levels:
                raise ValueError(f"multi_day.rules.{name}.{key}: unknown level(s) {sorted(set(raw) - levels)}")
            if on and set(raw) != levels:
                raise ValueError(f"multi_day.rules.{name}.{key} needs a value for every level")
            vals = {}
            for lv, v in raw.items():
                if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) or v <= 0:
                    raise ValueError(f"multi_day.rules.{name}.{key}.{lv}={v!r} must be a number > 0")
                vals[lv] = float(v)
            maps[key] = vals
        for lv, cap in maps["max_exposure_pct"].items():
            if cap > BOOK_HARD_CEILINGS.max_gross_pct:
                raise ValueError(f"multi_day.rules.{name}.max_exposure_pct.{lv}={cap:g} loosens the hard ceiling "
                                 f"({BOOK_HARD_CEILINGS.max_gross_pct:g})")
        if on and not registry.may_run_on_paper(name):
            raise ValueError(f"multi_day.rules.{name} is on, but it has not passed Gate B and is not a stakeholder "
                             "experiment (strategies/registry.EVIDENCE)")
        out[name] = MultiDayRule(name, on, maps["max_exposure_pct"], maps["retire_drawdown_pct"])
    return out


def enabled_multi_day(path: Path = DEFAULT_PATH) -> list[str]:
    """The multi-day rules switched on for the live (paper) path, in registry order (used only when
    KAIBOT_MULTIDAY=on)."""
    from kaibot.strategies import registry

    rules = load_multi_day_rules(path)
    return [s.name for s in registry.MULTI_DAY if s.name in rules and rules[s.name].enabled]


def rule_exposure_pct(name: str, level: str, path: Path = DEFAULT_PATH) -> float:
    """The exposure cap a multi-day rule trades under at `level` (the `exposure_pct` of engine/daily.momentum_limits
    and momentum_builder). A rule without a configured cap gets 0: it may hold nothing."""
    r = load_multi_day_rules(path).get(name)
    return float(r.max_exposure_pct.get(level, 0.0)) if r is not None else 0.0


def rule_book_limits(name: str, level: str, path: Path = DEFAULT_PATH, *, research: bool = False) -> BookLimits:
    """The level's multi-day book limits tightened by the rule's exposure cap: gross and overnight both at most the
    cap (never above the level's value: live with allow_overnight false the overnight cap stays 0). Validated
    against the level (tighten-only) and BOOK_HARD_CEILINGS."""
    base = load_book_limits(path, research=research)[level]
    cap = rule_exposure_pct(name, level, path)
    return tighten_book(base, {"max_gross_pct": min(base.max_gross_pct, cap),
                               "max_overnight_pct": min(base.max_overnight_pct, cap)})


def experiments(names_on, level: str, path: Path = DEFAULT_PATH) -> list[dict]:
    """The label rows for the multi-day rules among `names_on` that carry a Gate B verdict, for the engine journal
    (`session_start.experiments`) and the heartbeat (`detail.experiments`). Empty for an intraday-only session."""
    from kaibot.strategies import registry

    names = [n for n in registry.multi_day_names(names_on) if registry.evidence(n) is not None]
    if not names:
        return []
    rules = load_multi_day_rules(path)
    out = []
    for n in names:
        e, r = registry.evidence(n), rules.get(n)
        out.append({"strategy": n, "evidence": e.status, "experiment": e.experiment, "label": e.label,
                    "real_money": e.real_money, "decided": e.decided, "decision": e.decision, "level": level,
                    "max_exposure_pct": r.max_exposure_pct.get(level) if r else 0.0,
                    "retire_drawdown_pct": r.retire_drawdown_pct.get(level) if r else None})
    return out


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
