"""M5 (decision 12, S43/S44): the owner's Strategy config, applied at the session open. Pure and property-tested.

`tighten()` turns the level's policy plus a config body (docs/m5/contract.md) into the `Rules` the engine trades
under for the day. Each setting can only make Kaibot pickier or smaller:
- a value above the level, below its floor, or of the wrong type is REJECTED (ConfigError), never clamped;
- the result is validated against both the level policy and HARD_CEILINGS.

The engine refuses the session (fails closed) on ConfigError. A clamped config would trade differently from what
the owner reviewed, so clamping is never used.

Locked, never configurable: the news veto, the 15:55 flatten, brackets, quote freshness, no shorts / leverage /
overnight, and strategy entry thresholds."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace

from kaibot.risk.gate import HARD_CEILINGS, RiskPolicy, validate_policy

OPEN_MIN = 9 * 60 + 30
FIRST_ENTRY_M = 5  # 09:35
LAST_ENTRY_M = 375  # 15:45 on a normal day
LEVEL_PAUSE_LOSSES, LEVEL_PAUSE_MINUTES = 3, 60
LEVEL_VIX_MAX = 35.0  # the crisis gate (regime OFF above 35)
LEVEL_EVENT_WINDOW = 30
LEVEL_MIN_PRICE, LEVEL_MIN_ADV, LEVEL_MAX_SPREAD = 5.0, 50e6, 5.0

# field: (floor, policy attribute, integer?)
RISK_FIELDS = {
    "risk_per_trade_pct": (0.10, "risk_per_trade_pct", False),
    "max_positions": (1, "max_positions", True),
    "max_position_pct": (5.0, "max_position_pct", False),
    "max_entries_per_day": (1, "max_entries_per_day", True),
    "daily_stop_pct": (0.25, "max_daily_loss_pct", False),
    "run_drawdown_end_pct": (2.0, "run_drawdown_end_pct", False),
}
MIN_ONLY_SYMBOLS = 5
MAX_BODY_BYTES = 8192


class ConfigError(ValueError):
    """The config would loosen the engine or is malformed: the session is refused."""


@dataclass(frozen=True)
class Rules:
    version: int  # 0 = the level's own defaults
    policy: RiskPolicy
    strategies: tuple[str, ...]
    mode: str = "all"  # all | prefer | only
    focus: frozenset[str] = frozenset()
    excluded: frozenset[str] = frozenset()  # never traded, in every mode
    first_entry_m: int = FIRST_ENTRY_M
    last_entry_m: int = LAST_ENTRY_M
    pause_losses: int = LEVEL_PAUSE_LOSSES
    pause_minutes: int = LEVEL_PAUSE_MINUTES
    vix_max: float = LEVEL_VIX_MAX
    event_window_min: int = LEVEL_EVENT_WINDOW
    min_price_usd: float = LEVEL_MIN_PRICE
    min_adv_usd: float = LEVEL_MIN_ADV
    max_spread_bp: float = LEVEL_MAX_SPREAD
    config_sha: str = ""

    def tradable(self, symbol: str) -> bool:
        if symbol in self.excluded:
            return False
        return self.mode != "only" or symbol in self.focus

    def rank(self, symbol: str) -> int:
        """Sort key: focus names first in Prefer mode (0 before 1); everything equal otherwise."""
        return 0 if (self.mode == "prefer" and symbol in self.focus) else (1 if self.mode == "prefer" else 0)


def defaults(level_policy: RiskPolicy, level_strategies: list[str] | tuple[str, ...]) -> Rules:
    return Rules(0, level_policy, tuple(level_strategies))


def body_sha(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def tighten(level_policy: RiskPolicy, level_strategies: list[str] | tuple[str, ...], body: dict | None, *,
            version: int, universe: set[str] | frozenset[str], themes: dict[str, list[str]]) -> Rules:
    """The day's rules. Raises ConfigError on anything that is not a pure tightening of the level."""
    if body is None or body == {}:
        return defaults(level_policy, level_strategies)
    if not isinstance(body, dict):
        raise ConfigError("config must be an object")
    if len(json.dumps(body)) > MAX_BODY_BYTES:
        raise ConfigError("config too large")
    if body.get("schema", 1) != 1:
        raise ConfigError(f"unknown config schema {body.get('schema')!r}")
    _only_keys(body, {"schema", "preset", "boldness", "focus", "risk", "strategies", "timing", "advanced"}, "config")

    # ---- risk ----
    risk = _obj(body.get("risk"), "risk")
    _only_keys(risk, set(RISK_FIELDS), "risk")
    changes = {}
    for key, (floor, attr, integer) in RISK_FIELDS.items():
        if key not in risk:
            continue
        lv = getattr(level_policy, attr)
        v = _num(risk[key], f"risk.{key}", integer)
        if v < floor or v > lv:
            raise ConfigError(f"risk.{key}={v} outside {floor}..{lv} (the level's value)")
        changes[attr] = int(v) if integer else float(v)
    pol = replace(level_policy, **changes)
    if "max_daily_loss_pct" in changes:  # open risk may never exceed the room left before the daily stop
        pol = replace(pol, max_open_risk_pct=min(level_policy.max_open_risk_pct, pol.max_daily_loss_pct))
    validate_policy(pol, level_policy)
    validate_policy(pol, HARD_CEILINGS)

    # ---- strategies ----
    st = _obj(body.get("strategies"), "strategies")
    known = {"dip_reversal", "spy_late_momentum"}
    _only_keys(st, known, "strategies")
    on = [s for s in level_strategies if st.get(s, True) is True]
    for s, v in st.items():
        if not isinstance(v, bool):
            raise ConfigError(f"strategies.{s} must be true or false")
        if v and s not in level_strategies:
            raise ConfigError(f"{s} is not part of this level")
    if not on:
        raise ConfigError("at least one strategy must stay on")

    # ---- focus ----
    fo = _obj(body.get("focus"), "focus")
    _only_keys(fo, {"themes", "symbols", "exclude", "mode"}, "focus")
    mode = fo.get("mode", "all")
    if mode not in ("all", "prefer", "only"):
        raise ConfigError(f"focus.mode {mode!r} is not all, prefer or only")
    sel: set[str] = set()
    for t in _list(fo.get("themes"), "focus.themes"):
        if t not in themes:
            raise ConfigError(f"unknown theme {t!r}")
        sel |= set(themes[t])
    for s in _list(fo.get("symbols"), "focus.symbols"):
        if s not in universe:
            raise ConfigError(f"{s!r} is not in the universe")
        sel.add(s)
    excluded = set()
    for s in _list(fo.get("exclude"), "focus.exclude"):
        if s not in universe:
            raise ConfigError(f"{s!r} is not in the universe")
        excluded.add(s)
    sel -= excluded
    if mode == "only" and len(sel) < MIN_ONLY_SYMBOLS:
        raise ConfigError(f"'Only these' needs at least {MIN_ONLY_SYMBOLS} stocks (has {len(sel)})")
    if mode == "all":
        sel = set()

    # ---- timing ----
    ti = _obj(body.get("timing"), "timing")
    _only_keys(ti, {"entry_start", "entry_end", "pause_losses", "pause_minutes", "vix_max", "event_window_min"},
               "timing")
    first = _hhmm(ti.get("entry_start", "09:35"), "timing.entry_start")
    last = _hhmm(ti.get("entry_end", "15:45"), "timing.entry_end")
    if not (FIRST_ENTRY_M <= first < last <= LAST_ENTRY_M) or last - first < 30:
        raise ConfigError("entry window must sit inside 09:35-15:45 and be at least 30 minutes long")
    pl = int(_num(ti.get("pause_losses", LEVEL_PAUSE_LOSSES), "timing.pause_losses", True))
    pm = int(_num(ti.get("pause_minutes", LEVEL_PAUSE_MINUTES), "timing.pause_minutes", True))
    vx = float(_num(ti.get("vix_max", LEVEL_VIX_MAX), "timing.vix_max"))
    ew = int(_num(ti.get("event_window_min", LEVEL_EVENT_WINDOW), "timing.event_window_min", True))
    if not 2 <= pl <= LEVEL_PAUSE_LOSSES:
        raise ConfigError("timing.pause_losses must be 2 or 3")
    if not LEVEL_PAUSE_MINUTES <= pm <= 240:
        raise ConfigError("timing.pause_minutes must be 60..240")
    if not 20 <= vx <= LEVEL_VIX_MAX:
        raise ConfigError("timing.vix_max must be 20..35")
    if not LEVEL_EVENT_WINDOW <= ew <= 90:
        raise ConfigError("timing.event_window_min must be 30..90")

    # ---- advanced ----
    ad = _obj(body.get("advanced"), "advanced")
    _only_keys(ad, {"min_price_usd", "min_adv_usd", "max_spread_bp"}, "advanced")
    mp = float(_num(ad.get("min_price_usd", LEVEL_MIN_PRICE), "advanced.min_price_usd"))
    ma = float(_num(ad.get("min_adv_usd", LEVEL_MIN_ADV), "advanced.min_adv_usd"))
    ms = float(_num(ad.get("max_spread_bp", LEVEL_MAX_SPREAD), "advanced.max_spread_bp"))
    if mp < LEVEL_MIN_PRICE or mp > 1000:
        raise ConfigError("advanced.min_price_usd must be 5..1000")
    if ma < LEVEL_MIN_ADV or ma > 50e9:
        raise ConfigError("advanced.min_adv_usd must be at least 50,000,000")
    if not 1 <= ms <= LEVEL_MAX_SPREAD:
        raise ConfigError("advanced.max_spread_bp must be 1..5")

    return Rules(version=int(version), policy=pol, strategies=tuple(on), mode=mode, focus=frozenset(sel),
                 excluded=frozenset(excluded),
                 first_entry_m=first, last_entry_m=last, pause_losses=pl, pause_minutes=pm, vix_max=vx,
                 event_window_min=ew, min_price_usd=mp, min_adv_usd=ma, max_spread_bp=ms, config_sha=body_sha(body))


def is_tightening(r: Rules, level_policy: RiskPolicy, level_strategies) -> bool:
    """Invariant used by the property tests: every field is at least as strict as the level."""
    p = r.policy
    return all([
        p.risk_per_trade_pct <= level_policy.risk_per_trade_pct, p.max_positions <= level_policy.max_positions,
        p.max_position_pct <= level_policy.max_position_pct, p.max_entries_per_day <= level_policy.max_entries_per_day,
        p.max_daily_loss_pct <= level_policy.max_daily_loss_pct, p.max_open_risk_pct <= level_policy.max_open_risk_pct,
        p.run_drawdown_end_pct <= level_policy.run_drawdown_end_pct, p.max_quote_age_s <= level_policy.max_quote_age_s,
        not p.allow_short, set(r.strategies) <= set(level_strategies), len(r.strategies) >= 1,
        r.first_entry_m >= FIRST_ENTRY_M, r.last_entry_m <= LAST_ENTRY_M, r.pause_losses <= LEVEL_PAUSE_LOSSES,
        r.pause_minutes >= LEVEL_PAUSE_MINUTES, r.vix_max <= LEVEL_VIX_MAX, r.event_window_min >= LEVEL_EVENT_WINDOW,
        r.min_price_usd >= LEVEL_MIN_PRICE, r.min_adv_usd >= LEVEL_MIN_ADV, r.max_spread_bp <= LEVEL_MAX_SPREAD,
        r.mode != "only" or len(r.focus) >= MIN_ONLY_SYMBOLS,
    ])


# ---------------- helpers ----------------
def _obj(v, where: str) -> dict:
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ConfigError(f"{where} must be an object")
    return v


def _list(v, where: str) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v) or len(v) > 100:
        raise ConfigError(f"{where} must be a list of names")
    return v


def _only_keys(d: dict, allowed: set[str], where: str) -> None:
    extra = set(d) - allowed
    if extra:
        raise ConfigError(f"unknown {where} setting(s): {', '.join(sorted(extra))}")


def _num(v, where: str, integer: bool = False) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise ConfigError(f"{where} must be a number")
    if integer and float(v) != int(v):
        raise ConfigError(f"{where} must be a whole number")
    return float(v)


def _hhmm(v, where: str) -> int:
    """'HH:MM' ET -> session minute of the decision (09:35 -> 5)."""
    if not isinstance(v, str) or len(v) != 5 or v[2] != ":" or not (v[:2] + v[3:]).isdigit():
        raise ConfigError(f"{where} must be HH:MM")
    h, m = int(v[:2]), int(v[3:])
    if h > 23 or m > 59:
        raise ConfigError(f"{where} must be HH:MM")
    return h * 60 + m - OPEN_MIN
