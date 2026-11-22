"""The timed soak of the deterministic simulation (M12 ticket 3.2, #464): fresh seeds until the time is up.

    PYTHONHASHSEED=0 PYTHONPATH=src python tests/dst/soak.py --minutes 20 [--first SEED] [--jobs 4] [--hunt 40]

Seeds run in `--jobs` processes from `--first` on (default: a start drawn from the clock, printed, so that every
night searches seeds nobody has run). Exit code 1 when a seed breaks an invariant; `--out` gets a Markdown report
with each failing seed, its violations, the end of its trace and the command that runs it again (the nightly job
puts it into an issue).

`--hunt N`: afterwards, for each planted bug of scenarios.py, random seeds are run with the bug planted until one
breaks an invariant, at most N. How many seeds each took says how far the random search reaches (the hand-written
scenarios of test_dst.py find each for certain); a bug no seed found is listed, and is not a failure.
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import sys
import time
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

PLANT: str | None = None


def _init(plant: str | None) -> None:
    logging.disable(logging.CRITICAL)
    if plant is not None:
        import pytest
        from scenarios import FINDINGS

        FINDINGS[plant][1](pytest.MonkeyPatch())  # for the life of this worker process


def _run(seed: int) -> tuple[int, bool, str, dict]:
    from world import run_seed

    try:
        res = run_seed(seed)
    except Exception as e:  # the harness itself broke: that is a failure of the seed too
        import traceback

        return seed, False, f"seed {seed}: the simulation raised {type(e).__name__}: {e}\n{traceback.format_exc()}", {}
    return seed, res.ok, res.report(tail=80), res.stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--first", type=int, default=None)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--hunt", type=int, default=0, metavar="N")
    ap.add_argument("--max-seeds", type=int, default=None, help="stop after this many seeds (tests)")
    a = ap.parse_args(argv)
    first = a.first if a.first is not None else 1_000_000 + int(time.time()) % 1_000_000_000
    deadline = time.monotonic() + a.minutes * 60
    print(f"soak: seeds from {first}, {a.minutes:g} minutes, {a.jobs} processes", flush=True)
    done, failed, kinds = 0, [], set()
    totals = {"kills": 0, "pauses": 0, "faults": 0, "watchdog_acts": 0, "tick_errors": 0}
    ctx = mp.get_context("spawn")
    with ctx.Pool(a.jobs, initializer=_init, initargs=(None,)) as pool:
        seeds = iter(range(first, first + 10_000_000))
        pending = [pool.apply_async(_run, (next(seeds),)) for _ in range(a.jobs)]
        while pending:
            r = pending.pop(0)
            seed, ok, report, stats = r.get()
            done += 1
            kinds |= set(stats.get("fault_kinds") or ())
            for k in totals:
                totals[k] += int(stats.get(k) or 0)
            if not ok:
                failed.append((seed, report))
                print(report, flush=True)
            if time.monotonic() < deadline and (a.max_seeds is None or done + len(pending) < a.max_seeds):
                pending.append(pool.apply_async(_run, (next(seeds),)))
    print(f"soak: {done} seeds, {len(failed)} failed; {totals}; {len(kinds)} kinds of fault injected", flush=True)
    lines = [f"{done} seeds from {first}: {len(failed)} failed.", "",
             f"Injected over the run: {totals}; kinds of fault: {', '.join(sorted(kinds)) or 'none'}.", ""]
    for seed, report in failed[:5]:
        lines += [f"### seed {seed}", "", "```", report, "```", ""]
    if len(failed) > 5:
        lines += [f"... and {len(failed) - 5} more: {', '.join(str(s) for s, _ in failed[5:40])}", ""]
    if a.hunt:
        from scenarios import FINDINGS

        lines += ["### Planted bugs found by random seeds", ""]
        for name in sorted(FINDINGS):
            found, n = None, 0
            with ctx.Pool(a.jobs, initializer=_init, initargs=(name,)) as pool:
                for seed, ok, _report, _ in pool.imap(_run, range(first, first + a.hunt)):
                    n += 1
                    if not ok:
                        found = seed
                        break
            line = f"- {name}: " + (f"seed {found}, the {n}th tried" if found else f"none of {a.hunt} seeds")
            print(line, flush=True)
            lines.append(line)
    if a.out is not None:
        a.out.write_text("\n".join(lines) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
