"""Matched random-entry control (issue #126, Gate B mark 3) against a toy market in which the truth is known:
a rule with no skill lands near the 50th percentile on average, a rule with planted skill lands above the 90th.

The toy simulator stands in for Backend-quant's multi-day engine: it only has to turn one name per slot into
daily net returns."""

from __future__ import annotations

import numpy as np
import pytest

from kaibot.evaluation import random_control as rc

T, M = 250, 30  # sessions, names


def schedule(every: int = 5, names: int = 2, hold: int = 10, weight: float = 0.25) -> list[rc.Slot]:
    """Every fifth session two new positions of a quarter of the equity each, held ten sessions."""
    return [rc.Slot(e, hold, weight) for e in range(1, T - hold - 1, every) for _ in range(names)]


class Toy:
    """A position entered at the close of session `entry` earns that name's return on the next `hold` sessions;
    each entry costs 5 bp of the position. Returns come from a (T x M) matrix the test controls."""

    def __init__(self, returns: np.ndarray, slots: list[rc.Slot], cost: float = 0.0005) -> None:
        self.r = returns
        self.open = np.zeros((len(slots), returns.shape[0]), dtype=bool)
        self.cost = np.zeros(returns.shape[0])
        for k, s in enumerate(slots):
            self.open[k, s.entry + 1:s.entry + s.hold + 1] = True
            self.cost[s.entry] += cost * s.weight
        self.w = np.array([s.weight for s in slots])
        self.calls = 0

    def __call__(self, selection) -> np.ndarray:
        self.calls += 1
        sel = np.asarray(selection)
        filled = sel >= 0
        per_slot = self.r[:, np.where(filled, sel, 0)].T  # slots x sessions
        return (self.open * (self.w * filled)[:, None] * per_slot).sum(axis=0) - self.cost


def everyone(_t: int) -> np.ndarray:
    return np.arange(M)


def skilled_pick(slots: list[rc.Slot], returns: np.ndarray, noise: float, rng) -> np.ndarray:
    """Planted skill: sees each name's return over the coming holding period through noise, takes the best
    free name. `noise` 0.1 is larger than the spread of ten-day returns (0.063): a real but imperfect signal."""
    out, held = np.full(len(slots), rc.UNFILLED), {}
    for k in sorted(range(len(slots)), key=lambda k: slots[k].entry):
        s = slots[k]
        held = {j: last for j, last in held.items() if last >= s.entry}
        signal = returns[s.entry + 1:s.entry + s.hold + 1].sum(axis=0) + rng.normal(0, noise, returns.shape[1])
        j = next(int(j) for j in np.argsort(-signal) if int(j) not in held)
        out[k], held[j] = j, s.entry + s.hold
    return out


def market(rng) -> np.ndarray:
    return rng.normal(0.0003, 0.02, (T, M)) + rng.normal(0, 0.0005, M)  # names differ in drift, none is "the" pick


# ---------------------------------------------------------------- the two truths


def test_a_rule_with_no_skill_lands_near_the_50th_percentile_on_average():
    slots = schedule()
    pcts = []
    for seed in range(40):
        rng = np.random.default_rng(1000 + seed)
        toy = Toy(market(rng), slots)
        own = rc.draw_selection(slots, everyone, rng)  # names chosen without looking at anything
        pcts.append(rc.matched_random_control(toy(own), slots, everyone, toy, runs=60, seed=seed).percentile)
    assert 38.0 < float(np.mean(pcts)) < 62.0  # 40 draws of a uniform percentile: mean 50, standard error 4.6
    assert float(np.mean(np.array(pcts) >= 90.0)) < 0.30  # and it clears the 90th about one time in ten
    assert min(pcts) < 25.0 and max(pcts) > 75.0  # spread over the whole range, as luck should be


def test_a_rule_with_planted_skill_lands_above_the_90th_percentile():
    slots = schedule()
    for seed in range(4):
        rng = np.random.default_rng(2000 + seed)
        returns = market(rng)
        toy = Toy(returns, slots)
        own = skilled_pick(slots, returns, 0.1, rng)
        res = rc.matched_random_control(toy(own), slots, everyone, toy, runs=200, seed=seed, strategy_selection=own)
        assert res.percentile > 90.0 and res.strategy_sharpe > res.random_p95 > res.random_median
        # the rule's own names through the control's simulator reproduce the rule: the two are comparable
        assert res.simulated_strategy_sharpe == pytest.approx(res.strategy_sharpe)
        assert res.percentile_simulated == res.percentile and res.matched and res.unfilled == 0


def test_a_rule_that_only_rides_the_market_gets_no_credit():
    """Every name rises together (one common factor): the rule's return is large, and so is every random
    portfolio's with the same exposure. The percentile stays ordinary, where a comparison with zero would not."""
    slots = schedule()
    pcts, sharpes = [], []
    for seed in range(12):
        rng = np.random.default_rng(3000 + seed)
        returns = rng.normal(0.002, 0.01, (T, 1)) + rng.normal(0.0, 0.01, (T, M))  # a strong bull market
        toy = Toy(returns, slots)
        own = rc.draw_selection(slots, everyone, rng)
        res = rc.matched_random_control(toy(own), slots, everyone, toy, runs=60, seed=seed)
        pcts.append(res.percentile)
        sharpes.append(res.strategy_sharpe)
    assert float(np.mean(sharpes)) > 1.0 and 25.0 < float(np.mean(pcts)) < 75.0


# ---------------------------------------------------------------- the matching itself


def test_a_random_portfolio_keeps_the_schedule_and_changes_only_the_names():
    slots = schedule(every=3, names=3, hold=12)
    sel = rc.draw_selection(slots, everyone, np.random.default_rng(5))
    assert (sel != rc.UNFILLED).all() and sel.shape == (len(slots),)
    for k, s in enumerate(slots):  # never the same name twice at once
        for j, other in enumerate(slots[:k]):
            if other.entry + other.hold >= s.entry and other.entry <= s.entry:
                assert sel[j] != sel[k]
    prof = rc.schedule_profile(slots, T)
    assert prof["names_held"].max() == 15 and prof["exposure"].max() == pytest.approx(15 * 0.25)
    assert (prof["holds"] == 12).all()
    assert not np.array_equal(sel, rc.draw_selection(slots, everyone, np.random.default_rng(6)))
    assert np.array_equal(sel, rc.draw_selection(slots, everyone, np.random.default_rng(5)))  # seeded


def test_only_eligible_names_are_drawn():
    slots = schedule()
    odd_even = {s.entry: np.arange(s.entry % 2, M, 2) for s in slots}  # the universe changes with the date
    for seed in range(5):
        sel = rc.draw_selection(slots, odd_even, np.random.default_rng(seed))
        assert all(sel[k] % 2 == s.entry % 2 for k, s in enumerate(slots))


def test_a_universe_too_small_to_fill_the_slots_is_reported_as_not_matched():
    slots = [rc.Slot(1, 10, 0.5), rc.Slot(1, 10, 0.5), rc.Slot(3, 5, 0.5)]
    sel = rc.draw_selection(slots, lambda t: [7], np.random.default_rng(0))  # one name, e.g. a SPY-only rule
    assert list(sel) == [7, rc.UNFILLED, rc.UNFILLED]
    toy = Toy(np.random.default_rng(1).normal(0, 0.01, (T, M)), slots)
    res = rc.matched_random_control(toy(np.array([7, 8, 9])), slots, lambda t: [7], toy, runs=20, seed=1)
    assert not res.matched and res.unfilled == 40  # two of three slots empty in each of 20 runs


def test_the_control_is_seeded_and_counts_its_runs():
    slots = schedule()
    rng = np.random.default_rng(9)
    toy = Toy(market(rng), slots)
    own = toy(rc.draw_selection(slots, everyone, rng))
    a = rc.matched_random_control(own, slots, everyone, toy, runs=50, seed=3)
    assert toy.calls == 51 and a.runs == 50 and a.slots == len(slots) and a.seed == 3
    assert a == rc.matched_random_control(own, slots, everyone, toy, runs=50, seed=3)
    assert a.random_p05 < a.random_median < a.random_p90 <= a.random_p95
    assert a.to_dict()["percentile"] == a.percentile


def test_ties_count_against_the_rule():
    slots = [rc.Slot(1, 5, 1.0)]
    flat = np.zeros(T)
    res = rc.matched_random_control(flat, slots, everyone, lambda sel: flat, runs=10, seed=0)
    assert res.percentile == 0.0  # equal to every random portfolio: not above any of them


def test_bad_inputs_are_refused():
    slots = schedule()
    toy = Toy(np.zeros((T, M)), slots)
    with pytest.raises(ValueError, match="same sessions"):
        rc.matched_random_control(np.zeros(T - 1), slots, everyone, toy, runs=2)
    with pytest.raises(ValueError, match="empty"):
        rc.matched_random_control(np.zeros(T), [], everyone, toy, runs=2)
    with pytest.raises(ValueError):
        rc.schedule_from_trades([1, 2], [5], [0.1, 0.1])
    with pytest.raises(ValueError):
        rc.schedule_from_trades([1], [-1], [0.1])
    assert rc.schedule_from_trades([3, 4], [5, 6], [0.1, 0.2]) == [rc.Slot(3, 5, 0.1), rc.Slot(4, 6, 0.2)]
