"""Audit 2, 5.19 (#699): kill the REAL engine at every crash point inside `core.Engine`, `book_loop.tick` and
`Engine.recover`, start it again from the journal on disk, check the invariants. The harness, the scenario and the
regions are in engine_crash.py; test_crash_points.py keeps sweeping crash_world.py's scripted order path (it
reaches the replicator's every write, which this one leaves to it).

Every crash point is swept in the `dst` workflow (KAIBOT_ENGINE_CRASH=all, on every pull request and main, four
processes). The ordinary `ci` run sweeps the order path's points only: every effect but the journal's `row` and
`md_book_peak` entries (the store's rows, the book's peak), plus every point of `Engine.recover`.
The count of crash points, by region, is printed and, in CI, written to the job summary.
"""

from __future__ import annotations

import os
from collections import Counter

import pytest
from engine_crash import REGIONS, engine_points, known, run

PHASES = ("before", "after")
SLICES = 8  # each phase's points in this many tests, so that xdist spreads them
ALL = os.environ.get("KAIBOT_ENGINE_CRASH") == "all"


@pytest.fixture(scope="module")
def baseline():
    o = run()
    return {"effects": o.effects, "end": o.end, "violations": o.violations, "fired": o.fired}


def _report(title: str, lines: list[str]) -> None:
    print(title)
    for line in lines:
        print("  " + line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"**{title}**\n\n" + "".join(f"- {line}\n" for line in lines) + "\n")


def _selected(baseline) -> list[int]:
    pts = engine_points(baseline["effects"])
    if ALL:
        return pts
    return [k for k in pts if baseline["effects"][k - 1][1] == "recover"
            or not baseline["effects"][k - 1][0].startswith(("journal.row", "journal.md_book_peak", "store."))]


def test_the_uninterrupted_run_is_clean_and_reaches_every_region(baseline):
    assert baseline["violations"] == [] and baseline["fired"] == []
    assert sorted(baseline["end"]) == ["C", "D"]
    pts = engine_points(baseline["effects"])
    by_region = Counter(baseline["effects"][k - 1][1] for k in pts)
    assert set(by_region) == set(REGIONS) - {"main loop"}, by_region
    kinds = {baseline["effects"][k - 1][0].split()[0] for k in pts}
    assert {"journal.om_intent", "journal.om_state", "journal.fill", "journal.md_send", "journal.md_decision",
            "journal.run", "journal.reconcile", "venue.submit"} <= kinds
    recover = {baseline["effects"][k - 1][0].split()[0] for k in pts if baseline["effects"][k - 1][1] == "recover"}
    assert {"journal.fill", "journal.om_state"} <= recover, "the restart's recovery books the window's fills"
    assert len(pts) >= 150
    _report(f"engine crash points (#699): {len(pts)} effects inside the engine x 2 phases = {2 * len(pts)} crash "
            f"points ({len(_selected(baseline)) * 2} swept in this run)",
            [f"{r}: {n} effects" for r, n in sorted(by_region.items())]
            + [f"{k}: {n}" for k, n in sorted(Counter(baseline["effects"][k - 1][0].split()[0] for k in pts).items())])


@pytest.mark.parametrize("part", range(SLICES))
@pytest.mark.parametrize("phase", PHASES)
def test_a_kill_at_every_crash_point_of_the_engine_recovers(baseline, phase, part):
    """After the kill and the restart: the Checker's invariants after every pass and at quiescence, one kill, and the
    venue ends holding the names the uninterrupted run holds (not always the same shares: a decision whose first
    attempt died with the process is sized again at the price of the minute it is sent in)."""
    effects = baseline["effects"]
    pts = [k for i, k in enumerate(_selected(baseline)) if i % SLICES == part]
    broken, expected = {}, {}
    for k in pts:
        what = effects[k - 1]
        o = run(k, phase)
        found = list(o.violations)
        if len(o.fired) != 1 or not o.fired[0].endswith(what[0]):
            found.append(f"killed at {o.fired}, expected once at {phase} {what[0]}")
        if sorted(o.end) != sorted(baseline["end"]):
            found.append(f"ended holding {o.end}, the uninterrupted run {baseline['end']}")
        if found:
            issue = known(effects, k, phase)
            (expected if issue is not None else broken)[f"{k} {phase} {what[0]} [{what[1]}]"] = (issue, found)
    _report(f"engine crash points, kill {phase} (part {part + 1}/{SLICES}): {len(pts) - len(broken) - len(expected)} "
            f"of {len(pts)} pass, {len(expected)} fail on an open issue, {len(broken)} fail",
            [f"{k}: #{v[0]} {v[1][:2]}" for k, v in expected.items()]
            + [f"{k}: {v[1]}" for k, v in broken.items()])
    assert not broken, broken


@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("side", ["B sell", "D buy"])
def test_a_kill_between_an_orders_intent_and_its_venue_call_still_sends_it_in_the_window(baseline, side, phase):
    """#775 (was KNOWN): D2's sale of B and buy of D, killed after the intent and before the venue call (`before`
    the submit, or `after` the om_intent just before it). The restarted process looks the order up by its client id,
    finds nothing, and sends it again in the same window under the SAME client id: one order at the venue, the
    decision's first attempt, filled in D2's window; the run ends where the uninterrupted one does."""
    effects = baseline["effects"]
    k = next(i for i, (what, _) in enumerate(effects, start=1)
             if what.startswith(f"venue.submit {side}") and "-20260804-" in what)
    coid = effects[k - 1][0].split()[-1]
    assert coid.endswith("-1")
    o = run(k if phase == "before" else k - 1, phase)
    if phase == "after":
        assert effects[k - 2][0].startswith("journal.om_intent")
    assert o.violations == [] and sorted(o.end) == sorted(baseline["end"]), o.violations
    sent = [t for t in o.trace if " om_state " in t and coid in t]
    assert any("state=filled" in t for t in sent), sent
    assert not any(f"{coid[:-2]}-2" in t for t in o.trace)  # no second attempt: the venue had one order of it
    assert not any("lost: never reached the venue" in t for t in o.trace)


def test_the_kill_cannot_be_caught_by_the_engine():
    from engine_crash import Kill

    assert not issubclass(Kill, Exception)
