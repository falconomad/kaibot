"""Run one seed of the deterministic simulation again and print what happened (M12 ticket 3.2, #464).

    PYTHONHASHSEED=0 PYTHONPATH=src python tests/dst/run_seed.py 1234 [--trace] [--no-faults] [--only FAULT ...]
        [--week summer|thanksgiving|thanksgiving_no_calendar|march_gap|october_gap] [--book-loop | --intraday]

Without --week the seed runs in the week it draws (world.week_of), on the engine that week uses.

Exit code 1 when an invariant is broken.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from world import WEEKS, Scenario, run_scenario  # noqa: E402

from kaibot.broker.faulty import DEFAULT_FAULTS, NO_FAULTS  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("seeds", type=int, nargs="+")
    ap.add_argument("--trace", action="store_true", help="print the whole trace, not only its end")
    ap.add_argument("--no-faults", action="store_true", help="the scenario of the seed without any fault")
    ap.add_argument("--only", nargs="*", help="only these faults of the profile")
    ap.add_argument("--log", action="store_true", help="show the engine's own log lines")
    ap.add_argument("--week", choices=[w.name for w in WEEKS], help="the week (default: the seed's own draw)")
    eng = ap.add_mutually_exclusive_group()
    eng.add_argument("--book-loop", dest="book_loop", action="store_const", const=True, default=None,
                     help="the live process's engine, Engine(intraday=False)")
    eng.add_argument("--intraday", dest="book_loop", action="store_const", const=False, help="the intraday engine")
    a = ap.parse_args(argv)
    week = next((w for w in WEEKS if w.name == a.week), None)
    logging.basicConfig(level=logging.INFO if a.log else logging.CRITICAL)
    profile = NO_FAULTS if a.no_faults else DEFAULT_FAULTS.only(*a.only) if a.only else DEFAULT_FAULTS
    bad = 0
    for seed in a.seeds:
        t0 = time.time()
        res = run_scenario(Scenario.from_seed(seed, profile, week=week, book_loop=a.book_loop))
        print(res.report(tail=10_000 if a.trace else 60) if not res.ok or a.trace else f"seed {seed}: ok")
        print(f"  {time.time() - t0:.1f} s  {res.stats}")
        bad += not res.ok
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
