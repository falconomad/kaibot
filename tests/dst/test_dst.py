"""M12 ticket 3.2 (#464): the deterministic simulation with injected faults (world.py), its seed budget, the
regression seeds, and the proof that it has teeth.

Budget. `KAIBOT_DST_SEEDS` seeds from `KAIBOT_DST_FIRST` on (default 3 from seed 1: what the ordinary test run pays,
a few seconds each); the `dst` workflow runs 200 on every pull request, and its nightly job runs fresh seeds for a
fixed time (soak.py) and opens an issue with the failing seed. Every seed in regression_seeds.txt always runs.

A failure prints the seed, the scenario, the violations and the end of the trace. Run it again with
`PYTHONHASHSEED=0 PYTHONPATH=src python tests/dst/run_seed.py <seed> --trace`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from scenarios import FINDINGS, quiet
from world import D1, D2, KNOWN, Scenario, et, run_scenario, run_seed

from kaibot.broker.faulty import Forced

SEEDS = int(os.environ.get("KAIBOT_DST_SEEDS", "3"))
FIRST = int(os.environ.get("KAIBOT_DST_FIRST", "1"))
REGRESSION = [int(line.split("#")[0]) for line in (Path(__file__).parent / "regression_seeds.txt").read_text()
              .splitlines() if line.split("#")[0].strip()]


@pytest.mark.parametrize("seed", range(FIRST, FIRST + SEEDS))
def test_the_invariants_hold_under_seed(seed):
    res = run_seed(seed)
    assert res.ok, res.report()


@pytest.mark.parametrize("seed", REGRESSION)
def test_a_seed_that_once_failed_still_passes(seed):
    res = run_seed(seed)
    assert res.ok, res.report()


def test_the_same_seed_gives_the_same_run():
    """Reproducibility is what makes a failing seed worth anything: twice the same seed, the same trace, line for
    line (run_seed.py pins PYTHONHASHSEED as well; the fault draws do not depend on call order either way)."""
    a, b = run_seed(7), run_seed(7)
    assert a.trace == b.trace and a.stats == b.stats
    assert len(a.trace) > 10


def test_a_quiet_run_decides_sends_fills_and_holds():
    """The scenario without a single fault, as a story: the decision for D1 (taken late: the run starts after D0's
    close), the send in D1's window, the fill a minute later, nothing more to do on D2 and D3."""
    res = run_scenario(quiet({D1: ("B",)}))
    assert res.ok, res.report()
    story = [t.split(" ", 4)[4] for t in res.trace if " journal " in t]
    kinds = [s.split(" ")[0] for s in story]
    assert kinds.count("md_decision") == 4 and kinds.count("fill") == 1 and kinds.count("om_intent") == 1
    assert kinds.index("md_decision") < kinds.index("md_send") < kinds.index("om_intent") < kinds.index("fill")
    assert res.stats["kills"] == 0 and res.stats["faults"] == 0 and res.stats["tick_errors"] == 0


def test_the_generator_reaches_every_kind_of_scenario():
    """Jepsen's second lesson: a generator that is accidentally regular leaves paths unreached. Over 400 seeds
    every ingredient turns up, alone and together (cheap: no scenario is run)."""
    seen = {"flatten": 0, "watchdog": 0, "kill": 0, "pause": 0, "late_view": 0, "no_decision_day": 0, "sell_all": 0,
            "no_faults": 0, "missed_window": 0, "overnight": 0, "kill_and_watchdog": 0}
    for seed in range(1, 401):
        s = Scenario.from_seed(seed)
        seen["flatten"] += s.flatten is not None
        seen["watchdog"] += s.watchdog
        seen["kill"] += any(o.kill for o in s.outages)
        seen["pause"] += any(not o.kill for o in s.outages)
        seen["late_view"] += any(s.ready[ev] > et(ev, "23:00") for ev in list(s.ready)[:3])
        seen["no_decision_day"] += s.plan[D2] is None
        seen["sell_all"] += any(p == () for p in s.plan.values())
        seen["no_faults"] += s.profile.hold == 0
        seen["missed_window"] += any(o.kill and 20 <= o.down <= 60 for o in s.outages)
        seen["overnight"] += any(o.down >= 400 for o in s.outages)
        seen["kill_and_watchdog"] += s.watchdog and any(o.kill for o in s.outages)
    assert all(n >= 15 for n in seen.values()), seen


# ---------------------------------------------------------------- teeth: the M9 findings, fixed and planted again
@pytest.mark.parametrize("name", sorted(FINDINGS))
def test_the_engine_as_it_is_passes_the_scenario_of_a_fixed_finding(name):
    scenario, _plant, _word = FINDINGS[name]
    res = run_scenario(scenario)
    assert res.ok, res.report()


@pytest.mark.parametrize("name", sorted(FINDINGS))
def test_the_simulation_finds_the_finding_when_its_fix_is_taken_away(name, monkeypatch):
    """The proof the ticket asks for: the same scenario, the same invariants, and the fix patched out."""
    scenario, plant, word = FINDINGS[name]
    plant(monkeypatch)
    res = run_scenario(scenario)
    assert not res.ok, f"the planted bug ({plant.__name__}) went unnoticed\n{res.report()}"
    assert any(word in v for v in res.violations), res.report()


def test_a_run_that_never_leaves_scheduled_is_a_violation(monkeypatch):
    """#577: the oracle expects no decision from a scheduled run, so it checks the engine's word: a run started
    after D0's close that is still scheduled in D1's session (the planted bug: it never starts) is a violation,
    not a run with nothing to decide."""
    from kaibot.engine.core import Engine

    set_status = Engine._set_run_status
    monkeypatch.setattr(Engine, "_set_run_status",
                        lambda self, status, reason=None: None if status == "running" else set_status(self, status,
                                                                                                       reason))
    res = run_scenario(quiet({D1: ("B",)}))
    assert not res.ok
    assert any(f"still scheduled in the session of {D1}" in v for v in res.violations), res.report()


def test_a_lost_order_the_open_orders_list_shows_is_adopted_not_sent_again():
    """M9 1.11 at work: the lookup by client order id misses for minutes, the open-orders read finds the order."""
    scenario, _, _ = FINDINGS["lost order sent again"]
    res = run_scenario(scenario)
    assert res.ok, res.report()
    states = [t for t in res.trace if "om_state" in t and "-B-" in t]
    assert any("state=unknown" in t for t in states) and not any("lost" in t for t in states)


# ---------------------------------------------------------------- engine defects the simulation found (KNOWN)
def test_flatten_and_stop_sells_a_filled_buy_whose_activity_has_not_arrived():
    """#533: the buy filled at 15:51, its activity arrives at 16:21; the owner presses "Flatten and stop" at 15:53.
    The command waits for the booking (the venue holds the shares, the ledger does not yet), the sale goes out at
    the next open, and only then does the run end."""
    res = run_scenario(quiet({D1: ("B",)}, flatten=et(D1, "15:53"), heal=D2,
                             forced=(Forced("fill_dropped", "|B|buy", 30),)))
    assert res.ok, res.report()


def test_flatten_and_stop_waits_for_a_buy_whose_cancel_was_lost():
    """#533 (second case): the buy is still working when the owner presses "Flatten and stop" and the cancel never
    arrives. The command waits while the order is open (the cancel is sent again by the sync), books its fill, and
    sells it."""
    res = run_scenario(quiet({D1: ("B",)}, flatten=et(D1, "15:52"), heal=D2,
                             forced=(Forced("hold", "|B|buy", 5), Forced("cancel_lost", "", 1))))
    assert res.ok, res.report()


def test_flatten_and_stop_waits_for_an_order_called_lost_that_fills_later():
    """QA-Ops on #544 with #535: the buy is called lost at 15:52, the owner presses "Flatten and stop" at 15:53, the
    venue fills the order at 15:54. The command waits while the lost order's day lasts; the fill is adopted and
    booked (#535) and sold at the next open, and only then does the run end."""
    res = run_scenario(quiet({D1: ("B",)}, heal=D2, flatten=et(D1, "15:53"),
                             forced=(Forced("submit_timeout_accepted", "|B|buy"), Forced("lookup_lag", "|B|buy", 12),
                                     Forced("listing_lag", "|B|buy"), Forced("hold", "|B|buy", 4))))
    assert res.ok, res.report()


def test_an_order_called_lost_that_fills_later_is_booked():
    """#535: the venue's lookups lag the order for minutes, it is called lost at 15:52 and fills at 15:54. The fill
    is looked up by its venue order id, carries the lost record's client order id, and is booked to it."""
    res = run_scenario(quiet({D1: ("B",)}, heal=D2,
                             forced=(Forced("submit_timeout_accepted", "|B|buy"), Forced("lookup_lag", "|B|buy", 12),
                                     Forced("listing_lag", "|B|buy"), Forced("hold", "|B|buy", 4))))
    assert res.ok, res.report()


@pytest.mark.xfail(strict=True, reason="engine bug #529 (seed 190's case): a fill activity a day late; the sale the "
                                       "decision taken meanwhile wants is never sent")
def test_a_buy_booked_a_day_late_is_sold_by_the_decision_taken_meanwhile():
    """QA-Ops on #537: the finding added to #529 had no strict xfail. B is bought at D1's close and its activity is
    hidden for 1500 minutes (past D2's window, where the world is healed only from D3); D2's decision wants nothing.
    The engine plans D2 from the ledger, which has no B, so nothing sells it, and D3 is no decision day."""
    assert 529 in KNOWN
    res = run_scenario(quiet({D1: ("B",), D2: ()}, forced=(Forced("fill_dropped", "|B|buy", 1500),)))
    assert res.ok, res.report()
