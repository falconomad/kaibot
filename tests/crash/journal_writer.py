"""A child process for test_crash_sigkill.py: it appends numbered entries to a journal with the engine's own
durability settings (WAL, synchronous=FULL), reports each one on stdout once `append` has returned, and kills
itself with SIGKILL at the crash point it was given. Run as a script: `journal_writer.py <path> <n> <at> <phase>`.
"""

from __future__ import annotations

import os
import signal
import sys

from kaibot.engine.journal import Journal, JournalStore


def body(i: int) -> dict:
    return {"i": i, "text": f"entry {i}", "px": 100.0 + i / 100}


def main(path: str, n: int, at: int, phase: str) -> None:
    journal = Journal(path)
    store = JournalStore(journal)
    for i in range(1, n + 1):
        if i == at and phase == "before":
            os.kill(os.getpid(), signal.SIGKILL)
        if i % 3 == 0:  # every third write goes through the store (a `row` entry), the rest are plain entries
            store.insert("event", {"ts": "2026-09-15T14:00:00+00:00", "type": "crash_test", "payload": body(i)},
                         returning=False)
        else:
            journal.append("crash_test", body(i))
        if i == at and phase == "after":
            os.kill(os.getpid(), signal.SIGKILL)
        print(f"ack {i}", flush=True)
    journal.close()


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
