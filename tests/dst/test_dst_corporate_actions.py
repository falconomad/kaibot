"""Audit 2, 5.20 (#700): corporate actions among the seeded faults, and the book stop in band mode in the DST world.

    ca_split     (FaultyBroker) a 2- or 3-for-1 split before the open: shares, cost per share, the bars, a SPLIT row
    ca_spinoff   (FaultyBroker) new shares of `<symbol>W` with a tenth of the cost basis, a SPIN row, the new bars
    ca_merger    (FaultyBroker) a cash merger at a premium: the position goes, the cash comes (two MA rows), and the
                 symbol can no longer be bought

They are in DEFAULT_FAULTS and drawn in the DST world's new weeks only, as 5.18's kinds; the world serves the venue's
record to the engine as Alpaca's corporate actions (`World.recent_actions`, engine/corp_guard.RecentActions' shape),
follows a split in its daily store, and its oracle knows that a merged name cannot be held. A third of the new weeks'
seeds (`BAND_SHARE`) run the book's stop in band mode with the world's own band table (`World.band_config`). The
nightly soak counts all of it in its coverage section (soak.Coverage), and plays the exit-code-5 drill.

The spin-off found #780 (KNOWN): the adopted shares are outside the daily view the session's send sizes with, and the
whole send of that session fails.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest
import world
from scenarios import quiet_in
from soak import Coverage
from world import BAND_SHARE, D1, D2, KNOWN, SPINOFF_ISSUE, SUMMER, THANKSGIVING, Scenario, Venue, et, minute_frames
from world import run_scenario as run

from kaibot.broker.faulty import CA_FAULTS, DEFAULT_FAULTS, NO_FAULTS, FaultyBroker, Forced
from kaibot.broker.protocol import BrokerError, OrderRequest
from kaibot.engine.clock import SimClock
from kaibot.replay.history import HistoricalData


def venue(*forced: Forced):
    frames = dict(minute_frames(1))  # its own dict: a split replaces the symbol's frame in it
    clock = SimClock(et(D1, "09:40", 7))
    data = HistoricalData(frames, clock)
    sim = Venue(data.history, clock)
    b = FaultyBroker(sim, 1, NO_FAULTS, forced=forced)
    seen: list[dict] = []
    b.on_action = seen.append
    b.advance(pd.Timestamp(et(D1, "09:39")))
    b.submit(OrderRequest("B", "buy", 10, "market", "day", "kb1-s-B-20260803-buy-1"))
    b.advance(pd.Timestamp(et(D1, "09:40")))
    assert sim.pos["B"]["qty"] == 10
    clock.set(et(D2, "09:30", 7))
    b.advance(pd.Timestamp(et(D2, "09:30")))  # the first bar of D2: the corporate actions come before it
    return b, sim, data, seen


def test_a_split_multiplies_the_shares_and_adjusts_the_bars_and_the_cost():
    frames = minute_frames(1)
    before = float(frames["B"]["c"].iloc[100])
    b, sim, data, seen = venue(Forced("ca_split", f"B|{D2}", 2))
    p = sim.pos["B"]
    assert p["qty"] == 20 and abs(float(p["avg_entry_price"]) * 20 - float(sim.fills[0]["price"]) * 10) < 1e-6
    assert abs(float(data.history.frames["B"]["c"].iloc[100]) - before / 2) < 1e-9, "the feed is split-adjusted"
    rows = [a for a in sim.non_trade if a["activity_type"] == "SPLIT"]
    assert rows and rows[0]["symbol"] == "B" and rows[0]["date"] == D2
    assert [(a["kind"], a["symbol"], a["ratio"]) for a in seen] == [("split", "B", 2)] == \
        [(a["kind"], a["symbol"], a["ratio"]) for a in b.actions]
    assert [f for _, f, _ in b.log] == ["ca_split"]


def test_a_spin_off_credits_new_shares_with_a_tenth_of_the_cost_and_their_bars():
    b, sim, data, seen = venue(Forced("ca_spinoff", f"B|{D2}", 0.5))
    assert sim.pos["BW"]["qty"] == 5 and sim.pos["B"]["qty"] == 10
    assert abs(float(sim.pos["BW"]["cost_basis"]) - 0.1 * 10 * float(sim.fills[0]["price"])) < 1e-6
    assert "BW" in data.history.frames and data.history.last_close("BW", et(D2, "09:40")) is not None
    assert [a["activity_type"] for a in sim.non_trade] == ["SPIN"]
    assert seen[0]["kind"] == "spin_off" and seen[0]["new_symbol"] == "BW" and seen[0]["ratio"] == 0.5


def test_a_cash_merger_removes_the_position_pays_the_premium_and_the_name_cannot_be_bought_again():
    b, sim, data, seen = venue(Forced("ca_merger", f"B|{D2}", 1.2))
    cash0 = sim.cash
    assert "B" not in sim.pos and [a["activity_type"] for a in sim.non_trade] == ["MA", "MA"]
    price = seen[0]["ratio"]
    last = data.history.last_close("B", et(D2, "09:30"))
    assert abs(price - round(last * 1.2, 2)) < 1e-9 and cash0 is not None
    with pytest.raises(BrokerError) as e:
        b.submit(OrderRequest("B", "buy", 1, "market", "day", "kb1-s-B-20260804-buy-1"))
    assert e.value.status == 422 and "not active" in str(e.value)
    assert b.merged == {"B"}


def test_no_action_when_the_faults_are_off_and_none_on_a_name_not_held():
    b, sim, _, seen = venue()
    assert not seen and not b.actions and sim.pos["B"]["qty"] == 10
    b2, sim2, _, seen2 = venue(Forced("ca_split", f"C|{D2}", 2))  # C is not held
    assert not seen2 and sim2.pos["B"]["qty"] == 10


def test_the_draw_is_per_symbol_and_session_and_the_same_every_time():
    from kaibot.broker.faulty import Dice

    def draws(seed):
        d = FaultyBroker.__new__(FaultyBroker)
        d.dice, d.profile, d.forced, d.armed = Dice(seed), DEFAULT_FAULTS, (), True
        return tuple(k for k in CA_FAULTS if d._hit(k, "B", D2))

    hits = [draws(seed) for seed in range(400)]
    assert hits == [draws(seed) for seed in range(400)]
    assert {k for h in hits for k in h} == set(CA_FAULTS), "each kind is drawn at DEFAULT_FAULTS' rates"
    assert all(getattr(DEFAULT_FAULTS, k) > 0 for k in CA_FAULTS)


# ---------------------------------------------------------------- the generator
def test_the_summer_week_draws_no_corporate_action_and_no_band_mode():
    for seed in range(60):
        s = Scenario.from_seed(seed, week=SUMMER)
        assert all(getattr(s.profile, k) == 0 for k in CA_FAULTS) and s.book_stop == "fixed"


def test_the_new_weeks_draw_corporate_actions_and_band_mode():
    seeds = [Scenario.from_seed(seed, week=THANKSGIVING) for seed in range(200)]
    assert abs(sum(s.book_stop == "band" for s in seeds) / 200 - BAND_SHARE) < 0.1
    assert sum(all(getattr(s.profile, k) > 0 for k in CA_FAULTS) for s in seeds) > 100  # (scale 0 switches all off)


# ---------------------------------------------------------------- hand-written: the engine adopts each action
@pytest.mark.parametrize("fault, param", [("ca_split", 2), ("ca_split", 3), ("ca_merger", 1.05)])
def test_a_corporate_action_before_a_session_keeps_every_invariant(fault, param):
    """The book loop. D1's decision buys B; before D2's open the venue applies the action to B; D2's decision wants B
    and C. A split is booked from Alpaca's SPLIT row at the session's start and the sizing is in the new shares; a
    merger is booked from the sourced record (cash at Alpaca's MA amount) and the name is not bought again."""
    s = quiet_in(SUMMER, {D1: ("B",), D2: ("B", "C")}, forced=(Forced(fault, f"B|{D2}", param),))
    res = run(s)
    assert res.ok, res.report()
    want = "cash_merger" if fault == "ca_merger" else "split"
    assert res.stats["corporate_actions"] == [want]


def test_a_spin_off_is_adopted_and_sold_with_the_known_issue_left_out():
    """KNOWN #780 as the generator leaves it out: the new symbol joins the daily store and the spin-off's own session
    is not judged by invariants 11 and 12. The new shares are sold, and the book ends where the decision wanted it."""
    assert SPINOFF_ISSUE in KNOWN
    s = quiet_in(SUMMER, {D1: ("B",), D2: ("B", "C")}, forced=(Forced("ca_spinoff", f"B|{D2}", 1.0),))
    res = run(s)
    assert res.ok, res.report()
    assert res.stats["corporate_actions"] == ["spin_off"]


@pytest.mark.xfail(strict=True, reason=f"engine bug #{SPINOFF_ISSUE}: the spin-off's adopted shares are outside the "
                                       "daily view and the session's whole send fails with KeyError")
def test_known_a_spin_off_day_still_sends_its_window(monkeypatch):
    monkeypatch.delitem(world.KNOWN, SPINOFF_ISSUE)  # the generator's way round it, off
    s = quiet_in(SUMMER, {D1: ("B",), D2: ("B", "C")}, forced=(Forced("ca_spinoff", f"B|{D2}", 1.0),))
    res = run(s)
    assert res.ok, res.report()


# ---------------------------------------------------------------- the book stop in band mode
def test_a_band_mode_run_is_judged_by_its_band_alerts_and_keeps_every_invariant():
    s = quiet_in(SUMMER, {D1: ("B", "C", "D")}, book_stop="band")
    res = run(s)
    assert res.ok, res.report()
    assert res.stats["book_stop"] == "band" and res.stats["band_checks"] > 100
    assert res.stats["band_alerts"] >= 1, "the 95% line is low enough that the alert is reached"


def test_a_fixed_mode_run_never_reads_a_band():
    res = run(quiet_in(SUMMER, {D1: ("B",)}))
    assert res.ok and res.stats["book_stop"] == "fixed" and res.stats["band_checks"] == 0


# ---------------------------------------------------------------- the nightly coverage
def test_the_soak_counts_band_mode_the_corporate_actions_and_the_weeks():
    c = Coverage()
    c.add({"week": "summer", "book_stop": "fixed", "corporate_actions": [], "band_checks": 0, "band_alerts": 0})
    c.add({"week": "thanksgiving", "book_stop": "band", "corporate_actions": ["split", "spin_off"],
           "band_checks": 40, "band_alerts": 2})
    c.add({"week": "march_gap", "book_stop": "fixed", "corporate_actions": ["cash_merger"]})
    text = "\n".join(c.lines())
    assert "summer 1" in text and "thanksgiving 1" in text
    assert "band 1" in text and "fixed 2" in text and "band checks 40, band alerts 2" in text
    assert "cash_merger 1, spin_off 1, split 1" in text


def test_the_nightly_job_plays_the_refused_start_drill_and_reports_coverage():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert '--drills "refused start loop"' in (root / ".github" / "workflows" / "dst.yml").read_text()
    assert '"refused start loop": (refused_start_loop,' in (root / "tests" / "drills" / "drills.py").read_text()


def test_a_band_config_is_written_as_the_engine_reads_it(tmp_path):
    from kaibot.risk import book_stop as bs

    w = world.World(quiet_in(SUMMER, {D1: ("B",)}, book_stop="band"), tmp_path)
    cfg, why = bs.load_or_fixed(w.band_config())
    assert why is None and cfg.mode == "band"
    t = bs.table_for(cfg, "trend_momentum", "balanced", root=tmp_path)
    assert t is not None and t.at(20)[1] == pytest.approx(0.12)
    assert bs.table_unusable(t, (pd.Timestamp(D1) + timedelta(days=1)).date()) is None


# ---------------------------------------------------------------- seeds that draw each action (in their own week)
# found with the new kinds (run in the week and on the engine the seed draws: tests/dst/regression_seeds.txt runs the
# summer week, which draws no corporate action)
CA_SEEDS = {40: "spin_off", 79: "cash_merger", 88: "split", 130: "spin_off"}


@pytest.mark.parametrize("seed", sorted(CA_SEEDS))
def test_a_seed_that_draws_a_corporate_action_keeps_every_invariant(seed):
    """Seed 40 (thanksgiving_no_calendar, book stop in band mode) found #780; with KNOWN's way round it, it passes."""
    from world import run_seed

    res = run_seed(seed)
    assert res.ok, res.report()
    assert CA_SEEDS[seed] in res.stats["corporate_actions"]
