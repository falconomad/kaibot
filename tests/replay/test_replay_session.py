"""M12 ticket 3.4 (#466): the session replay (scripts/replay_session.py).

- a session the simulator recorded on this code replays without a difference;
- the sessions recorded by the engine before milestone M9 (84250c7) differ when replayed through this code, in at
  least two of M9's behaviour changes (1.3 late decision, 1.4 late send, 1.8 flatten through the order manager),
  and the allowlist acknowledges exactly those differences, each with a reason;
- a difference nobody acknowledged fails the replay (exit code 1);
- the export of a journal is read-only and sanitised, and refuses to write anything that looks like a key;
- a live session (no market data in the file) is checked by folding its ledger with today's code.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
_spec = importlib.util.spec_from_file_location("replay_session", ROOT / "scripts" / "replay_session.py")
rs = importlib.util.module_from_spec(_spec)
sys.modules["replay_session"] = rs
_spec.loader.exec_module(rs)

PRE_M9 = sorted(FIXTURES.glob("pre_m9_*.jsonl"))


@pytest.fixture(autouse=True)
def _hash_seed(monkeypatch):
    monkeypatch.setenv("PYTHONHASHSEED", "0")


@pytest.fixture(scope="module")
def outcomes():
    acks = rs.load_allowlist(HERE / "allowlist.toml")
    return {p.stem: rs.replay(p, acks) for p in [*PRE_M9, FIXTURES / "sample_main.jsonl"]}, acks


def test_the_fixtures_are_there():
    assert {p.stem for p in PRE_M9} == {"pre_m9_late_start", "pre_m9_missed_window", "pre_m9_flatten_partial"}
    for p in PRE_M9:
        head = json.loads(p.read_text().splitlines()[0])
        assert head["format"] == rs.FORMAT and head["inputs"]["kind"] == "dst"
        assert head["code_sha"] == "84250c7", f"{p.name} was not recorded by the engine before M9"


def test_a_session_recorded_on_this_code_replays_without_a_difference(outcomes):
    o = outcomes[0]["sample_main"]
    assert o.ok and not o.acked, rs.report([o], [])


def test_the_pre_m9_sessions_show_m9s_behaviour_changes(outcomes):
    got = {name: {k: (a, b) for k, a, b, _ in o.acked} for name, o in outcomes[0].items()}
    # 1.3: a late start takes the session's decision
    _, now = got["pre_m9_late_start"]["decision 2026-08-03"]
    assert now["late"] is True and now["names"] == ["B"]
    # 1.4: a missed window is sent on the next session, once
    was, now = got["pre_m9_missed_window"]["send 2026-08-04"]
    assert was == {"late_of": None, "targets": []} and now["late_of"] == "2026-08-03"
    # 1.8: the flatten sale's partial fill is booked (before M9 the book kept every share)
    was, now = got["pre_m9_flatten_partial"]["ledger shares B"]
    assert (was, now) == (23.0, 12.0)
    assert got["pre_m9_flatten_partial"]["order kb-trend_momentum-B-20260805-flatten-1"][0] is None


def test_the_allowlist_acknowledges_every_difference_with_a_reason_and_nothing_else(outcomes):
    res, acks = outcomes
    for name in ("pre_m9_late_start", "pre_m9_missed_window", "pre_m9_flatten_partial"):
        o = res[name]
        assert o.ok, rs.report([o], acks)
        assert o.acked, f"{name}: no difference at all: the fixture no longer shows M9's changes"
        for key, _a, _b, why in o.acked:
            assert "M9 1." in why and "#4" in why, f"{name} {key}: the reason names no M9 ticket: {why}"
    assert all(a.get("_used") for a in acks), "an allowlist entry matched nothing"


def test_an_unacknowledged_difference_fails(tmp_path, capsys):
    rc = rs.main(["replay", str(FIXTURES / "pre_m9_missed_window.jsonl"), "--allowlist", str(tmp_path / "none.toml"),
                  "--report", str(tmp_path / "r.md")])
    assert rc == 1
    text = (tmp_path / "r.md").read_text()
    assert "**difference** `send 2026-08-04`" in text and "late_of" in text


def test_an_allowlist_entry_needs_a_reason(tmp_path):
    p = tmp_path / "a.toml"
    p.write_text('[[ack]]\njournal = "*"\nkey = "ledger *"\nreason = " "\n')
    with pytest.raises(ValueError, match="reason"):
        rs.load_allowlist(p)


@pytest.mark.parametrize("entry, why", [
    ('journal = "pre_m9_*"\nkey = "ledger cash"\nwas = "0.0"\nnow = "1.0"\n', "no globs"),
    ('journal = "pre_m9_late_start"\nkey = "ledger *"\nwas = "0.0"\nnow = "1.0"\n', "no globs"),
    ('journal = "pre_m9_late_start"\nkey = "ledger cash"\n', "was"),
    ('journal = "pre_m9_late_start"\nkey = "ledger cash"\nwas = "0.0"\nnow = "{"\n', "not JSON"),
])
def test_an_allowlist_entry_names_one_difference_and_its_values(tmp_path, entry, why):
    p = tmp_path / "a.toml"
    p.write_text("[[ack]]\n" + entry + 'reason = "M9 1.3 (#428)"\n')
    with pytest.raises(ValueError, match=why):
        rs.load_allowlist(p)


def _acks_with(tmp_path, edit):
    text = (HERE / "allowlist.toml").read_text()
    p = tmp_path / "a.toml"
    p.write_text(edit(text))
    return rs.load_allowlist(p)


def test_an_acknowledged_key_with_another_value_fails(tmp_path):
    """The entry pins the replayed value: a later change to the same decision is not hidden by the M9 entry."""
    acks = _acks_with(tmp_path, lambda t: t.replace("now = '-2256.58'", "now = '-2256.59'"))
    o = rs.replay(FIXTURES / "pre_m9_late_start.jsonl", acks)
    assert not o.ok and [k for k, _a, _b in o.diffs] == ["ledger cash"]


def test_an_acknowledged_difference_that_no_longer_happens_fails():
    """Undoing an M9 change makes the replay match the pre-M9 recording again: the entries of that change then match
    nothing, and that fails (a revert of the fix must not pass silently)."""
    acks = rs.load_allowlist(HERE / "allowlist.toml")
    acks.append({"journal": "pre_m9_missed_window", "key": "send 2026-08-09", "was": "null", "now": "null",
                 "_was": None, "_now": None, "reason": "a difference that does not happen"})
    o = rs.replay(FIXTURES / "pre_m9_missed_window.jsonl", acks)
    assert not o.diffs and [a["key"] for a in o.stale] == ["send 2026-08-09"] and not o.ok
    assert "no longer happens" in rs.report([o], acks)


def test_a_stale_allowlist_entry_is_reported():
    acks = [{"journal": "nothing", "key": "decision 2026-08-03", "reason": "gone"}]
    assert "matched nothing" in rs.report([], acks)


# ---------------------------------------------------------------- export, and a live session
@pytest.fixture(scope="module")
def journal(tmp_path_factory):
    """A real journal file written by this code: the sample session of the simulator."""
    root = tmp_path_factory.mktemp("j")
    head = json.loads((FIXTURES / "sample_main.jsonl").read_text().splitlines()[0])
    os.environ.setdefault("PYTHONHASHSEED", "0")
    rs.run_dst(head["inputs"], root=root)
    return root / "journal.sqlite"


def test_the_export_is_read_only(journal):
    before = (journal.stat().st_mtime_ns, journal.stat().st_size, sorted(p.name for p in journal.parent.iterdir()))
    rs.export(journal)
    after = (journal.stat().st_mtime_ns, journal.stat().st_size, sorted(p.name for p in journal.parent.iterdir()))
    assert before == after


def test_the_export_is_sanitised(journal, tmp_path):
    lines = rs.export(journal, sessions=None)
    kinds = {x["kind"] for x in lines[1:]}
    assert kinds <= set(rs.KEEP) | {"row"}
    assert {x["body"]["table"] for x in lines[1:] if x["kind"] == "row"} == {"ledger_position"}
    text = json.dumps(lines)
    for word in ('"order_id"', '"ids"', "engine_start", "fingerprint", "heartbeat"):
        assert word not in text
    fills = [x["body"] for x in lines if x.get("kind") == "fill"]
    assert fills and all(f["activity"].startswith("h") and "::" not in f["activity"] for f in fills)
    assert lines[0]["inputs"] == {"kind": "live"}


def test_the_export_keeps_the_last_session(journal):
    lines = rs.export(journal, sessions=1)
    assert lines[0]["sessions"] == ["2026-08-05"]
    assert any(x["kind"] == "run" for x in lines[1:]), "the run entry is always kept: the ledger opens from it"
    assert min(x["ts"] for x in lines[1:] if x["kind"] != "run") >= "2026-08-04"


def test_the_export_refuses_a_key(journal, tmp_path):
    bad = tmp_path / "journal.sqlite"
    with sqlite3.connect(bad) as db, sqlite3.connect(journal) as src:
        src.backup(db)
        db.execute("insert into entry (ts, kind, body) values ('2026-08-05T20:00:00+00:00', 'md_withheld', ?)",
                   (json.dumps({"why": "PK" + "A" * 18}),))
    with pytest.raises(ValueError, match="looks like a key"):
        rs.export(bad, sessions=None)


def test_a_live_session_is_checked_by_folding_its_ledger(journal, tmp_path):
    out = tmp_path / "live.jsonl"
    assert rs.main(["export", str(journal), "--out", str(out), "--all"]) == 0
    o = rs.replay(out, [])
    assert o.ok, rs.report([o], [])
    assert any("decisions" in n for n in o.notes)
    o = rs.replay(journal, [])  # the journal file itself: today's Journal opens it and verifies its checksums
    assert o.ok and any("verified" in n for n in o.notes)
    # a book that does not fold to what the engine published is a difference
    lines = out.read_text().splitlines()
    i = next(i for i, x in enumerate(lines) if json.loads(x).get("kind") == "fill")
    e = json.loads(lines[i])
    e["body"]["qty"] = float(e["body"]["qty"]) + 1
    lines[i] = json.dumps(e)
    out.write_text("\n".join(lines) + "\n")
    o = rs.replay(out, [])
    assert not o.ok and o.diffs[0][0].startswith("ledger shares")


def test_the_export_hashes_the_brokers_activity_ids_and_drops_the_retirement_id():
    fee = rs.sanitise("fee", {"run_id": 1, "amount": -0.02, "date": "2026-08-05", "ref": "alpaca:20260805::abc"})
    assert fee["ref"].startswith("h") and "alpaca" not in json.dumps(fee)
    retired = rs.sanitise("md_retired", {"rule": "trend_momentum", "run_id": 1, "retirement_id": "2026-08-05.a1b2c3"})
    assert "retirement_id" not in retired and retired["rule"] == "trend_momentum"
