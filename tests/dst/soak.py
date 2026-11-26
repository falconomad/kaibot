"""The timed soak of the deterministic simulation (M12 ticket 3.2, #464): fresh seeds until the time is up.

    PYTHONHASHSEED=0 PYTHONPATH=src python tests/dst/soak.py --minutes 20 [--first SEED] [--jobs 4] [--hunt 40]
        [--drills "refused start loop"]

Seeds run in `--jobs` processes from `--first` on (default: a start drawn from the clock, printed, so that every
night searches seeds nobody has run). Exit code 1 when a seed breaks an invariant; `--out` gets a Markdown report
with each failing seed, its violations, the end of its trace and the command that runs it again (the nightly job
puts it into an issue).

Coverage (audit 2, 5.20): the report says what the night reached besides the faults: the weeks, the book stop's
modes (`band`: seeds judged by a band table, with the passes it judged and the alerts it raised), the corporate
actions the venue applied (split, spin-off, merger: also in the fault kinds as ca_split, ca_spinoff, ca_merger), and
with `--drills NAME ...` the fail-safe drills played once (tests/drills; the nightly job plays the refused-start
loop, exit code 5). A drill that fails is a failure of the soak.

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
    ap.add_argument("--drills", nargs="*", default=[], metavar="NAME", help="fail-safe drills to play once (5.20)")
    a = ap.parse_args(argv)
    first = a.first if a.first is not None else 1_000_000 + int(time.time()) % 1_000_000_000
    deadline = time.monotonic() + a.minutes * 60
    print(f"soak: seeds from {first}, {a.minutes:g} minutes, {a.jobs} processes", flush=True)
    done, failed, kinds = 0, [], set()
    totals = {"kills": 0, "pauses": 0, "faults": 0, "watchdog_acts": 0, "tick_errors": 0}
    cover = Coverage()
    ctx = mp.get_context("spawn")
    with ctx.Pool(a.jobs, initializer=_init, initargs=(None,)) as pool:
        seeds = iter(range(first, first + 10_000_000))
        pending = [pool.apply_async(_run, (next(seeds),)) for _ in range(a.jobs)]
        while pending:
            r = pending.pop(0)
            seed, ok, report, stats = r.get()
            done += 1
            kinds |= set(stats.get("fault_kinds") or ())
            cover.add(stats)
            for k in totals:
                totals[k] += int(stats.get(k) or 0)
            if not ok:
                failed.append((seed, report))
                print(report, flush=True)
            if time.monotonic() < deadline and (a.max_seeds is None or done + len(pending) < a.max_seeds):
                pending.append(pool.apply_async(_run, (next(seeds),)))
    print(f"soak: {done} seeds, {len(failed)} failed; {totals}; {len(kinds)} kinds of fault injected", flush=True)
    drilled = play_drills(a.drills)
    print(f"soak coverage: {cover.line()}", flush=True)
    lines = [f"{done} seeds from {first}: {len(failed)} failed.", "",
             f"Injected over the run: {totals}; kinds of fault: {', '.join(sorted(kinds)) or 'none'}.", "",
             "### Coverage", "", *cover.lines(), *[f"- drill {n}: {v}" for n, v in drilled.items()], ""]
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
    return 1 if failed or any(not v.startswith("PASS") for v in drilled.values()) else 0


class Coverage:
    """Audit 2, 5.20: what the seeds reached besides the faults (each seed's `Result.stats`)."""

    def __init__(self) -> None:
        self.weeks: dict[str, int] = {}
        self.modes: dict[str, int] = {}
        self.actions: dict[str, int] = {}  # corporate action -> seeds that met it
        self.band_checks = self.band_alerts = 0

    def add(self, stats: dict) -> None:
        if not stats:
            return
        for d, k in ((self.weeks, stats.get("week")), (self.modes, stats.get("book_stop"))):
            if k:
                d[k] = d.get(k, 0) + 1
        for k in stats.get("corporate_actions") or ():
            self.actions[k] = self.actions.get(k, 0) + 1
        self.band_checks += int(stats.get("band_checks") or 0)
        self.band_alerts += int(stats.get("band_alerts") or 0)

    def lines(self) -> list[str]:
        def fmt(d: dict) -> str:
            return ", ".join(f"{k} {v}" for k, v in sorted(d.items())) or "none"

        return [f"- weeks (seeds): {fmt(self.weeks)}",
                f"- book stop mode (seeds): {fmt(self.modes)}; band checks {self.band_checks}, band alerts "
                f"{self.band_alerts}",
                f"- corporate actions (seeds that met one): {fmt({k: self.actions.get(k, 0) for k in CA})}"]

    def line(self) -> str:
        return "; ".join(x[2:] for x in self.lines())


CA = ("split", "spin_off", "cash_merger")


def play_drills(names: list[str]) -> dict[str, str]:
    """The named fail-safe drills of tests/drills, each played once: name -> "PASS ..." or "FAIL ..."."""
    if not names:
        return {}
    import tempfile

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "drills"))
    import drills

    out = {}
    for name in names:
        with tempfile.TemporaryDirectory(prefix="kb-soak-drill-") as d:
            rec = drills.run(name, Path(d))
        n_ok = sum(c.ok for c in rec.checks)
        verdict = "PASS" if rec.ok else "FAIL"
        out[name] = f"{verdict} ({n_ok}/{len(rec.checks)} checks, {rec.seconds} s)" + (
            "" if rec.ok else ": " + "; ".join([rec.error or "", *(str(c) for c in rec.checks if not c.ok)])[:600])
        print(f"soak drill {name}: {out[name]}", flush=True)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
