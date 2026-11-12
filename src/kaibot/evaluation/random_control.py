"""Matched random-entry control for multi-day rules (decision 15 section 7, Gate B mark 3; audit G1 and G3).

The question it answers: did the rule's choice of NAMES add anything, or would any portfolio with the same
timetable have done as well? The M4 baseline (a random minute, intraday exits, no limits) cannot be matched to a
rule that holds for weeks, and it once passed a rule with no edge.

Matching. The rule's realised schedule is a list of slots, one per position it opened:
(entry session, sessions held, share of equity put in). A random portfolio keeps EVERY slot and replaces only
the name: for each slot it draws uniformly from the names that were eligible on the entry session (the same
universe and liquidity filter the rule chose from) and that it does not already hold. So on every session the
random portfolio holds the same number of names with the same committed exposure, and its holding periods are
the rule's own. What differs is which names, and that is the skill being tested.

The simulator is not here. `simulate(selection)` is any callable that turns one name per slot into the daily
net returns of that portfolio, with the same costs and fill model as the rule's own run (for #113 that is the
multi-day engine driven by a rule that replays the slots; docs/ops/gate_v2.md has the hand-off). This module
draws the selections, calls it at least 500 times and reports where the rule's net Sharpe falls.

Percentile = share of random portfolios whose Sharpe is strictly below the rule's, in percent. Ties count
against the rule. Tests use a toy simulator in which the truth is known: a rule with no skill lands near the
50th percentile on average, a rule with planted skill lands above the 90th.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

PERIODS = 252
UNFILLED = -1  # a slot for which no eligible name was free


@dataclass(frozen=True)
class Slot:
    entry: int  # index of the session the position was opened
    hold: int  # sessions it was held; it is in the book on sessions entry .. entry + hold
    weight: float  # share of equity put into it at entry (its exposure)


def schedule_from_trades(entry: Sequence[int], hold: Sequence[int], weight: Sequence[float]) -> list[Slot]:
    """The realised schedule from a trade table: per trade the entry session's index, the sessions held and
    the position's value at entry divided by the account's equity at entry."""
    if not len(entry) == len(hold) == len(weight):
        raise ValueError("entry, hold and weight must have one value per trade")
    out = [Slot(int(e), int(h), float(w)) for e, h, w in zip(entry, hold, weight, strict=True)]
    if any(s.entry < 0 or s.hold < 0 or not s.weight >= 0.0 for s in out):
        raise ValueError("a slot needs entry >= 0, hold >= 0 and weight >= 0")
    return out


def schedule_profile(schedule: Sequence[Slot], n_sessions: int) -> dict:
    """What "matched" means, as arrays: names held and exposure committed on each session, and the sorted
    holding periods. Identical for the rule and for every random portfolio that filled all its slots."""
    names, exposure = np.zeros(n_sessions, dtype=int), np.zeros(n_sessions)
    for s in schedule:
        a, b = min(s.entry, n_sessions), min(s.entry + s.hold + 1, n_sessions)
        names[a:b] += 1
        exposure[a:b] += s.weight
    return {"names_held": names, "exposure": exposure, "holds": np.sort([s.hold for s in schedule])}


class _Eligible:
    """The eligible ids per session, sorted and without duplicates; read once and kept for every run."""

    def __init__(self, eligible) -> None:
        self._get = eligible.__getitem__ if isinstance(eligible, Mapping) else eligible
        self._cache: dict[int, np.ndarray] = {}

    def __call__(self, t: int) -> np.ndarray:
        pool = self._cache.get(t)
        if pool is None:
            pool = self._cache[t] = np.unique(np.asarray(self._get(t), dtype=int))
        return pool


def _draw(schedule: Sequence[Slot], order: Sequence[int], elig: _Eligible, rng: np.random.Generator) -> np.ndarray:
    out = np.full(len(schedule), UNFILLED, dtype=int)
    first = rng.random(len(schedule))  # one uniform number per slot up front: most slots need no second try
    held: dict[int, int] = {}  # name id -> last session it is in the book
    for k in order:
        s = schedule[k]
        if held:
            held = {j: last for j, last in held.items() if last >= s.entry}
        pool = elig(s.entry)
        n = pool.size
        if not n:
            continue
        pick = int(pool[int(first[k] * n)])
        if pick in held:  # rejection sampling: uniform over the free names, cheap while few are held
            pick = UNFILLED
            for _ in range(64):
                j = int(pool[int(rng.random() * n)])
                if j not in held:
                    pick = j
                    break
            else:
                free = pool[~np.isin(pool, list(held))]
                if free.size:
                    pick = int(free[int(rng.random() * free.size)])
        if pick != UNFILLED:
            out[k] = pick
            held[pick] = s.entry + s.hold
    return out


def _order(schedule: Sequence[Slot]) -> list[int]:
    return sorted(range(len(schedule)), key=lambda k: schedule[k].entry)  # stable: ties keep the schedule's order


def draw_selection(schedule: Sequence[Slot], eligible, rng: np.random.Generator) -> np.ndarray:
    """One random portfolio: a name id for every slot (in the order of `schedule`), `UNFILLED` where none was
    free. `eligible` maps a session index to the ids that may be bought that session (a callable or a mapping).
    Each slot draws uniformly from the eligible names it does not already hold: a name is never held twice at
    once, so a slot cannot take a name that an earlier slot still holds on its entry session."""
    return _draw(schedule, _order(schedule), eligible if isinstance(eligible, _Eligible) else _Eligible(eligible), rng)


def annual_sharpe(returns, periods: int = PERIODS) -> float:
    r = np.asarray(returns, dtype=float)
    sd = r.std(ddof=1) if r.size > 1 else 0.0
    return float(r.mean() / sd * math.sqrt(periods)) if sd > 0 else 0.0


@dataclass(frozen=True)
class ControlResult:
    percentile: float  # share of random portfolios with a Sharpe strictly below the rule's, in %
    strategy_sharpe: float  # annualised, net
    runs: int
    seed: int
    slots: int
    unfilled: int  # slots, over all runs, that found no free eligible name
    matched: bool  # every random portfolio filled every slot, so exposure and holding periods are the rule's
    random_p05: float
    random_median: float
    random_p90: float
    random_p95: float
    simulated_strategy_sharpe: float | None = None  # the rule's own names through `simulate`, when given
    percentile_simulated: float | None = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def matched_random_control(strategy_returns, schedule: Sequence[Slot], eligible,
                           simulate: Callable[[np.ndarray], Sequence[float]], *, runs: int = 500, seed: int = 0,
                           periods: int = PERIODS, strategy_selection: Sequence[int] | None = None,
                           ) -> ControlResult:
    """Percentile of the rule's net Sharpe among `runs` random portfolios with the same schedule.

    strategy_returns: the rule's own daily net mark-to-market returns (from its real run).
    schedule, eligible: see `draw_selection`. simulate(selection) -> daily net returns over the same sessions.
    Run k draws from its own stream seeded by (seed, k), so a run can be repeated on its own.
    strategy_selection: the names the rule really held, one per slot. When given, they are run through
    `simulate` too: `simulated_strategy_sharpe` should be close to `strategy_sharpe`, otherwise the control
    simulator is not the rule's simulator and the percentile compares two different things."""
    r = np.asarray(strategy_returns, dtype=float)
    if runs < 1:
        raise ValueError("runs must be at least 1")
    if not len(schedule):
        raise ValueError("the schedule is empty: a rule that never traded has nothing to match")
    own = annual_sharpe(r, periods)
    sharpes = np.empty(runs)
    unfilled = 0
    order, elig = _order(schedule), _Eligible(eligible)
    for k in range(runs):
        sel = _draw(schedule, order, elig, np.random.default_rng(np.random.SeedSequence([seed, k])))
        unfilled += int((sel == UNFILLED).sum())
        sim = np.asarray(simulate(sel), dtype=float)
        if sim.shape != r.shape:
            raise ValueError(f"simulate returned {sim.shape[0] if sim.ndim else 0} daily returns, the rule has "
                             f"{r.size}: both must cover the same sessions")
        sharpes[k] = annual_sharpe(sim, periods)
    sim_own = pct_sim = None
    if strategy_selection is not None:
        sim_own = annual_sharpe(simulate(np.asarray(strategy_selection, dtype=int)), periods)
        pct_sim = float((sharpes < sim_own).mean() * 100.0)
    q = np.quantile(sharpes, [0.05, 0.5, 0.9, 0.95])
    return ControlResult(float((sharpes < own).mean() * 100.0), own, runs, seed, len(schedule), unfilled,
                         unfilled == 0, float(q[0]), float(q[1]), float(q[2]), float(q[3]), sim_own, pct_sim)
