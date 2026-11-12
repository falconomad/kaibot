"""Every strategy Kaibot knows, in the order the engine asks them (issue #116).

The engine, the owner-config validator (engine/userconfig.py), the SQL validator (migration 0016's
`kaibot.strategy_registry`, a test keeps it equal to this file), the gate report and the research command take
their strategy names from here. Which live strategies a level trades is `config/profiles.yaml` (`strategies`);
a registered live strategy that no level lists does not trade.

Order matters and is part of the engine's behaviour: within one minute the engine asks the live strategies in
this order (dip reversal's 15:30 scan runs before SPY momentum's 15:30 decision), and the shadow strategies after
the minute's ledger row, ORB before news continuation.

To add a rule: write its pre-registration in docs/prereg/ first, implement `strategies.base.Strategy` in its own
module, add one line below, list its files under `fingerprint.strategies` in config/gate.yaml, and add a row to
`kaibot.strategy_registry` in a migration. It starts in `Mode.SHADOW` or unlisted by any level.
"""

from __future__ import annotations

from kaibot.strategies.base import Holding, Mode, Strategy
from kaibot.strategies.dip import DipReversal
from kaibot.strategies.momentum import VERSIONS as _MOMENTUM
from kaibot.strategies.news_continuation import NewsContinuation
from kaibot.strategies.orb import OrbStocksInPlay
from kaibot.strategies.spy_momentum import SpyLateMomentum

STRATEGIES: tuple[Strategy, ...] = (DipReversal(), SpyLateMomentum(), OrbStocksInPlay(), NewsContinuation())
BY_NAME: dict[str, Strategy] = {s.name: s for s in STRATEGIES}
assert len(BY_NAME) == len(STRATEGIES), "strategy names must be unique"

# What a level without a `strategies` list runs (risk/profiles.load_strategies) and what an engine built without a
# strategy map runs (tests, replays).
DEFAULT_LIVE: tuple[str, ...] = (DipReversal.name,)

# Registered, still runnable by the research command, but in no level's list. An owner config saved before the
# retirement may still say `true` for one of these: that is ignored (it can never switch the rule on), not an
# error that would refuse the whole session. Product decision on #128 (12 Nov 2026): SPY late momentum retired.
RETIRED: frozenset[str] = frozenset({SpyLateMomentum.name})


# Multi-day rules (issue #161): runnable only by the research command through the engine's daily path
# (engine/daily.py). They are NOT in STRATEGIES (so not in the SQL registry, no level, no owner toggle), and the live
# engine refuses them by name: no multi-day rule trades until #118-#120 and Gate B (decision 15 section 7).
MULTI_DAY: tuple = tuple(_MOMENTUM.values())
RESEARCH_ONLY: dict = {s.name: s for s in MULTI_DAY}
assert not set(RESEARCH_ONLY) & set(BY_NAME), "a multi-day rule must not share a name with a registered one"


class RefusedStrategy(ValueError):
    """A multi-day strategy was asked for on the live path."""


def refuse_multi_day(names_on) -> None:
    """Raise when `names_on` names a multi-day strategy: the live engine trades intraday rules only (#161)."""
    bad = sorted(n for n in set(names_on or ()) if n in RESEARCH_ONLY
                 or (n in BY_NAME and BY_NAME[n].holding is Holding.MULTI_DAY))
    if bad:
        raise RefusedStrategy(f"multi-day strategies cannot run live yet (#118-#120 and Gate B): {', '.join(bad)}")


def get(name: str) -> Strategy | None:
    return BY_NAME.get(name) or RESEARCH_ONLY.get(name)


def names(mode: Mode | None = None) -> tuple[str, ...]:
    return tuple(s.name for s in STRATEGIES if mode is None or s.mode is mode)


def live(names_on) -> tuple[Strategy, ...]:
    """The registered live strategies among `names_on`, in registry order. Unknown names are ignored; a multi-day
    strategy is refused (`RefusedStrategy`)."""
    refuse_multi_day(names_on)
    on = set(names_on)
    return tuple(s for s in STRATEGIES if s.mode is Mode.LIVE and s.name in on)


def shadow(only=None) -> tuple[Strategy, ...]:
    """The registered shadow strategies (or those among `only`), in registry order."""
    return tuple(s for s in STRATEGIES if s.mode is Mode.SHADOW and (only is None or s.name in set(only)))


def owner_toggles() -> frozenset[str]:
    """The strategies an owner config may switch on or off (`strategies` in docs/m5/contract.md): the live ones."""
    return frozenset(names(Mode.LIVE))
