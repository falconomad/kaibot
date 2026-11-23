"""The index core's settings and its portfolio builder (M11 ticket 2.7, issue #458; docs/prereg/index_core.md;
docs/research/event_driven/design.md section 2.9). Pure: reads `config/index_core.yaml`, returns numbers.

Research only. Nothing in the live engine imports this module: `config/index_core.yaml` says `enabled: false`, the
live profiles hold no index fund (`BookLimits.max_index_pct` is 0 for every level of config/profiles.yaml), and the
live daily path's builder (`engine/live_daily.live_builder`) names no index symbol.

`book_limits(level)` is the book an index core is tested under at a level: the hard ceilings (gap scenario 15%,
40% sector cap, 30 names, the 1% cash floor), the level's per-name cap for the other names, the level's exposure
cap as gross and overnight, and the index fund's own cap. `builder(level)` is the ranked fill in index mode on that
book: k = 0.1% (the momentum rules' sizing for any other name), the 10% band, the 3% cash reserve.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from kaibot.risk.gate import BOOK_HARD_CEILINGS, BookLimits, validate_book
from kaibot.risk.portfolio import PortfolioBuilder

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "index_core.yaml"
PROFILES = Path(__file__).resolve().parents[3] / "config" / "profiles.yaml"
K = 0.001  # shares = equity x k / ATR(20) for every name but the index (the momentum rules' k)
BAND = 0.10  # no trade within 10% of the target


@dataclass(frozen=True)
class Level:
    exposure_pct: float
    max_index_pct: float


@dataclass(frozen=True)
class IndexCoreConfig:
    enabled: bool
    index_symbols: tuple[str, ...]
    cash_reserve_pct: float
    levels: dict[str, Level]
    path: Path


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) or v < 0:
        raise ValueError(f"{what}={v!r} must be a number >= 0")
    return float(v)


def load(path: Path | str = DEFAULT_PATH) -> IndexCoreConfig:
    """`config/index_core.yaml`, validated. Each level's index cap is at most BOOK_HARD_CEILINGS.max_index_pct and
    at most its exposure cap less the cash reserve; the exposure cap at most the hard ceiling; the levels are those
    of config/profiles.yaml. Anything else refuses to load (ValueError)."""
    path = Path(path)
    cfg = yaml.safe_load(path.read_text()) or {}
    on = cfg.get("enabled")
    if not isinstance(on, bool):
        raise ValueError(f"{path.name}: enabled must be true or false")
    syms = tuple(str(s) for s in cfg.get("index_symbols") or ())
    if len(syms) != 1:
        raise ValueError(f"{path.name}: index_symbols must name exactly one fund (the universe's market symbol)")
    reserve = _num(cfg.get("cash_reserve_pct"), "cash_reserve_pct")
    known = set((yaml.safe_load(PROFILES.read_text()) or {}).get("levels") or {})
    raw = cfg.get("levels") or {}
    if set(raw) != known:
        raise ValueError(f"{path.name}: levels must be exactly {sorted(known)}")
    levels = {}
    for name, body in raw.items():
        exp = _num((body or {}).get("exposure_pct"), f"levels.{name}.exposure_pct")
        idx = _num((body or {}).get("max_index_pct"), f"levels.{name}.max_index_pct")
        if exp > BOOK_HARD_CEILINGS.max_gross_pct:
            raise ValueError(f"levels.{name}.exposure_pct={exp:g} loosens the hard ceiling")
        if idx > BOOK_HARD_CEILINGS.max_index_pct:
            raise ValueError(f"levels.{name}.max_index_pct={idx:g} loosens the hard ceiling "
                             f"({BOOK_HARD_CEILINGS.max_index_pct:g})")
        if idx > exp - reserve + 1e-9:
            raise ValueError(f"levels.{name}.max_index_pct={idx:g} is above the exposure cap {exp:g} less the "
                             f"{reserve:g}% cash reserve")
        levels[name] = Level(exp, idx)
    return IndexCoreConfig(on, syms, reserve, levels, path)


def book_limits(level: str, cfg: IndexCoreConfig | None = None, *, name_pct: float | None = None) -> BookLimits:
    """The book an index core is tested under at `level` (module docstring). `name_pct`: the per-name cap of the
    other names (default: the level's `max_position_pct` of config/profiles.yaml)."""
    cfg = cfg or load()
    lv = cfg.levels[level]
    if name_pct is None:
        name_pct = float(yaml.safe_load(PROFILES.read_text())["levels"][level]["max_position_pct"])
    out = replace(BOOK_HARD_CEILINGS, max_name_pct=float(name_pct), max_gross_pct=lv.exposure_pct,
                  max_overnight_pct=lv.exposure_pct, max_index_pct=lv.max_index_pct)
    validate_book(out)
    return out


def builder(level: str, cfg: IndexCoreConfig | None = None, *, sleeve_budget_pct: float | None = None,
            k: float = K, limits: BookLimits | None = None) -> PortfolioBuilder:
    """The ranked fill in index mode at `level` (or on `limits`), with the 3% cash reserve and the 10% band."""
    cfg = cfg or load()
    return PortfolioBuilder(limits or book_limits(level, cfg), k=k, band=BAND, fill="ranked",
                            cash_reserve=cfg.cash_reserve_pct / 100, index_symbol=cfg.index_symbols[0],
                            sleeve_budget_pct=sleeve_budget_pct)


def sleeve_builder(level: str, budget_pct: float, sleeve_k: float, sleeve_name_pct: float,
                   cfg: IndexCoreConfig | None = None) -> PortfolioBuilder:
    """#459 (docs/prereg/index_core_trend_momentum.md): the core with one return sleeve at `budget_pct` of equity,
    the sleeve scaled to its budget: k = the rule's k x budget / 100 (`sleeve_k`) and the per-name cap the rule's x
    budget / 100 (`sleeve_name_pct`), so the sleeve is the tested rule at a fraction of its size."""
    if not 0 < budget_pct <= 100 or not 0 < sleeve_k <= BOOK_HARD_CEILINGS.atr_risk_pct / 100:
        raise ValueError("the budget must be in (0, 100] and the sleeve's k inside the hard ceiling")
    cfg = cfg or load()
    return builder(level, cfg, sleeve_budget_pct=budget_pct, k=sleeve_k,
                   limits=book_limits(level, cfg, name_pct=sleeve_name_pct))
