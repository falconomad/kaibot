"""M12 ticket 3.3 (#465): a real `kill -9` in the middle of journal writes. test_crash_points.py simulates the kill
inside one process (and so can enumerate every crash point of a whole scenario); this file checks the one thing
that simulation cannot: that with the engine's own settings (WAL, synchronous=FULL) a record `append` has returned
for is in the file after the process is destroyed without any chance to clean up.

A child process (journal_writer.py) appends numbered entries, reports each on stdout after `append` returned, and
sends itself SIGKILL just before or just after the k-th write. The parent then opens the file.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from journal_writer import body

from kaibot.engine.journal import Journal, JournalStore

HERE = Path(__file__).parent
N = 12
POINTS = [(1, "before"), (1, "after"), (2, "after"), (3, "before"), (3, "after"), (7, "before"), (9, "after"),
          (12, "after")]


@pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason="needs SIGKILL")
@pytest.mark.parametrize(("at", "phase"), POINTS)
def test_what_append_returned_for_survives_sigkill(tmp_path, at, phase):
    path = tmp_path / "journal.sqlite"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(HERE.parents[1] / "src"), str(HERE)])}
    child = subprocess.run([sys.executable, str(HERE / "journal_writer.py"), str(path), str(N), str(at), phase],
                           env=env, capture_output=True, text=True, timeout=120)
    assert child.returncode == -signal.SIGKILL, child.stderr
    acked = [int(line.split()[1]) for line in child.stdout.splitlines() if line.startswith("ack ")]
    assert acked == list(range(1, at)), "the child reported every write before the crash point, and no other"

    journal = Journal(path)
    rows = journal.entries()
    written = at if phase == "after" else at - 1
    assert [r[0] for r in rows] == list(range(1, written + 1)), "a gapless prefix: nothing lost, nothing half-written"
    for seq, _, kind, b in rows:
        if seq % 3 == 0:
            assert kind == "row" and b["row"]["payload"] == body(seq)
        else:
            assert kind == "crash_test" and b == body(seq)
    store = JournalStore(journal)  # the fold of what is there works
    assert len(store.tables.get("event", [])) == written // 3
    assert journal.append("crash_test", body(written + 1)) == written + 1, "and the journal goes on from there"
    assert journal.db.execute("pragma integrity_check").fetchone()[0] == "ok"
    journal.close()
