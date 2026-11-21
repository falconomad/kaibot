"""M12 ticket 3.3 (#465): kill the engine's order path at every numbered crash point, restart it from the disk
image, check the invariants; then the same with a truncated and with a corrupted last record.

The harness, the scenario and the invariants are in crash_world.py. A crash point is (effect number, phase):
`before` an effect (it did not happen) or `after` it (it happened, nothing else did). The effects are every journal
write, every write to the replica and every write to the venue the scenario makes; their count is asserted below
(a scenario that silently stopped exercising writes would otherwise pass with nothing to check) and reported in
the test's output and, in CI, in the job summary.
"""

from __future__ import annotations

import os
import sqlite3
from collections import Counter

import pytest
from crash_world import STEPS, Engine, Fuse, Kill, World, fingerprint, run, s_open_run, violations

from kaibot.engine.journal import Journal

PHASES = ("before", "after")
KNOWN_491 = "#491"  # a cancel that never reached the venue is never sent again (found by ticket 3.1's machine)


@pytest.fixture(scope="module")
def baseline():
    o = run()
    yield {"effects": list(o.effects), "end": fingerprint(o), "violations": violations(o)}
    o.engine.abandon()
    o.world.close()


def _sweep(baseline, phase: str, damage: str | None) -> tuple[dict, dict]:
    """Every crash point of one phase: {point: what broke} and {point: the known defect it hit}."""
    broken: dict[str, list[str]] = {}
    known: dict[str, list[str]] = {}
    for k, what in enumerate(baseline["effects"], start=1):
        name = f"{k} {phase} {what}"
        o = run(k, phase, damage)
        try:
            found = violations(o)
            if o.restarts != 1 or o.killed_at != what:
                found.append(f"killed {o.restarts} times at {o.killed_at!r}, expected once at {what!r}")
            if fingerprint(o) != baseline["end"]:
                found.append(f"ended with {fingerprint(o)}, the uninterrupted run with {baseline['end']}")
            mine = [v for v in found if v.startswith(KNOWN_491)]
            rest = [v for v in found if not v.startswith(KNOWN_491)]
            if mine:
                known[name] = mine
            if rest:
                broken[name] = rest
        finally:
            o.engine.abandon()
            o.world.close()
    return broken, known


def _report(title: str, lines: list[str]) -> None:
    print(title)
    for line in lines:
        print("  " + line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"**{title}**\n\n" + "".join(f"- {line}\n" for line in lines) + "\n")


def test_the_uninterrupted_scenario_is_clean_and_exercises_every_kind_of_write(baseline):
    assert baseline["violations"] == []
    kinds = Counter(" ".join(e.split()[:1]) for e in baseline["effects"])
    wrote = {e.split()[0] for e in baseline["effects"]}
    assert {"journal.append", "journal.set_cursor", "store.insert", "store.upsert", "store.update", "venue.submit",
            "venue.cancel", "venue.replace"} <= wrote
    entry_kinds = {e.split()[1] for e in baseline["effects"] if e.startswith("journal.append")}
    assert {"row", "run", "om_intent", "om_state", "om_terms", "fill", "mark"} <= entry_kinds
    assert len(baseline["effects"]) >= 80 and len(STEPS) == 9
    _report(f"crash points: {len(baseline['effects'])} numbered writes x 2 phases = "
            f"{2 * len(baseline['effects'])} crash points",
            [f"{k}: {n}" for k, n in sorted(kinds.items())])


@pytest.mark.parametrize("phase", PHASES)
def test_a_kill_at_every_crash_point_recovers(baseline, phase):
    """Process-crash consistency: whatever was written is on disk. After the restart the venue holds what the
    uninterrupted run holds, the ledger equals the venue, every execution is booked once, no decision has two
    orders, no order is unknown, and the replica has every row once."""
    broken, known = _sweep(baseline, phase, None)
    _report(f"kill {phase} each write: {len(baseline['effects']) - len(broken) - len(known)} of "
            f"{len(baseline['effects'])} crash points pass, {len(known)} hit a known defect, {len(broken)} fail",
            [f"{k}: {v}" for k, v in {**known, **broken}.items()])
    assert not broken, broken
    # the known defect is hit only where a kill separates the cancel's journal entry from its venue call
    assert all("pending_cancel" in k or "venue.cancel" in k for k in known), known


@pytest.mark.parametrize("damage", ["torn", "flipped"])
@pytest.mark.parametrize("phase", PHASES)
def test_a_kill_with_a_damaged_last_record_recovers(baseline, phase, damage):
    """Power-loss variants: the last record in the write-ahead log is cut short (`torn`) or has one bit flipped
    (`flipped`). SQLite drops a frame whose checksum does not match, so the journal opens as it was one record
    earlier, although the dead process had already acted on that record (the venue may hold its order). The
    restart must still converge: deterministic client order ids and the activity ids make every lost record
    recoverable from the venue."""
    broken, known = _sweep(baseline, phase, damage)
    _report(f"kill {phase} each write, last record {damage}: {len(baseline['effects']) - len(broken) - len(known)} "
            f"of {len(baseline['effects'])} crash points pass, {len(known)} hit a known defect, {len(broken)} fail",
            [f"{k}: {v}" for k, v in {**known, **broken}.items()])
    assert not broken, broken


@pytest.mark.parametrize("damage", ["torn", "flipped"])
def test_the_damage_really_loses_the_last_record_and_nothing_else(damage):
    """The variants above are only worth something if the damaged image is in fact one record short."""
    w = World(Fuse())
    e = Engine(w, w.image_dir())
    try:
        s_open_run(w, e)
        for i in range(5):
            e.journal.append("note", {"i": i})
        whole, cut = Journal(e.image() / "journal.sqlite"), Journal(e.image(damage) / "journal.sqlite")
        assert whole.last_seq() == e.journal.last_seq()
        assert cut.last_seq() == whole.last_seq() - 1
        assert cut.entries() == whole.entries()[:-1], "the records before the damaged one are untouched"
        assert cut.append("note", {"i": "next"}) == whole.last_seq(), "and the journal can be written again"
        whole.close()
        cut.close()
    finally:
        e.abandon()
        w.close()


def test_the_kill_cannot_be_caught_by_the_engine():
    assert not issubclass(Kill, Exception)


# ---------------------------------------------------------------- a corrupted record that SQLite does not notice
def _checkpointed_copy(e: Engine):
    """The image with its write-ahead log folded into the database file (as after a clean shutdown)."""
    path = e.image() / "journal.sqlite"
    db = sqlite3.connect(path)
    db.execute("pragma wal_checkpoint(truncate)")
    db.close()
    return path


def _journal_with_a_fill():
    w = World(Fuse())
    e = Engine(w, w.image_dir())
    s_open_run(w, e)
    e.journal.append("fill", {"run_id": e.run_id, "activity": "x::1", "strategy": "s1", "symbol": "A", "side": "buy",
                              "qty": 5, "px": 100.25, "fees": 0.0, "coid": "c1"})
    return w, e


def test_a_record_corrupted_into_invalid_json_stops_the_start():
    """Corruption inside the database file (not the log) that breaks the record's JSON: the fold refuses it. The
    engine does not start on it, which is the safe side."""
    w, e = _journal_with_a_fill()
    try:
        path = _checkpointed_copy(e)
        raw = bytearray(path.read_bytes())
        at = raw.rindex(b'"qty":5')
        raw[at] = ord("x")  # `"qty":5` -> `xqty":5`
        path.write_bytes(bytes(raw))
        with pytest.raises(ValueError):
            Journal(path).entries()
    finally:
        e.abandon()
        w.close()


@pytest.mark.xfail(strict=True, reason="#496: a journal record has no checksum; a flipped digit is folded")
def test_a_record_corrupted_into_valid_json_is_noticed():
    """One byte of the last record changes `"qty":5` into `"qty":7`. Nothing in the file says so: SQLite keeps no
    checksum of a page in the database file and the journal keeps none of its entries, so the ledger folds seven
    shares. The start should notice (and refuse, or hand the difference to reconciliation by name)."""
    w, e = _journal_with_a_fill()
    try:
        path = _checkpointed_copy(e)
        raw = bytearray(path.read_bytes())
        at = raw.rindex(b'"qty":5') + len(b'"qty":')
        raw[at] = ord("7")
        path.write_bytes(bytes(raw))
        with pytest.raises(Exception, match="(?i)checksum|corrupt"):
            Journal(path).entries()
    finally:
        e.abandon()
        w.close()


@pytest.mark.xfail(strict=True, reason="#491: a cancel that never reached the venue is never sent again")
def test_491_a_kill_between_the_cancels_journal_entry_and_its_venue_call_still_cancels(baseline):
    """The one crash point of the sweep that does not recover (tolerated there by name, pinned here)."""
    k = baseline["effects"].index("venue.cancel") + 1
    o = run(k, "before")
    try:
        assert not [v for v in violations(o) if v.startswith(KNOWN_491)]
    finally:
        o.engine.abandon()
        o.world.close()
