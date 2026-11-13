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

import os
from dataclasses import dataclass

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


# Multi-day rules (issue #161): runnable by the research command through the engine's daily path (engine/daily.py).
# They are NOT in STRATEGIES (so not in the SQL registry, no level, no owner toggle), and the live engine refuses
# them by name unless KAIBOT_MULTIDAY=on (S80, #120): the fail-safes for overnight positions are in place, so the
# first paper start of a multi-day rule (#133, after Gate B) is one explicit switch, off by default.
MULTI_DAY: tuple = tuple(_MOMENTUM.values())
RESEARCH_ONLY: dict = {s.name: s for s in MULTI_DAY}
assert not set(RESEARCH_ONLY) & set(BY_NAME), "a multi-day rule must not share a name with a registered one"


@dataclass(frozen=True)
class Evidence:
    """What the evidence gate said about a multi-day rule (decision 15 sections 7 and 11). The engine journal
    (`session_start`) and the heartbeat (`detail.experiments`) carry it so the dashboard can label the rule.

    `status`: `failed_gate_b` | `passed_gate_b`. `experiment`: the stakeholder lets the rule trade on paper although
    it failed Gate B (section 11); such a run is NOT Gate C of a passed rule. `real_money` is False for every rule
    here: no rule below may ever trade real money (Gate D is the stakeholder's, and only for a rule that passed)."""

    status: str
    experiment: bool
    label: str
    decided: str  # ISO date of the decision
    decision: str  # where it is written
    real_money: bool = False


FAILED_GATE_B, PASSED_GATE_B = "failed_gate_b", "passed_gate_b"
_CORE_PIT = "docs/decisions/15_engine_v3.md section 11 (core_pit, 13 Nov 2026, code 7c0123e)"
EVIDENCE: dict[str, Evidence] = {
    "trend_momentum": Evidence(FAILED_GATE_B, True, "Experiment: failed the ten-year test", "2026-11-13",
                               _CORE_PIT),
    "mom_12_1_band": Evidence(FAILED_GATE_B, False, "Failed the ten-year test", "2026-11-13", _CORE_PIT),
}
assert set(EVIDENCE) <= set(RESEARCH_ONLY), "evidence is recorded for multi-day rules only"
assert not any(e.real_money for e in EVIDENCE.values()), "no rule that failed Gate B may trade real money"


def evidence(name: str) -> Evidence | None:
    """The recorded Gate B verdict of a multi-day rule (a neighbour `rule~x` has its rule's); None: not judged."""
    return EVIDENCE.get(str(name).split("~", 1)[0])


def may_run_on_paper(name: str) -> bool:
    """A multi-day rule may be switched on for paper only if it passed Gate B, or the stakeholder made it an
    experiment (decision 15 section 11). Not judged, or failed without the experiment decision: no."""
    e = evidence(name)
    return e is not None and (e.status == PASSED_GATE_B or e.experiment)


class RefusedStrategy(ValueError):
    """A multi-day strategy was asked for on the live path."""


MULTIDAY_FLAG = "KAIBOT_MULTIDAY"


def multi_day_enabled() -> bool:
    """S80 (#120): the live engine accepts multi-day rules only with KAIBOT_MULTIDAY=on (default off). Read at each
    call, from the environment only (decision 11: env-only settings)."""
    return os.environ.get(MULTIDAY_FLAG, "off").strip().lower() == "on"


def multi_day_names(names_on) -> list[str]:
    return sorted(n for n in set(names_on or ()) if n in RESEARCH_ONLY
                  or (n in BY_NAME and BY_NAME[n].holding is Holding.MULTI_DAY))


def refuse_multi_day(names_on) -> None:
    """Raise when `names_on` names a multi-day strategy and KAIBOT_MULTIDAY is not on: by default the live engine
    trades intraday rules only (#161, #120)."""
    bad = multi_day_names(names_on)
    if bad and not multi_day_enabled():
        raise RefusedStrategy(f"multi-day strategies are off on the live path ({MULTIDAY_FLAG}=on turns them on, "
                              f"after Gate B, #133): {', '.join(bad)}")


def get(name: str) -> Strategy | None:
    return BY_NAME.get(name) or RESEARCH_ONLY.get(name)


def names(mode: Mode | None = None) -> tuple[str, ...]:
    return tuple(s.name for s in STRATEGIES if mode is None or s.mode is mode)


def live(names_on) -> tuple[Strategy, ...]:
    """The registered live strategies among `names_on`, in registry order, then (KAIBOT_MULTIDAY=on only) the
    multi-day rules named. Unknown names are ignored; a multi-day strategy with the flag off is refused
    (`RefusedStrategy`)."""
    refuse_multi_day(names_on)
    on = set(names_on)
    out = tuple(s for s in STRATEGIES if s.mode is Mode.LIVE and s.name in on)
    return out + tuple(s for s in MULTI_DAY if s.name in on)


def shadow(only=None) -> tuple[Strategy, ...]:
    """The registered shadow strategies (or those among `only`), in registry order."""
    return tuple(s for s in STRATEGIES if s.mode is Mode.SHADOW and (only is None or s.name in set(only)))


def owner_toggles() -> frozenset[str]:
    """The strategies an owner config may switch on or off (`strategies` in docs/m5/contract.md): the live ones."""
    return frozenset(names(Mode.LIVE))
